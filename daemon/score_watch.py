"""Spots a score change (goal, touchdown, basket) from the numbers OCR reads off a scorebug.

OCR output is noisy (clocks, logos, down-and-distance), so a change only counts when:
  * the same numbers were read on two consecutive readings (stable), and
  * the new set of numbers has the same length as before, every number stayed the same or went up,
    and the total rose by 1 to MAX_RISE (typical scoring plays).
Anything else (numbers appearing or disappearing, going down) just resets the baseline.
"""
import re

SCORE_PAIR_RE = re.compile(r"^(\d{1,3})\s?[-–:]\s?(\d{1,3})$")  # "2-0"
NUMBER_RE = re.compile(r"^\d{1,3}$")
MAX_RISE = 10


def score_numbers(texts: list[str], clocks: list[str]) -> tuple[int, ...]:
    """Small whole numbers (and 'a-b' pairs split in two) found in the OCR text, in reading order."""
    skip = set(clocks)
    numbers: list[int] = []
    for text in texts:
        t = text.strip()
        if t in skip or re.fullmatch(r"\d{1,3}\s?[:.,]\s?\d{2}", t):
            continue  # a game clock, not a score
        pair = SCORE_PAIR_RE.match(t)
        if pair:
            numbers += [int(pair.group(1)), int(pair.group(2))]
        elif NUMBER_RE.match(t):
            numbers.append(int(t))
    return tuple(numbers)


class ScoreWatcher:
    """Follows one video area. `update` returns True on the reading where a score change is confirmed."""

    def __init__(self):
        self._baseline: tuple[int, ...] | None = None
        self._candidate: tuple[int, ...] | None = None

    def update(self, texts: list[str], clocks: list[str]) -> bool:
        numbers = score_numbers(texts, clocks)
        if len(numbers) < 2:  # a score has a number for each side; a lone number is noise (jersey, ad, down and distance)
            return False
        if numbers != self._candidate:
            self._candidate = numbers  # needs to be seen twice in a row before it is trusted
            return False
        if self._baseline is None or numbers == self._baseline:
            self._baseline = numbers
            return False

        old, new = self._baseline, numbers
        self._baseline = new
        return (
            len(old) == len(new)
            and all(n >= o for o, n in zip(old, new))
            and 1 <= sum(new) - sum(old) <= MAX_RISE
        )
