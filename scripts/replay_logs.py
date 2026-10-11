"""Replays the saved OCR logs through the real score reader and game tracker (offline, no daemon needed).

Usage (from the repository root, with the main folder's logs):
  .venv\Scripts\python.exe scripts\replay_logs.py coverage [LOGDIR]
      per tab/tile: frames where a scorebug is surely on screen (down/quarter text) and how many give no score
  .venv\Scripts\python.exe scripts\replay_logs.py score TAB TILE [SINCE] [LOGDIR]
      the voted score over time and the frames where a change fires (e.g. score 452597555 0 "2026-10-11 02:00")
  .venv\Scripts\python.exe scripts\replay_logs.py state TAB TILE [SINCE] [LOGDIR]
      the live/break/over state over time from the `ocr tab` lines of daemon.log

boxes.log only keeps (text, height, x, y) per item, so the right edge of each box is estimated (0.55 x height per
character); fine for the geometry rules, but check a screenshot of the window before trusting a result.
"""
import ast
import collections
import pathlib
import re
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from daemon.game_state import DOWN_RE, QUARTER_RE, GameTracker  # noqa: E402
from daemon.score_watch import STAT_GRAPHIC_RE, ScoreWatcher, find_score, find_score_geo  # noqa: E402
from daemon.screen_ocr import find_clocks  # noqa: E402

BOX_RE = re.compile(r"(\S+ \S+) .*?tab (\d+)\[(\d+)\] (\[.*\])\s*$")
OCR_RE = re.compile(r"(\S+ \S+)\s+ocr tab (\d+)\[(\d+)\]: clocks=(\[.*?\]) texts=(\[.*\])\s*$")


def stamp(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S,%f").timestamp()


def boxes(logdir: pathlib.Path):
    for line in (logdir / "boxes.log").read_text(encoding="utf-8", errors="replace").splitlines():
        m = BOX_RE.match(line)
        if not m:
            continue
        try:
            raw = ast.literal_eval(m.group(4))
        except (ValueError, SyntaxError):
            continue
        items = [{"text": t, "x0": x, "y0": y, "x1": x + h * 0.55 * len(t), "y1": y + h} for t, h, x, y in raw]
        yield m.group(1), m.group(2), m.group(3), items


def coverage(logdir):
    stats = collections.defaultdict(collections.Counter)
    for when, tab, tile, items in boxes(logdir):
        texts = [i["text"] for i in items]
        if any(STAT_GRAPHIC_RE.search(t) for t in texts):
            continue
        if not any(DOWN_RE.fullmatch(t.strip()) or QUARTER_RE.fullmatch(t.strip()) for t in texts):
            continue   # only frames with a scorebug surely on screen
        clocks = find_clocks(texts)
        found = find_score_geo(items, clocks) or find_score(texts, clocks, anchored_only=True)
        stats[(tab, tile)]["board"] += 1
        stats[(tab, tile)]["noscore"] += found is None
    for key, c in sorted(stats.items()):
        print(key, dict(c), f"{100 * c['noscore'] / max(c['board'], 1):.0f}% unread")


def score(logdir, tab, tile, since):
    watcher, last = ScoreWatcher(), "start"
    for when, t, i, items in boxes(logdir):
        if t != tab or i != tile or when < since:
            continue
        texts = [x["text"] for x in items]
        fired = watcher.update(texts, find_clocks(texts), stamp(when), items)
        if fired or watcher.score != last:
            print(when[11:19], watcher.score, "FIRED" if fired else "")
            last = watcher.score


def state(logdir, tab, tile, since):
    tracker, last = GameTracker(None), None
    for line in (logdir / "daemon.log").read_text(encoding="utf-8", errors="replace").splitlines():
        m = OCR_RE.match(line)
        if not m or m.group(2) != tab or m.group(3) != tile or m.group(1) < since:
            continue
        try:
            clocks, texts = ast.literal_eval(m.group(4)), ast.literal_eval(m.group(5))
        except (ValueError, SyntaxError):
            continue
        now = tracker.update(stamp(m.group(1)), texts, clocks)
        if now != last:
            print(m.group(1)[11:19], now)
            last = now


if __name__ == "__main__":
    args = sys.argv[1:]
    default_logs = ROOT / "logs"
    if not args or args[0] not in ("coverage", "score", "state"):
        sys.exit(__doc__)
    if args[0] == "coverage":
        coverage(pathlib.Path(args[1]) if len(args) > 1 else default_logs)
    else:
        logs = pathlib.Path(args[4]) if len(args) > 4 else default_logs
        (score if args[0] == "score" else state)(logs, args[1], args[2], args[3] if len(args) > 3 else "")
