"""The one game-clock parser of the daemon: '12:21' -> 741.0 seconds, '0:07.5' -> 7.5, anything else -> None.

Four copies of this used to disagree on odd input (`dataset_builder.parse_clock`, `feed.clock_seconds`, `feed_compare.parse_clock`,
`score_watch.clock_seconds`): see `daemon/tests/test_clock.py` for every case. The rules, chosen as the safest of the four:
* minutes: 1 to 3 digits and at most `MAX_MINUTES` (no game clock reads past 130 minutes; a stats-table clock like `150:00` is not a time);
* seconds: exactly two digits and at most 59 (`12:75` is a misread of `12:15`, `9:5` is cut text), with an optional decimal fraction
  (ESPN shows `0:07.5` in the last minute);
* surrounding blanks are ignored; anything that is not text gives None (never an exception).
"""
import re

MAX_MINUTES = 130
_CLOCK_RE = re.compile(r"(\d{1,3}):([0-5]\d)(\.\d+)?")


def clock_seconds(text) -> float | None:
    if not isinstance(text, str):
        return None
    m = _CLOCK_RE.fullmatch(text.strip())
    if not m or int(m.group(1)) > MAX_MINUTES:
        return None
    return int(m.group(1)) * 60 + float(m.group(2) + (m.group(3) or ""))


def first_clock_seconds(clocks) -> float | None:
    """The first of several clock readings that parses (a reading may hold junk next to the real clock)."""
    for c in clocks:
        s = clock_seconds(c)
        if s is not None:
            return s
    return None
