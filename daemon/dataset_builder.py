"""Dataset builder (task T2 of docs/learning-plan.md): turns the recorder's files (`logs/dataset/<date>.jsonl` rows and
`<date>.events.jsonl` events) and the feed recordings (`logs/feed/*.jsonl`) into LABELLED rows and per-swap REGRET / MISS verdicts.

OFFLINE and READ-ONLY on `logs/`: nothing here talks to the daemon, the network or a setting. Only `write_outputs` writes, and only inside the
directory it is given (it refuses any other path). The functions are pure so they can be tested with tiny synthetic data.

Times. Rows and events carry `t` = wall clock epoch seconds. The feed's `t` is ISO with an offset; it is converted to epoch. A row's `t` is
STREAM time (the picture shown at that moment). A feed event is first placed in stream time (its PICTURE time `pt`); all windows
("the next 30 s", the regret window, the miss test) are then measured in stream time of the stream concerned. This is the "shifted by the
picture lag" of the learning plan.

Alignment of a feed event to the picture, in this order (each result records its source in `align`):
  1. `clock`  (football only: the football clock runs 1 s per second). Rows of the game with a parsed on-screen clock "MM:SS" (state live, no
     ad, no replay) within `clock_window_s` of the event's match clock c each give a guess `row.t + (c - row.clock)`. The median is the picture
     time; the median absolute deviation (MAD) is the jitter. Needs `clock_min_rows` rows and MAD <= `clock_max_jitter_s`, else it is
     refused (a frozen or flickering OCR clock is not trusted). A stoppage-time text ("45+2") or a missing clock gives no sample. American
     football has a stopping clock, so it never uses this method.
  2. `score`  (goal-like events): the OCR `score_change` event of a tab mapped to the game, 0..150 s after (or up to 30 s before) the feed
     time and not yet used by another goal, minus GOAL_LAG_S (12 s: the scoreboard confirms a goal 5 to 12 s after the picture).
  3. `lag`    (weak): the feed time plus the game's median lag (picture time minus feed time of the GOALS resolved by 1 and 2: a goal's feed
     time is when the feed first knew it; a card is published minutes late, tonight up to 340 s, so cards do not enter the lag), when at
     least one goal of that game was resolved. One sample is flagged weak in `label_quality` (`feed-weak`).
  An event that none of this can place is UNRESOLVED. It is never guessed: every row of that game whose next 60 s could hold it (feed time
  - 150 s .. + 400 s, the range of the lag: the picture may be up to `unresolved_before_s` 400 s ahead of the feed, as seen on 2026-10-13, or 150 s behind it) is marked `aligned` false and gets NO labels. A row of a game with no
  resolved lag is still labelled when no event of the game is near it in feed time (`align_src` `no-event-nearby`).
"""
import bisect
import collections
import contextlib
import dataclasses
import datetime
import json
import pathlib
import statistics
import time

GOAL_LAG_S = 12.0                      # same value as daemon.backtest.GOAL_LAG_S (OCR confirms a goal this long after the picture)
SPORT_OF_FEED = {"soccer": "football", "american football": "american football", "basketball": "basketball"}
GOAL_KINDS = {"football": ("goal", "penalty goal", "own goal"), "american football": ("touchdown", "field goal good")}
# Event tiers v1 (docs/learning-plan.md section 3). A kind not listed is tier 3 (note only). Basketball is parked: no tiers.
FEED_TIER = {
    "football": {"goal": 1, "penalty goal": 1, "own goal": 1, "red card": 1, "penalty": 1, "yellow card": 3},
    "american football": {"touchdown": 1, "field goal good": 1, "safety": 1, "score": 1, "interception": 2, "fumble": 2, "field goal": 2,
                          "sack": 3, "penalty": 3, "big play": 3},
}
# Spoken / banner cue names (daemon.speech.CUES, game_state banner cues) -> tier. A cue is only a weak, circular clue (the build-up tracker
# uses the same cues), so it is kept apart: `verdict_with_cues` and `cue_t12_60`, never mixed into the feed labels.
CUE_TIER = {
    "football": {"goal": 1, "penalty awarded": 1, "chance": 2, "post": 2, "post area": 2, "free kick": 2, "corner": 2, "var": 2, "review": 2,
                 "penalty": 2, "var check": 2, "coup franc": 2, "card": 3, "foul": 3, "offside": 3, "replay": 3},
    "american football": {"touchdown": 1, "red zone": 2, "goal line": 2, "fourth down": 2, "field goal": 2, "interception": 2, "review": 2,
                          "flag": 3, "penalty": 3, "replay": 3},
}
FEED_DEDUP_S = 120.0                   # the feed repeats an event: same game, kind, team within this = one
SNAP_BEFORE_S = 180.0                  # a score rise this long before the event row is when the feed first knew a goal
ROW_KEYS = ("date", "t", "tab", "role", "game_id", "league", "sport", "is_main", "state", "ad", "replay", "goal_hold", "clock", "period",
            "home", "away", "score", "surge", "crowd", "speech", "music", "shout", "fav", "lev", "swap_pending")


@dataclasses.dataclass(frozen=True)
class Params:
    goal_horizon_s: float = 30.0       # y_goal_30
    t12_horizon_s: float = 60.0        # y_t12_60, y_t1_60
    regret_window_s: float = 30.0      # W (user decision 2026-10-13)
    flipflop_s: float = 30.0           # a swap back within this is a flip-flop (starting flag)
    miss_ahead_s: float = 12.0         # a swap must come at least this long before the event to count as ahead
    miss_back_s: float = 10.0          # Main's own tier is read from pt - this
    clock_window_s: float = 90.0
    clock_min_rows: int = 3
    clock_max_jitter_s: float = 6.0
    unresolved_before_s: float = 400.0 # an unresolved event may show on the picture this much BEFORE its feed time (tonight: up to 340 s, the feed publishes late) ...
    unresolved_after_s: float = 150.0   # ... or this much after it
    coverage_s: float = 400.0          # the feed must have a snapshot of the game this close before a row
    row_gap_s: float = 6.0             # a row older than this is no reading of the stream
    dt_cap_s: float = 5.0              # watched-time step cap
    near_boundary_s: float = 10.0
    wide_margin_s: float = 45.0        # a swap in a game with an unknown or weak lag is judged on [t - this, t + W + this]
    cue_confirm_s: float = 30.0        # a cue this close to a user-marked missed chance (same tab) is confirmed
    left_main_s: float = 30.0          # an event within this after Main left the stream is also reported as "left just before"


# Typical picture lag (picture time minus feed time, s) when no goal of the sport was measured in the data: tonight (2026-10-13, football,
# Champions League) the picture was about 65 s AHEAD of the feed; American football from PLAN.md (the feed leads the picture by about 33 s).
DEFAULT_LAG_S = {"football": -65.0, "american football": 33.0}


# ---------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------
def load_jsonl(path) -> list[dict]:
    """The JSON objects of a file; a broken or partial line (a file being written) is skipped."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def iso_to_epoch(text) -> float | None:
    try:
        d = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return d.timestamp()


def read_days(dataset_dir, days: list[str]) -> tuple[list[dict], list[dict]]:
    """(rows, events) of the given dates, each stamped with its `date`."""
    rows, events = [], []
    base = pathlib.Path(dataset_dir)
    for day in days:
        for name, dest in ((f"{day}.jsonl", rows), (f"{day}.events.jsonl", events)):
            path = base / name
            if path.exists():
                for obj in load_jsonl(path):
                    obj["date"] = day
                    dest.append(obj)
    rows.sort(key=lambda r: r.get("t", 0))
    events.sort(key=lambda e: e.get("t", 0))
    return rows, events


def read_feed(feed_dir, t_min: float, t_max: float, margin_s: float = 3 * 3600.0) -> list[dict]:
    """The feed recordings (every file), `t` converted to epoch seconds, kept when within the margin of the rows' time span."""
    out = []
    base = pathlib.Path(feed_dir)
    if not base.exists():
        return out
    for path in sorted(base.glob("*.jsonl")):
        for obj in load_jsonl(path):
            t = iso_to_epoch(obj.get("t"))
            if t is not None and t_min - margin_s <= t <= t_max + margin_s:
                obj["t"] = t
                out.append(obj)
    return out


def parse_clock(text) -> float | None:
    """'36:15' -> 2175.0. Anything else (stoppage '45+2', countdown junk, None) -> None: no guess."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    minutes, seconds = int(parts[0]), int(parts[1])
    if seconds > 59 or minutes > 130:
        return None
    return float(minutes * 60 + seconds)


# ---------------------------------------------------------------------------------------------------------------
# Games and sports
# ---------------------------------------------------------------------------------------------------------------
def assign_games(rows: list[dict], maps=(), label_to_game: dict | None = None) -> None:
    """Fills `game_id` (and `game_src`) of the rows in place. Order: the row's own id (`row`), the first tile with an id (`tile`), a
    `feed map` interval of the daemon (`map`; `maps` = daemon.backtest.Mapping, `label_to_game` = 'league HOME-AWAY' -> game id), and, when the
    tab showed ONE game all day, that game (`tab-single`). A tab that changed game is never filled by guessing."""
    label_to_game = label_to_game or {}
    for r in rows:
        if r.get("game_id"):
            r["game_src"] = "row"
            continue
        tile = next((x.get("game_id") for x in (r.get("tiles") or []) if x.get("game_id")), None)
        if tile:
            r["game_id"], r["game_src"] = tile, "tile"
            continue
        for m in maps:
            if m.tab == r.get("tab") and m.covers(datetime.datetime.fromtimestamp(r["t"])) and m.label in label_to_game:
                r["game_id"], r["game_src"] = label_to_game[m.label], "map"
                break
    seen: dict = collections.defaultdict(set)
    for r in rows:
        if r.get("game_id"):
            seen[r["tab"]].add(r["game_id"])
    for r in rows:
        if not r.get("game_id"):
            r["game_src"] = None
            if len(seen.get(r.get("tab"), ())) == 1:
                r["game_id"], r["game_src"] = next(iter(seen[r["tab"]])), "tab-single"


def tab_sports(rows: list[dict]) -> dict:
    """tab -> its most common non-null sport (the early rows of a tab often have none)."""
    count: dict = collections.defaultdict(collections.Counter)
    for r in rows:
        if r.get("sport"):
            count[r["tab"]][r["sport"]] += 1
    return {tab: c.most_common(1)[0][0] for tab, c in count.items()}


# ---------------------------------------------------------------------------------------------------------------
# Feed events
# ---------------------------------------------------------------------------------------------------------------
def feed_sport(league: str) -> str:
    from daemon import feed
    return SPORT_OF_FEED.get(feed.LEAGUES[league][0], "unknown") if league in feed.LEAGUES else "unknown"


def tier_of(sport: str, kind: str) -> int:
    return FEED_TIER.get(sport, {}).get(kind, 3)


def feed_events(feed_rows: list[dict]) -> list[dict]:
    """Deduplicated feed events with a tier: {game, league, sport, kind, tier, goal, t, clock_s, team}. A goal-like event's `t` is the first
    moment the feed knew it (the latest snapshot whose score went up in the SNAP_BEFORE_S before the event row, else the event row)."""
    rises: dict = collections.defaultdict(list)
    last: dict = {}
    for r in sorted((r for r in feed_rows if r.get("type") == "snap" and isinstance(r.get("score"), list)), key=lambda r: r["t"]):
        total = sum(x for x in r["score"] if isinstance(x, (int, float)))
        if r["game"] in last and total > last[r["game"]]:
            rises[r["game"]].append(r["t"])
        last[r["game"]] = total
    out, seen = [], {}
    for e in sorted((r for r in feed_rows if r.get("type") == "event" and r.get("game") and r.get("kind")), key=lambda r: r["t"]):
        sport = feed_sport(e.get("league", ""))
        goal = e["kind"] in GOAL_KINDS.get(sport, ())
        key = (e["game"], e["kind"], e.get("team", "")) if goal else (e["game"], e["kind"], e.get("team", ""), e.get("text", ""))
        previous = seen.get(key)
        seen[key] = e["t"]
        if previous is not None and e["t"] - previous <= FEED_DEDUP_S:
            continue
        t = e["t"]
        if goal:
            before = [x for x in rises.get(e["game"], []) if 0 <= e["t"] - x <= SNAP_BEFORE_S]
            t = max(before) if before else t
        out.append({"game": e["game"], "league": e.get("league"), "sport": sport, "kind": e["kind"], "tier": tier_of(sport, e["kind"]),
                    "goal": goal, "t": t, "clock_s": e.get("clock_s"), "team": e.get("team", "")})
    return sorted(out, key=lambda x: x["t"])


def _mad(values: list[float]) -> float:
    m = statistics.median(values)
    return statistics.median([abs(v - m) for v in values])


def clock_samples(rows: list[dict]) -> list[tuple]:
    """(t, clock seconds) of the rows that can be trusted as a running football clock."""
    out = []
    for r in rows:
        if r.get("state") == "live" and not r.get("ad") and not r.get("replay"):
            c = parse_clock(r.get("clock"))
            if c is not None:
                out.append((r["t"], c))
    return out


def picture_time_from_clock(samples: list[tuple], clock_s: float, p: Params):
    """(picture time, jitter, n) from the rows' clock, or None (too few rows or too noisy)."""
    near = [t + (clock_s - c) for t, c in samples if abs(c - clock_s) <= p.clock_window_s]
    if len(near) < p.clock_min_rows:
        return None
    jitter = _mad(near)
    return None if jitter > p.clock_max_jitter_s else (statistics.median(near), jitter, len(near))


def align_events(events: list[dict], rows: list[dict], changes: list[dict], p: Params = Params()) -> dict:
    """Places the feed events of the WATCHED games in stream time. Sets in place on each event: `pt` (picture time or None), `align`
    ('clock', 'score', 'lag' or None), `pt_jitter`. Returns {game: {lag_s, lag_jitter_s, lag_n, lag_src}} (lag = picture time minus feed
    time, positive = the picture is behind the feed). `changes` = the `score_change` events of the recorder."""
    by_game: dict = collections.defaultdict(list)
    tabs_of: dict = collections.defaultdict(set)
    for r in rows:
        if r.get("game_id"):
            by_game[r["game_id"]].append(r)
            tabs_of[r["game_id"]].add(r["tab"])
    used: set = set()
    games: dict = {}
    for g, grows in by_game.items():
        gev = [e for e in events if e["game"] == g]
        samples = clock_samples(grows)
        sport = gev[0]["sport"] if gev else None
        for e in gev:
            e["pt"], e["align"], e["pt_jitter"] = None, None, None
            if sport == "football" and e.get("clock_s") is not None and samples:
                hit = picture_time_from_clock(samples, float(e["clock_s"]), p)
                if hit:
                    e["pt"], e["pt_jitter"], e["align"] = hit[0], hit[1], "clock"
        mine = [c for c in changes if c.get("tab") in tabs_of[g]]
        for e in gev:
            if e["pt"] is None and e["goal"]:
                close = [c for c in mine if id(c) not in used and -30.0 <= c["t"] - e["t"] <= 150.0]
                if close:
                    c = min(close, key=lambda c: c["t"])
                    used.add(id(c))
                    e["pt"], e["align"], e["pt_jitter"] = c["t"] - GOAL_LAG_S, "score", None
        for e in gev:                 # cross-check of the two methods: the OCR change of the same goal minus GOAL_LAG_S minus the clock time
            if e["goal"] and e["align"] == "clock":
                close = [c for c in mine if -60.0 <= c["t"] - GOAL_LAG_S - e["pt"] <= 60.0]
                e["ocr_check_s"] = round(min((c["t"] - GOAL_LAG_S - e["pt"] for c in close), key=abs), 1) if close else None
        lags = [e["pt"] - e["t"] for e in gev if e["pt"] is not None and e["goal"]]   # only goals: their feed time is when the feed FIRST knew; a card is published minutes late
        info = {"lag_s": None, "lag_jitter_s": None, "lag_n": len(lags), "lag_src": None}
        if lags:
            info.update(lag_s=statistics.median(lags), lag_jitter_s=_mad(lags) if len(lags) > 1 else None,
                        lag_src="+".join(sorted({e["align"] for e in gev if e["pt"] is not None and e["goal"]})))
            for e in gev:
                if e["pt"] is None:
                    e["pt"], e["align"], e["pt_jitter"] = e["t"] + info["lag_s"], "lag", info["lag_jitter_s"]
        games[g] = info
    return games


# ---------------------------------------------------------------------------------------------------------------
# Row labels
# ---------------------------------------------------------------------------------------------------------------
def cue_events(events: list[dict], sports: dict, tab_game: dict | None = None) -> list[dict]:
    """The speech and banner cues of the events file as tiered, per-tab clues: {tab, pt, tier, kind}. Stream time = the cue's own time."""
    out = []
    for e in events:
        if e.get("kind") not in ("speech_cue", "banner_cue") or not e.get("cue"):
            continue
        sport = e.get("sport") or sports.get(e.get("tab"))
        tier = CUE_TIER.get(sport, {}).get(e["cue"])
        if tier is not None:
            out.append({"tab": e["tab"], "pt": e["t"], "tier": tier, "kind": e["cue"], "src": "cue"})
    return sorted(out, key=lambda x: x["pt"])


def label_rows(rows: list[dict], events: list[dict], games: dict, cues: list[dict], sports: dict, p: Params = Params()) -> None:
    """Adds in place: `sport_used`, `aligned`, `align_src`, `lag_s`, `label_jitter_s`, `label_quality`, `y_goal_30`, `y_t12_60`, `y_t1_60`,
    `next_event_kind`, `secs_to_next_event`, `label_weight`, `cue_t12_60`. A row without labels has them all None and says why in
    `align_src` (`no-game`, `no-feed` or `unresolved-event`)."""
    ev_by_game: dict = collections.defaultdict(list)
    for e in events:
        ev_by_game[e["game"]].append(e)
    cues_by_tab: dict = collections.defaultdict(list)
    for c in cues:
        cues_by_tab[c["tab"]].append(c)
    snaps = {}                       # the feed's snapshot times per game come in through `games`
    for r in rows:
        r["sport_used"] = r.get("sport") or sports.get(r["tab"])
        for k in ("aligned", "y_goal_30", "y_t12_60", "y_t1_60", "next_event_kind", "secs_to_next_event", "label_quality", "label_jitter_s",
                  "label_weight", "cue_t12_60", "lag_s"):
            r[k] = None
        r["aligned"] = False
        g = r.get("game_id")
        if not g:
            r["align_src"] = "no-game"
            continue
        info = games.get(g, {})
        if not info.get("covered", lambda t: True)(r["t"]):
            r["align_src"] = "no-feed"
            continue
        reach = max(p.t12_horizon_s, p.goal_horizon_s)
        evs = ev_by_game.get(g, [])
        blocked = any(e["pt"] is None and r["t"] - p.unresolved_after_s <= e["t"] <= r["t"] + reach + p.unresolved_before_s for e in evs)
        if blocked:
            r["align_src"] = "unresolved-event"
            continue
        r["aligned"] = True
        r["lag_s"] = info.get("lag_s")
        r["label_jitter_s"] = info.get("lag_jitter_s")
        nearby = [e for e in evs if e["pt"] is not None and -p.near_boundary_s <= e["pt"] - r["t"] <= reach + p.near_boundary_s]
        r["align_src"] = info.get("lag_src") or ("+".join(sorted({e["align"] for e in nearby})) if nearby else "no-event-nearby")
        r["label_quality"] = "feed-weak" if any(e["align"] == "lag" for e in nearby) or (info.get("lag_n", 0) == 1 and nearby) else "feed"
        ahead = [(e["pt"] - r["t"], e) for e in evs if e["pt"] is not None and 0 < e["pt"] - r["t"]]
        r["y_goal_30"] = int(any(e["goal"] and d <= p.goal_horizon_s for d, e in ahead))
        r["y_t12_60"] = int(any(e["tier"] <= 2 and d <= p.t12_horizon_s for d, e in ahead))
        r["y_t1_60"] = int(any(e["tier"] == 1 and d <= p.t12_horizon_s for d, e in ahead))
        nxt = min(ahead, key=lambda x: x[0], default=None)
        if nxt and nxt[0] <= 600:
            r["next_event_kind"], r["secs_to_next_event"] = nxt[1]["kind"], round(nxt[0], 1)
        edge = [d for d, e in ahead if e["goal"] and abs(d - p.goal_horizon_s) <= p.near_boundary_s]
        r["label_weight"] = 0.5 if edge else 1.0
        r["cue_t12_60"] = int(any(0 < c["pt"] - r["t"] <= p.t12_horizon_s and c["tier"] <= 2 for c in cues_by_tab.get(r["tab"], ())))


def add_coverage(games: dict, feed_rows: list[dict], rows: list[dict], p: Params = Params()) -> None:
    """Gives each watched game a `covered(t)` test: the feed has a snapshot of it within `coverage_s` before t (else no label is trustworthy)."""
    times: dict = collections.defaultdict(list)
    for r in feed_rows:
        if r.get("type") == "snap" and r.get("game"):
            times[r["game"]].append(r["t"])
    for g in {r["game_id"] for r in rows if r.get("game_id")}:
        ts = sorted(times.get(g, []))
        games.setdefault(g, {"lag_s": None, "lag_jitter_s": None, "lag_n": 0, "lag_src": None})["covered"] = (
            lambda t, ts=ts: bool(ts) and any(0 <= t - x <= p.coverage_s for x in ts[max(0, bisect.bisect_right(ts, t) - 1):bisect.bisect_right(ts, t)]))


def split_by_game(rows: list[dict], holdout_games=()) -> None:
    """`split` = 'holdout' for the rows of the given games, else 'fit'. The key is the game (rows of one game are never divided); a row with
    no game id uses its day and tab, so a stream without a mapping is also kept whole."""
    held = set(holdout_games)
    for r in rows:
        key = r.get("game_id") or f"{r.get('date')}:tab{r.get('tab')}"
        r["game_key"] = key
        r["split"] = "holdout" if key in held else "fit"


# ---------------------------------------------------------------------------------------------------------------
# Swaps: regret, flip-flops
# ---------------------------------------------------------------------------------------------------------------
class RowIndex:
    """The rows of each tab, searched by time."""

    def __init__(self, rows: list[dict], p: Params):
        self.p = p
        self.by_tab: dict = collections.defaultdict(list)
        for r in rows:
            self.by_tab[r["tab"]].append(r)
        self.times = {tab: [r["t"] for r in rs] for tab, rs in self.by_tab.items()}

    def at(self, tab, t: float):
        """The newest row of `tab` at or before t, not older than row_gap_s; else the first one just after (within row_gap_s)."""
        ts = self.times.get(tab)
        if not ts:
            return None
        i = bisect.bisect_right(ts, t) - 1
        if i >= 0 and t - ts[i] <= self.p.row_gap_s:
            return self.by_tab[tab][i]
        if i + 1 < len(ts) and ts[i + 1] - t <= self.p.row_gap_s:
            return self.by_tab[tab][i + 1]
        return None

    def span(self, tab, t0: float, t1: float) -> list[dict]:
        ts = self.times.get(tab, [])
        return self.by_tab[tab][bisect.bisect_left(ts, t0):bisect.bisect_right(ts, t1)]


def assumed_lags(games: dict, events: list[dict]) -> dict:
    """sport -> the lag to assume for a game that has none: the median of the measured game lags of that sport, else DEFAULT_LAG_S."""
    sport_of = {e["game"]: e["sport"] for e in events}
    measured: dict = collections.defaultdict(list)
    for g, info in games.items():
        if info.get("lag_s") is not None and g in sport_of:
            measured[sport_of[g]].append(info["lag_s"])
    return {sp: statistics.median(measured[sp]) if measured.get(sp) else DEFAULT_LAG_S.get(sp, 0.0) for sp in set(sport_of.values()) | set(DEFAULT_LAG_S)}


def tab_events(events: list[dict], rows: list[dict], assumed: dict | None = None) -> dict:
    """tab -> the feed events of the game that tab showed, as {pt, tier, kind, goal, align, src='feed'}. Resolved events only; with `assumed`
    (sport -> lag) an unresolved event is placed at feed time + that lag and marked align 'assumed' (used for the WIDE judgement only)."""
    tabs: dict = collections.defaultdict(set)
    for r in rows:
        if r.get("game_id"):
            tabs[r["game_id"]].add(r["tab"])
    out: dict = collections.defaultdict(list)
    for e in events:
        pt, align = e.get("pt"), e.get("align")
        if pt is None:
            if assumed is None:
                continue
            pt, align = e["t"] + assumed.get(e["sport"], 0.0), "assumed"
        for tab in tabs.get(e["game"], ()):
            out[tab].append({"tab": tab, "game": e["game"], "pt": pt, "tier": e["tier"], "kind": e["kind"], "goal": e["goal"],
                             "align": align, "src": "feed", "sport": e["sport"], "ocr_check_s": e.get("ocr_check_s")})
    for v in out.values():
        v.sort(key=lambda x: x["pt"])
    return out


def confirmed_evidence(cues: list[dict], event_marks: list[dict], swap_marks: list[tuple], sports: dict, p: Params = Params()) -> tuple:
    """(extra events per tab, confirmed cue count). Cue tiers count as evidence ONLY when the user confirmed them:
    * a user event mark (kind 'event': a missed chance with a time and a tab) is itself a tier-2 event of that tab (src 'user') and confirms the
      cues of that tab within `cue_confirm_s` of it;
    * a mark on a swap confirms the cues of its two tabs in [t - 30 s, t + 60 s].
    `event_marks` = [{t, tab, verdict}], `swap_marks` = [(swap t, new tab, old tab)] of the marked swaps."""
    extra: dict = collections.defaultdict(list)
    confirmed = set()
    for m in event_marks:
        sport = sports.get(m["tab"])
        extra[m["tab"]].append({"tab": m["tab"], "game": None, "pt": m["t"], "tier": 2, "kind": "user-marked " + str(m.get("verdict")),
                                "goal": False, "align": "user", "src": "user", "sport": sport or "unknown", "ocr_check_s": None})
        for i, c in enumerate(cues):
            if c["tab"] == m["tab"] and abs(c["pt"] - m["t"]) <= p.cue_confirm_s:
                confirmed.add(i)
    for t, new, old in swap_marks:
        for i, c in enumerate(cues):
            if c["tab"] in (new, old) and t - 30.0 <= c["pt"] <= t + 60.0:
                confirmed.add(i)
    for i in confirmed:
        c = cues[i]
        extra[c["tab"]].append({"tab": c["tab"], "game": None, "pt": c["pt"], "tier": c["tier"], "kind": "cue:" + c["kind"], "goal": False,
                                "align": "cue-confirmed", "src": "cue", "sport": sports.get(c["tab"]) or "unknown", "ocr_check_s": None})
    return extra, len(confirmed)


def merge_tab_events(*maps) -> dict:
    out: dict = collections.defaultdict(list)
    for m in maps:
        for tab, evs in m.items():
            out[tab].extend(evs)
    for v in out.values():
        v.sort(key=lambda x: x["pt"])
    return out


def window_tier(evs: list[dict], t0: float, t1: float) -> tuple:
    """(value, events in the window): value = the best (lowest) tier there, 3 when none (tier 3 and nothing are the same)."""
    inside = [e for e in evs if t0 <= e["pt"] <= t1]
    return min([e["tier"] for e in inside], default=3), inside


def _verdict(v_old: int, v_new: int) -> tuple:
    if v_old < v_new:
        return "regret", v_new - v_old
    if v_old > v_new:
        return "ok", 0
    return "tie", 0


def judge_swaps(swaps: list[dict], index: RowIndex, tab_ev: dict, cues_by_tab: dict, sports: dict, p: Params = Params(), marks: dict | None = None,
                tab_ev_wide: dict | None = None, lags: dict | None = None) -> list[dict]:
    """One detail row per `swap` event of the recorder.

    verdict: `excluded` (a swap by the user, or the old Main was on an ad / in a break), `unjudged` (a side has no usable feed labels: no game,
    no feed, or an unresolved event nearby), `regret` (tier of the old Main in [t, t+W] strictly better than the new Main's), `ok` (the new
    Main's is better), `tie` (equal: both quiet, or the same tier), `flip-flop-candidate` (reversed within flipflop_s and not already a
    regret; listed for the user's review). `regret_weight` = the tier gap (1 or 2). `review` = flip-flops and quiet ties (T3 list).
    `verdict_with_cues` repeats the rule with the speech/banner cues added as tiers (circular, shown separately).
    `marks` = the user's review marks (daemon.swap_review.read_marks, keyed '<YYYY-MM-DD> <HH:MM:SS>'): the user is the FINAL reference, so a
    mark `good` makes the verdict `ok` and `regret` makes it `regret` (weight = the tier gap, at least 1), also over `unjudged`; `late`, `other` and
    `missed-chance` keep the automatic verdict (the swap itself was not marked wrong). A swap by the user stays `excluded`. The automatic verdict
    is kept in `auto_verdict`, the mark in `user_mark`.
    LAG: a swap whose game lag is unknown (an event of the game could not be placed) or weak (an event placed only by the game's median lag) is NOT
    dropped: it is judged on `tab_ev_wide` (unresolved events placed at feed time + `lags[sport]`, the median measured lag of the sport or
    DEFAULT_LAG_S) over the wider window [t - wide_margin_s, t + W + wide_margin_s], and carries `lag` = 'assumed' (else 'measured'),
    `window_s`. No game / no feed stays `unjudged`. Cue tiers are NOT in `tab_ev` unless the user confirmed them (see `confirmed_evidence`);
    `verdict_with_cues` counts every cue (unconfirmed, shown apart)."""
    marks = marks or {}
    tab_ev_wide = tab_ev if tab_ev_wide is None else tab_ev_wide
    lags = lags or {}
    out = []
    ordered = sorted(swaps, key=lambda s: s["t"])
    for s in ordered:
        t, old, new = s["t"], s.get("demote"), s.get("promote")
        sport = s.get("sport") or sports.get(new) or sports.get(old)
        d = {"date": s.get("date"), "t": t, "sport": sport, "source": s.get("source"), "old": old, "new": new, "reason_excluded": None,
             "tier_old": None, "tier_new": None, "events_old": [], "events_new": [], "regret_weight": 0, "flipflop": False,
             "flipflop_back": False, "review": False, "verdict_with_cues": None, "cue_tier_old": None, "cue_tier_new": None,
             "lag": None, "lag_assumed_s": None, "window_s": None}
        r_old, r_new = index.at(old, t), index.at(new, t)
        if s.get("source") == "user":
            d["verdict"], d["reason_excluded"] = "excluded", "user"
        elif r_old is not None and (r_old.get("state") != "live" or r_old.get("ad") or r_old.get("on_ad")):
            d["verdict"], d["reason_excluded"] = "excluded", "old Main on a break or ad"
        elif r_old is None or r_new is None or any(not r.get("aligned") and r.get("align_src") != "unresolved-event" for r in (r_old, r_new)):
            d["verdict"] = "unjudged"
            d["reason_excluded"] = "no row" if r_old is None or r_new is None else next(
                r.get("align_src") for r in (r_old, r_new) if not r.get("aligned") and r.get("align_src") != "unresolved-event")
        else:
            wide = not r_old.get("aligned") or not r_new.get("aligned")          # an unplaced event: the lag is unknown
            if not wide:                                                           # a weak lag: an event placed by the median lag near the window
                wide = any(e["align"] in ("lag", "assumed") and t - p.wide_margin_s <= e["pt"] <= t + p.regret_window_s + p.wide_margin_s
                           for tab in (old, new) for e in tab_ev.get(tab, []))
            evs, lo, hi = (tab_ev_wide, t - p.wide_margin_s, t + p.regret_window_s + p.wide_margin_s) if wide else (tab_ev, t, t + p.regret_window_s)
            d["lag"], d["window_s"] = ("assumed" if wide else "measured"), [round(lo - t, 1), round(hi - t, 1)]
            if wide:
                d["lag_assumed_s"] = lags.get(sport)
            v_old, e_old = window_tier(evs.get(old, []), lo, hi)
            v_new, e_new = window_tier(evs.get(new, []), lo, hi)
            d["tier_old"], d["tier_new"] = v_old, v_new
            fmt = lambda es: [{"kind": e["kind"], "tier": e["tier"], "dt": round(e["pt"] - t, 1), "align": e["align"]} for e in es]  # noqa: E731
            d["events_old"], d["events_new"] = fmt(e_old), fmt(e_new)
            d["verdict"], d["regret_weight"] = _verdict(v_old, v_new)
            c_old = window_tier(cues_by_tab.get(old, []), lo, hi)[0]
            c_new = window_tier(cues_by_tab.get(new, []), lo, hi)[0]
            d["cue_tier_old"], d["cue_tier_new"] = c_old, c_new
            d["verdict_with_cues"] = _verdict(min(v_old, c_old), min(v_new, c_new))[0]
            if d["verdict"] == "tie" and v_old == 3:
                d["review"] = True                 # a quiet tie: regret only if the user marks it so (decision I)
        d["auto_verdict"] = d["verdict"]
        d["user_mark"] = (marks.get(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))) or {}).get("verdict")
        out.append(d)
    for i, d in enumerate(out):
        for j in range(i + 1, len(out)):
            b = out[j]
            if b["t"] - d["t"] > p.flipflop_s:
                break
            if d["source"] != "user" and b["source"] != "user" and b["new"] == d["old"] and b["old"] == d["new"]:
                d["flipflop"], b["flipflop_back"], d["reversed_at"] = True, True, b["t"]
                d["review"] = b["review"] = True
                if d["verdict"] in ("ok", "tie"):
                    d["verdict"] = "flip-flop-candidate"
                break
    for d in out:
        d["auto_verdict"] = d["verdict"]
        if d["user_mark"] in ("good", "regret") and d["source"] != "user":
            d["verdict"] = "ok" if d["user_mark"] == "good" else "regret"
            d["regret_weight"] = max(d["regret_weight"], 1) if d["verdict"] == "regret" else 0
            d["review"] = False
    return out


# ---------------------------------------------------------------------------------------------------------------
# Misses and leads
# ---------------------------------------------------------------------------------------------------------------
def judge_events(events: list[dict], rows: list[dict], swaps: list[dict], index: RowIndex, tab_ev: dict, p: Params = Params()) -> list[dict]:
    """One row per (feed event of tier 1 or 2, tab that showed its game) with a resolved picture time.

    `status`: `no-rows` (the tab has no row there), `main` (the tab already was MAIN for the 30 s before: no swap needed), `ahead` (a swap to
    it came >= miss_ahead_s before), `covered` (MAIN had an event of the same or better tier in [pt - 10, pt + W]: not a miss), `unjudged`
    (MAIN's labels are unusable), `miss-late` (it became MAIN, but under miss_ahead_s before), `miss-after` (a swap came only after the
    event, within W), `miss` (no swap in time). `lead_s` = event picture time minus the time of the last swap to that tab (goal-like events
    only count as 'lead before goals'; negative = swapped after). Swaps by the user are listed but do not count as `ahead`."""
    swaps_to: dict = collections.defaultdict(list)
    swaps_from: dict = collections.defaultdict(list)
    for s in sorted(swaps, key=lambda s: s["t"]):
        swaps_to[s.get("new")].append(s)
        swaps_from[s.get("old")].append(s)
    out = []
    for tab, evs in tab_ev.items():
        for e in evs:
            if e["tier"] > 2 or e.get("src") == "cue":
                continue
            pt = e["pt"]
            row = index.at(tab, pt)
            d = {"t": pt, "tab": tab, "game": e["game"], "sport": e["sport"], "kind": e["kind"], "tier": e["tier"], "goal": e["goal"],
                 "align": e["align"], "status": None, "lead_s": None, "swap_source": None, "ocr_check_s": e.get("ocr_check_s"),
                 "src": e.get("src"), "lead_last_s": None, "last_swap_source": None, "left_main_s": None}
            out.append(d)
            if row is None:
                d["status"] = "no-rows"
                continue
            main_rows = index.span(tab, pt - 30.0, pt)
            prior = [s for s in swaps_to.get(tab, []) if s["t"] <= pt]
            last = prior[-1] if prior else None
            if row.get("is_main") and last is not None:       # lead from the LAST swap to the stream, whatever its age (decision 5)
                d["lead_last_s"], d["last_swap_source"] = round(pt - last["t"], 1), last.get("source")
            left = [s for s in swaps_from.get(tab, []) if pt - p.left_main_s <= s["t"] <= pt]
            if left and not row.get("is_main"):               # decision 4: Main left this stream just before the event
                d["left_main_s"] = round(pt - left[-1]["t"], 1)
            if main_rows and all(r.get("is_main") for r in main_rows):
                d["status"] = "main"
                continue
            main_tab = next((t2 for t2 in index.by_tab if t2 != tab and (r2 := index.at(t2, pt)) is not None and r2.get("is_main")), None)
            if row.get("is_main") and last is not None:
                d["lead_s"], d["swap_source"] = round(pt - last["t"], 1), last.get("source")
                d["status"] = "ahead" if pt - last["t"] >= p.miss_ahead_s and last.get("source") != "user" else "miss-late"
                if last.get("source") == "user":
                    d["status"] = "user-main"
                continue
            m_value = None
            if main_tab is not None:
                r_main = index.at(main_tab, pt)
                if r_main is None or not r_main.get("aligned"):
                    d["status"] = "unjudged"
                    continue
                m_value = window_tier(tab_ev.get(main_tab, []), pt - p.miss_back_s, pt + p.regret_window_s)[0]
                if m_value <= e["tier"]:
                    d["status"] = "covered"
                    continue
            later = [s for s in swaps_to.get(tab, []) if pt < s["t"] <= pt + p.regret_window_s]
            if later:
                d["status"], d["lead_s"], d["swap_source"] = "miss-after", round(pt - later[0]["t"], 1), later[0].get("source")
            else:
                d["status"] = "miss"
    return sorted(out, key=lambda x: (x["t"], x["tab"]))


# ---------------------------------------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------------------------------------
def watched_live_minutes(rows: list[dict], sports: dict, p: Params = Params()) -> dict:
    """sport -> minutes the Main stream was live (no break, no ad), from the rows' spacing (capped at dt_cap_s per step)."""
    out: dict = collections.defaultdict(float)
    by_tab: dict = collections.defaultdict(list)
    for r in rows:
        by_tab[r["tab"]].append(r)
    for tab, rs in by_tab.items():
        for a, b in zip(rs, rs[1:] + [None]):
            if a.get("is_main") and a.get("state") == "live" and not a.get("ad") and not a.get("on_ad"):
                dt = p.dt_cap_s if b is None else min(b["t"] - a["t"], p.dt_cap_s)
                out[a.get("sport") or sports.get(tab) or "unknown"] += max(dt, 0.0) / 60.0
    return dict(out)


def summarise(rows: list[dict], swaps: list[dict], event_rows: list[dict], minutes: dict) -> dict:
    """Per sport numbers (sport -> dict); see `format_summary`."""
    sports = {r.get("sport_used") or "unknown" for r in rows} | {s.get("sport") or "unknown" for s in swaps} | {e["sport"] for e in event_rows}
    out = {}
    for sp in sorted(sports):
        rs = [r for r in rows if (r.get("sport_used") or "unknown") == sp]
        sw = [s for s in swaps if (s.get("sport") or "unknown") == sp]
        ev = [e for e in event_rows if e["sport"] == sp]
        verdicts = collections.Counter(s["verdict"] for s in sw)
        judged = [s for s in sw if s["verdict"] in ("regret", "ok", "tie", "flip-flop-candidate") and s.get("lag")]
        judged += [s for s in sw if s["verdict"] in ("regret", "ok") and not s.get("lag")]            # decided by the user's mark alone
        regrets = [s for s in sw if s["verdict"] == "regret"]
        exact = [s for s in judged if s.get("lag") == "measured"]
        wide = [s for s in judged if s.get("lag") == "assumed"]
        mins = minutes.get(sp, 0.0)
        goals = [e for e in ev if e["goal"]]
        leads = [e["lead_s"] for e in goals if e["status"] in ("ahead", "miss-late") and e["lead_s"] is not None and e["lead_s"] >= 0]
        last_leads = [e["lead_last_s"] for e in goals if e["lead_last_s"] is not None and e["last_swap_source"] != "user"]
        misses = [e for e in ev if e["status"].startswith("miss")]
        missed_marks = [e for e in ev if e.get("src") == "user"]
        out[sp] = {
            "rows": len(rs), "aligned_rows": sum(1 for r in rs if r["aligned"]), "y_goal_30": sum(1 for r in rs if r["y_goal_30"]),
            "y_t12_60": sum(1 for r in rs if r["y_t12_60"]), "unaligned": dict(collections.Counter(r["align_src"] for r in rs if not r["aligned"])),
            "goals": len(goals), "goals_aligned": len([e for e in goals if e["status"] != "no-rows"]),
            "swaps": len(sw), "verdicts": dict(verdicts), "judged": len(judged), "judged_exact": len(exact), "judged_wide": len(wide),
            "regrets": len(regrets), "regrets_exact": sum(1 for s in exact if s["verdict"] == "regret"),
            "regrets_wide": sum(1 for s in wide if s["verdict"] == "regret"),
            "regret_weight": sum(s["regret_weight"] for s in regrets), "flipflops": sum(1 for s in sw if s["flipflop"]),
            "user_marks": sum(1 for s in sw if s.get("user_mark")), "mark_overrides": sum(1 for s in sw if s.get("user_mark") and s["verdict"] != s.get("auto_verdict")),
            "regrets_with_cues": sum(1 for s in sw if s.get("verdict_with_cues") == "regret"), "review": sum(1 for s in sw if s["review"]),
            "live_main_minutes": mins, "regret_per_10min": (len(regrets) / mins * 10.0) if mins else None,
            "regret_share": (len(regrets) / len(judged)) if judged else None,
            "tier12_events": len([e for e in ev if e["status"] != "no-rows"]), "misses": len(misses),
            "miss_per_10min": (len(misses) / mins * 10.0) if mins else None, "event_status": dict(collections.Counter(e["status"] for e in ev)),
            "goal_leads": leads, "mean_lead_s": statistics.mean(leads) if leads else None,
            "goals_ahead_12s": sum(1 for x in leads if x >= 12.0),
            "last_leads": last_leads, "goals_left_just_before": sum(1 for e in goals if e["left_main_s"] is not None),
            "misses_left_just_before": sum(1 for e in misses if e["left_main_s"] is not None),
            "regrets_goal_left": sum(1 for s in regrets if any(x["tier"] == 1 for x in s["events_old"])),
            "user_event_marks": len(missed_marks), "user_event_marks_missed": sum(1 for e in missed_marks if e["status"].startswith("miss")),
        }
    return out


def format_summary(summary: dict) -> str:
    def f(x, spec=".1f"):
        return "-" if x is None else format(x, spec)

    def stats(v):
        return "none" if not v else f"mean {statistics.mean(v):.1f}, median {statistics.median(v):.1f}, min {min(v):.1f} s (n={len(v)})"
    lines = []
    for sp, s in summary.items():
        lines.append(f"{sp}: {s['rows']} rows, {s['aligned_rows']} labelled; positives y_goal_30 {s['y_goal_30']}, y_t12_60 {s['y_t12_60']}"
                     + (f"; unlabelled because {s['unaligned']}" if s["unaligned"] else ""))
        lines.append(f"    goals on watched tiles {s['goals']} ({s['goals_aligned']} with a picture time)")
        lines.append(f"    lead from the LAST swap to the scoring stream (any age, system swaps): {stats(s['last_leads'])}")
        lines.append(f"    strict: swap made >= 12 s ahead and within 30 s before the goal: {s['goals_ahead_12s']} of {s['goals']} goals; mean lead {f(s['mean_lead_s'])} s over {len(s['goal_leads'])} reached by a swap")
        lines.append(f"    swaps {s['swaps']} {s['verdicts']}; judged {s['judged']} = {s['judged_exact']} exact lag + {s['judged_wide']} wide margin (lag assumed); user marks {s['user_marks']} ({s['mark_overrides']} changed the verdict)")
        lines.append(f"    REGRET {s['regrets']} (exact {s['regrets_exact']}, wide {s['regrets_wide']}; weight {s['regret_weight']}), rate {f(s['regret_per_10min'], '.2f')} per 10 live Main min "
                     f"({f(s['live_main_minutes'])} min), share of judged {f(s['regret_share'], '.2f')}; flip-flops {s['flipflops']}; for review {s['review']}")
        lines.append(f"        of which a tier-1 event on the stream left: {s['regrets_goal_left']}; [column: regrets if ALL cues counted, unconfirmed: {s['regrets_with_cues']}]")
        lines.append(f"    MISS (tier-1/2 events, {s['tier12_events']}): {s['misses']} ({f(s['miss_per_10min'], '.2f')} per 10 min); of which Main had left that stream <= 30 s before: {s['misses_left_just_before']}; "
                     f"user-marked missed chances {s['user_event_marks']} ({s['user_event_marks_missed']} counted as misses); status {s['event_status']}")
    return "\n".join(lines) if lines else "No rows."


# ---------------------------------------------------------------------------------------------------------------
# Build and write
# ---------------------------------------------------------------------------------------------------------------
def build(rows: list[dict], events_file: list[dict], feed_rows: list[dict], p: Params = Params(), holdout=(), maps=(), label_to_game=None, marks: dict | None = None,
          event_marks=()) -> dict:
    """The whole pipeline on in-memory data. Returns {rows, swaps, events, games, summary}."""
    rows = [r for r in rows if "tab" in r and "t" in r]
    assign_games(rows, maps, label_to_game)
    sports = tab_sports(rows)
    fev = feed_events(feed_rows)
    watched = {r["game_id"] for r in rows if r.get("game_id")}
    fev = [e for e in fev if e["game"] in watched]
    changes = [e for e in events_file if e.get("kind") == "score_change"]
    games = align_events(fev, rows, changes, p)
    add_coverage(games, feed_rows, rows, p)
    cues = cue_events(events_file, sports)
    label_rows(rows, fev, games, cues, sports, p)
    split_by_game(rows, holdout)
    index = RowIndex(rows, p)
    lags = assumed_lags(games, fev)
    swap_marks = [(e["t"], e.get("promote"), e.get("demote")) for e in events_file if e.get("kind") == "swap"
                  and time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["t"])) in (marks or {})]
    extra, n_confirmed = confirmed_evidence(cues, list(event_marks), swap_marks, sports, p)
    tab_ev = merge_tab_events(tab_events(fev, rows), extra)
    tab_ev_wide = merge_tab_events(tab_events(fev, rows, lags), extra)
    cues_by_tab: dict = collections.defaultdict(list)
    for c in cues:
        cues_by_tab[c["tab"]].append(c)
    swap_events = [e for e in events_file if e.get("kind") == "swap"]
    swaps = judge_swaps(swap_events, index, tab_ev, cues_by_tab, sports, p, marks, tab_ev_wide, lags)
    event_rows = judge_events(fev, rows, swaps, index, tab_ev, p)
    minutes = watched_live_minutes(rows, sports, p)
    summary = summarise(rows, swaps, event_rows, minutes)
    game_rows = [{"game": g, **{k: v for k, v in i.items() if k != "covered"}} for g, i in sorted(games.items())]
    return {"rows": rows, "swaps": swaps, "events": event_rows, "games": game_rows, "summary": summary, "feed_events": fev, "assumed_lags": lags, "confirmed_cues": n_confirmed}


def slim_row(r: dict) -> dict:
    out = {k: r.get(k) for k in ROW_KEYS}
    out["clock_s"] = parse_clock(r.get("clock"))
    bu = r.get("bu") or {}
    out.update(bu_level=bu.get("level"), bu_cues_sum=bu.get("cues_sum"), bu_qualifies=bu.get("qualifies"), bu_blocked=bu.get("blocked"))
    for k in ("game_key", "split", "sport_used", "game_src", "aligned", "align_src", "lag_s", "label_jitter_s", "label_quality", "label_weight",
              "y_goal_30", "y_t12_60", "y_t1_60", "cue_t12_60", "next_event_kind", "secs_to_next_event"):
        out[k] = r.get(k)
    return out


def write_outputs(out_dir, result: dict) -> list[pathlib.Path]:
    """Writes `rows.jsonl`, `swaps.jsonl`, `events.jsonl`, `games.jsonl` into `out_dir` (created) and NOWHERE else."""
    out = pathlib.Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, items in (("rows.jsonl", [slim_row(r) for r in result["rows"]]), ("swaps.jsonl", result["swaps"]),
                        ("events.jsonl", result["events"]), ("games.jsonl", result["games"])):
        path = (out / name).resolve()
        if path.parent != out:
            raise ValueError(f"refusing to write outside the output directory: {path}")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
        written.append(path)
    return written


def available_days(dataset_dir) -> list[str]:
    return sorted(p.name[:-len(".jsonl")] for p in pathlib.Path(dataset_dir).glob("????-??-??.jsonl"))


def row_span(rows: list[dict]) -> tuple:
    ts = [r["t"] for r in rows if "t" in r]
    return (min(ts), max(ts)) if ts else (0.0, 0.0)


def load_maps(events_log, feed_rows: list[dict]):
    """(maps, label_to_game): the daemon's `feed map` intervals from events.log (optional file) and the 'league HOME-AWAY' -> game id table of
    the feed. Empty when the file is missing."""
    from daemon import backtest
    path = pathlib.Path(events_log)
    if not path.exists():
        return [], {}
    with contextlib.suppress(Exception):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        labels = {f"{r['league']} {r.get('home')}-{r.get('away')}": r["game"] for r in feed_rows if r.get("league") and r.get("game")}
        return backtest.parse_mappings(lines), labels
    return [], {}


def load_marks(logs_dir, days: list[str]) -> dict:
    """The user's swap marks of the given days (logs/review/<day>.jsonl, read-only, via daemon.swap_review), keyed '<day> <HH:MM:SS>'."""
    from daemon import swap_review
    out = {}
    for day in days:
        for mark_id, mark in swap_review.read_marks(pathlib.Path(logs_dir), day).items():
            if mark.get("kind", "swap") == "swap":
                out[f"{day} {mark_id}"] = mark
    return out


def load_event_marks(logs_dir, days: list[str]) -> list[dict]:
    """The user's missed-chance marks (kind 'event' in logs/review/<day>.jsonl, read-only): [{t, tab, verdict}]. The time is the mark's `t`, else
    the HH:MM:SS in its id ('evt-21:28:43-<tab>') on that day."""
    import re
    from daemon import swap_review
    out = []
    for day in days:
        for mark_id, mark in swap_review.read_marks(pathlib.Path(logs_dir), day).items():
            if mark.get("kind") != "event":
                continue
            m = re.match(r"^evt-(\d\d:\d\d:\d\d)-(\d+)$", mark_id)
            t = mark.get("t")
            if t is None and m:
                t = time.mktime(time.strptime(f"{day} {m.group(1)}", "%Y-%m-%d %H:%M:%S"))
            tab = mark.get("tab") if mark.get("tab") is not None else (int(m.group(2)) if m else None)
            if t is not None and tab is not None:
                out.append({"t": float(t), "tab": int(tab), "verdict": mark.get("verdict")})
    return out
