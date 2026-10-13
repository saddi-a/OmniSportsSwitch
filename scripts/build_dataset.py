r'''Build the labelled learning dataset and the regret / miss numbers (offline, read-only on logs/).

    .venv\Scripts\python.exe scripts\build_dataset.py [YYYY-MM-DD ...] [--out logs\dataset_built] [--summary] [--holdout GAMEID,GAMEID]
                                                      [--fetch-history] [--no-commentary] [--logs DIR]

Reads logs\dataset\<date>.jsonl + .events.jsonl (the recorder), logs\feed\*.jsonl (labels), logs\review\<date>.jsonl (the user's swap marks, which
override the automatic verdict) and, if present, logs\events.log (`feed map` lines). Without dates it uses every recorded day. Writes ONLY into --out:
rows.jsonl (labelled rows; JSON lines because fields are nested/null and a field may be added later), swaps.jsonl, events.jsonl, games.jsonl.

ESPN commentary labels: finished soccer games cached in logs\feed\history\<league>\<game>\ (never in the repo) add tier-1/2 events (shots, saves,
corners, VAR, free kicks, ...) next to the feed events; the cache is read automatically (--no-commentary ignores it). --fetch-history first
fetches and caches the finished games the days' rows watched (sequential, 2 s apart, the daemon's User-Agent, a cached game is never fetched
again, a game whose summary is not FULL_TIME yet is skipped) and prints what it fetched or skipped. Without --fetch-history nothing touches the network.
See daemon/dataset_builder.py, daemon/espn_history.py and docs/dataset-format.md ("Dataset builder").
'''
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from daemon import dataset_builder as db   # noqa: E402
from daemon import espn_history, feed      # noqa: E402


def fetch_history(cache_dir, leagues: dict, watched) -> None:
    """Fetches and caches the finished soccer games in `watched`; prints one line per game. Never raises on a network problem."""
    pacer = espn_history.Pacer()
    for game in sorted(watched):
        league = leagues.get(game)
        if league not in feed.LEAGUES or feed.LEAGUES[league][0] != "soccer":
            continue
        rep = espn_history.fetch_game(league, game, cache_dir, pacer=pacer)
        print(f"  history {league} {game}: {rep['action']} ({rep['status']}), {rep['requests']} request(s), {rep['pages']} plays page(s)")


def main(argv: list[str]) -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    logs = root / "logs"
    out = default_out = logs / "dataset_built"
    days, holdout, summary_only, fetch, use_commentary = [], [], False, False, True
    it = iter(argv)
    for a in it:
        if a == "--out":
            out = pathlib.Path(next(it))
        elif a == "--logs":
            logs = pathlib.Path(next(it))
            out = logs / "dataset_built" if out == default_out else out
        elif a == "--summary":
            summary_only = True
        elif a == "--holdout":
            holdout = [x for x in next(it).split(",") if x]
        elif a == "--fetch-history":
            fetch = True
        elif a == "--no-commentary":
            use_commentary = False
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
    cache = logs / "feed" / "history"
    commentary = None
    if fetch or use_commentary:
        db.assign_games(rows, maps, labels)              # idempotent; only to know which games were watched
        watched = {r["game_id"] for r in rows if r.get("game_id")}
        leagues = db.game_leagues(feed_rows)
        if fetch:
            print("Fetching finished games into", cache)
            fetch_history(cache, leagues, watched)
        if use_commentary:
            commentary = db.load_commentary(cache, leagues, watched)
    result = db.build(rows, events, feed_rows, holdout=holdout, maps=maps, label_to_game=labels, marks=marks, event_marks=event_marks, commentary=commentary)
    db.write_outputs(out, result)
    print(f"days {days}: {len(result['rows'])} rows, {len(result['swaps'])} swaps, {len(result['feed_events'])} feed events of watched games, "
          f"{len(marks)} swap marks + {len(event_marks)} event marks -> {out}; assumed lags {result['assumed_lags']}; {result['confirmed_cues']} confirmed cues")
    if commentary is not None:
        print(f"commentary: {len(commentary)} tier-1/2 events loaded from the cache, {len(result['commentary_events'])} added next to the feed events "
              f"(the rest merged with a feed event or unplaced): {result['commentary_report']}")
    print(db.format_summary(result["summary"]))
    if not summary_only:
        print("\nGames (lag = picture time minus feed time; lag_wc = picture time minus commentary wallclock):")
        for g in result["games"]:
            print("  ", json.dumps(g))
        print("\nSwaps:")
        for s in result["swaps"]:
            print(f"  {s['t']:.0f} {s['sport']} {s['source']} {str(s['old'])[-4:]}->{str(s['new'])[-4:]} {s['verdict']}"
                  + (f" (auto {s['auto_verdict']}, mark {s['user_mark']})" if s.get("user_mark") else "")
                  + f" tiers {s['tier_old']}/{s['tier_new']}" + (" FLIP" if s["flipflop"] else "") + (f" [{s['reason_excluded']}]" if s["reason_excluded"] else ""))
        print("\nTier-1/2 events (feed and commentary):")
        for e in result["events"]:
            print(f"  {e['t']:.0f} {e['sport']} tab {str(e['tab'])[-4:]} {e['kind']} tier {e['tier']} {e.get('src')} {e['align']} -> {e['status']} lead {e['lead_s']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
