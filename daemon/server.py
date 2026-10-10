"""WebSocket server: receives audio levels from the Edge extension, sends back swap commands.

Messages are JSON, one object per message.

Extension -> daemon:
  {"type": "register",   "tab_id": 12, "weight": 1.0, "is_main": false}
  {"type": "unregister", "tab_id": 12}
  {"type": "audio",      "tab_id": 12, "rms": 0.031}   (simple loudness-only form)
  {"type": "set_weight", "tab_id": 12, "weight": 1.5}
  {"type": "freeze",     "value": true}
  binary frame: 4-byte little-endian tab id + 32 kHz mono int16 samples (100 ms per frame)

Daemon -> extension:
  {"type": "swap", "promote": 12, "demote": 7}
"""
import asyncio
import json
import logging
import struct
import time

import numpy as np
from websockets.asyncio.server import serve

from daemon.scoring import Scorer

HOST, PORT = "127.0.0.1", 8765
DECIDE_INTERVAL_S = 0.25
SCORE_LOG_INTERVAL_S = 2.0
CLASSIFY_INTERVAL_S = 1.0
SAMPLE_RATE = 32000
CLASSIFY_WINDOW = 3 * SAMPLE_RATE  # classify the last 3 seconds of each tab
CROWD_CLASSES = ["Cheering", "Crowd", "Applause"]

log = logging.getLogger("daemon")


class Server:
    def __init__(self, scorer: Scorer | None = None, classifier=None):
        self.scorer = scorer or Scorer()
        self.classifier = classifier
        self.clients = set()
        self.buffers: dict[int, np.ndarray] = {}  # tab id -> last 3 s of audio
        self.tags: dict[int, np.ndarray] = {}     # tab id -> latest classifier probabilities

    def handle_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "register":
            self.scorer.register(msg["tab_id"], msg.get("weight", 1.0), msg.get("is_main", False))
            log.info("registered tab %s", msg["tab_id"])
        elif kind == "unregister":
            self.scorer.remove(msg["tab_id"])
            self.buffers.pop(msg["tab_id"], None)
            self.tags.pop(msg["tab_id"], None)
            log.info("unregistered tab %s", msg["tab_id"])
        elif kind == "audio":
            self.scorer.add_audio(msg["tab_id"], float(msg["rms"]), time.monotonic())
        elif kind == "set_weight":
            self.scorer.set_weight(msg["tab_id"], float(msg["weight"]))
        elif kind == "freeze":
            self.scorer.frozen = bool(msg["value"])
        else:
            log.warning("unknown message type: %r", kind)

    def handle_audio(self, raw: bytes) -> None:
        """Binary frame: 4-byte little-endian tab id, then 16-bit little-endian mono samples."""
        tab_id = struct.unpack_from("<I", raw)[0]
        samples = np.frombuffer(raw, dtype="<i2", offset=4).astype(np.float32) / 32768.0
        if samples.size == 0:
            return
        rms = float(np.sqrt(np.mean(samples * samples)))
        self.scorer.add_audio(tab_id, rms, time.monotonic())
        if self.classifier is not None:
            old = self.buffers.get(tab_id)
            buf = samples if old is None else np.concatenate([old, samples])
            self.buffers[tab_id] = buf[-CLASSIFY_WINDOW:]

    async def _client(self, ws) -> None:
        self.clients.add(ws)
        log.info("extension connected (%d client(s))", len(self.clients))
        try:
            async for raw in ws:
                try:
                    if isinstance(raw, bytes):
                        self.handle_audio(raw)
                    else:
                        self.handle_message(json.loads(raw))
                except (ValueError, KeyError, TypeError, struct.error) as e:
                    log.warning("bad message (%d bytes): %s", len(raw), e)
        finally:
            self.clients.discard(ws)
            log.info("extension disconnected")

    def _log_scores(self, now: float) -> None:
        for tab_id in self.scorer.streams:
            tag = "MAIN " if tab_id == self.scorer.main else ""
            c = self.scorer.components(tab_id, now)
            if c is None:
                log.info("  %s%s: warming up or no audio", tag, tab_id)
                continue
            text = (f"{tag}{tab_id}: score={c['score']:.2f} surge={c['surge']:.2f} crowd={c['crowd']:.2f} "
                    f"speech={c['speech']:.2f} music={c['music']:.2f}")
            probs = self.tags.get(tab_id)
            if probs is not None:
                text += " top=" + ",".join(f"{n}:{p:.2f}" for n, p in self.classifier.top(probs, 3))
            log.info("  %s", text)

    async def _classify_loop(self) -> None:
        while True:
            await asyncio.sleep(CLASSIFY_INTERVAL_S)
            ready = [t for t, b in self.buffers.items() if len(b) >= CLASSIFY_WINDOW]
            if not ready:
                continue
            batch = np.stack([self.buffers[t] for t in ready])
            probs = await asyncio.to_thread(self.classifier.predict, batch)
            idx = self.classifier.index
            now = time.monotonic()
            for tab_id, p in zip(ready, probs):
                self.tags[tab_id] = p
                crowd = float(max(p[idx[name]] for name in CROWD_CLASSES))
                self.scorer.set_tags(tab_id, crowd, float(p[idx["Speech"]]), float(p[idx["Music"]]), now)

    async def _decide_loop(self) -> None:
        last_log = 0.0
        while True:
            await asyncio.sleep(DECIDE_INTERVAL_S)
            now = time.monotonic()
            if now - last_log >= SCORE_LOG_INTERVAL_S:
                self._log_scores(now)
                last_log = now
            swap = self.scorer.decide(now)
            if swap:
                log.info("swap: promote tab %d, demote tab %d | %s", swap.promote, swap.demote, swap.reason)
                text = json.dumps({"type": "swap", "promote": swap.promote, "demote": swap.demote})
                for ws in list(self.clients):
                    await ws.send(text)

    async def run(self, host: str = HOST, port: int = PORT) -> None:
        async with serve(self._client, host, port):
            log.info("listening on ws://%s:%d", host, port)
            loops = [self._decide_loop()]
            if self.classifier is not None:
                loops.append(self._classify_loop())
            await asyncio.gather(*loops)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from daemon.audio_classifier import AudioClassifier

    classifier = AudioClassifier()
    log.info("audio classifier ready on %s", classifier.device)
    try:
        asyncio.run(Server(classifier=classifier).run())
    except KeyboardInterrupt:
        pass
