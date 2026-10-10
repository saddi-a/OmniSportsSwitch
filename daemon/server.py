"""WebSocket server: receives audio levels from the Edge extension, sends back swap commands.

Messages are JSON, one object per message.

Extension -> daemon:
  {"type": "register",   "tab_id": 12, "weight": 1.0, "is_main": false}
  {"type": "unregister", "tab_id": 12}
  {"type": "audio",      "tab_id": 12, "rms": 0.031}
  {"type": "set_weight", "tab_id": 12, "weight": 1.5}
  {"type": "freeze",     "value": true}

Daemon -> extension:
  {"type": "swap", "promote": 12, "demote": 7}
"""
import asyncio
import json
import logging
import time

from websockets.asyncio.server import serve

from daemon.scoring import Scorer

HOST, PORT = "127.0.0.1", 8765
DECIDE_INTERVAL_S = 0.25

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

    async def _client(self, ws) -> None:
        self.clients.add(ws)
        log.info("extension connected (%d client(s))", len(self.clients))
        try:
            async for raw in ws:
                try:
                    self.handle_message(json.loads(raw))
                except (ValueError, KeyError, TypeError) as e:
                    log.warning("bad message %r: %s", raw, e)
        finally:
            self.clients.discard(ws)
            log.info("extension disconnected")

    async def _decide_loop(self) -> None:
        while True:
            await asyncio.sleep(DECIDE_INTERVAL_S)
            swap = self.scorer.decide(time.monotonic())
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
