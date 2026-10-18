"""Compares what the feed recorded (logs/feed/*.jsonl) with what the streams showed (the OCR clock lines of daemon.log),
to measure how far each stream runs behind the feed. Offline and read-only: it changes nothing (see scripts/feed_compare.py).

For one moment of a game (a soccer minute turning over, a goal, a touchdown) the feed was first asked at wall time
`t_feed` (the poll that saw it) and the stream's own game clock reached that moment at `t_stream`. The LEAD is
`t_stream - t_feed`: positive = the feed was ahead of the picture by that many seconds (room to switch early),
negative = the picture was already there (the feed was no use for that moment). Accuracy is about one poll (5 s) plus one
OCR pass (2.5 s). American football clocks count down inside a period and the OCR gives no period, so those moments
are matched by the clock crossing the target close to the feed's time: an approximation.
"""
import ast
import dataclasses
import datetime
import json
import re
import statistics
import unicodedata

from daemon import feed
from daemon.clock import clock_seconds

OCR_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})\s+ocr tab (\d+)\[(\d+)\]: clocks=(\[[^\]]*\]) texts=(\[.*\])\s*$")
CLOCK = re.compile(r"^(\d{1,3}):(\d{2})$")
SEARCH_BEFORE_S, SEARCH_AFTER_S = 300.0, 300.0     # how far from the feed's time the stream's moment is looked for
MIN_TEXT_HITS = 3                                   # readings that must show both teams before a tile is tied to a game


@dataclasses.dataclass(frozen=True)
class Reading:
    t: datetime.datetime
    tab: int
    tile: int
    clock_s: float | None
    texts: tuple


@dataclasses.dataclass(frozen=True)
class Measurement:
    game: str            # "league HOME-AWAY"
    what: str            # what moment ("minute 85", "goal 53'", ...)
    feed_t: datetime.datetime
    stream_t: datetime.datetime | None
    tile: tuple | None

    @property
    def lead_s(self) -> float | None:
        return None if self.stream_t is None else (self.stream_t - self.feed_t).total_seconds()


def parse_clock(text: str) -> float | None:
    return clock_seconds(text)   # the shared parser (`daemon/clock.py`)


def parse_ocr(lines) -> list[Reading]:
    out = []
    for line in lines:
        m = OCR_LINE.match(line)
        if not m:
            continue
        try:
            clocks = ast.literal_eval(m.group(4))
            texts = ast.literal_eval(m.group(5))
        except (ValueError, SyntaxError):
            continue
        seconds = [c for c in (parse_clock(str(x)) for x in clocks) if c is not None]
        out.append(Reading(datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f"), int(m.group(2)), int(m.group(3)),
                           seconds[0] if seconds else None, tuple(str(x) for x in texts)))
    return out


def parse_feed(lines) -> list[dict]:
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
            row["t"] = datetime.datetime.fromisoformat(row["t"]).replace(tzinfo=None)
            rows.append(row)
        except (ValueError, KeyError, TypeError):
            continue
    return rows


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))


def shows_teams(texts, home: str, away: str) -> bool:
    """Both teams' codes (first three letters of the feed's abbreviation) appear in one reading."""
    folded = [_fold(t) for t in texts]
    return all(any(code in t for t in folded) for code in (_fold(home)[:3], _fold(away)[:3]))


def tiles_of(readings: list[Reading], home: str, away: str) -> list[tuple]:
    """(tab, tile) pairs whose readings show this game's two teams in at least MIN_TEXT_HITS readings."""
    hits: dict[tuple, int] = {}
    for r in readings:
        if shows_teams(r.texts, home, away):
            hits[(r.tab, r.tile)] = hits.get((r.tab, r.tile), 0) + 1
    return [key for key, n in hits.items() if n >= MIN_TEXT_HITS]


def crossing(readings: list[Reading], tile: tuple, target_s: float, counts_up: bool, around: datetime.datetime) -> datetime.datetime | None:
    """First time near `around` that the clock of a tile passes `target_s` (up-counting: reaches it; countdown: goes
    to it or below). The reading before must be on the other side, so a clock that was already past it does not count."""
    window = [r for r in readings if (r.tab, r.tile) == tile and r.clock_s is not None
              and -SEARCH_BEFORE_S <= (r.t - around).total_seconds() <= SEARCH_AFTER_S]
    window.sort(key=lambda r: r.t)
    for before, after in zip(window, window[1:]):
        if counts_up and before.clock_s < target_s <= after.clock_s and after.clock_s - before.clock_s < 60:
            return after.t
        if not counts_up and before.clock_s > target_s >= after.clock_s and before.clock_s - after.clock_s < 60:
            return after.t
    return None


def measure(rows: list[dict], readings: list[Reading]) -> list[Measurement]:
    out: list[Measurement] = []
    last_display: dict[str, str] = {}
    for row in rows:
        league = row.get("league")
        if league not in feed.LEAGUES:
            continue
        sport = feed.LEAGUES[league][0]
        name = f"{league} {row.get('home')}-{row.get('away')}"
        tiles = tiles_of(readings, row.get("home", ""), row.get("away", ""))
        if row.get("type") == "snap" and sport == "soccer" and row.get("state") == "in":
            display = row.get("display", "")
            m = re.fullmatch(r"(\d+)'", display)
            before = last_display.get(row["game"], "")
            last_display[row["game"]] = display
            if m and re.fullmatch(r"\d+'", before) and int(m.group(1)) == int(before[:-1]) + 1:
                target = (int(m.group(1)) - 1) * 60.0      # "85'" begins when the match clock reads 84:00
                for tile in tiles:
                    out.append(Measurement(name, f"minute {m.group(1)}", row["t"], crossing(readings, tile, target, True, row["t"]), tile))
        elif row.get("type") == "event" and row.get("clock_s") is not None:
            target = float(row["clock_s"])
            for tile in tiles:
                out.append(Measurement(name, f"{row.get('kind')} {row.get('display', '')}".strip(), row["t"],
                                       crossing(readings, tile, target, sport == "soccer", row["t"]), tile))
    return out


def summarise(measurements: list[Measurement]) -> str:
    if not measurements:
        return "no moment could be matched (is a tile showing both teams, and did the recorder run during the game?)"
    lines = []
    for m in measurements:
        lead = "stream clock not found" if m.lead_s is None else f"lead {m.lead_s:+.0f} s"
        lines.append(f"{m.feed_t:%H:%M:%S}  {m.game}  {m.what}  tab {m.tile[0]}[{m.tile[1]}]: {lead}")
    leads = [m.lead_s for m in measurements if m.lead_s is not None]
    if leads:
        lines.append(f"{len(leads)} moments matched: median lead {statistics.median(leads):+.0f} s, "
                     f"min {min(leads):+.0f} s, max {max(leads):+.0f} s (positive = the feed was ahead of the picture)")
    return "\n".join(lines)
