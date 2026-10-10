"""Phase 0 spike: run frame_capture_test.js in every tab of an Edge launched with --remote-debugging-port."""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import websockets

PORT = 9222
SNIPPET = (Path(__file__).parent / "frame_capture_test.js").read_text(encoding="utf-8")

# Collect results instead of console.log so we can read them back.
EXPRESSION = """
(() => {
  const out = [];
  const log = (...a) => out.push(a.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(' '));
  const console = { log };
  %s
  return out.join('\\n');
})()
""" % SNIPPET


async def eval_in_tab(ws_url):
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": EXPRESSION, "returnByValue": True}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                return msg["result"].get("result", {}).get("value", msg)


async def main():
    tabs = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json"))
    for t in tabs:
        if t["type"] != "page" or t["url"].startswith(("edge://", "devtools://")):
            continue
        print(f"\n=== {t['title'][:60]} | {t['url'][:70]}")
        try:
            print(await asyncio.wait_for(eval_in_tab(t["webSocketDebuggerUrl"]), 15))
        except Exception as e:
            print("  error:", repr(e))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
