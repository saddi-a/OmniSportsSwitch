"""Saves a sample of every video area currently playing, to study how well the scoreboard readers cope.

Usage (daemon running, streams tracked):   .venv\\Scripts\\python.exe scripts\\capture_samples.py "NFL Fox"
The label names the folder (samples/<label>/). Each sample is a screenshot (.png) and what OCR found (.json:
every text item with its box, plus the clocks and the score each reader finds). samples/ is not tracked by git,
so broadcast screenshots stay on this PC; to turn a sample into a test, copy only its .json into
daemon/tests/fixtures/ (text and positions, no picture).
"""
import ast
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mss  # noqa: E402
import mss.tools  # noqa: E402
import numpy as np  # noqa: E402

from daemon.score_watch import find_score, find_score_geo  # noqa: E402
from daemon.screen_ocr import ScreenReader, find_clocks  # noqa: E402

AREA_RE = re.compile(r"video area tab (\d+): (\{.*\})\s*$")


def latest_video_areas() -> dict[int, dict]:
    """Newest 'video area' line per tab from the daemon log."""
    areas = {}
    for line in (ROOT / "logs" / "daemon.log").read_text(encoding="utf-8", errors="replace").splitlines():
        m = AREA_RE.search(line)
        if m:
            try:
                areas[int(m.group(1))] = ast.literal_eval(m.group(2))
            except (ValueError, SyntaxError):
                pass
    return areas


def main() -> None:
    label = re.sub(r"[^\w .-]", "", sys.argv[1]).strip() if len(sys.argv) > 1 else "unlabelled"
    out = ROOT / "samples" / label
    out.mkdir(parents=True, exist_ok=True)
    reader = ScreenReader()
    stamp = time.strftime("%H%M%S")
    with mss.mss() as screen:
        for tab_id, info in latest_video_areas().items():
            if not info.get("visible"):
                continue
            for rect in info.get("rects", []):
                region = {k: int(rect[k]) for k in ("left", "top", "width", "height")}
                shot = screen.grab(region)
                name = f"{stamp}_tab{str(tab_id)[-4:]}_tile{rect.get('index', 0)}"
                mss.tools.to_png(shot.rgb, shot.size, output=str(out / f"{name}.png"))
                items = reader.read_items(np.asarray(shot)[:, :, [2, 1, 0]])
                texts = [i["text"] for i in items]
                clocks = find_clocks(texts)
                geo, text_only = find_score_geo(items, clocks), find_score(texts, clocks)
                (out / f"{name}.json").write_text(
                    json.dumps({"label": label, "size": [shot.width, shot.height], "clocks": clocks,
                                "geo_score": geo, "text_score": text_only, "items": items}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                print(f"{name}: clocks={clocks[:2]} geo={geo} text-only={text_only}  texts={texts[:8]}")
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
