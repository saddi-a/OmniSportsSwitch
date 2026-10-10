"""Read audio levels collected by the tabCapture spike extension, via the Edge debug port.
Usage: read_levels.py            -> print levels/errors
       read_levels.py gain 0     -> set gain 0 on every captured tab, then print
"""
import asyncio
import json
import sys
import urllib.request

import websockets

PORT = 9222


async def evaluate(ws_url, expression):
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": expression, "returnByValue": True, "awaitPromise": True}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                return msg["result"].get("result", {}).get("value", msg)


async def main():
    targets = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json"))
    sw = [t for t in targets if t["type"] == "service_worker" and "spike" in t.get("title", "").lower() + t["url"]]
    sw = sw or [t for t in targets if t["type"] == "service_worker" and "extension" in t["url"]]
    if not sw:
        print("extension service worker not found; targets:", [(t["type"], t["url"][:60]) for t in targets])
        return
    ws_url = sw[0]["webSocketDebuggerUrl"]
    if len(sys.argv) == 3 and sys.argv[1] == "gain":
        v = float(sys.argv[2])
        await evaluate(ws_url, f"Object.keys(levels).forEach(id => setGain(Number(id), {v}))")
        await asyncio.sleep(2)
    print(await evaluate(ws_url, "JSON.stringify({now: Date.now(), levels, errors}, null, 1)"))


sys.stdout.reconfigure(encoding="utf-8")
asyncio.run(main())
