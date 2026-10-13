"""Review the swaps of a day (read-only on the logs; marks go to logs/review/<date>.jsonl).

The default list is what needs you: swaps you remarked on (logs/feedback), swaps within 30 s of a goal / VAR / penalty / chance cue, and each
flip-flop chain as ONE entry. Plain quiet ties and 'MAIN idle' swaps are left out (use --all; you can still --mark them by time).

  review_swaps.py [YYYY-MM-DD]                        the review list (same as --list)
  review_swaps.py [DAY] --all                         every swap, with its flags
  review_swaps.py [DAY] --mark 21:28:43 late "too late"   mark one entry (any swap time of a chain marks the whole chain)
  review_swaps.py [DAY] --mark 21:35:17 missed "hot build-up" --tab 485660740   a missed chance (not a swap): needs the tab
  review_swaps.py [DAY] --interactive                 walk through unmarked entries: g good, r regret, l late, m missed, o other, s skip, q quit
  review_swaps.py [DAY] --markdown                    write logs/review/<DAY>.md, a sheet you can read or paste in chat

DAY defaults to today. See daemon/swap_review.py.
"""
import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from daemon import swap_review as sr   # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Review the swaps of a day.")
    ap.add_argument("day", nargs="?", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true", help="list every swap (default: only the review list)")
    ap.add_argument("--flagged", action="store_true", help="same as the default review list (kept for old habits)")
    ap.add_argument("--mark", nargs="+", metavar=("TIME", "VERDICT"), help="TIME VERDICT [note]")
    ap.add_argument("--tab", type=int, help="with --mark: tab id of a missed chance (an event that is not a swap)")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--logs", default=str(ROOT / "logs"), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    logs = pathlib.Path(a.logs)
    swaps = sr.load_day(logs, a.day)
    marks = sr.read_marks(logs, a.day)

    if a.mark:
        if len(a.mark) < 2:
            print("--mark needs TIME and VERDICT")
            return 2
        when, verdict, note = a.mark[0], a.mark[1], " ".join(a.mark[2:])
        try:
            if a.tab is not None:
                obj = sr.add_mark(logs, a.day, f"evt-{when}-{a.tab}", verdict, note, tab=a.tab, kind="event")
            else:
                s = sr.find_swap(swaps, when)
                if s is None:
                    print(f"no swap near {when} on {a.day}; for a missed chance add --tab TAB")
                    return 1
                en = sr.entry_for(sr.build_entries(swaps, include_all=True), s["id"])
                if en is None:
                    print(f"{s['id']} is your own swap; it is not judged")
                    return 1
                obj = sr.mark_entry(logs, a.day, en, verdict, note)
        except ValueError as e:
            print(e)
            return 2
        print(f"marked {obj['id']}: {obj['verdict']}")
        return 0

    if a.all:
        print(f"{a.day}: {len(swaps)} swaps, {sum(s['user'] for s in swaps)} by the user, {len(marks)} marked")
        print(sr.table(swaps, marks))
        return 0
    entries = sr.build_entries(swaps)
    if a.markdown:
        print(f"wrote {sr.write_markdown(logs, a.day)}")
    if a.interactive:
        todo = [en for en in entries if en["id"] not in marks]
        print(f"{len(todo)} entr(y/ies) to judge. Keys: g good, r regret, l late, m missed, o other, s skip, q quit")
        for n, en in enumerate(todo, 1):
            print(f"\n[{n}/{len(todo)}]\n{sr.entry_detail(en)}")
            while True:
                try:
                    key = input("verdict (g/r/l/m/o/s/q)? ").strip().lower()[:1]
                except EOFError:
                    key = "q"
                if key in sr.KEYS:
                    sr.mark_entry(logs, a.day, en, sr.KEYS[key], input("note (optional)? ").strip())
                    break
                if key in ("s", "q"):
                    break
            if key == "q":
                break
        return 0
    if a.markdown:
        return 0
    print(f"{a.day}: {len(swaps)} swaps, {sum(s['user'] for s in swaps)} by the user, {len(entries)} entries to review, {len(marks)} swaps marked")
    print(sr.entry_table(entries, marks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
