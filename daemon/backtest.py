"""Replays the logs to measure how far AHEAD of a goal each stream's score was (offline and read-only: it changes nothing, see
scripts/backtest.py).

The goal of the daemon is to put a stream in MAIN BEFORE its goal, not after the scoreboard confirms it. The logs hold what we need to
measure that: every 1 s (2 s before 2026-10-17) the score of each stream (`live fav=... score=...` lines of daemon.log), the goals (`score change tab ...` lines of
events.log, the scoreboard confirmation) and, when the feed knew the game, how far the picture lagged the feed (`feed confirmed ... feed lag`).
For each goal this module answers: what was the score of the scoring stream (and of the best other stream) 30 / 20 / 10 / 5 seconds before,
was the stream already MAIN, and WHEN would the swap rule (min_score, threshold, min_gap, confirm_s) first have taken MAIN for it.
The goal time is the confirmation, 5 to 12 s after the real goal, and a swap after the goal is TOO LATE (the user wants to watch it live), so
only a lead of at least GOAL_LAG_S counts as "ahead of the goal"; the summary counts those and no others.
"""
import dataclasses
import datetime
import re
import statistics

TS = r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})"
SIGNAL_LINE = re.compile(
    TS + r"\s+(MAIN )?(\d+): (live|break) fav=(-?[\d.]+) score=(-?[\d.]+) surge=(-?[\d.]+) crowd=(-?[\d.]+) speech=(-?[\d.]+)")
GOAL_LINE = re.compile(TS + r" score change tab (\d+) tile (\d+):")
CUE_LINE = re.compile(TS + r" anticipation (.+?) tab (\d+)(?: tile (\d+))?(?: \(spoken\))?(?: weight ([\d.]+))?\s*$")
SPORT_LINE = re.compile(TS + r" sport detected tab (\d+) tile (\d+): (.+?)\s*$")
BUILDUP_LINE = re.compile(TS + r" buildup would take MAIN tab (\d+)(?: tile (\d+))?(?: \([^)]*\))?:")   # a multiview line: "tab 7 tile 1 (football):"
FEED_LAG_LINE = re.compile(TS + r" feed confirmed tab (\d+) tile (\d+) .*feed lag (-?\d+) s")
BEFORE_S = (30.0, 20.0, 10.0, 5.0)        # how long before the goal the scores are read
SEARCH_S = 90.0                           # how far back the first qualifying moment is looked for
MAX_AGE_S = 6.0                           # a reading older than this (before the asked time) is no reading
GOAL_LAG_S = 12.0                         # the scoreboard confirms a goal 5-12 s after it happened (PSG 54'): a lead under this is already too late
GOAL_DEDUP_S = 20.0                       # two score changes of one tab this close are one goal (OCR re-reads)


def parse_time(text: str) -> datetime.datetime:
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S,%f")


@dataclasses.dataclass(frozen=True)
class Reading:
    t: datetime.datetime
    tab: int
    main: bool
    live: bool            # False = the game looked like a break
    score: float
    surge: float
    crowd: float
    speech: float


@dataclasses.dataclass(frozen=True)
class Goal:
    t: datetime.datetime
    tab: int
    tile: int = 0


@dataclasses.dataclass(frozen=True)
class Cue:
    t: datetime.datetime
    tab: int
    kind: str             # "var", "penalty", "chance (spoken)"...
    weight: float | None
    tile: int | None = None   # the video of a multiview it was seen on (None = not said: the whole tab)


@dataclasses.dataclass(frozen=True)
class Rule:
    """The swap rule of `scoring` (defaults of settings.toml)."""
    min_score: float = 0.35
    threshold: float = 0.40
    min_gap: float = 0.15
    confirm_s: float = 2.0


@dataclasses.dataclass(frozen=True)
class Lead:
    goal: Goal
    already_main: bool            # the scoring stream was MAIN 30 s before the goal
    score_before: dict            # seconds before -> score of the scoring stream (None = no reading)
    best_other_before: dict       # seconds before -> best score of the other streams (None = none)
    swap_lead_s: float | None     # seconds before the goal that the swap rule first qualified (None = never in SEARCH_S)
    first_cue: tuple | None       # (seconds before the goal, kind) of the earliest cue in SEARCH_S
    sport: str                    # "Football", "American football", "Basketball"... as detected on the tab ("unknown" when never detected)
    buildup_lead_s: float | None  # seconds before the goal of the earliest "buildup would take MAIN" line in SEARCH_S (None = none)
    feed_lag_s: int | None        # the feed's lead over the picture, when the log knows it


def parse_signals(lines) -> list[Reading]:
    out = []
    for line in lines:
        m = SIGNAL_LINE.search(line)
        if m:
            out.append(Reading(parse_time(m.group(1)), int(m.group(3)), bool(m.group(2)), m.group(4) == "live",
                               float(m.group(6)), float(m.group(7)), float(m.group(8)), float(m.group(9))))
    return out


def parse_goals(lines) -> list[Goal]:
    """Score changes, one per goal: a second change of the same tab within GOAL_DEDUP_S is the OCR reading the same goal again."""
    goals: list[Goal] = []
    for line in lines:
        m = GOAL_LINE.search(line)
        if not m:
            continue
        goal = Goal(parse_time(m.group(1)), int(m.group(2)), int(m.group(3)))
        if not any(g.tab == goal.tab and abs((goal.t - g.t).total_seconds()) < GOAL_DEDUP_S for g in goals):
            goals.append(goal)
    return goals


def parse_cues(lines) -> list[Cue]:
    out = []
    for line in lines:
        m = CUE_LINE.search(line)
        if m:
            out.append(Cue(parse_time(m.group(1)), int(m.group(3)), m.group(2) + (" (spoken)" if "(spoken)" in line else ""),
                           float(m.group(5)) if m.group(5) else None, int(m.group(4)) if m.group(4) else None))
    return out


def parse_sports(lines) -> list[tuple[datetime.datetime, int, str]]:
    """(time, tab, sport) of every `sport detected` line, in log order."""
    return [(parse_time(m.group(1)), int(m.group(2)), m.group(4)) for line in lines if (m := SPORT_LINE.search(line))]


def parse_tile_sports(lines) -> list[tuple[datetime.datetime, int, int, str]]:
    """(time, tab, tile, sport) of every `sport detected` line: in a mixed multiview each tile has its own sport."""
    return [(parse_time(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)) for line in lines if (m := SPORT_LINE.search(line))]


def parse_buildups(lines) -> list[tuple[datetime.datetime, int]]:
    """(time, tab) of the `buildup would take MAIN` lines (the shadow build-up tracker, daemon/buildup.py)."""
    return [(parse_time(m.group(1)), int(m.group(2))) for line in lines if (m := BUILDUP_LINE.search(line))]


def parse_tile_buildups(lines) -> list[tuple[datetime.datetime, int, int | None]]:
    """(time, tab, tile or None) of the `buildup would take MAIN` lines; a multiview line names its tile."""
    return [(parse_time(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None) for line in lines if (m := BUILDUP_LINE.search(line))]


def parse_feed_lags(lines) -> list[tuple[datetime.datetime, int, int]]:
    """(time, tab, lag in s) from `feed confirmed` lines: positive = the feed was that many seconds ahead of the picture."""
    out = []
    for line in lines:
        m = FEED_LAG_LINE.search(line)
        if m:
            out.append((parse_time(m.group(1)), int(m.group(2)), -int(m.group(4))))
    return out


class Series:
    """The readings of one log, searched by time. Readings are about 1 s apart per stream (2 s in logs before 2026-10-17)."""

    def __init__(self, readings: list[Reading]):
        self.by_tab: dict[int, list[Reading]] = {}
        for r in sorted(readings, key=lambda x: x.t):
            self.by_tab.setdefault(r.tab, []).append(r)

    def at(self, tab: int, t: datetime.datetime) -> Reading | None:
        """The newest reading of `tab` at or before `t`, not older than MAX_AGE_S."""
        best = None
        for r in self.by_tab.get(tab, []):
            if r.t > t:
                break
            best = r
        if best is not None and (t - best.t).total_seconds() <= MAX_AGE_S:
            return best
        return None

    def window(self, tab: int, start: datetime.datetime, end: datetime.datetime) -> list[Reading]:
        return [r for r in self.by_tab.get(tab, []) if start <= r.t <= end]

    def others(self, tab: int, t: datetime.datetime) -> list[Reading]:
        return [r for other in self.by_tab if other != tab for r in [self.at(other, t)] if r is not None]


def qualifies(challenger: float, main: float, rule: Rule) -> bool:
    """The score part of `Scorer.decide`: high enough, and ahead of MAIN by the fraction AND by the gap."""
    return challenger >= rule.min_score and challenger >= main * (1 + rule.threshold) and challenger - main >= rule.min_gap


def swap_lead(series: Series, goal: Goal, rule: Rule) -> float | None:
    """Seconds before the goal at which the scoring stream FIRST qualified to take MAIN and kept qualifying for `confirm_s`, up to the goal.
    0 = only at the goal; None = it never qualified in SEARCH_S. A stream that was MAIN the whole time has no lead to measure (see `already_main`)."""
    start = goal.t - datetime.timedelta(seconds=SEARCH_S)
    streak_from = None
    first = None
    for r in series.window(goal.tab, start, goal.t):
        if r.main:
            streak_from = first = None
            continue
        main = [o for o in series.others(goal.tab, r.t) if o.main]
        main_score = main[0].score if main else 0.0
        if r.live and qualifies(r.score, main_score, rule):
            streak_from = streak_from or r.t
            if first is None and (r.t - streak_from).total_seconds() >= rule.confirm_s:
                first = streak_from
        else:
            streak_from = first = None
    return None if first is None else max((goal.t - first).total_seconds(), 0.0)


def measure(readings: list[Reading], goals: list[Goal], cues: list[Cue], feed_lags=(), rule: Rule = Rule(), sports=(), buildups=()) -> list[Lead]:
    series = Series(readings)
    out = []
    for goal in goals:
        before, other = {}, {}
        for s in BEFORE_S:
            t = goal.t - datetime.timedelta(seconds=s)
            mine = series.at(goal.tab, t)
            theirs = series.others(goal.tab, t)
            before[s] = None if mine is None else mine.score
            other[s] = max((o.score for o in theirs), default=None)
        early = series.at(goal.tab, goal.t - datetime.timedelta(seconds=BEFORE_S[0]))
        cues_before = [c for c in cues if c.tab == goal.tab and (c.tile is None or c.tile == goal.tile) and 0 <= (goal.t - c.t).total_seconds() <= SEARCH_S]
        first_cue = max(((goal.t - c.t).total_seconds(), c.kind) for c in cues_before) if cues_before else None
        lag = next((lag for t, tab, lag in feed_lags if tab == goal.tab and 0 <= (t - goal.t).total_seconds() <= 60), None)
        # `sports` entries are (time, tab, sport) or (time, tab, tile, sport); `buildups` (time, tab) or (time, tab, tile): a tile of a mixed
        # multiview is matched on its own tile, an entry without a tile on the tab
        sport = next((e[-1] for e in reversed(list(sports)) if e[1] == goal.tab and e[0] <= goal.t and (len(e) < 4 or e[2] == goal.tile)), "unknown")
        built = [(goal.t - e[0]).total_seconds() for e in buildups if e[1] == goal.tab and (len(e) < 3 or e[2] is None or e[2] == goal.tile)
                 and 0 <= (goal.t - e[0]).total_seconds() <= SEARCH_S]
        out.append(Lead(goal, bool(early and early.main), before, other, swap_lead(series, goal, rule), first_cue, sport,
                        max(built) if built else None, lag))
    return out


def summarise(leads: list[Lead], detail: bool = True) -> str:
    """A readable report: one block per goal (unless `detail` is False), then the metrics PER SPORT (each sport has its own clues, so
    its numbers are never mixed with another sport's)."""
    if not leads:
        return "No goals (score change lines) found in these logs."
    lines = []
    for lead in leads if detail else []:
        g = lead.goal
        lines.append(f"{g.t:%Y-%m-%d %H:%M:%S}  tab {g.tab}  {lead.sport}  goal" + ("  (already MAIN 30 s before)" if lead.already_main else ""))
        for s in BEFORE_S:
            mine, other = lead.score_before[s], lead.best_other_before[s]
            lines.append(f"    {s:>4.0f} s before:  this stream {'-' if mine is None else f'{mine:.2f}'}   best other {'-' if other is None else f'{other:.2f}'}")
        swap = "never qualified" if lead.swap_lead_s is None else f"{lead.swap_lead_s:.0f} s before the goal"
        cue = "none" if lead.first_cue is None else f"{lead.first_cue[0]:.0f} s before ({lead.first_cue[1]})"
        built = "none" if lead.buildup_lead_s is None else f"{lead.buildup_lead_s:.0f} s before"
        feed = "" if lead.feed_lag_s is None else f"   feed was {lead.feed_lag_s} s ahead of the picture"
        lines.append(f"    swap rule first qualified: {swap};  earliest cue: {cue};  build-up would take MAIN: {built}{feed}")
    if lines:
        lines.append("")
    for sport in sorted({x.sport for x in leads}):
        mine = [x for x in leads if x.sport == sport]
        counted = [x for x in mine if not x.already_main]
        rule = [x.swap_lead_s for x in counted if x.swap_lead_s is not None and x.swap_lead_s >= GOAL_LAG_S]
        built = [x.buildup_lead_s for x in counted if x.buildup_lead_s is not None and x.buildup_lead_s >= GOAL_LAG_S]
        cued = [x.first_cue[0] for x in counted if x.first_cue is not None and x.first_cue[0] >= GOAL_LAG_S]
        lines.append(f"{sport}: {len(mine)} goals, {len(mine) - len(counted)} already in MAIN, {len(counted)} to take over")
        lines.append(f"    swap rule qualified AHEAD of the goal (>= {GOAL_LAG_S:.0f} s before the confirmation): {len(rule)} of {len(counted)}" + (f" (median {statistics.median(rule):.0f} s ahead)" if rule else ""))
        lines.append(f"    build-up tracker would have taken MAIN ahead of the goal: {len(built)} of {len(counted)}" + (f" (median {statistics.median(built):.0f} s ahead)" if built else ""))
        lines.append(f"    a cue was heard or seen ahead of the goal: {len(cued)} of {len(counted)}" + (f" (median {statistics.median(cued):.0f} s ahead)" if cued else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# The feed recordings (logs/feed/*.jsonl) as GROUND TRUTH. MEASURING ONLY: nothing here reaches a score or a setting.
# ---------------------------------------------------------------------------------------------------------------
# The feed's "t" carries +02:00 and the logs are naive local time of the same zone, so the offset is dropped (feed_compare.parse_feed)
# and both are compared as they are. Checked on Coventry-Newcastle 2026-10-12: feed score 2-0 -> 2-1 at 22:28:15, OCR change 22:28:22.
MAP_LINE = re.compile(TS + r" feed map tab (\d+) tile (\d+) -> (\S+) (\S+) \(")
DROP_LINE = re.compile(TS + r" feed map tab (\d+) tile (\d+) dropped (\S+) (\S+):")
SPORT_NAMES = {"soccer": "Football", "american football": "American football", "basketball": "Basketball"}   # feed sport -> the daemon's name
GOAL_KINDS = {"Football": ("goal", "penalty goal", "own goal"), "American football": ("touchdown", "field goal good")}
BUILDUP_KINDS = ("yellow card", "red card", "penalty", "interception", "fumble", "big play")
FEED_DEDUP_S = 120.0          # the feed repeats an event (the clock moves by a second, the key changes): same game, kind, team within this = one
SNAP_BEFORE_S = 180.0         # a score increase of a snapshot this long before the event row is when the feed first knew the goal
MATCH_BEFORE_S, MATCH_AFTER_S = 30.0, 150.0     # the OCR score change of a feed goal comes this far from the feed time (the picture is usually late)
MAP_MAX_OPEN_S = 4 * 3600.0   # a map line with no later line holds this long at most (a game does not last longer)
BASE_RATE_S = 120.0           # "a goal followed within this many seconds of the feed time"
BASE_RATE_MIN = 3             # fewer events than this: say too few


def feed_sport(league: str) -> str:
    from daemon import feed
    return SPORT_NAMES.get(feed.LEAGUES[league][0], "unknown") if league in feed.LEAGUES else "unknown"


@dataclasses.dataclass(frozen=True)
class FeedGoal:
    t: datetime.datetime           # when the feed first knew it: the earlier of the event row and the snapshot whose score went up
    event_t: datetime.datetime     # the event row itself
    game_id: str
    label: str                     # "eng.1 COV-NEW" (the form of the `feed map` lines)
    sport: str
    kind: str
    team: str


@dataclasses.dataclass(frozen=True)
class FeedNonGoal:
    t: datetime.datetime
    game_id: str
    label: str
    sport: str
    kind: str
    team: str


@dataclasses.dataclass(frozen=True)
class Mapping:
    tab: int
    tile: int
    start: datetime.datetime
    end: datetime.datetime
    label: str                     # "eng.1 COV-NEW"
    sport: str

    def covers(self, t: datetime.datetime) -> bool:
        return self.start <= t <= self.end


def _dedup(events: list[dict], same_key) -> list[dict]:
    """Events (sorted by time) with the repeats removed: an event whose key was last seen less than FEED_DEDUP_S ago is a repeat."""
    last: dict = {}
    out = []
    for e in events:
        key = same_key(e)
        seen = last.get(key)
        last[key] = e["t"]
        if seen is None or (e["t"] - seen).total_seconds() > FEED_DEDUP_S:
            out.append(e)
    return out


def _score_rises(rows: list[dict]) -> dict[str, list[datetime.datetime]]:
    """game id -> the times of the snapshots whose total score is higher than the snapshot before."""
    last: dict[str, int] = {}
    out: dict[str, list[datetime.datetime]] = {}
    for r in sorted((r for r in rows if r.get("type") == "snap" and isinstance(r.get("score"), list)), key=lambda r: r["t"]):
        total = sum(x for x in r["score"] if isinstance(x, (int, float)))
        if r["game"] in last and total > last[r["game"]]:
            out.setdefault(r["game"], []).append(r["t"])
        last[r["game"]] = total
    return out


def parse_feed_events(rows: list[dict]) -> tuple[list[FeedGoal], list[FeedNonGoal]]:
    """The goal-like events (soccer goal / penalty goal / own goal, American football touchdown / field goal good; basketball has none) and
    the build-up events (cards, penalty, interception, fumble, big play), repeats removed. A goal's time is the first moment the feed knew:
    the latest snapshot score rise in the SNAP_BEFORE_S before the event row, else the event row."""
    events = sorted((r for r in rows if r.get("type") == "event" and r.get("league") and r.get("game")), key=lambda r: r["t"])
    rises = _score_rises(rows)
    label = lambda r: f"{r['league']} {r.get('home')}-{r.get('away')}"          # noqa: E731
    goals, others = [], []
    goal_rows = [e for e in events if e.get("kind") in GOAL_KINDS.get(feed_sport(e["league"]), ())]
    for e in _dedup(goal_rows, lambda e: (e["game"], e["kind"], e.get("team", ""))):
        before = [t for t in rises.get(e["game"], []) if 0 <= (e["t"] - t).total_seconds() <= SNAP_BEFORE_S]
        goals.append(FeedGoal(max(before) if before else e["t"], e["t"], e["game"], label(e), feed_sport(e["league"]), e["kind"], e.get("team", "")))
    other_rows = [e for e in events if e.get("kind") in BUILDUP_KINDS]
    for e in _dedup(other_rows, lambda e: (e["game"], e["kind"], e.get("team", ""), e.get("text", ""))):
        others.append(FeedNonGoal(e["t"], e["game"], label(e), feed_sport(e["league"]), e["kind"], e.get("team", "")))
    return sorted(goals, key=lambda g: g.t), others


def parse_mappings(lines) -> list[Mapping]:
    """Which tab/tile showed which feed game: from a `feed map ... -> LEAGUE GAME` line until the next map line of the same tab and tile
    for ANOTHER game, a `dropped` line, or MAP_MAX_OPEN_S after the last line saying so."""
    per_tile: dict[tuple, list[tuple]] = {}
    for line in lines:
        m = MAP_LINE.search(line)
        if m:
            per_tile.setdefault((int(m.group(2)), int(m.group(3))), []).append((parse_time(m.group(1)), f"{m.group(4)} {m.group(5)}"))
            continue
        m = DROP_LINE.search(line)
        if m:
            per_tile.setdefault((int(m.group(2)), int(m.group(3))), []).append((parse_time(m.group(1)), None))
    out = []
    for (tab, tile), events in per_tile.items():
        events.sort(key=lambda x: x[0])
        current = None                   # [start, label, time of the last line saying so]

        def close(until):
            end = current[2] + datetime.timedelta(seconds=MAP_MAX_OPEN_S)
            out.append(Mapping(tab, tile, current[0], end if until is None else min(until, end), current[1], feed_sport(current[1].split()[0])))

        for t, label in events:
            if current is not None and label != current[1]:
                close(t)
                current = None
            if label is not None:
                if current is None:
                    current = [t, label, t]
                else:
                    current[2] = t
        if current is not None:
            close(None)
    return sorted(out, key=lambda x: x.start)


@dataclasses.dataclass(frozen=True)
class FeedRow:
    """One feed goal as seen by one tab/tile that was mapped to its game."""
    goal: FeedGoal
    tab: int
    tile: int
    ocr_t: datetime.datetime | None        # the OCR score change (None = missed by the OCR)
    picture_t: datetime.datetime | None    # when the goal happened ON THE PICTURE (None = unknown)
    anchor: str                            # "OCR change - 12 s", "feed + median lag of the tab" / "... of the sport", or "none"
    lead: Lead | None                      # the existing measurements, counted from the confirmation (the OCR change, or the feed time + median lag)
    logged_lag_s: int | None               # the daemon's own `feed confirmed ... feed lag` line (positive = feed ahead), for a cross-check

    @property
    def detected(self) -> bool:
        return self.ocr_t is not None

    @property
    def feed_lead_s(self) -> float | None:
        """OCR change minus feed time: positive = the feed knew the goal that many seconds before the picture's scoreboard showed it."""
        return None if self.ocr_t is None else (self.ocr_t - self.goal.t).total_seconds()

    @property
    def feed_ahead_s(self) -> float | None:
        """Seconds the feed was available BEFORE the goal on the picture (negative = the goal was already on the picture)."""
        return None if self.picture_t is None else (self.picture_t - self.goal.t).total_seconds()

    @property
    def already_main(self) -> bool:
        return bool(self.lead and self.lead.already_main)

    def ahead(self, which: str) -> float | None:
        """Seconds before the PICTURE goal that `which` (swap / buildup / cue / feed) was available; None = never. Lead numbers are counted
        from the confirmation, GOAL_LAG_S after the picture goal."""
        if which == "feed":
            return self.feed_ahead_s
        if self.lead is None:
            return None
        value = {"swap": self.lead.swap_lead_s, "buildup": self.lead.buildup_lead_s,
                 "cue": None if self.lead.first_cue is None else self.lead.first_cue[0]}[which]
        return None if value is None else value - GOAL_LAG_S


@dataclasses.dataclass(frozen=True)
class FeedReport:
    rows: list
    ocr_only: list                 # (Goal, sport, label): an OCR score change of a mapped tile that no feed goal explains
    goals: list                    # every deduped feed goal (mapped or not)
    nongoals: list                 # every deduped build-up event


def _match_ocr(goals: list[FeedGoal], maps: list[Mapping], ocr: list[Goal]):
    """Pairs (feed goal, mapping, OCR Goal or None) and the OCR changes nobody claimed. One OCR change explains one feed goal."""
    taken: set = set()
    pairs = []
    for g in goals:
        for mp in (x for x in maps if x.label == g.label and x.covers(g.t)):
            close = [o for o in ocr if o.tab == mp.tab and o.tile == mp.tile and id(o) not in taken
                     and -MATCH_BEFORE_S <= (o.t - g.t).total_seconds() <= MATCH_AFTER_S]
            hit = min(close, key=lambda o: o.t) if close else None
            if hit is not None:
                taken.add(id(hit))
            pairs.append((g, mp, hit))
    only = []
    for o in ocr:
        if id(o) in taken:
            continue
        mp = next((x for x in maps if x.tab == o.tab and x.tile == o.tile and x.covers(o.t)), None)
        if mp is not None:
            only.append((o, mp.sport, mp.label))
    return pairs, only


def measure_feed(goals: list[FeedGoal], nongoals: list[FeedNonGoal], maps: list[Mapping], ocr: list[Goal], readings: list[Reading], cues=(),
                 feed_lags=(), rule: Rule = Rule(), sports=(), buildups=()) -> FeedReport:
    """Ties the feed's goals to the OCR score changes of the tabs that showed the game, and measures them with the same functions as the
    OCR goals. `ocr` must be the raw score changes WITH their tile (`parse_goals`). The goal's time on the picture is the OCR change minus
    GOAL_LAG_S; for a goal the OCR missed it is the feed time plus the median (OCR change - feed time) of that tab (else of that sport), minus
    GOAL_LAG_S, and the lead numbers are then counted from that tab's imagined confirmation time."""
    pairs, only = _match_ocr(goals, maps, ocr)
    by_tab: dict[int, list[float]] = {}
    by_sport: dict[str, list[float]] = {}
    for g, mp, hit in pairs:
        if hit is not None:
            lead_s = (hit.t - g.t).total_seconds()
            by_tab.setdefault(mp.tab, []).append(lead_s)
            by_sport.setdefault(g.sport, []).append(lead_s)
    confirm: list[Goal] = []
    plan = []
    for g, mp, hit in pairs:
        if hit is not None:
            anchor, goal = "OCR change - 12 s", hit
        elif mp.tab in by_tab:
            anchor, goal = "feed + median lag of the tab", Goal(g.t + datetime.timedelta(seconds=statistics.median(by_tab[mp.tab])), mp.tab, mp.tile)
        elif g.sport in by_sport:
            anchor, goal = "feed + median lag of the sport", Goal(g.t + datetime.timedelta(seconds=statistics.median(by_sport[g.sport])), mp.tab, mp.tile)
        else:
            anchor, goal = "none", None
        plan.append((g, mp, hit, anchor, goal))
        if goal is not None:
            confirm.append(goal)
    leads = measure(readings, confirm, list(cues), feed_lags, rule, sports, buildups) if confirm else []
    rows = []
    for g, mp, hit, anchor, goal in plan:
        lead = None if goal is None else next((l for l in leads if l.goal is goal), None)
        picture = None if goal is None else goal.t - datetime.timedelta(seconds=GOAL_LAG_S)
        lag = None if hit is None else next((v for t, tab, v in feed_lags if tab == hit.tab and 0 <= (t - hit.t).total_seconds() <= 60), None)
        rows.append(FeedRow(g, mp.tab, mp.tile, None if hit is None else hit.t, picture, anchor, lead, lag))
    return FeedReport(rows, only, goals, nongoals)


def base_rates(goals: list[FeedGoal], nongoals: list[FeedNonGoal]) -> dict:
    """sport -> kind -> (events, followed by a goal of the same game within BASE_RATE_S after the feed time of the event)."""
    out: dict = {}
    for n in nongoals:
        followed = any(g.game_id == n.game_id and 0 < (g.t - n.t).total_seconds() <= BASE_RATE_S for g in goals)
        events, hits = out.setdefault(n.sport, {}).get(n.kind, (0, 0))
        out[n.sport][n.kind] = (events + 1, hits + followed)
    return out


def _stats(values: list[float], unit: str = "s") -> str:
    return f"median {statistics.median(values):+.0f} {unit}, min {min(values):+.0f}, max {max(values):+.0f}" if values else "none"


def summarise_feed(report: FeedReport, detail: bool = True) -> str:
    """The feed block of the report: per row (unless `detail` is False), then per sport the counts, the feed's lead over the picture, how far
    ahead of the picture goal each signal (swap rule, build-up tracker, first cue, the feed itself) was for goals not in MAIN, and the base
    rates of the build-up events."""
    if not report.rows and not report.ocr_only and not report.nongoals:
        return "Feed: no goal of a mapped game and no build-up event found in the recordings."
    lines = ["Feed recordings as ground truth (measuring only; a positive number = ahead of the picture):"]
    for r in report.rows if detail else []:
        g = r.goal
        got = "missed by OCR" if not r.detected else f"OCR change {r.ocr_t:%H:%M:%S} (feed lead {r.feed_lead_s:+.0f} s)"
        extra = "" if r.lead is None else "".join(f"  {name} {'-' if r.ahead(k) is None else f'{r.ahead(k):+.0f}'}"
                                                   for name, k in (("swap", "swap"), ("build-up", "buildup"), ("cue", "cue"), ("feed", "feed")))
        log = "" if r.logged_lag_s is None else f"  (logged feed lag {r.logged_lag_s:+d} s)"
        main = "  already MAIN" if r.already_main else ""
        lines.append(f"{g.t:%Y-%m-%d %H:%M:%S}  {g.label}  {g.kind} {g.team}  tab {r.tab}[{r.tile}]  {got}  anchor: {r.anchor}{main}{extra}{log}")
    if detail and report.rows:
        lines.append("")
    rates = base_rates(report.goals, report.nongoals)
    for sport in sorted({r.goal.sport for r in report.rows} | {s for _, s, _ in report.ocr_only} | set(rates)):
        mine = [r for r in report.rows if r.goal.sport == sport]
        found = [r for r in mine if r.detected]
        counted = [r for r in mine if not r.already_main and r.lead is not None]
        only = [x for x in report.ocr_only if x[1] == sport]
        lines.append(f"{sport} (feed): {len(mine)} feed goals on mapped tiles, {len(found)} detected by the OCR, {len(mine) - len(found)} missed by the OCR, "
                     f"{len(only)} OCR score changes with no feed goal (OCR-only, likely noise)")
        if found:
            lines.append(f"    feed lead over the picture (OCR change minus feed time): {_stats([r.feed_lead_s for r in found])}")
        if mine:
            anchors = {a: sum(1 for r in mine if r.anchor == a) for a in sorted({r.anchor for r in mine})}
            lines.append("    picture-goal anchor: " + ", ".join(f"{a} x{n}" for a, n in anchors.items()))
            lines.append(f"    goals not in MAIN: {len(counted)} (of {len(mine)}); seconds BEFORE the picture goal that each was available:")
            for name, key in (("swap rule", "swap"), ("build-up tracker", "buildup"), ("first cue", "cue"), ("the feed", "feed")):
                # for the feed only the goals the OCR saw are measured: for a missed goal the number is just the assumed median lag
                pool = [r for r in counted if r.detected] if key == "feed" else counted
                values = [r.ahead(key) for r in pool if r.ahead(key) is not None and r.ahead(key) >= 0]
                lines.append(f"        {name}: {len(values)} of {len(pool)}" + (f" ({_stats(values)})" if values else "")
                             + (" [goals seen by the OCR only]" if key == "feed" else ""))
        for kind, (n, hit) in sorted(rates.get(sport, {}).items()):
            lines.append(f"    feed event {kind}: {n}, " + (f"{hit} followed by a goal within {BASE_RATE_S:.0f} s" if n >= BASE_RATE_MIN
                                                           else "too few events for a base rate"))
    return "\n".join(lines)
