"""Which game a video shows, told from the two team codes of its scorebug (multiplex channels: NFL RedZone, a Premier League multiplex).

Pure logic. `team_pair` reads the codes of one OCR reading; `GameIdentity` turns the readings of one tile into a stable game key and
reports the reading where a DIFFERENT pair has been read `readings` times in a row. No pair in a reading (a studio shot, no scorebug,
a ticker with many codes, a lone misread) changes nothing: the old game is kept."""
import re

from daemon.game_state import NOT_TEAM_CODES, TEAM_CODE_RE
from daemon.team_names import CLUBS, fold


def team_pair(texts: list[str]) -> frozenset | None:
    """The two team codes of the scorebug in one reading, or None unless there are exactly two different ones (a results ticker or a
    standings table shows three or more, a studio shot none)."""
    codes = {t for t in (re.sub(r"[^\w\s-]", "", x).strip() for x in texts) if TEAM_CODE_RE.fullmatch(t) and t not in NOT_TEAM_CODES}
    return frozenset(codes) if len(codes) == 2 else None


def key_text(pair: frozenset | None) -> str | None:
    """'AAA-BBB' (sorted), for logs and the dataset."""
    return "-".join(sorted(pair)) if pair else None


class GameIdentity:
    def __init__(self, readings: int = 3):
        self.readings = max(int(readings), 1)
        self.key: frozenset | None = None            # the stable pair: the game this tile shows
        self._candidate: frozenset | None = None     # a different pair being read
        self._count = 0

    def update(self, pair: frozenset | None) -> tuple[frozenset | None, frozenset] | None:
        """One reading. Returns (old key, new key) on the reading where a new game is confirmed; the first game ever read is no change
        (old key None is reported only when `key` was set before, so callers never reset for it)."""
        if pair is None:
            return None
        if pair == self.key:
            self._candidate, self._count = None, 0
            return None
        if pair != self._candidate:
            self._candidate, self._count = pair, 0
        self._count += 1
        if self._count < self.readings:
            return None
        old, self.key, self._candidate, self._count = self.key, pair, None, 0
        return (old, pair) if old is not None else None


def known_pair(pair: frozenset | None) -> frozenset | None:
    """The pair only if BOTH codes are team codes of the team tables (ESPN abbreviation or active alias), else None. Used on multiplex
    tabs: a calendar or standings graphic with two code-like words (CALL-EPIG) must not count as a new game; the old game is kept."""
    return pair if pair is not None and all(code in known_codes() for code in pair) else None


def known_codes() -> frozenset:
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = frozenset(fold(c.abbr) for c in CLUBS) | frozenset(a for c in CLUBS for a in c.aliases)
    return _KNOWN


_KNOWN: frozenset | None = None
