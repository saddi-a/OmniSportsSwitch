"""External game data (Stage 0: record only). Reads ESPN's public JSON, turns it into a common shape, notices what
changed and writes it to logs/feed/ so it can be compared with what the streams show. It changes NO score and NO swap.

ESPN's feed is unofficial (no key, no account, no documented limits; Disney's terms forbid automated access, the user
accepted that for now: see docs/api-plan.md). `Cache-Control: max-age=5`, so nothing is asked more often than every 5 s.

Everything here is pure except `fetch_json` and `FeedRecorder` (disk). `FeedSession.step` is called from a thread by the
server; time and the network are injected, so the tests use fixtures and a fake fetch.
"""
import dataclasses
import datetime
import json
import logging
import pathlib
import re
import urllib.error
import urllib.request

log = logging.getLogger("daemon")

BASE = "https://site.api.espn.com/apis/site/v2/sports/"
USER_AGENT = "OmniSportsSwitch/0.1 (personal project, one request per league every 5 s)"
# slug -> (sport, path). Only ita.1, uefa.champions, nfl, college-football and nba were checked; a slug that does not
# exist answers 404 and is switched off after two answers (logged), which tells us which ones work.
LEAGUES = {
    "ita.1": ("soccer", "soccer/ita.1"), "eng.1": ("soccer", "soccer/eng.1"), "esp.1": ("soccer", "soccer/esp.1"),
    "fra.1": ("soccer", "soccer/fra.1"), "ger.1": ("soccer", "soccer/ger.1"),
    "uefa.champions": ("soccer", "soccer/uefa.champions"), "uefa.europa": ("soccer", "soccer/uefa.europa"),
    "fifa.world": ("soccer", "soccer/fifa.world"), "uefa.nations": ("soccer", "soccer/uefa.nations"),
    "fifa.friendly": ("soccer", "soccer/fifa.friendly"),
    "nfl": ("american football", "football/nfl"), "college-football": ("american football", "football/college-football"),
    "nba": ("basketball", "basketball/nba"),
}
QUARTER_S = {"american football": 900.0, "basketball": 720.0}
BACKOFF_S = (5.0, 10.0, 30.0, 60.0)     # after errors, per league
SOON_MIN = 20.0                         # a game starting within this many minutes counts as active
MAX_SUMMARIES = 6                       # live American football games whose plays are fetched at once
BIG_PLAY_YD = 20


def scoreboard_url(league: str) -> str:
    return f"{BASE}{LEAGUES[league][1]}/scoreboard"


def summary_url(league: str, game_id: str) -> str:
    return f"{BASE}{LEAGUES[league][1]}/summary?event={game_id}"


# ---------------------------------------------------------------------------------------------------------------
# The common shape
# ---------------------------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Team:
    id: str
    abbr: str
    name: str


@dataclasses.dataclass(frozen=True)
class FeedEvent:
    game_id: str
    key: tuple              # identity, to notice an event only once
    kind: str               # goal, red card, touchdown, field goal, interception, big play, ...
    period: int | None
    clock_s: float | None   # soccer: seconds into the match; American football / basketball: seconds left in the period
    display: str            # the feed's own clock text ("53'", "10:51")
    team: str               # team abbreviation ("" if unknown)
    text: str = ""
    yards: int = 0


@dataclasses.dataclass(frozen=True)
class Snapshot:
    game_id: str
    league: str
    sport: str
    home: Team
    away: Team
    state: str              # pre, in, post
    status: str             # STATUS_SECOND_HALF, STATUS_HALFTIME, STATUS_FINAL...
    period: int | None
    clock_s: float | None
    display: str
    home_score: int | None
    away_score: int | None
    start: str              # ISO time of the kick-off
    events: tuple = ()      # FeedEvent (soccer: from the scoreboard; American football: filled from the summary)
    situation: dict | None = None   # American football: down, distance, yard line, red zone (as the feed gives it)

    @property
    def label(self) -> str:
        return f"{self.home.abbr}-{self.away.abbr}"


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clock_seconds(text) -> float | None:
    """'12:21' -> 741.0; '0:07.5' -> 7.5; None when it is not a clock."""
    m = re.fullmatch(r"\s*(\d+):(\d{1,2}(?:\.\d+)?)\s*", text or "")
    return int(m.group(1)) * 60 + float(m.group(2)) if m else None


def game_elapsed_s(sport: str, period: int | None, clock_s: float | None) -> float | None:
    """Seconds since the start of the game on one scale, so two moments of a game can be compared. Soccer clocks already
    run up from the kick-off; American football and basketball clocks count down inside each period."""
    if clock_s is None:
        return None
    if sport == "soccer":
        return clock_s
    quarter = QUARTER_S.get(sport)
    if quarter is None or not period or period < 1:
        return None
    return (period - 1) * quarter + (quarter - clock_s)


def _team(c: dict) -> Team:
    t = c.get("team", {})
    return Team(str(t.get("id", c.get("id", ""))), t.get("abbreviation") or t.get("shortDisplayName") or "?",
                t.get("displayName") or t.get("shortDisplayName") or "?")


# ---------------------------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------------------------
def _soccer_kind(d: dict) -> str:
    text = (d.get("type") or {}).get("text", "").lower()
    if d.get("shootout"):
        return "shootout"
    if d.get("scoringPlay"):
        return "own goal" if d.get("ownGoal") else ("penalty goal" if d.get("penaltyKick") else "goal")
    if d.get("redCard"):
        return "red card"
    if d.get("yellowCard"):
        return "yellow card"
    if d.get("penaltyKick") or "penalty" in text:
        return "penalty"
    return text or "other"


def _soccer_events(game_id: str, details: list, teams: dict) -> tuple:
    events = []
    for d in details or []:
        clock = d.get("clock") or {}
        team_id = str((d.get("team") or {}).get("id", ""))
        text = (d.get("type") or {}).get("text", "")
        events.append(FeedEvent(game_id, (game_id, text, clock.get("value"), team_id), _soccer_kind(d), None,
                                _float(clock.get("value")), clock.get("displayValue", ""),
                                teams[team_id].abbr if team_id in teams else "", text))
    return tuple(events)


def parse_scoreboard(data: dict, league: str) -> list[Snapshot]:
    sport = LEAGUES[league][0]
    out = []
    for e in (data or {}).get("events", []):
        try:
            comp = e["competitions"][0]
            sides = {c.get("homeAway"): c for c in comp["competitors"]}
            home, away = sides["home"], sides["away"]
            teams = {t.id: t for t in (_team(home), _team(away))}
            status = e["status"]
            situation = comp.get("situation")
            out.append(Snapshot(
                str(e["id"]), league, sport, _team(home), _team(away), status["type"]["state"], status["type"]["name"],
                _int(status.get("period")), _float(status.get("clock")), status.get("displayClock", ""),
                _int(home.get("score")), _int(away.get("score")), e.get("date", ""),
                _soccer_events(str(e["id"]), comp.get("details"), teams) if sport == "soccer" else (),
                dict(situation) if isinstance(situation, dict) else None))
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            log.warning("feed: a game of %s could not be read (%s: %s)", league, type(error).__name__, error)
    return out


def _play_kind(p: dict) -> str:
    text = (p.get("type") or {}).get("text", "")
    low = text.lower()
    if "touchdown" in low:
        return "touchdown"
    if "field goal" in low:
        return "field goal good" if "good" in low else "field goal"
    if "safety" in low:
        return "safety"
    if "interception" in low:
        return "interception"
    if "fumble" in low:
        return "fumble"
    if low == "sack":
        return "sack"
    if low == "penalty":
        return "penalty"
    if p.get("scoringPlay"):
        return "score"
    if (p.get("statYardage") or 0) >= BIG_PLAY_YD:
        return "big play"
    return ""


def parse_summary(data: dict, snap: Snapshot) -> tuple:
    """American football: the plays worth knowing about (scores, turnovers, sacks, penalties, big gains) of a game's
    summary. Quiet plays are not events."""
    drives = (data or {}).get("drives") or {}
    plays = [p for d in list(drives.get("previous", [])) + ([drives["current"]] if isinstance(drives.get("current"), dict) else [])
             for p in d.get("plays", [])]
    events = []
    for p in plays:
        kind = _play_kind(p)
        if not kind:
            continue
        clock = (p.get("clock") or {}).get("displayValue", "")
        events.append(FeedEvent(snap.game_id, (snap.game_id, "play", str(p.get("id"))), kind, _int((p.get("period") or {}).get("number")),
                                clock_seconds(clock), clock, "", p.get("text", ""), _int(p.get("statYardage")) or 0))
    return tuple(events)


# ---------------------------------------------------------------------------------------------------------------
# What changed
# ---------------------------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Note:
    kind: str               # event, score, status, game
    snap: Snapshot
    text: str
    event: FeedEvent | None = None


class FeedTracker:
    """Compares each poll with the previous one. Events already present when a game is first seen are remembered but
    not reported (starting the daemon mid-game must not look like a burst of events)."""

    def __init__(self) -> None:
        self.last: dict[str, Snapshot] = {}
        self.seen: set = set()

    def update(self, snaps: list[Snapshot]) -> list[Note]:
        notes = []
        for s in snaps:
            before = self.last.get(s.game_id)
            if before is None:
                notes.append(Note("game", s, f"{s.state} {s.display}"))
                self.seen.update(e.key for e in s.events)
            else:
                if (s.home_score, s.away_score) != (before.home_score, before.away_score):
                    notes.append(Note("score", s, f"{before.home_score}-{before.away_score} -> {s.home_score}-{s.away_score}"))
                if (s.state, s.status) != (before.state, before.status):
                    notes.append(Note("status", s, f"{before.status} -> {s.status}"))
                for e in s.events:
                    if e.key not in self.seen:
                        self.seen.add(e.key)
                        notes.append(Note("event", s, e.text, e))
            self.last[s.game_id] = s
        return notes

    def add_events(self, game_id: str, events: tuple, first: bool) -> list[Note]:
        """Events found in a game's summary (American football)."""
        snap = self.last.get(game_id)
        if snap is None:
            return []
        notes = []
        for e in events:
            if e.key not in self.seen:
                self.seen.add(e.key)
                if not first:
                    notes.append(Note("event", snap, e.text, e))
        return notes


# ---------------------------------------------------------------------------------------------------------------
# When to ask
# ---------------------------------------------------------------------------------------------------------------
def starts_within(snap: Snapshot, utc: datetime.datetime, minutes: float) -> bool:
    try:
        start = datetime.datetime.fromisoformat(snap.start.replace("Z", "+00:00"))
    except ValueError:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=datetime.timezone.utc)
    return -60 * 6 <= (start - utc).total_seconds() / 60.0 <= minutes and snap.state == "pre"


class Poller:
    """One request per league. A league with a live game or one starting soon is asked every `poll_s` (never less
    than 5 s, the feed's own cache time); a quiet one every `idle_s`. Errors back off; two 404s switch a league off."""

    def __init__(self, leagues, poll_s: float, idle_s: float) -> None:
        self.leagues = list(leagues)
        self.poll_s, self.idle_s = max(poll_s, 5.0), max(idle_s, 5.0)
        self.next_at: dict[str, float] = {}
        self.errors: dict[str, int] = {}
        self.not_found: dict[str, int] = {}
        self.disabled: set[str] = set()

    def due(self, now: float) -> list[str]:
        return [lg for lg in self.leagues if lg not in self.disabled and now >= self.next_at.get(lg, 0.0)]

    def done(self, league: str, now: float, snaps: list[Snapshot], utc: datetime.datetime) -> None:
        self.errors[league] = self.not_found[league] = 0
        active = any(s.state == "in" or starts_within(s, utc, SOON_MIN) for s in snaps)
        self.next_at[league] = now + (self.poll_s if active else self.idle_s)

    def failed(self, league: str, now: float, status: int | None) -> bool:
        """Returns True when this failure switched the league off."""
        if status == 404:
            self.not_found[league] = self.not_found.get(league, 0) + 1
            if self.not_found[league] >= 2:
                self.disabled.add(league)
                return True
        n = self.errors.get(league, 0)
        self.errors[league] = n + 1
        self.next_at[league] = now + BACKOFF_S[min(n, len(BACKOFF_S) - 1)]
        return False


# ---------------------------------------------------------------------------------------------------------------
# Disk and network
# ---------------------------------------------------------------------------------------------------------------
def fetch_json(url: str, timeout: float = 8.0) -> tuple[int | None, dict | None]:
    """(HTTP status, parsed JSON). (None, None) on a network error, (status, None) on an HTTP error or bad JSON."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, None


def snapshot_row(s: Snapshot) -> dict:
    return {"league": s.league, "game": s.game_id, "home": s.home.abbr, "away": s.away.abbr, "state": s.state, "status": s.status,
            "period": s.period, "display": s.display, "clock_s": s.clock_s, "score": [s.home_score, s.away_score]}


class FeedRecorder:
    """logs/feed/<date>.jsonl: a 'snap' line when a game's clock text, score, period or state changes, an 'event' line
    for each new event, and 'error' lines. Every line has the local time `t` (what the comparison needs)."""

    def __init__(self, directory: pathlib.Path) -> None:
        self.directory = directory
        self.last: dict[str, dict] = {}

    def _write(self, now: datetime.datetime, row: dict) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with open(self.directory / f"{now:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": now.isoformat(timespec="milliseconds"), **row}, ensure_ascii=False) + "\n")
        except OSError as error:
            log.warning("feed: could not write the record (%s)", error)

    def snaps(self, snaps: list[Snapshot], now: datetime.datetime) -> None:
        for s in snaps:
            row = snapshot_row(s)
            if self.last.get(s.game_id) != row:
                self.last[s.game_id] = row
                self._write(now, {"type": "snap", **row})

    def notes(self, notes: list[Note], now: datetime.datetime) -> None:
        for n in notes:
            if n.event is not None:
                e = n.event
                self._write(now, {"type": "event", "league": n.snap.league, "game": n.snap.game_id, "home": n.snap.home.abbr,
                                  "away": n.snap.away.abbr, "kind": e.kind, "period": e.period, "clock_s": e.clock_s,
                                  "display": e.display, "team": e.team, "yards": e.yards, "text": e.text,
                                  "score": [n.snap.home_score, n.snap.away_score]})

    def error(self, league: str, status: int | None, now: datetime.datetime) -> None:
        self._write(now, {"type": "error", "league": league, "status": status})


class FeedSession:
    """Runs on a thread: asks the leagues that are due, parses, finds what changed, records it. `step` returns the notes
    so the server can write them to the log."""

    def __init__(self, config, recorder: FeedRecorder | None = None, fetch=fetch_json, leagues=None) -> None:
        self.config = config
        self.recorder = recorder
        self.fetch = fetch
        self.poller = Poller(list(leagues or LEAGUES), config.feed_poll_s, config.feed_idle_poll_s)
        self.tracker = FeedTracker()
        self.summary_at: dict[str, float] = {}
        self.summary_done: set[str] = set()
        self.messages: list[str] = []     # warnings for the log (league switched off, errors)

    def step(self, now: float, wall: datetime.datetime) -> list[Note]:
        self.poller.poll_s = max(self.config.feed_poll_s, 5.0)
        self.poller.idle_s = max(self.config.feed_idle_poll_s, 5.0)
        utc = wall.astimezone(datetime.timezone.utc)
        notes: list[Note] = []
        for league in self.poller.due(now):
            status, data = self.fetch(scoreboard_url(league))
            if data is None:
                if self.recorder:
                    self.recorder.error(league, status, wall)
                if self.poller.failed(league, now, status):
                    self.messages.append(f"feed league {league} not available (HTTP {status}), switched off")
                continue
            snaps = parse_scoreboard(data, league)
            self.poller.done(league, now, snaps, utc)
            found = self.tracker.update(snaps)
            notes += found
            if self.recorder:
                self.recorder.snaps(snaps, wall)
                self.recorder.notes(found, wall)
            notes += self._summaries(snaps, now, wall)
        return notes

    def _summaries(self, snaps: list[Snapshot], now: float, wall: datetime.datetime) -> list[Note]:
        interval = self.config.feed_summary_s
        if interval <= 0:
            return []
        notes = []
        live = [s for s in snaps if s.state == "in" and s.sport == "american football"][:MAX_SUMMARIES]
        for s in live:
            if now < self.summary_at.get(s.game_id, 0.0):
                continue
            self.summary_at[s.game_id] = now + max(interval, 5.0)
            status, data = self.fetch(summary_url(s.league, s.game_id))
            if data is None:
                continue
            first = s.game_id not in self.summary_done
            self.summary_done.add(s.game_id)
            found = self.tracker.add_events(s.game_id, parse_summary(data, s), first)
            notes += found
            if self.recorder:
                self.recorder.notes(found, wall)
        return notes
