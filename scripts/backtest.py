r'''Measure how far AHEAD of each goal the streams' scores were (read-only).

    .venv\Scripts\python.exe scripts\backtest.py [LOGS_DIR] [YYYY-MM-DD] [--summary]

Reads LOGS_DIR\daemon.log (+ .1 .2 .3), LOGS_DIR\events.log and LOGS_DIReed\*.jsonl (the ESPN feed recordings, used as ground truth: feed
goals tied to tabs by the `feed map` lines, detected or missed by the OCR, the feed's lead, base rates); without a date it uses every line. Prints, for each goal (a score change of a
stream), the score of that stream and of the best other stream 30/20/10/5 s before, and when the swap rule would first have taken MAIN for it.
See daemon/backtest.py.
'''
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from daemon.backtest import (Rule, measure, measure_feed, parse_cues, parse_feed_events, parse_feed_lags, parse_goals,   # noqa: E402
                             parse_mappings, parse_signals, parse_tile_buildups, parse_tile_sports, summarise, summarise_feed)
from daemon.feed_compare import parse_feed   # noqa: E402


def read_lines(logs: pathlib.Path, names: list[str], day: str | None) -> list[str]:
    lines = []
    for name in names:
        path = logs / name
        if path.exists():
            lines += [x for x in path.read_text(encoding="utf-8", errors="replace").splitlines() if day is None or x.startswith(day)]
    return lines


def main() -> None:
    logs = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1][:2].isdigit() and not sys.argv[1].startswith('-') else pathlib.Path(__file__).resolve().parent.parent / "logs"
    day = next((a for a in sys.argv[1:] if a[:2].isdigit()), None)
    daemon = read_lines(logs, ["daemon.log.3", "daemon.log.2", "daemon.log.1", "daemon.log"], day)
    events = read_lines(logs, ["events.log"], day)
    readings, cues, lags, sports = parse_signals(daemon), parse_cues(events), parse_feed_lags(events), parse_tile_sports(events)
    buildups = parse_tile_buildups(daemon + events)
    leads = measure(readings, parse_goals(events), cues, lags, Rule(), sports=sports, buildups=buildups)
    print(summarise(leads, detail="--summary" not in sys.argv))
    feed_lines = []
    for path in sorted((logs / "feed").glob("*.jsonl")):
        feed_lines += [x for x in path.read_text(encoding="utf-8", errors="replace").splitlines() if day is None or day in x[:20]]
    goals, others = parse_feed_events(parse_feed(feed_lines))
    report = measure_feed(goals, others, parse_mappings(events), parse_goals(events), readings, cues, lags, Rule(), sports, buildups)
    print()
    print(summarise_feed(report, detail="--summary" not in sys.argv))


if __name__ == "__main__":
    main()
