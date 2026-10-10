"""Spots a score change (goal, touchdown, basket) from the numbers OCR reads off a scorebug.

OCR output is noisy: clocks misread with a dash ("31-56"), channel logos ("1+3" read as "13"), stat graphics,
down-and-distance. So:
  * a score is found by anchoring to team names ("NAP 2-0 FRO", "PSG 1 [noise] LMFC 1"), with "a-b" pairs
    only accepted for small values (a misread clock like "31-56" is not a score), or as two numbers next
    to each other ("INDIANA 20 17 NEBRASKA");
  * each side's score is the value most of the last few readings agree on (a vote), so one or two bad
    frames cannot flip it;
  * a goal is a stable change in which at least one side rose by 1 to MAX_RISE. The other side may have
    changed because of noise and is not checked;
  * if the game clock jumps by more than the elapsed time allows (a different game moved into this video
    slot, or a rewind), the baseline is thrown away, so numbers of two different games are never compared.
"""
import re
from collections import Counter, deque

SCORE_PAIR_RE = re.compile(r"^(\d{1,3})\s?[-–:]\s?(\d{1,3})$")  # "2-0"
NUMBER_RE = re.compile(r"^\d{1,3}$")
CLOCK_RE = re.compile(r"^\d{1,3}\s?[:.,]\s?\d{2}$")
LOGO_RE = re.compile(r"^[A-Za-z]{0,2}\d\s?\+\s?\d$")  # channel logos such as "L1+3"
TEAM_RE = re.compile(r"^[^\W\d_]{2,}$")                 # letters only, at least two ("PSG", "LMFC")
MAX_PAIR_VALUE = 9   # "a-b" tokens above this are more likely a misread clock than a score
MAX_RISE = 10
CLOCK_SLACK_S = 20   # allowed clock movement beyond twice the elapsed time before it counts as a jump
CLOCK_TEXT_RE = re.compile(r"^(\d{1,3}):(\d{2})$")
HISTORY = 5          # readings that vote
NEEDED = 3           # votes a value needs to count


def _items(texts: list[str], clocks: list[str]) -> list[str]:
    skip = set(clocks)
    return [t.strip() for t in texts
            if t.strip() and t.strip() not in skip and not CLOCK_RE.match(t.strip()) and not LOGO_RE.match(t.strip())]


def _small_pair(item: str) -> tuple[int, int] | None:
    m = SCORE_PAIR_RE.match(item)
    if m and max(int(m.group(1)), int(m.group(2))) <= MAX_PAIR_VALUE:
        return int(m.group(1)), int(m.group(2))
    return None


def find_score(texts: list[str], clocks: list[str]) -> tuple[int, int] | None:
    """The (left, right) score found in the OCR text items, or None."""
    items = _items(texts, clocks)
    is_team = [bool(TEAM_RE.match(t)) for t in items]

    # 1. "TEAM a-b TEAM"
    for i, item in enumerate(items):
        pair = _small_pair(item)
        if pair and 0 < i < len(items) - 1 and is_team[i - 1] and is_team[i + 1]:
            return pair

    # 2. TEAM1, number, (up to 2 noise items), TEAM2, (up to 1 noise item), number
    for a in range(len(items)):
        if not is_team[a]:
            continue
        n1 = next((k for k in range(a + 1, min(a + 3, len(items))) if NUMBER_RE.match(items[k])), None)
        if n1 is None:
            continue
        for b in range(n1 + 1, min(n1 + 4, len(items))):
            if not is_team[b]:
                continue
            n2 = next((k for k in range(b + 1, min(b + 3, len(items))) if NUMBER_RE.match(items[k])), None)
            if n2 is not None:
                return int(items[n1]), int(items[n2])

    # 3. any small "a-b" token
    for item in items:
        pair = _small_pair(item)
        if pair:
            return pair

    # 4. two numbers next to each other, or with up to two text items between them
    for i, item in enumerate(items):
        if not NUMBER_RE.match(item):
            continue
        for j in (i + 1, i + 2, i + 3):
            if j >= len(items) or not NUMBER_RE.match(items[j]):
                continue
            if all(not NUMBER_RE.match(x) for x in items[i + 1 : j]):
                return int(item), int(items[j])
    return None


def clock_seconds(text: str) -> int | None:
    m = CLOCK_TEXT_RE.match(text)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _vote(values) -> int | None:
    value, count = Counter(values).most_common(1)[0]
    return value if count >= NEEDED else None


class ScoreWatcher:
    """Follows one video area. `update` returns True on the reading where a score change becomes stable."""

    def __init__(self):
        self._history: deque[tuple[int, int]] = deque(maxlen=HISTORY)
        self._stable: tuple[int, int] | None = None
        self._clock: tuple[int, float] | None = None   # last plausible game clock and when it was read
        self._alt: tuple[int, float] | None = None     # an implausible clock seen once, waiting for confirmation

    def _reset(self) -> None:
        self._history.clear()
        self._stable = None

    def _clock_jumped(self, clocks: list[str], now: float) -> bool:
        seconds = [s for s in map(clock_seconds, clocks) if s is not None]
        if not seconds:
            return False
        if self._clock is None:
            self._clock = (seconds[0], now)
            return False
        previous, then = self._clock
        plausible = [s for s in seconds if abs(s - previous) <= abs(now - then) * 2 + CLOCK_SLACK_S]
        if plausible:
            self._clock, self._alt = (plausible[0], now), None
            return False
        # Only a clock seen twice in a row, continuing from itself, counts (a lone "20:45" kickoff text does not).
        if self._alt is not None:
            alt, alt_at = self._alt
            near = [s for s in seconds if abs(s - alt) <= abs(now - alt_at) * 2 + CLOCK_SLACK_S]
            if near:
                self._clock, self._alt = (near[0], now), None
                return True
        self._alt = (seconds[0], now)
        return False

    def update(self, texts: list[str], clocks: list[str], now: float | None = None) -> bool:
        if now is not None and self._clock_jumped(clocks, now):
            self._reset()
        score = find_score(texts, clocks)
        if score is None:
            return False
        self._history.append(score)
        left, right = _vote(p[0] for p in self._history), _vote(p[1] for p in self._history)
        if left is None or right is None:
            return False
        current = (left, right)
        if self._stable is None:
            self._stable = current
            return False
        if current == self._stable:
            return False
        old, self._stable = self._stable, current
        return any(1 <= n - o <= MAX_RISE for o, n in zip(old, current))
