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
TNF_DIGIT_VS_LOGO = 1.2         # Prime TNF bug: each score is at least this much taller than the 'TNF' logo / replay banner text (measured 1.2-2.8)
TNF_DIGIT_CONF = 0.8            # ...and read with at least this confidence
TNF_DIGIT_PAIR = 0.7            # ...and the two scores are within this ratio of each other's height
TNF_LEFT_REACH = 1.2            # ...the left score ends at most this many of ITS heights left of the logo / banner (measured 0.4-1.0)
TNF_RIGHT_REACH = 1.5           # ...the right score starts at most this many of ITS heights right of the centre panel's right edge (measured 0.6-0.7)
TNF_PANEL_WIDTH = 6.0           # ...and at most this many of its heights right of the logo / banner (measured 1.0-5.3)
PANEL_DIGIT_VS_DOWN = 1.4       # centre-panel bug: each score is at least this much taller than the down text above the panel (measured 1.7-2.8)
PANEL_DIGIT_CONF = 0.6          # ...read with at least this confidence
PANEL_DIGIT_PAIR = 0.7          # ...and the two scores are within this ratio of each other's height (measured 0.78-1.0)
PANEL_REACH = 1.5               # ...each lies at most this many of ITS heights from the panel edge (measured 0.3-1.2)
PANEL_SIDE = 0.6                # ...the panel's lower row reaches at most this many down-text widths beside the down text
PANEL_QUARTER_RE = re.compile(r"^(?:[A-Za-z0-9]?(?:st|nd|rd|th|sr)|OT)$", re.I)   # "2ND", "4tH", "Ath", "Ist"
PANEL_CLOCK_RE = re.compile(r"^\d{1,2}[:.,;-]\d{2}$")   # a clock as OCR mangles it: "13.20", "7-50"
PANEL_CLOCK_IN_RE = re.compile(r"(?<!\d)\d{1,2}[:.,;-]\d{2}$")   # a clock at the end of a longer text ("3RD   10:14")
PANEL_DOWN_RE = re.compile(r"^(?:[A-Za-z0-9]?(?:st|nd|rd|th))\s?&\s?(?:\d{1,2}|goal)$", re.I)   # "1st & 10", "AtH & GOAL", "ZND & GOAL"
NBC_DIGIT_CONF = 0.8            # NBC SNF bug: scores and team codes are read with at least this confidence
NBC_CODE_PAIR = 0.6             # ...the two team codes are within this ratio of each other's height (measured 0.68-1.0: a crest touching the code makes its box taller)
NBC_DIGIT_REACH = 1.5           # ...each score lies at most this many code heights from its code (measured 1.2) and from the clock disc (measured 1.0)
NBC_CLOCK_RE = re.compile(r"^\d{1,2}[:.,;'\-]\d{2}$")
FOX_DIGIT_VS_CLOCK = 1.5        # Fox bug: each score is at least this much taller than the game-clock text of the chip between them (measured 1.8-2.6)
FOX_DIGIT_CONF = 0.8            # ...read with at least this confidence
FOX_DIGIT_PAIR = 0.7            # ...the two scores are within this ratio of each other's height (measured 0.85-1.0)
FOX_REACH = 1.5                 # ...each lies at most this many of ITS heights from the chip (measured 0.5-0.95)
FOX_CHIP_RE = re.compile(r"^[\d:;.,]{3,5}$")   # the clock chip's text as OCR gives it: "2:49", "7;20", "810"
FOX_PAIR_RATIO = 0.8            # ...chip-only form: the two scores are within this ratio of each other's height (measured 0.83-1.0)
FOX_GAP_MIN, FOX_GAP_MAX = 1.5, 3.5   # ...and the gap between them is this many score heights (measured 2.4-2.9)
FOX_ORDINAL_RE = re.compile(r"^(?:[1I]st|[2Z]nd|3rd|4th|[A4]tH|OT)$", re.I)
TNF_ORDINAL_RE = re.compile(r"^(?:[1I]ST|2ND|3RD|4TH|OT)$", re.I)
TNF_DOWN_RE = re.compile(r"^(?:[1I]st|[2Z]nd|3rd|4th)\s?&\s?(?:\d{1,2}|goal)$", re.I)


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


def read_prime_tnf(items: list[dict], clocks: list[str]):
    """Prime Video 'Thursday Night Football' bug: crests only (no team names), records under the crests, the centre panel (a 'TNF' logo or, in
    replays, a 'THURSDAY NIGHT FOOTBALL' banner; down text; quarter, game clock and play clock) between the two scores. Anchor: exactly one
    'TNF' logo or banner; the score is the ONE tall number just left of it and the ONE just right of the panel, on its row, with a down text
    or a quarter ordinal between them. A play clock, a record ('2-2') or a stat number is never tall enough; two candidates on a side give nothing."""
    marks = [it for it in items if (it["text"].strip() == "TNF" or it["text"].strip().upper().startswith("THURSDAY NIGHT")) and it.get("conf", 1.0) >= 0.8]
    if len(marks) != 1:
        return None
    mark = marks[0]
    cand = [it for it in items if NUMBER_RE.match(it["text"].strip()) and _h(it) >= TNF_DIGIT_VS_LOGO * _h(mark)
            and it.get("conf", 1.0) >= TNF_DIGIT_CONF and abs(_cy(it) - _cy(mark)) <= 0.8 * _h(it)]
    left = [it for it in cand if it["x1"] <= mark["x0"] + 0.2 * _h(it) and mark["x0"] - it["x1"] <= TNF_LEFT_REACH * _h(it)]
    if len(left) != 1:
        return None
    lft = left[0]
    right = []
    for it in cand:
        if it["x0"] < mark["x1"] or it["x0"] - mark["x1"] > TNF_PANEL_WIDTH * _h(it):
            continue
        between = [m for m in items if m is not it and m is not lft and lft["x1"] <= m["x0"] and m["x1"] <= it["x0"] + 0.2 * _h(it)
                   and abs(_cy(m) - _cy(mark)) <= 2.5 * _h(mark) + _h(it)]
        if (any(TNF_DOWN_RE.match(m["text"].strip()) or TNF_ORDINAL_RE.match(m["text"].strip()) for m in between)
                and it["x0"] - max(m["x1"] for m in between) <= TNF_RIGHT_REACH * _h(it)
                and min(_h(it), _h(lft)) >= TNF_DIGIT_PAIR * max(_h(it), _h(lft))):
            right.append(it)
    if len(right) == 1:
        return int(lft["text"]), int(right[0]["text"])
    return None


def _centre_panel_anchor(items: list[dict]):
    """(anchor box, the panel's items) of a centre-panel bug, or None. Preferred anchor: ONE down text ('1st & 10', '3RD & GOAL') with a quarter
    ordinal or a clock under it. When the down text was not read: ONE quarter ordinal with a clock right beside it and a two-digit play clock after that."""
    downs = [it for it in items if PANEL_DOWN_RE.match(it["text"].strip())]
    if len(downs) == 1:
        down = downs[0]
        dw, dh = down["x1"] - down["x0"], _h(down)
        lower = [it for it in items if it is not down and _h(it) <= 1.2 * dh and down["y1"] - 0.3 * dh <= _cy(it) <= down["y1"] + 3.0 * dh
                 and down["x0"] - PANEL_SIDE * dw <= it["x0"] and it["x1"] <= down["x1"] + PANEL_SIDE * dw]
        if any(PANEL_QUARTER_RE.match(it["text"].strip()) or PANEL_CLOCK_IN_RE.search(it["text"].strip()) for it in lower):
            return down, lower + [down]
        return None
    if downs:
        return None
    found = []
    for q in items:
        if not PANEL_QUARTER_RE.match(q["text"].strip()) or len(q["text"].strip()) < 2:
            continue
        row = [it for it in items if it is not q and abs(_cy(it) - _cy(q)) <= 0.6 * _h(q) and q["x1"] - 0.2 * _h(q) <= it["x0"] <= q["x1"] + 8 * _h(q)]
        clock = [it for it in row if PANEL_CLOCK_RE.match(it["text"].strip()) and 0 <= it["x0"] - q["x1"] <= 1.5 * _h(q)]
        play = [it for it in row if re.match(r"^\d{1,2}$", it["text"].strip()) and _h(it) <= 1.2 * _h(q) and clock and 0 <= it["x0"] - clock[0]["x1"] <= 1.5 * _h(q)]
        if len(clock) == 1 and len(play) == 1:
            found.append((q, [q, clock[0], play[0]]))
    return found[0] if len(found) == 1 else None


def read_centre_panel(items: list[dict], clocks: list[str]):
    """Centre-panel NFL bug (CBS, ESPN Monday Night, NFL Network: crest + score at each end, the panel between them with the down text on top
    and quarter | game clock | play clock below). The score is the ONE tall number just left of the panel and the ONE just right of it, on the
    panel's rows. The ticker of other games in a corner (small, stacked, nowhere near the panel), a record ('3-1'), a play clock or a
    yard-line number never qualify; two candidates on one side, or one side missing, give None."""
    anchor = _centre_panel_anchor(items)
    if anchor is None:
        return None
    ref, panel = anchor
    dh = _h(ref)
    panel_left, panel_right = min(it["x0"] for it in panel), max(it["x1"] for it in panel)
    cand = [it for it in items if it not in panel and NUMBER_RE.match(it["text"].strip()) and _h(it) >= PANEL_DIGIT_VS_DOWN * dh
            and it.get("conf", 1.0) >= PANEL_DIGIT_CONF and abs(_cy(it) - _cy(ref)) <= 0.6 * _h(it) + 0.5 * dh]
    left = [it for it in cand if it["x1"] <= panel_left + 0.2 * _h(it) and panel_left - it["x1"] <= PANEL_REACH * _h(it)]
    right = [it for it in cand if it["x0"] >= panel_right - 0.2 * _h(it) and it["x0"] - panel_right <= PANEL_REACH * _h(it)]
    if len(left) == 1 and len(right) == 1 and min(_h(left[0]), _h(right[0])) >= PANEL_DIGIT_PAIR * max(_h(left[0]), _h(right[0])):
        return int(left[0]["text"]), int(right[0]["text"])
    return None


def read_nbc_snf(items: list[dict], clocks: list[str]):
    """NBC Sunday Night Football bug: 'BAL 9 (clock disc) 17 ATL': a team code at each end, the score right after the left code and right before
    the right code, the round clock disc (game clock, quarter, play clock) in the middle. Anchor: exactly two team codes on one row with a
    clock between them; the score is the ONE number between the left code and the clock and the ONE between the clock and the right code.
    Yard-line numbers on the field, the play clock and the records ('3-1') are never between those codes at that height."""
    codes = [it for it in items if CODE_RE.match(it["text"].strip()) and it.get("conf", 1.0) >= NBC_DIGIT_CONF]
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            left, right = (a, b) if a["x0"] <= b["x0"] else (b, a)
            h = max(_h(left), _h(right))
            if (abs(_cy(left) - _cy(right)) > 0.4 * h or min(_h(left), _h(right)) < NBC_CODE_PAIR * h
                    or left["text"].strip() == right["text"].strip() or right["x0"] - left["x1"] < 6 * h):
                continue
            row = [it for it in items if left["x1"] <= it["x0"] and it["x1"] <= right["x0"] and abs(_cy(it) - _cy(left)) <= 0.8 * h]
            clock = [it for it in row if NBC_CLOCK_RE.match(it["text"].strip())]
            if len(clock) != 1:
                continue
            c = clock[0]
            lo = [it for it in row if NUMBER_RE.match(it["text"].strip()) and it["x1"] <= c["x0"] + 0.2 * h and it["x0"] - left["x1"] <= NBC_DIGIT_REACH * h
                  and it.get("conf", 1.0) >= NBC_DIGIT_CONF and _h(it) >= 0.9 * min(_h(left), _h(right))]
            hi = [it for it in row if NUMBER_RE.match(it["text"].strip()) and it["x0"] >= c["x1"] - 0.2 * h and right["x0"] - it["x1"] <= NBC_DIGIT_REACH * h
                  and it.get("conf", 1.0) >= NBC_DIGIT_CONF and _h(it) >= 0.9 * min(_h(left), _h(right))]
            if len(lo) == 1 and len(hi) == 1:
                return int(lo[0]["text"]), int(hi[0]["text"])
            return None
    return None


def _read_fox_chip_only(items: list[dict]):
    """Fox bug whose quarter ordinal was not read (or was glued to the clock into one tall item): two numbers of the same height on one row with
    exactly ONE clock-looking item between them, nothing else tall, and a team record ('3-1') under the bug. Exactly one such pair, else None."""
    nums = [it for it in items if NUMBER_RE.match(it["text"].strip()) and it.get("conf", 1.0) >= FOX_DIGIT_CONF and _h(it) >= 60]
    found = []
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            l, r = (a, b) if a["x0"] <= b["x0"] else (b, a)
            h = max(_h(l), _h(r))
            gap = r["x0"] - l["x1"]
            if (abs(_cy(l) - _cy(r)) > 0.2 * h or min(_h(l), _h(r)) < FOX_PAIR_RATIO * h or not FOX_GAP_MIN * h <= gap <= FOX_GAP_MAX * h):
                continue
            between = [it for it in items if l["x1"] <= it["x0"] and it["x1"] <= r["x0"] and abs(_cy(it) - _cy(l)) <= 0.6 * h]
            chips = [it for it in between if FOX_CHIP_RE.match(it["text"].strip()) and _h(it) <= 1.2 * h]
            if (len(chips) == 1 and len(between) == 1 and abs(_cy(chips[0]) - _cy(l)) <= 0.4 * h
                    and any(RECORD_RE.match(it["text"].strip()) and l["x0"] - 0.3 * h <= it["x0"] <= r["x1"] + 0.3 * h and _cy(l) <= it["y0"] and it["y1"] <= l["y1"] + 0.6 * h
                            for it in items)):
                found.append((l, r))
    if len(found) == 1:
        return int(found[0][0]["text"]), int(found[0][1]["text"])
    return None


def read_fox_nfl(items: list[dict], clocks: list[str]):
    """Fox NFL bug: crest only, big white scores that float on the field, the small dark clock chip between them with the quarter ordinal glued
    UNDER the game clock ('2:49' over '3RD'), records under the crests. Anchor: exactly one quarter ordinal with a short text right above it
    (the clock: its own text is often mangled, so only its place counts); the score is the ONE number much taller than the chip text just left
    of the chip and the ONE just right of it, centred on the chip's rows. Field markings and stat cards are not centred on the chip."""
    chip_only = _read_fox_chip_only(items)
    ords = [it for it in items if FOX_ORDINAL_RE.match(it["text"].strip()) and len(it["text"].strip()) >= 3]
    chips = []
    for o in ords:
        above = [it for it in items if it is not o and not NUMBER_RE.match(it["text"].strip()) or (it is not o and PANEL_CLOCK_RE.match(it["text"].strip()))]
        above = [it for it in above if it is not o and it["y1"] <= o["y0"] + 0.3 * _h(o) and o["y0"] - it["y1"] <= 0.6 * _h(it)
                 and min(it["x1"], o["x1"]) - max(it["x0"], o["x0"]) >= 0.5 * (o["x1"] - o["x0"]) and 2 <= len(it["text"].strip()) <= 6
                 and any(ch.isdigit() or ch == ":" for ch in it["text"])]
        if len(above) == 1:
            chips.append((above[0], o))
    if len(chips) != 1:
        return chip_only
    clock, o = chips[0]
    h = _h(clock)
    left_edge, right_edge = min(clock["x0"], o["x0"]), max(clock["x1"], o["x1"])
    mid = (clock["y0"] + o["y1"]) / 2
    cand = [it for it in items if it is not clock and it is not o and NUMBER_RE.match(it["text"].strip()) and _h(it) >= FOX_DIGIT_VS_CLOCK * h
            and it.get("conf", 1.0) >= FOX_DIGIT_CONF and abs(_cy(it) - mid) <= 0.5 * _h(it)]
    left = [it for it in cand if it["x1"] <= left_edge + 0.2 * _h(it) and left_edge - it["x1"] <= FOX_REACH * _h(it)]
    right = [it for it in cand if it["x0"] >= right_edge - 0.2 * _h(it) and it["x0"] - right_edge <= FOX_REACH * _h(it)]
    if len(left) == 1 and len(right) == 1 and min(_h(left[0]), _h(right[0])) >= FOX_DIGIT_PAIR * max(_h(left[0]), _h(right[0])):
        return int(left[0]["text"]), int(right[0]["text"])
    return chip_only


TEMPLATES: tuple[Template, ...] = (
    Template("american football", "bein", "beIN SPORTS NFL bug", read_bein_nfl),
    Template("american football", "espn", "ESPN / Disney+ college bug", read_espn_bug),
    Template("american football", "prime", "Prime Video TNF bug", read_prime_tnf),
    Template("american football", "fox", "Fox NFL bug", read_fox_nfl),
    Template("american football", "nbc", "NBC Sunday Night Football bug", read_nbc_snf),
    Template("american football", "centre-panel", "centre-panel bug (CBS, ESPN MNF, NFL Network)", read_centre_panel),
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
