"""WebSocket server: receives audio levels from the Edge extension, sends back swap commands.

Messages are JSON, one object per message.

Extension -> daemon:
  {"type": "register",   "tab_id": 12, "weight": 1.0, "is_main": false, "sport": "Football", "status": "auto|live|break|over"}
  {"type": "unregister", "tab_id": 12}
  {"type": "audio",      "tab_id": 12, "rms": 0.031}   (simple loudness-only form)
  {"type": "set_weight", "tab_id": 12, "weight": 1.5}
  {"type": "freeze",     "value": true}
  {"type": "video_rect", "tab_id": 12, "rects": [{"left":0,"top":0,"width":1920,"height":1080}], "visible": true, "fullscreen": false}
  {"type": "focus_tile", "tab_id": 12, "index": 2}   (daemon -> extension: click that video tile)
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

from daemon.game_state import GameTracker, combine
from daemon.multiview import TileFocuser
from daemon.score_watch import ScoreWatcher
from daemon.screen_ocr import find_clocks, grab
from daemon.scoring import Scorer

HOST, PORT = "127.0.0.1", 8765
DECIDE_INTERVAL_S = 0.25
SCORE_LOG_INTERVAL_S = 2.0
CLASSIFY_INTERVAL_S = 1.0
OCR_INTERVAL_S = 2.0
OCR_MIN_WIDTH = 900  # smaller areas (multiview tiles) are enlarged before reading
SAMPLE_RATE = 32000
CLASSIFY_WINDOW = 3 * SAMPLE_RATE  # classify the last 3 seconds of each tab
CROWD_CLASSES = ["Cheering", "Crowd", "Applause"]

log = logging.getLogger("daemon")


class Server:
    def __init__(self, scorer: Scorer | None = None, classifier=None, screen_reader=None):
        self.scorer = scorer or Scorer()
        self.classifier = classifier
        self.screen_reader = screen_reader
        self.ocr: dict[tuple[int, int], dict] = {}  # (tab id, video index) -> latest OCR reading
        self.sports: dict[int, str | None] = {}     # tab id -> sport label chosen in the popup
        self.trackers: dict[tuple[int, int], GameTracker] = {}  # (tab id, video index) -> game state
        self.watchers: dict[tuple[int, int], ScoreWatcher] = {}  # (tab id, video index) -> score-change detector
        self.event_at: dict[tuple[int, int], float] = {}        # (tab id, video index) -> last score change
        self.focuser = TileFocuser()
        self.clients = set()
        self.buffers: dict[int, np.ndarray] = {}  # tab id -> last 3 s of audio
        self.rects: dict[int, dict] = {}          # tab id -> where its playing videos are on screen
        self.tags: dict[int, np.ndarray] = {}     # tab id -> latest classifier probabilities

    def handle_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "register":
            self.scorer.register(msg["tab_id"], msg.get("weight", 1.0), msg.get("is_main", False))
            tab_id = msg["tab_id"]
            self.sports[tab_id] = msg.get("sport") or None
            status = msg.get("status", "auto")
            self.scorer.set_status(tab_id, None if status == "auto" else status)
            for (t, _), tracker in self.trackers.items():
                if t == tab_id:
                    tracker.set_sport(self.sports[tab_id])
            log.info("registered tab %s (sport=%s, status=%s)", tab_id, self.sports[tab_id], status)
        elif kind == "unregister":
            self.scorer.remove(msg["tab_id"])
            self.buffers.pop(msg["tab_id"], None)
            self.rects.pop(msg["tab_id"], None)
            for store in (self.ocr, self.trackers, self.watchers, self.event_at):
                for key in [k for k in store if k[0] == msg["tab_id"]]:
                    del store[key]
            self.sports.pop(msg["tab_id"], None)
            self.tags.pop(msg["tab_id"], None)
            log.info("unregistered tab %s", msg["tab_id"])
        elif kind == "audio":
            self.scorer.add_audio(msg["tab_id"], float(msg["rms"]), time.monotonic())
        elif kind == "video_rect":
            tab_id = msg["tab_id"]
            info = {"rects": msg.get("rects") or [], "visible": bool(msg.get("visible")), "fullscreen": bool(msg.get("fullscreen"))}
            if self.rects.get(tab_id) != info:
                log.info("video area tab %s: %s", tab_id, info)
            self.rects[tab_id] = info
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
            text = (f"{tag}{tab_id}: {c['status']} score={c['score']:.2f} surge={c['surge']:.2f} crowd={c['crowd']:.2f} "
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

    def _ocr_pass(self, jobs: list[tuple[int, int, dict]]) -> dict:
        """Runs in a worker thread: screenshot each video area and read its text."""
        from PIL import Image

        results = {}
        for tab_id, index, rect in jobs:
            image = grab(rect)
            if image.shape[1] < OCR_MIN_WIDTH:
                factor = OCR_MIN_WIDTH / image.shape[1]
                size = (OCR_MIN_WIDTH, int(image.shape[0] * factor))
                image = np.asarray(Image.fromarray(image).resize(size, Image.LANCZOS))
            texts = [text for text, _ in self.screen_reader.read(image)]
            results[(tab_id, index)] = {"texts": texts, "clocks": find_clocks(texts)}
        return results

    def _update_game_states(self, results: dict, now: float) -> None:
        per_tab: dict[int, list[str]] = {}
        for (tab_id, index), r in results.items():
            tracker = self.trackers.setdefault((tab_id, index), GameTracker(self.sports.get(tab_id)))
            per_tab.setdefault(tab_id, []).append(tracker.update(now, r["texts"], r["clocks"]))
            if self.watchers.setdefault((tab_id, index), ScoreWatcher()).update(r["texts"], r["clocks"]):
                self.event_at[(tab_id, index)] = now
                self.scorer.set_event(tab_id, now)
                log.info("score change tab %s tile %s: %s", tab_id, index, r["texts"][:8])
        for tab_id, states in per_tab.items():
            state = combine(states)
            stream = self.scorer.streams.get(tab_id)
            if stream is not None and stream.auto_state != state:
                log.info("game state tab %s: %s -> %s", tab_id, stream.auto_state, state)
            self.scorer.set_auto_state(tab_id, state)

    def _focus_commands(self, now: float) -> list[dict]:
        """For multiview tabs: which tile should have the sound?"""
        commands = []
        for tab_id, info in self.rects.items():
            if not info["visible"] or len(info["rects"]) < 2:
                continue
            tiles = []
            for pos, rect in enumerate(info["rects"][:4]):
                index = rect.get("index", pos)
                tracker = self.trackers.get((tab_id, index))
                age = now - self.event_at.get((tab_id, index), float("-inf"))
                tile = {"index": index, "audible": bool(rect.get("audible")),
                        "state": tracker.state if tracker else "live",
                        "event": max(0.0, 1.0 - age / self.scorer.config.event_s)}
                if tile["audible"]:
                    c = self.scorer.components(tab_id, now)
                    tile["crowd"] = c["crowd"] if c else 0.0
                tiles.append(tile)
            target = self.focuser.decide(tab_id, now, tiles)
            if target is not None:
                log.info("focus tile: tab %s -> tile %s | %s", tab_id, target, tiles)
                commands.append({"type": "focus_tile", "tab_id": tab_id, "index": target})
        return commands

    async def _broadcast(self, obj: dict) -> None:
        text = json.dumps(obj)
        for ws in list(self.clients):
            await ws.send(text)

    async def _ocr_loop(self) -> None:
        while True:
            await asyncio.sleep(OCR_INTERVAL_S)
            jobs = [
                (tab_id, i, rect)
                for tab_id, info in list(self.rects.items())
                if info["visible"]
                for i, rect in ((r.get("index", pos), r) for pos, r in enumerate(info["rects"][:4]))
            ]
            if not jobs:
                continue
            try:
                results = await asyncio.to_thread(self._ocr_pass, jobs)
            except Exception as e:  # e.g. window moved off-screen mid-capture
                log.warning("ocr pass failed: %s", e)
                continue
            self.ocr.update(results)
            self._update_game_states(results, time.monotonic())
            for command in self._focus_commands(time.monotonic()):
                await self._broadcast(command)
            for (tab_id, i), r in results.items():
                log.info("  ocr tab %s[%d]: clocks=%s texts=%s", tab_id, i, r["clocks"], r["texts"][:10])

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
                await self._broadcast({"type": "swap", "promote": swap.promote, "demote": swap.demote})

    async def run(self, host: str = HOST, port: int = PORT) -> None:
        async with serve(self._client, host, port):
            log.info("listening on ws://%s:%d", host, port)
            loops = [self._decide_loop()]
            if self.classifier is not None:
                loops.append(self._classify_loop())
            if self.screen_reader is not None:
                loops.append(self._ocr_loop())
            await asyncio.gather(*loops)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from daemon.audio_classifier import AudioClassifier

    from daemon.screen_ocr import ScreenReader

    classifier = AudioClassifier()
    log.info("audio classifier ready on %s", classifier.device)
    screen_reader = ScreenReader()
    log.info("screen reader ready")
    try:
        asyncio.run(Server(classifier=classifier, screen_reader=screen_reader).run())
    except KeyboardInterrupt:
        pass
