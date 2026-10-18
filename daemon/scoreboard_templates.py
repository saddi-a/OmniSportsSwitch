"""Scoreboard TEMPLATES: the look of ONE network's or league's scorebug, read from its known structure.

A template is visual (where the team codes, scores, records, down text and clocks sit for that broadcaster); the rules of a sport
are PROFILES (`scoreboard_profiles.py`). Each entry is keyed by (sport, network or league). `find_score_geo` tries the templates
first; a template that does not recognise the board, or recognises it but cannot read a safe score, returns None and the sport's
profile reads the board as before: a template never lowers what the generic reader gets, and never guesses (None beats a wrong score).

Only layouts with real data (committed fixtures or the git-ignored benchmark frames) get an entry: see docs/scoreboard-profiles.md.
Boxes are `{text, x0, y0, x1, y1}`; a reader takes (items, clocks) and returns (left, right) or None.
"""
import re
from dataclasses import dataclass
from typing import Callable

RECORD_RE = re.compile(r"^\d{1,2}\s?-\s?\d{1,2}(?:-\d)?$")      # a team record "3-1", "2-2"
CODE_RE = re.compile(r"^[A-Z]{2,4}$")
CODE_SCORE_SEP = r"[|!Il/\]\[}{)(E]"
CODE_SCORE_RE = re.compile(rf"^([A-Z]{{2,4}})\s?{CODE_SCORE_SEP}\s?(\d{{1,3}})$")   # "PHI| 14", "JAX] 10" (the separator is OCR-mangled)
NUMBER_RE = re.compile(r"^\d{1,3}$")
ESPN_DIGIT_VS_RECORD = 1.5      # ESPN bug: the score digits are at least this much taller than the records beside them (measured 3.0-3.6)
ESPN_DIGIT_CONF = 0.8           # ...and each is read with at least this confidence (a template is stricter than the generic reader: an early weak read must not stop the rescue passes)
ESPN_RECORD_REACH = 1.2         # ...and each lies within this many digit heights of ITS record (a middle number such as "5:11" is no score)
ESPN_DIGIT_PAIR = 0.75          # ...and the two digits are within this ratio of each other's height
BEIN_NUMBER_REACH = 1.5         # beIN bug: the second score starts at most this many lines of text after its code


def _h(it):
    return it["y1"] - it["y0"]


def _cy(it):
    return (it["y0"] + it["y1"]) / 2


@dataclass(frozen=True)
class Template:
    sport: str            # key of `scoreboard_profiles.PROFILES`
    network: str          # network or league, lowercase ("bein", "espn")
    name: str
    read: Callable[[list[dict], list[str]], "tuple[int, int] | None"]


def read_bein_nfl(items: list[dict], clocks: list[str]):
    """beIN SPORTS NFL bug: 'rec rec PHI| 14  3-1 JAX 7  3RD 9:39 37'. The left team's code and score are ONE item (separator read as | / ] ...),
    the right team's code is a second item and its score the number right after it; or both are code+score items ('JAX] 10')."""
    live = [it for it in items if it["text"].strip() not in clocks]
    first = [it for it in live if CODE_SCORE_RE.match(it["text"].strip())]
    if len(first) == 0 or any(it["text"].strip().startswith("LIVE") for it in first):
        return None
    a = min(first, key=lambda it: it["x0"])
    ca = CODE_SCORE_RE.match(a["text"].strip())
    row = [it for it in live if it is not a and it["x0"] >= a["x1"] and abs(_cy(it) - _cy(a)) <= 0.6 * _h(a)]
    for b in sorted(row, key=lambda it: it["x0"]):
        t = b["text"].strip()
        cb = CODE_SCORE_RE.match(t)
        if cb and cb.group(1) != ca.group(1):
            return int(ca.group(2)), int(cb.group(2))
        if CODE_RE.match(t) and t != ca.group(1):
            n = [it for it in row if NUMBER_RE.match(it["text"].strip()) and it["x0"] >= b["x1"] - 0.5 * _h(b)
                 and it["x0"] - b["x1"] <= BEIN_NUMBER_REACH * _h(b) and it.get("conf", 1.0) >= 0.8]
            if len(n) == 1:
                return int(ca.group(2)), int(n[0]["text"])
            return None
    return None


def read_espn_bug(items: list[dict], clocks: list[str]):
    """ESPN / Disney+ college bug: a team record at each END ('3-1' ... '2-2', on the lower row) and the two score digits between them, much taller
    than the records (Kentucky-South Carolina, BYU-TCU, McNeese-LSU). Exactly two such digits are needed; a digit hidden behind the player's
    controls leaves one, and then nothing is read."""
    records = [it for it in items if RECORD_RE.match(it["text"].strip())]
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            left, right = (a, b) if a["x0"] <= b["x0"] else (b, a)
            if abs(_cy(left) - _cy(right)) > 0.5 * max(_h(left), _h(right)) or right["x0"] - left["x1"] < 6 * _h(left):
                continue
            rec_h = max(_h(left), _h(right))
            digits = [it for it in items if NUMBER_RE.match(it["text"].strip()) and left["x1"] <= it["x0"] and it["x1"] <= right["x0"]
                      and _h(it) >= ESPN_DIGIT_VS_RECORD * rec_h and abs(_cy(it) - _cy(left)) <= 3.0 * _h(it)
                      and it.get("conf", 1.0) >= ESPN_DIGIT_CONF]
            if len(digits) == 2:
                d1, d2 = sorted(digits, key=lambda it: it["x0"])
                if (d1["x0"] - left["x1"] > ESPN_RECORD_REACH * _h(d1) or right["x0"] - d2["x1"] > ESPN_RECORD_REACH * _h(d2)):
                    continue
                if abs(_cy(d1) - _cy(d2)) <= 0.5 * max(_h(d1), _h(d2)) and min(_h(d1), _h(d2)) >= ESPN_DIGIT_PAIR * max(_h(d1), _h(d2)):
                    return int(d1["text"]), int(d2["text"])
    return None


TEMPLATES: tuple[Template, ...] = (
    Template("american football", "bein", "beIN SPORTS NFL bug", read_bein_nfl),
    Template("american football", "espn", "ESPN / Disney+ college bug", read_espn_bug),
)


def read_template(items: list[dict], clocks: list[str], sport: str | None = None) -> "tuple[Template, tuple[int, int]] | None":
    """The first template (of this sport, or of any sport when it is None) that reads a score from the board, with that score; or None."""
    for t in TEMPLATES:
        if sport is not None and t.sport != sport:
            continue
        score = t.read(items, clocks)
        if score is not None:
            return t, score
    return None
