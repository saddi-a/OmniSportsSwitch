"""Offline check: how long before a scoreboard change does the audio react, and how often does it react for nothing?

Reads logs/daemon.log (the per-stream score lines written every ~2 s) and logs/events.log (score changes and big
plays). For every event it looks at the same tab in the 30 s before the event and reports when the crowd or surge
signal first crossed a threshold. It also counts threshold crossings that no event follows (false alarms).

Caveat: the events themselves can be false (stat graphics, records), so run it with --since after a fix.

    .venv\\Scripts\\python.exe scripts\\analyze_audio_lead.py --since "2026-10-11 01:12:24"
"""
import argparse
import datetime as dt
import pathlib
import re
import statistics

LOGS = pathlib.Path(__file__).resolve().parent.parent / "logs"
STAMP = "%Y-%m-%d %H:%M:%S,%f"
SCORE_LINE = re.compile(r"^(\S+ \S+)\s+(?:MAIN )?(\d+): (\w+) fav=\S+ score=(\S+) surge=(\S+) crowd=(\S+) speech=(\S+) music=(\S+)")
EVENT_LINE = re.compile(r"^(\S+ \S+) (score change|big play) tab (\d+)")


def parse_time(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, STAMP)


def read_scores(since):
    """{tab id: [(time, state, surge, crowd, speech, music)]}"""
    streams = {}
    for line in (LOGS / "daemon.log").read_text(encoding="utf-8", errors="replace").splitlines():
        m = SCORE_LINE.match(line)
        if not m:
            continue
        t = parse_time(m[1])
        if since and t < since:
            continue
        streams.setdefault(int(m[2]), []).append((t, m[3], float(m[5]), float(m[6]), float(m[7]), float(m[8])))
    return streams


def read_events(since):
    events = []
    for line in (LOGS / "events.log").read_text(encoding="utf-8", errors="replace").splitlines():
        m = EVENT_LINE.match(line)
        if m and (not since or parse_time(m[1]) >= since):
            events.append((parse_time(m[1]), m[2], int(m[3])))
    return events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help='only look at the log from this time, e.g. "2026-10-11 01:12:24"')
    ap.add_argument("--crowd", type=float, default=0.25, help="crowd level that counts as a reaction (default 0.25)")
    ap.add_argument("--surge", type=float, default=0.30, help="surge level that counts as a reaction (default 0.30)")
    ap.add_argument("--before", type=float, default=30.0, help="seconds before an event to look back (default 30)")
    ap.add_argument("--after", type=float, default=10.0, help="seconds after an event that still count (default 10)")
    args = ap.parse_args()
    since = parse_time(args.since + ",000") if args.since else None

    streams = read_scores(since)
    events = read_events(since)
    reacts = lambda row: row[3] >= args.crowd or row[2] >= args.surge  # noqa: E731

    print(f"{len(events)} event(s), {sum(len(v) for v in streams.values())} audio rows, "
          f"{len(streams)} stream(s); reaction = crowd >= {args.crowd} or surge >= {args.surge}\n")

    leads, missed = [], 0
    for t, kind, tab in events:
        rows = [r for r in streams.get(tab, []) if t - dt.timedelta(seconds=args.before) <= r[0] <= t + dt.timedelta(seconds=args.after)]
        if not rows:
            print(f"{t:%H:%M:%S} {kind:12s} tab {tab}: no audio rows")
            continue
        hits = [r for r in rows if reacts(r)]
        if hits:
            lead = (t - hits[0][0]).total_seconds()
            peak = max(max(r[3], r[2]) for r in rows)
            leads.append(lead)
            print(f"{t:%H:%M:%S} {kind:12s} tab {tab}: audio reacted {lead:+5.1f} s before (peak {peak:.2f})")
        else:
            missed += 1
            print(f"{t:%H:%M:%S} {kind:12s} tab {tab}: no audio reaction (max crowd {max(r[3] for r in rows):.2f}, "
                  f"surge {max(r[2] for r in rows):.2f})")

    # reactions with no event nearby: false alarms (only counted outside ads, where music is not the cause)
    window = dt.timedelta(seconds=args.before)
    alarms = total = 0
    for tab, rows in streams.items():
        times = [t for t, _, other in events if other == tab]
        last = None
        for r in rows:
            total += 1
            if reacts(r) and r[1] != "AD":
                near = any(e - window <= r[0] <= e + dt.timedelta(seconds=args.after) for e in times)
                if not near and (last is None or (r[0] - last).total_seconds() > 20):
                    alarms += 1
                    last = r[0]

    print()
    if leads:
        ahead = [x for x in leads if x > 0]
        print(f"events with an audio reaction: {len(leads)} of {len(leads) + missed}; "
              f"audio first (>0 s): {len(ahead)}; median lead {statistics.median(leads):+.1f} s")
    print(f"events with no audio reaction: {missed}")
    hours = max(1e-9, sum((rows[-1][0] - rows[0][0]).total_seconds() for rows in streams.values() if len(rows) > 1) / 3600)
    print(f"audio reactions with no event within {args.before:.0f} s (outside ads): {alarms} in {hours:.1f} stream-hours "
          f"({alarms / hours:.1f} per stream-hour)")


if __name__ == "__main__":
    main()
