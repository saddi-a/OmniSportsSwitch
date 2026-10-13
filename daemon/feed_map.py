"""Stage 1 of the external feed: which feed game does each tab/tile show? (Measuring only: nothing here changes scoring or swaps.)

Inputs, per tile: the OCR texts (team codes of the scorebug), the OCR score and clock, and the tab's teams text from the popup.
A game is a candidate when BOTH of its teams are found in one reading. A candidate becomes a lock after MIN_HITS readings
out of the last WINDOW, and only while the OCR score and clock fit the feed's own timeline at that clock (a replay or an
old game with the same teams does not fit). The popup's teams text counts as a second source: it lowers the number of
readings needed by one, and a lock that only the popup supports is logged as "unverified". Locks are dropped when the
score has disagreed for DROP_AFTER readings in a row. Failure to lock is always safe: that tile just stays local.
"""
import collections
import dataclasses
import functools
import re
import unicodedata

from daemon import feed, team_names as names_table

WINDOW = 10            # readings (with at least one candidate) remembered per tile
MIN_HITS = 3           # readings in the window that must show the game's two teams
MIN_OK = 2             # of those, readings whose score/clock fit the feed's timeline
DROP_AFTER = 20        # readings in a row that disagree with the feed before a lock is dropped (the referee speaks at 10, first)
SLACK_S = 90.0         # soccer: the score at the stream clock may come from the feed within this many seconds
AHEAD_S = 120.0        # a stream cannot be ahead of the feed's clock by more than this
NAME_PREFIXES = {"AS", "AC", "FC", "SS", "US", "SSC", "ACF", "CF", "RC", "SC", "AFC", "BSC", "FK", "SV", "VFB", "VFL", "TSG", "RB"}

# Folded team name (or abbreviation) -> other ways broadcasters write it. These hand-written aliases are the OVERRIDE layer: they
# always apply and are never reduced. `daemon/team_names.py` (the table for the top-5 leagues, UEFA clubs, NFL and FBS) fills the gaps
# on top of them. A name that is missing only means "unmapped", which is safe.
ALIASES = {
    "ROMA": ("ROME",), "COMO": ("COME", "COMO 1907"),
    "PARIS SAINT-GERMAIN": ("PSG", "PARIS SG", "PARIS"), "PARIS SAINT GERMAIN": ("PSG", "PARIS SG", "PARIS"),
    "INTERNAZIONALE": ("INTER", "INTER MILAN", "INTER DE MILAN"), "INTER MILAN": ("INTER", "INTERNAZIONALE"),
    "AC MILAN": ("MILAN",), "NAPOLI": ("NAPLES",), "JUVENTUS": ("JUVE",), "TORINO": ("TURIN",),
    "BAYERN MUNICH": ("BAYERN", "BAYERN MUNCHEN", "BAYERN MUNICH"), "BORUSSIA DORTMUND": ("DORTMUND", "BVB"),
    "ATLETICO MADRID": ("ATLETICO", "ATLETICO DE MADRID", "ATM"), "REAL MADRID": ("MADRID",),
    "BARCELONA": ("BARCA", "BARÇA", "FC BARCELONA", "BARCELONE"), "MANCHESTER UNITED": ("MAN UNITED", "MAN UTD", "MANCHESTER UTD", "MUN", "MANU", "MAN U"), "MAN": ("MUN", "MANU"), "LILL": ("LIL", "LILLE"),
    "MANCHESTER CITY": ("MAN CITY", "MCI"), "MNC": ("MCI",), "MUN": ("MAN",), "TOTTENHAM HOTSPUR": ("TOTTENHAM", "SPURS"), "LYON": ("OL", "OLYMPIQUE LYONNAIS", "LYONNAIS"),
    "MARSEILLE": ("OM", "OLYMPIQUE DE MARSEILLE", "OLYMPIQUE MARSEILLE"), "MONACO": ("AS MONACO",),
}


def fold(text: str) -> str:
    """Upper case, no accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))


@functools.lru_cache(maxsize=512)
def team_names(team: feed.Team) -> tuple:
    """Every way a team may be written on screen: abbreviation, full name, the name without a club prefix, aliases."""
    names = {fold(team.abbr), fold(team.name)}
    words = fold(team.name).split()
    if len(words) > 1 and words[0] in NAME_PREFIXES:
        names.add(" ".join(words[1:]))
    if len(words) > 1 and words[-1] in NAME_PREFIXES:
        names.add(" ".join(words[:-1]))
    for name in list(names):
        names.update(fold(a) for a in ALIASES.get(name, ()))
    names.update(names_table.aliases_for(team.name))     # the table fills the gaps
    candidates = set(names_table.candidates_for(team.name))
    names.update(candidates)       # ambiguous / 2-letter codes: only candidates (see `shows_game`), the only names under 3 characters
    return tuple(sorted(n for n in names if (len(n) >= 3 or n in candidates) and n != "?"))


@functools.lru_cache(maxsize=2048)
def _pattern(name: str):
    return re.compile(r"(?<![A-Z0-9])" + re.escape(name) + r"(?![A-Z0-9])")


def _hits(texts: list[str], team: feed.Team) -> set:
    """The names of the team found in the texts. Names of 2 characters (candidate codes) must be written in upper case."""
    folded = [fold(t) for t in texts]
    found = set()
    for n in team_names(team):
        if any(_pattern(n).search(t) for t in (texts if len(n) < 3 else folded)):
            found.add(n)
    return found


def shows_team(texts: list[str], team: feed.Team) -> bool:
    return bool(_hits(texts, team))


def shows_game(texts: list[str], snap: feed.Snapshot) -> bool:
    """Both teams are in the texts of one reading. A candidate code (REAL, MSU, OM...) may stand for either team, but one token
    never stands for both: the other team needs a different name or code."""
    home, away = _hits(texts, snap.home), _hits(texts, snap.away)
    return any(h != a for h in home for a in away)


def parse_clock_s(clocks: list[str]) -> float | None:
    for c in clocks:
        s = feed.clock_seconds(c)
        if s is not None:
            return s
    return None


@dataclasses.dataclass(frozen=True)
class Mapping:
    game_id: str
    league: str
    label: str
    via: str          # "ocr", "ocr+text" or "text"
    verified: bool    # the OCR score/clock fitted the feed's timeline
    hits: int
    ok: int


class _Tile:
    def __init__(self) -> None:
        self.window: collections.deque = collections.deque(maxlen=WINDOW)   # per reading: {game_id: True/False/None}
        self.lock: Mapping | None = None
        self.bad = 0                  # readings in a row that disagree with the locked game
        self.ambiguous = False


class FeedMapper:
    """Lives in the server, fed from the event loop: `set_games` after each feed step, `observe` after each OCR pass."""

    def __init__(self) -> None:
        self.games: dict[str, feed.Snapshot] = {}
        self.history: dict[str, list] = {}       # game id -> [(elapsed_s, home_score, away_score)] as the feed showed them
        self.sport_of: dict[str, str] = {}
        self.text: dict[int, str] = {}           # tab id -> teams typed in the popup
        self.tiles: dict[tuple, _Tile] = {}

    # -- inputs ------------------------------------------------------------------------------------------------------
    def set_games(self, snaps: list[feed.Snapshot]) -> None:
        self.games = {s.game_id: s for s in snaps}
        for s in snaps:
            if s.state == "pre" or s.clock_s is None or s.home_score is None or s.away_score is None:
                continue
            elapsed = feed.game_elapsed_s(s.sport, s.period, s.clock_s)
            if elapsed is None:
                continue
            self.sport_of[s.game_id] = s.sport
            rows = self.history.setdefault(s.game_id, [])
            row = (elapsed, s.home_score, s.away_score)
            if not rows or rows[-1][1:] != row[1:] or row[0] < rows[-1][0]:
                rows.append(row)

    def set_text(self, tab_id: int, text: str) -> None:
        self.text[tab_id] = text or ""

    def forget(self, tab_id: int) -> None:
        self.text.pop(tab_id, None)
        for key in [k for k in self.tiles if k[0] == tab_id]:
            del self.tiles[key]

    def mapping(self, tab_id: int, tile: int) -> Mapping | None:
        t = self.tiles.get((tab_id, tile))
        return t.lock if t else None

    def allowed_scores(self, game_id: str, clock_s: float, period: int | None, before_s: float = 8.0, after_s: float = 60.0):
        """The scores the screen may show at this stream moment: the feed's score a few seconds before it, and every score the
        feed reached up to `after_s` later (the feed notices a goal after the picture showed it, never before). None = the feed
        recorded nothing for that moment (started later, no clock), so nobody can judge."""
        rows = self.history.get(game_id)
        sport = self.sport_of.get(game_id)
        if not rows or sport is None:
            return None
        stream = clock_s if sport == "soccer" else feed.game_elapsed_s(sport, period, clock_s)
        if stream is None:
            return None
        allowed = {r[1:] for r in rows if stream - before_s < r[0] <= stream + after_s}
        earlier = [r for r in rows if r[0] <= stream - before_s]
        if earlier:
            allowed.add(earlier[-1][1:])
        return allowed or None

    # -- the fit with the feed's timeline ---------------------------------------------------------------------------
    def fits(self, snap: feed.Snapshot, score: tuple | None, clock_s: float | None) -> bool | None:
        """Does the OCR score (and clock) fit the feed at the stream's clock? None = cannot tell."""
        if snap.state == "pre":
            return False
        if score is None or snap.home_score is None or snap.away_score is None:
            return None
        if snap.sport == "soccer" and clock_s is not None and snap.clock_s is not None:
            if clock_s > snap.clock_s + AHEAD_S:
                return False
            rows = self.history.get(snap.game_id)
            if rows and rows[0][0] <= clock_s - SLACK_S:
                allowed = {r[1:] for r in rows if clock_s - SLACK_S <= r[0] <= clock_s + SLACK_S}
                before = [r for r in rows if r[0] < clock_s - SLACK_S]
                if before:
                    allowed.add(before[-1][1:])
                return any(score in (a, a[::-1]) for a in allowed)
        # No timeline for that clock (other sports, or recording started later): scores only grow, the stream is behind
        h, a = snap.home_score, snap.away_score
        return (score[0] <= h and score[1] <= a) or (score[0] <= a and score[1] <= h)

    # -- one OCR reading ---------------------------------------------------------------------------------------------
    def observe(self, tab_id: int, tile: int, texts: list[str], score: tuple | None, clocks: list[str]) -> list[str]:
        """Returns log messages (only when a lock appears, changes or is dropped)."""
        t = self.tiles.setdefault((tab_id, tile), _Tile())
        clock_s = parse_clock_s(clocks)
        popup = self.text.get(tab_id, "")
        reading = {}
        for gid, snap in self.games.items():
            if shows_game(texts, snap):
                reading[gid] = self.fits(snap, score, clock_s)
        if reading:
            t.window.append(reading)
        msgs: list[str] = []
        if t.lock is not None:
            msgs += self._follow_lock(tab_id, tile, t, reading)
        if t.lock is None or not t.lock.verified:
            msgs += self._try_lock(tab_id, tile, t, popup)
        return msgs

    def _follow_lock(self, tab_id, tile, t: _Tile, reading: dict) -> list[str]:
        gid = t.lock.game_id
        if gid in reading and reading[gid] is False:
            t.bad += 1
        elif reading.get(gid) is True:
            t.bad = 0
        if t.bad >= DROP_AFTER:
            msg = f"feed map tab {tab_id} tile {tile} dropped {t.lock.league} {t.lock.label}: score disagrees for {t.bad} readings"
            t.lock, t.bad = None, 0
            t.window.clear()
            return [msg]
        return []

    def _try_lock(self, tab_id, tile, t: _Tile, popup: str) -> list[str]:
        hits: dict[str, int] = {}
        oks: dict[str, int] = {}
        bads: dict[str, int] = {}
        for reading in t.window:
            for gid, fit in reading.items():
                hits[gid] = hits.get(gid, 0) + 1
                if fit is True:
                    oks[gid] = oks.get(gid, 0) + 1
                elif fit is False:
                    bads[gid] = bads.get(gid, 0) + 1
        by_popup = {gid for gid, s in self.games.items() if popup and s.state != "pre" and shows_game([popup], s)}
        eligible = []
        for gid, n in hits.items():
            need = MIN_HITS - (1 if gid in by_popup else 0)
            if n >= need and oks.get(gid, 0) >= MIN_OK and not bads.get(gid, 0):
                eligible.append(gid)
        best = None
        if len(eligible) == 1:
            best = (eligible[0], True)
        elif len(eligible) > 1:
            if not t.ambiguous:
                t.ambiguous = True
                return [f"feed map tab {tab_id} tile {tile} ambiguous: {', '.join(self.games[g].label for g in eligible)} all fit"]
            return []
        elif not hits and len(by_popup) == 1 and t.lock is None:
            best = (next(iter(by_popup)), False)     # only the popup's text supports it
        if best is None:
            return []
        t.ambiguous = False
        gid, verified = best
        snap = self.games[gid]
        via = ("ocr+text" if gid in by_popup else "ocr") if verified else "text"
        new = Mapping(gid, snap.league, snap.label, via, verified, hits.get(gid, 0), oks.get(gid, 0))
        old, t.lock, t.bad = t.lock, new, 0
        if old is not None and (old.game_id, old.verified) == (new.game_id, new.verified):
            return []
        detail = f"{new.ok}/{new.hits} readings fit the feed" if verified else "unverified, popup text only"
        return [f"feed map tab {tab_id} tile {tile} -> {new.league} {new.label} (via {new.via}, {detail})"]
