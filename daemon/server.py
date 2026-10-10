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

log = logging.getLogger("daemon")


class Server:
    def __init__(self, scorer: Scorer | None = None):
        self.scorer = scorer or Scorer()
        self.clients = set()

    def handle_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "register":
            self.scorer.register(msg["tab_id"], msg.get("weight", 1.0), msg.get("is_main", False))
            log.info("registered tab %s", msg["tab_id"])
        elif kind == "unregister":
            self.scorer.remove(msg["tab_id"])
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
        parts = []
        for tab_id in self.scorer.streams:
            score = self.scorer.score(tab_id, now)
            tag = "MAIN " if tab_id == self.scorer.main else ""
            parts.append(f"{tag}{tab_id}={'-' if score is None else f'{score:.4f}'}")
        if parts:
            log.info("scores: %s", "  ".join(parts))

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
                log.info("swap: promote tab %d, demote tab %d", swap.promote, swap.demote)
                text = json.dumps({"type": "swap", "promote": swap.promote, "demote": swap.demote})
                for ws in list(self.clients):
                    await ws.send(text)

    async def run(self, host: str = HOST, port: int = PORT) -> None:
        async with serve(self._client, host, port):
            log.info("listening on ws://%s:%d", host, port)
            await self._decide_loop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        asyncio.run(Server().run())
    except KeyboardInterrupt:
        pass
