r'''Measure how far the streams run behind the recorded feed (read-only).

    .venv\Scripts\python.exe scripts\feed_compare.py [LOGS_DIR] [YYYY-MM-DD]

Reads LOGS_DIR\feed\<date>.jsonl (written by the daemon with feed_mode = 1) and LOGS_DIR\daemon.log (+ .1), prints for each
soccer minute change and each recorded event the LEAD of the feed over the picture. See daemon/feed_compare.py.
'''
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from daemon.feed_compare import measure, parse_feed, parse_ocr, summarise   # noqa: E402


def main() -> None:
    logs = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent / "logs"
    day = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()
    feed_file = logs / "feed" / f"{day}.jsonl"
    if not feed_file.exists():
        sys.exit(f"{feed_file} not found (was the daemon started with feed_mode = 1?)")
    ocr_lines = []
    for name in ("daemon.log.1", "daemon.log"):
        path = logs / name
        if path.exists():
            ocr_lines += [x for x in path.read_text(encoding="utf-8", errors="replace").splitlines() if x.startswith(day)]
    print(summarise(measure(parse_feed(feed_file.read_text(encoding="utf-8").splitlines()), parse_ocr(ocr_lines))))


if __name__ == "__main__":
    main()
