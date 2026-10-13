r'''Build the labelled learning dataset and the regret / miss numbers (offline, read-only on logs/).

    .venv\Scripts\python.exe scripts\build_dataset.py [YYYY-MM-DD ...] [--out logs\dataset_built] [--summary] [--holdout GAMEID,GAMEID]

Reads logs\dataset\<date>.jsonl + .events.jsonl (the recorder), logs\feed\*.jsonl (labels), logs\review\<date>.jsonl (the user's swap marks, which
override the automatic verdict) and, if present, logs\events.log (`feed map` lines). Without dates it uses every recorded day. Writes ONLY into --out:
rows.jsonl (labelled rows; JSON lines because fields are nested/null and a field may be added later), swaps.jsonl, events.jsonl, games.jsonl.
See daemon/dataset_builder.py and docs/dataset-format.md ("Dataset builder").
'''
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from daemon import dataset_builder as db   # noqa: E402


def main(argv: list[str]) -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    logs = root / "logs"
    out = logs / "dataset_built"
    days, holdout, summary_only = [], [], False
    it = iter(argv)
    for a in it:
        if a == "--out":
            out = pathlib.Path(next(it))
        elif a == "--summary":
            summary_only = True
        elif a == "--holdout":
            holdout = [x for x in next(it).split(",") if x]
        elif a[:2].isdigit():
            days.append(a)
        else:
            print(__doc__)
            return 2
    days = days or db.available_days(logs / "dataset")
    rows, events = db.read_days(logs / "dataset", days)
    if not rows:
        print("No dataset rows for", days or "any day")
        return 1
    t0, t1 = db.row_span(rows)
    feed_rows = db.read_feed(logs / "feed", t0, t1)
    maps, labels = db.load_maps(logs / "events.log", feed_rows)
    marks = db.load_marks(logs, days)
    event_marks = db.load_event_marks(logs, days)
    result = db.build(rows, events, feed_rows, holdout=holdout, maps=maps, label_to_game=labels, marks=marks, event_marks=event_marks)
    db.write_outputs(out, result)
    print(f"days {days}: {len(result['rows'])} rows, {len(result['swaps'])} swaps, {len(result['feed_events'])} feed events of watched games, "
          f"{len(marks)} swap marks + {len(event_marks)} event marks -> {out}; assumed lags {result['assumed_lags']}; {result['confirmed_cues']} confirmed cues")
    print(db.format_summary(result["summary"]))
    if not summary_only:
        print("\nGames (lag = picture time minus feed time):")
        for g in result["games"]:
            print("  ", json.dumps(g))
        print("\nSwaps:")
        for s in result["swaps"]:
            print(f"  {s['t']:.0f} {s['sport']} {s['source']} {str(s['old'])[-4:]}->{str(s['new'])[-4:]} {s['verdict']}"
                  + (f" (auto {s['auto_verdict']}, mark {s['user_mark']})" if s.get("user_mark") else "")
                  + f" tiers {s['tier_old']}/{s['tier_new']}" + (" FLIP" if s["flipflop"] else "") + (f" [{s['reason_excluded']}]" if s["reason_excluded"] else ""))
        print("\nTier-1/2 feed events:")
        for e in result["events"]:
            print(f"  {e['t']:.0f} {e['sport']} tab {str(e['tab'])[-4:]} {e['kind']} tier {e['tier']} {e['align']} -> {e['status']} lead {e['lead_s']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
