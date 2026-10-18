"""Score digits read slot by slot: the rescue for a scorebug whose normal reading found the team names but not both scores.

A broadcast scorebug puts the left score right after the left team name and the right score right before the right team name. When the
normal OCR pass misses a digit (a pale digit on a light plate, an italic condensed font whose 3 is read as an 8), each of the two slots
is read again with a recognition-only reader (no text detection) over a few sliding windows, and the confident readings vote.
Each slot's windows stay on its own side of the midpoint between the names, so the left window never reads the right score.

Pure logic: the image reader is a function `rec(crop) -> (text, confidence)` given by the caller (see `daemon/rapid_reader.py`).
"""
import re
from collections import Counter

NUMBER_RE = re.compile(r"^\d{1,3}$")
MIN_CONFIDENCE = 0.8                  # a window's reading counts only when the recogniser is this sure (ScoreSight's 80-90% rule)
OFFSETS = (0.0, 0.4, 0.8, 1.2, 1.6)   # window starts, in name heights, moving away from the name the slot belongs to
WIDTHS = (0.8, 1.2, 1.6, 2.0, 2.4)        # window widths, in name heights (the narrow ones read one digit beside a crest)
SLOT_REACH = 0.2                      # the first window starts this far (name heights) inside the name's edge
MID_SLACK = 0.3                       # a window may cross the midpoint by this much (name heights)
BAND = 1.0                            # a window covers this many name heights above and below the names' centre line


def windows(left: dict, right: dict, side: str) -> list[tuple[float, float]]:
    """The (x0, x1) windows to read for one slot. `left` / `right` are the team-name items (x0, x1, y0, y1)."""
    h = max(left["y1"] - left["y0"], right["y1"] - right["y0"], 1)
    mid = (left["x1"] + right["x0"]) / 2
    found = []
    for offset in OFFSETS:
        for width in WIDTHS:
            if side == "left":
                x0 = left["x1"] - SLOT_REACH * h + offset * h
                x1 = x0 + width * h
                if x1 > mid + MID_SLACK * h:
                    continue
            else:
                x1 = right["x0"] + SLOT_REACH * h - offset * h
                x0 = x1 - width * h
                if x0 < mid - MID_SLACK * h:
                    continue
            found.append((x0, x1))
    return found


def read_slot(image, left: dict, right: dict, side: str, rec, min_confidence: float = MIN_CONFIDENCE) -> Counter:
    """Votes (text -> summed confidence) for one slot. `image` is an RGB array, `rec(crop)` returns (text, confidence)."""
    h = max(left["y1"] - left["y0"], right["y1"] - right["y0"], 1)
    ymid = ((left["y0"] + left["y1"]) + (right["y0"] + right["y1"])) / 4
    height, width = image.shape[:2]
    y0, y1 = int(max(0, ymid - BAND * h)), int(min(height, ymid + BAND * h))
    votes: Counter = Counter()
    for x0, x1 in windows(left, right, side):
        xa, xb = int(max(0, x0)), int(min(width, x1))
        if xb - xa < 8 or y1 - y0 < 8:
            continue
        text, confidence = rec(image[y0:y1, xa:xb])
        if text and NUMBER_RE.match(text.strip()) and confidence >= min_confidence:
            votes[text.strip()] += confidence
    return votes


def read_slots(image, left: dict, right: dict, rec, min_confidence: float = MIN_CONFIDENCE) -> tuple[int, int] | None:
    """(left score, right score) from the two slots, or None unless both slots have a confident, different-looking reading."""
    if right["x0"] - left["x1"] < 2 * max(left["y1"] - left["y0"], right["y1"] - right["y0"], 1):
        return None   # no room between the names for two scores
    first = read_slot(image, left, right, "left", rec, min_confidence)
    second = read_slot(image, left, right, "right", rec, min_confidence)
    if not first or not second:
        return None
    return int(first.most_common(1)[0][0]), int(second.most_common(1)[0][0])


def stacked_windows(name: dict) -> list[tuple[float, float]]:
    """The (x0, x1) windows right of one team name of a STACKED scorebug (two names one above the other, each score beside its own name)."""
    h = max(name["y1"] - name["y0"], 1)
    return [(name["x1"] - SLOT_REACH * h + offset * h, name["x1"] - SLOT_REACH * h + offset * h + width * h) for offset in OFFSETS for width in WIDTHS]


def read_stacked(image, top: dict, bottom: dict, rec, min_confidence: float = MIN_CONFIDENCE, known: tuple = (None, None)) -> tuple[int, int] | None:
    """(top score, bottom score) of a stacked scorebug, or None unless both slots have a confident number. Each slot is read on its own name's
    row only (the band never reaches the other row). `known` = a score the readers already found for the top / bottom name (None = read the slot)."""
    h = max(top["y1"] - top["y0"], bottom["y1"] - bottom["y0"], 1)
    half = min(BAND * h, 0.5 * abs((bottom["y0"] + bottom["y1"]) - (top["y0"] + top["y1"])) / 2)
    height, width = image.shape[:2]
    scores = []
    for name, found in zip((top, bottom), known):
        if found is not None:
            scores.append(int(found))
            continue
        cy = (name["y0"] + name["y1"]) / 2
        y0, y1 = int(max(0, cy - half)), int(min(height, cy + half))
        votes: Counter = Counter()
        for x0, x1 in stacked_windows(name):
            xa, xb = int(max(0, x0)), int(min(width, x1))
            if xb - xa < 8 or y1 - y0 < 8:
                continue
            text, confidence = rec(image[y0:y1, xa:xb])
            if text and NUMBER_RE.match(text.strip()) and confidence >= min_confidence:
                votes[text.strip()] += confidence
        if not votes:
            return None
        scores.append(int(votes.most_common(1)[0][0]))
    return scores[0], scores[1]
