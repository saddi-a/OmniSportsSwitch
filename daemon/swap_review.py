"""Swap review: list the swaps of a day, attach their context, flag the ones that need the user, store the user's marks.

OFFLINE and read-only on the logs (`scripts/review_swaps.py`). The only thing this module writes is `logs/review/<date>.jsonl` (marks, append-only,
last mark for the same id wins) and, on request, `logs/review/<date>.md` (a readable sheet). Pure logic otherwise: no sockets, no sleeps.

Sources of a day's swaps: `logs/dataset/<date>.events.jsonl` when present, else the `swap:` lines of `logs/events.log`. Swaps with
`MAIN forced by the user` (source `user`) are listed but never judged. The user's marks are the FINAL reference (docs/director-training.md section 9).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import time

VERDICTS = {"good": "good", "regret": "regret", "late": "late", "missed": "missed-chance", "missed-chance": "missed-chance", "other": "other"}
KEYS = {"g": "good", "r": "regret", "l": "late", "m": "missed-chance", "o": "other"}

BEFORE_S = 30.0            # context window around a swap: from this many seconds before ...
AFTER_S = 60.0             # ... to this many after
FLIPFLOP_S = 30.0          # a swap back to the previous Main within this is a flip-flop
QUIET_S = 30.0             # quiet tie: nothing on either stream in this window after the swap
FEEDBACK_S = 60.0          # the user's remarks within +- this of a swap
CONTEXT_KINDS = ("speech_cue", "banner_cue", "score_change", "goal_banner", "game_state")

_LOG_TIME = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) (.*)$")
_SWAP = re.compile(r"^swap: promote tab (\d+), demote tab (\d+) \| (.*)$")
_SPEECH = re.compile(r"^speech cue (.+?) tab (\d+): (.*)$")
_SCORE = re.compile(r"^score change tab (\d+) tile (\d+): (.*)$")
_BANNER = re.compile(r"^goal banner tab (\d+) tile \d+: (.*)$")
_STATE = re.compile(r"^game state tab (\d+): (\w+) -> (\w+)$")
_MAP = re.compile(r"^feed map tab (\d+) tile (\d+) -> (\S+) (\S+)")
_LEVEL = re.compile(r"level ([0-9.]+).* vs main ([0-9.]+)")


def _epoch(stamp: str, ms: str = "000") -> float:
    return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S")) + int(ms) / 1000.0


def clock(t: float) -> str:
    """Local HH:MM:SS of an epoch time (the log clock)."""
    return time.strftime("%H:%M:%S", time.localtime(t))


def _day_of(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


# ---------------------------------------------------------------- reading the logs

def _read_lines(path: pathlib.Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _log_events(logs: pathlib.Path, day: str) -> tuple[list[dict], list[dict], list[dict]]:
    """(swaps, context events, feed-map lines) of `day` from logs/events.log."""
    swaps: list[dict] = []
    events: list[dict] = []
    maps: list[dict] = []
    for line in _read_lines(logs / "events.log"):
        m = _LOG_TIME.match(line)
        if not m or not m.group(1).startswith(day):
            continue
        t = _epoch(m.group(1), m.group(2))
        msg = m.group(3)
        if (s := _SWAP.match(msg)):
            reason = s.group(3)
            swaps.append({"t": t, "promote": int(s.group(1)), "demote": int(s.group(2)), "reason": reason,
                          "source": "user" if "forced by the user" in reason else "auto"})
        elif (s := _SPEECH.match(msg)):
            events.append({"t": t, "kind": "speech_cue", "tab": int(s.group(2)), "cue": s.group(1), "text": s.group(3)})
        elif (s := _SCORE.match(msg)):
            events.append({"t": t, "kind": "score_change", "tab": int(s.group(1)), "text": s.group(3)})
        elif (s := _BANNER.match(msg)):
            events.append({"t": t, "kind": "goal_banner", "tab": int(s.group(1)), "text": s.group(2)})
        elif (s := _STATE.match(msg)):
            events.append({"t": t, "kind": "game_state", "tab": int(s.group(1)), "old": s.group(2), "new": s.group(3)})
        elif (s := _MAP.match(msg)):
            maps.append({"t": t, "tab": int(s.group(1)), "game": s.group(4), "league": s.group(3)})
    return swaps, events, maps


def _dataset_events(path: pathlib.Path) -> tuple[list[dict], list[dict]]:
    swaps: list[dict] = []
    events: list[dict] = []
    for line in _read_lines(path):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not isinstance(o, dict) or not isinstance(o.get("t"), (int, float)):
            continue
        kind = o.get("kind")
        if kind == "swap":
            swaps.append({"t": float(o["t"]), "promote": o.get("promote"), "demote": o.get("demote"), "reason": str(o.get("reason") or ""),
                          "source": "user" if o.get("source") == "user" or "forced by the user" in str(o.get("reason")) else "auto"})
        elif kind in CONTEXT_KINDS:
            e = {k: o.get(k) for k in ("cue", "text", "banner", "weight", "old", "new", "home", "away", "verdict") if o.get(k) is not None}
            e.update(t=float(o["t"]), kind=kind, tab=o.get("tab"))
            events.append(e)
    return swaps, events


def parse_feedback(path: pathlib.Path) -> list[dict]:
    """Tolerant parser of logs/feedback/<date>.md: `HH:MM:SS ... | game/tab | verdict | words`, indented lines after it are evidence.
    Returns [{"clock": "21:28:43", "game": ..., "verdict": ..., "words": ..., "evidence": ...}]; anything it cannot read is skipped."""
    out: list[dict] = []
    for raw in _read_lines(path):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\d\d:\d\d:\d\d)\b[^|]*\|(.*)$", line)
        if m:
            parts = [p.strip() for p in m.group(2).split("|", 2)]
            while len(parts) < 3:
                parts.append("")
            out.append({"clock": m.group(1), "game": parts[0], "verdict": parts[1], "words": parts[2], "evidence": ""})
        elif out and line[:1] in " \t":
            out[-1]["evidence"] = (out[-1]["evidence"] + " " + line.strip()).strip()
    return out


# ---------------------------------------------------------------- building the review

def _is_key_event(e: dict) -> bool:
    """A goal, penalty or red card on a stream."""
    if e["kind"] in ("score_change", "goal_banner"):
        return e.get("verdict") != "rewind"
    if e["kind"] in ("speech_cue", "banner_cue"):
        cue = str(e.get("cue") or "").lower()
        if "penalty" in cue or "red" in cue:
            return True
        if cue == "card":
            return bool(re.search(r"\b(red|rouge|roja|rot)\b", str(e.get("text") or e.get("banner") or "").lower()))
    return False


def describe(e: dict) -> str:
    k = e["kind"]
    if k == "speech_cue":
        return f"cue {e.get('cue')}: {str(e.get('text') or '')[:70]}"
    if k == "banner_cue":
        return f"banner cue {e.get('cue')} ({e.get('banner') or ''})"
    if k == "score_change":
        return f"score change {e.get('text') or str(e.get('home')) + '-' + str(e.get('away'))}"
    if k == "goal_banner":
        return f"goal banner {e.get('text') or ''}"
    if k == "game_state":
        return f"game state {e.get('old')} -> {e.get('new')}"
    return k


def _game_of(maps: list[dict], tab, t: float) -> str:
    game = ""
    for m in maps:
        if m["tab"] == tab and m["t"] <= t + 120:     # the map line may come a little after the swap; the last one before wins
            game = m["game"]
    return game


def _level(reason: str) -> str:
    m = _LEVEL.search(reason)
    if m:
        return f"{m.group(1)} vs {m.group(2)}"
    if "forced by the user" in reason:
        return "user"
    return "idle" if "idle" in reason else reason[:20]


def build_swaps(swaps: list[dict], events: list[dict], maps: list[dict], feedback: list[dict], day: str) -> list[dict]:
    """The swaps of the day with context and flags. Each: id (HH:MM:SS), t, promote, demote, games, reason, level, user, flags, context, remarks."""
    swaps = sorted(swaps, key=lambda s: s["t"])
    events = sorted(events, key=lambda e: e["t"])
    out: list[dict] = []
    for i, s in enumerate(swaps):
        t, tabs = s["t"], (s["promote"], s["demote"])
        ctx = [dict(e, text_line=describe(e), rel=round(e["t"] - t, 1)) for e in events
               if e.get("tab") in tabs and t - BEFORE_S <= e["t"] <= t + AFTER_S]
        remarks = []
        for f in feedback:
            try:
                ft = _epoch(f"{day} {f['clock']}")
            except ValueError:
                continue
            if abs(ft - t) <= FEEDBACK_S:
                remarks.append(f)
        flags: list[str] = []
        if s["source"] != "user":
            for j in (i - 1, i + 1):          # reversed by a swap back, or itself a swap back, within 30 s
                if 0 <= j < len(swaps) and abs(swaps[j]["t"] - t) <= FLIPFLOP_S:
                    a, b = (swaps[j], s) if j < i else (s, swaps[j])
                    if b["promote"] == a["demote"] and b["demote"] == a["promote"]:
                        flags.append("flip-flop")
                        break
            if not any(t < e["t"] <= t + QUIET_S for e in ctx if e["kind"] != "game_state"):
                flags.append("quiet-tie")
            if any(_is_key_event(e) for e in ctx):
                flags.append("key-event")
        out.append({"id": clock(t), "t": t, "promote": s["promote"], "demote": s["demote"],
                    "games": [_game_of(maps, s["promote"], t), _game_of(maps, s["demote"], t)], "reason": s["reason"], "level": _level(s["reason"]),
                    "user": s["source"] == "user", "flags": flags, "context": ctx, "remarks": remarks})
    return out


def load_day(logs: pathlib.Path, day: str) -> list[dict]:
    """All swaps of `day` (YYYY-MM-DD) with context and flags. Missing files give an empty list."""
    log_swaps, log_events, maps = _log_events(logs, day)
    ds = logs / "dataset" / f"{day}.events.jsonl"
    if ds.exists():
        swaps, events = _dataset_events(ds)
    else:
        swaps, events = log_swaps, log_events
    feedback = parse_feedback(logs / "feedback" / f"{day}.md")
    return build_swaps(swaps, events, maps, feedback, day)


def needs_user(swap: dict) -> bool:
    return not swap["user"] and bool(swap["flags"])


# ---------------------------------------------------------------- marks

def marks_path(logs: pathlib.Path, day: str) -> pathlib.Path:
    return logs / "review" / f"{day}.jsonl"


def add_mark(logs: pathlib.Path, day: str, mark_id: str, verdict: str, note: str = "", t: float | None = None, tab: int | None = None,
             kind: str = "swap", members: list[str] | None = None) -> dict:
    """Append one mark. `kind` 'swap' (id = the swap's HH:MM:SS) or 'event' (a missed chance etc.: needs t and tab). Returns the stored object."""
    chosen = VERDICTS.get(verdict.lower()) or KEYS.get(verdict.lower())
    if not chosen:
        raise ValueError(f"unknown verdict {verdict!r}; use good, regret, late, missed or other")
    verdict = chosen
    obj = {"id": mark_id, "kind": kind, "verdict": verdict, "marked_at": dt.datetime.now().isoformat(timespec="seconds")}
    if t is not None:
        obj["t"] = t
    if tab is not None:
        obj["tab"] = tab
    if note:
        obj["note"] = note
    if members:
        obj["members"] = list(members)       # a chain entry: one mark covers all these swap ids
    path = marks_path(logs, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    return obj


def read_marks(logs: pathlib.Path, day: str) -> dict[str, dict]:
    """id -> the LAST mark with that id (earlier ones stay in the file as history)."""
    marks: dict[str, dict] = {}
    for line in _read_lines(marks_path(logs, day)):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if isinstance(o, dict) and o.get("id"):
            marks[str(o["id"])] = o
            for m in o.get("members") or []:      # a chain mark applies to every member swap (later marks win)
                marks[str(m)] = o
    return marks


def find_swap(swaps: list[dict], when: str, tolerance_s: float = 5.0) -> dict | None:
    """The swap at HH:MM:SS (exact id first, else the nearest within `tolerance_s`)."""
    for s in swaps:
        if s["id"] == when:
            return s
    try:
        h, m, sec = (int(x) for x in when.split(":"))
    except ValueError:
        return None
    best = None
    for s in swaps:
        lt = time.localtime(s["t"])
        d = abs((lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec) - (h * 3600 + m * 60 + sec))
        if d <= tolerance_s and (best is None or d < best[0]):
            best = (d, s)
    return best[1] if best else None


# ---------------------------------------------------------------- text output

def _tab(tab) -> str:
    return str(tab)[-4:] if tab is not None else "?"


def _name(swap: dict, side: int) -> str:
    game = swap["games"][side]
    tab = swap["promote"] if side == 0 else swap["demote"]
    return f"{game or 'tab'}:{_tab(tab)}"


def table(swaps: list[dict], marks: dict[str, dict]) -> str:
    rows = ["id        from -> to                           level            flags                     mark"]
    for s in swaps:
        mk = marks.get(s["id"])
        flags = "USER" if s["user"] else ",".join(s["flags"])
        rows.append(f"{s['id']}  {_name(s, 1) + ' -> ' + _name(s, 0):<36} {s['level'][:16]:<16} {flags:<25} {mk['verdict'] if mk else '-'}")
    return "\n".join(rows)


def detail(s: dict, mark: dict | None = None) -> str:
    lines = [f"{s['id']}  {_name(s, 1)} -> {_name(s, 0)}   {'USER SWAP' if s['user'] else s['reason'][:90]}"]
    if s["flags"]:
        lines.append("  flags: " + ", ".join(s["flags"]))
    for e in s["context"]:
        side = "NEW" if e["tab"] == s["promote"] else "OLD"
        lines.append(f"  {e['rel']:+6.0f}s {side} {e['text_line']}")
    for r in s["remarks"]:
        lines.append(f"  you said {r['clock']} [{r['verdict']}]: {r['words']}")
    if mark:
        lines.append(f"  mark: {mark['verdict']}" + (f" ({mark['note']})" if mark.get("note") else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- the review list (entries)
# Default list for the user: swaps with a remark of the user, swaps within 30 s of a goal / VAR / penalty / chance cue on either tab, and each
# flip-flop CHAIN (swaps back and forth between the same two tabs, each within 30 s of the previous) collapsed into one entry. Plain quiet ties
# and 'MAIN idle' swaps stay out (reachable with --all and markable by time). A mark on a chain is stored once: id = first swap's id,
# `members` = all swap ids of the chain (read_marks gives every member that mark).

NEAR_S = 30.0
_NEAR_CUES = ("chance", "penalty", "review", "var", "post", "goal", "red")


def _is_near_cue(e: dict) -> bool:
    if e["kind"] in ("score_change", "goal_banner"):
        return e.get("verdict") != "rewind"
    if e["kind"] in ("speech_cue", "banner_cue"):
        cue = str(e.get("cue") or "").lower()
        return any(w in cue for w in _NEAR_CUES)
    return False


_JUDGING = ("good", "regret", "late", "missed", "missed-chance", "miss", "false-alarm", "false")


def is_judging_remark(r: dict) -> bool:
    """A remark whose own verdict word judges a swap (not 'other', which is a general comment)."""
    m = re.match(r"\s*([a-z][a-z-]*)", str(r.get("verdict") or "").lower())
    return bool(m and m.group(1) in _JUDGING)


def build_entries(swaps: list[dict], include_all: bool = False) -> list[dict]:
    """Group the (user-excluded) swaps into entries. Each: id, t, t_last, swaps (member swaps), why, final (tab on Main at the end), games,
    context (merged, rel to the first swap), remarks, chain (bool). `include_all` keeps the quiet ties and idle swaps too."""
    autos = [x for x in swaps if not x["user"]]
    groups: list[list[dict]] = []
    for x in autos:
        g = groups[-1] if groups else None
        if g and x["t"] - g[-1]["t"] <= FLIPFLOP_S and {x["promote"], x["demote"]} == {g[-1]["promote"], g[-1]["demote"]}:
            g.append(x)
        else:
            groups.append([x])
    out: list[dict] = []
    for g in groups:
        first, last = g[0], g[-1]
        why: list[str] = []
        if len(g) > 1:
            why.append(f"flip-flop x{len(g)}")
        if any(is_judging_remark(r) for x in g for r in x["remarks"]):
            why.append("your remark")
        if any(_is_near_cue(e) and abs(e["rel"]) <= NEAR_S for x in g for e in x["context"]):
            why.append("goal/VAR/penalty/chance cue nearby")
        idle = all(x["level"] == "idle" for x in g)
        if not (include_all or (why and (not idle or "your remark" in why))):
            continue
        ctx, seen = [], set()
        for x in g:
            for e in x["context"]:
                key = (e["t"], e.get("tab"), e["kind"], e.get("cue"))
                if key not in seen:
                    seen.add(key)
                    ctx.append(dict(e, rel=round(e["t"] - first["t"], 1)))
        ctx.sort(key=lambda e: e["t"])
        rem = []
        for x in g:
            rem += [r for r in x["remarks"] if r not in rem]
        out.append({"id": first["id"], "t": first["t"], "t_last": last["t"], "swaps": g, "why": why or ["(not flagged)"], "final": last["promote"],
                    "games": last["games"], "context": ctx, "remarks": rem, "chain": len(g) > 1, "members": [x["id"] for x in g]})
    return out


def entry_for(entries: list[dict], swap_id: str) -> dict | None:
    for en in entries:
        if swap_id in en["members"]:
            return en
    return None


def _span(en: dict) -> str:
    return en["id"] if not en["chain"] else f"{en['id']}-{clock(en['t_last'])}"


def entry_table(entries: list[dict], marks: dict[str, dict]) -> str:
    rows = ["time               from -> to / ends on                 why                                   mark"]
    for en in entries:
        a, b = en["swaps"][0], en["swaps"][-1]
        route = (f"{_name(b, 0)} (ends on Main)" if en["chain"] else f"{_name(a, 1)} -> {_name(a, 0)}")
        mk = marks.get(en["id"])
        rows.append(f"{_span(en):<18} {route:<36} {'; '.join(en['why'])[:36]:<37} {mk['verdict'] if mk else '-'}")
    return "\n".join(rows)


def entry_detail(en: dict, mark: dict | None = None) -> str:
    a, b = en["swaps"][0], en["swaps"][-1]
    if en["chain"]:
        head = f"{_span(en)}  CHAIN of {len(en['swaps'])} swaps between {_name(a, 0)} and {_name(a, 1)}, ends on {_name(b, 0)}"
    else:
        head = f"{en['id']}  {_name(a, 1)} -> {_name(a, 0)}   {a['reason'][:90]}"
    lines = [head, "  why: " + "; ".join(en["why"])]
    if en["chain"]:
        lines.append("  swaps at: " + ", ".join(en["members"]))
    for e in en["context"]:
        lines.append(f"  {clock(e['t'])} ({e['rel']:+.0f}s) tab {_tab(e.get('tab'))} {e['text_line']}")
    for r in en["remarks"]:
        lines.append(f"  you said {r['clock']} [{r['verdict']}]: {r['words']}")
    if mark:
        lines.append(f"  mark: {mark['verdict']}" + (f" ({mark['note']})" if mark.get("note") else ""))
    return "\n".join(lines)


def mark_entry(logs: pathlib.Path, day: str, en: dict, verdict: str, note: str = "") -> dict:
    """One mark for an entry: id = first swap id, members = all its swap ids (a single swap has no members)."""
    return add_mark(logs, day, en["id"], verdict, note, t=en["t"], members=en["members"] if en["chain"] else None)


def markdown(day: str, swaps: list[dict], marks: dict[str, dict]) -> str:
    entries = build_entries(swaps)
    out = [f"# Swap review {day}", "",
           f"{len(swaps)} swaps ({sum(x['user'] for x in swaps)} by you, not judged); {len(entries)} entries to review "
           "(chains of flip-flops count as one). Verdicts: good, regret, late, missed, other. Times are the log clock.", "", "```",
           entry_table(entries, marks), "```", ""]
    for en in entries:
        out += [f"## {_span(en)}", "", "```", entry_detail(en, marks.get(en["id"])), "```", ""]
    return "\n".join(out) + "\n"


def write_markdown(logs: pathlib.Path, day: str) -> pathlib.Path:
    swaps = load_day(logs, day)
    path = logs / "review" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown(day, swaps, read_marks(logs, day)), encoding="utf-8", newline="\n")
    return path
