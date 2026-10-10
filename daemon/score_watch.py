"""Spots a score change (goal, touchdown, basket) from the numbers OCR reads off a scorebug.

OCR output is noisy (clocks, logos, stat graphics, down-and-distance), so:
  * only a *pair* of numbers counts as a score: written as "2-0", next to each other, or with one
    text item (a team name) between them. Numbers scattered across a graphic are ignored.
  * a change must be seen on two consecutive readings, the total must rise by 1 to MAX_RISE, and
    neither number may go down.
Anything else just resets the baseline.
"""
import re

SCORE_PAIR_RE = re.compile(r"^(\d{1,3})\s?[-–:]\s?(\d{1,3})$")  # "2-0"
NUMBER_RE = re.compile(r"^\d{1,3}$")
CLOCK_RE = re.compile(r"^\d{1,3}\s?[:.,]\s?\d{2}$")
MAX_RISE = 10


def find_score(texts: list[str], clocks: list[str]) -> tuple[int, int] | None:
    """The first (left, right) score pair found in the OCR text items, or None."""
    skip = set(clocks)
    items = [t.strip() for t in texts if t.strip() and t.strip() not in skip and not CLOCK_RE.match(t.strip())]
    for i, item in enumerate(items):
        pair = SCORE_PAIR_RE.match(item)
        if pair:
            return int(pair.group(1)), int(pair.group(2))
        if not NUMBER_RE.match(item):
            continue
        for j in (i + 1, i + 2):  # directly next, or with one team name in between
            if j < len(items) and NUMBER_RE.match(items[j]) and (j == i + 1 or not NUMBER_RE.match(items[i + 1])):
                return int(item), int(items[j])
    return None


class ScoreWatcher:
    """Follows one video area. `update` returns True on the reading where a score change is confirmed."""

    def __init__(self):
        self._baseline: tuple[int, int] | None = None
        self._candidate: tuple[int, int] | None = None

    def update(self, texts: list[str], clocks: list[str]) -> bool:
        score = find_score(texts, clocks)
        if score is None:
            return False
        if score != self._candidate:
            self._candidate = score  # needs to be seen twice in a row before it is trusted
            return False
        if self._baseline is None or score == self._baseline:
            self._baseline = score
            return False

        old, new = self._baseline, score
        self._baseline = new
        return all(n >= o for o, n in zip(old, new)) and 1 <= sum(new) - sum(old) <= MAX_RISE
