"""Phase 0 spike: can we screenshot the Edge test window's pixels while DRM video plays?
Captures only the bounds of the Edge window launched with --remote-debugging-port, never other screens."""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import mss
import mss.tools
import websockets

PORT = 9222
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out"
OUT.mkdir(parents=True, exist_ok=True)


async def window_bounds(ws_url):
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Browser.getWindowForTarget"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                return msg["result"]["bounds"]


async def main():
    tabs = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json"))
    seen = set()
    with mss.mss() as sct:
        for t in tabs:
            if t["type"] != "page" or t["url"].startswith(("edge://", "devtools://")):
                continue
            b = await window_bounds(t["webSocketDebuggerUrl"])
            key = (b["left"], b["top"], b["width"], b["height"])
            if key in seen:
                continue
            seen.add(key)
            print("window bounds:", b)
            region = {"left": b["left"], "top": b["top"], "width": b["width"], "height": b["height"]}
            img = sct.grab(region)
            path = OUT / f"edge_window_{len(seen)}.png"
            mss.tools.to_png(img.rgb, img.size, output=str(path))
            print("saved", path)


asyncio.run(main())
