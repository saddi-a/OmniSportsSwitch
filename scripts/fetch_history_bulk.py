r'''Bulk-fetch finished soccer games of a league and date range into the ESPN history cache (offline tool, the daemon never calls it).

    .venv\Scripts\python.exe scripts\fetch_history_bulk.py --league uefa.champions --from 20250901 [--to YYYYMMDD] [--limit 10] [--cache DIR]

Lists FINISHED games from the public scoreboard (one request per calendar year), then fetches each with daemon.espn_history.fetch_game (2 s spacing, complete games
never refetched, cache logs/feed/history, gitignored). Prints one line per game with its tier counts.
'''
import argparse
import collections
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from daemon import espn_history as eh   # noqa: E402


def tier_counts(cache_dir, league, game_id) -> str:
    c = collections.Counter()
    for e in eh.game_events(cache_dir, league, game_id, tiers=(1, 2, 3)):
        c[e["group"] if e["tier"] == 2 else f"t{e['tier']}"] += 1
    return " ".join(f"{k}={c.get(k, 0)}" for k in ("t1", "2a", "2b", "t3"))


def main(argv=None) -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="uefa.champions")
    ap.add_argument("--from", dest="start", required=True, help="YYYYMMDD")
    ap.add_argument("--to", dest="end", default=datetime.date.today().strftime("%Y%m%d"), help="YYYYMMDD (default today)")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--cache", default=str(root / "logs" / "feed" / "history"))
    a = ap.parse_args(argv)
    res = eh.fetch_bulk(a.league, a.start, a.end, a.cache, a.limit)
    print(f"listed {res['listed']} finished games (scoreboard HTTP {res['listing_status']})")
    for r in res["reports"]:
        print(f"  {r['game']}: {r['action']} ({r['status']}), {r['requests']} request(s), {r['pages']} plays page(s); "
              f"{tier_counts(a.cache, a.league, r['game']) if r['action'] == 'fetched' else '-'}")
    print(f"{len(res['reports'])} games tried, {res['requests']} requests in total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
