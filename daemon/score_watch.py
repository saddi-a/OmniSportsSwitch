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

from daemon.scoring import Config
from daemon.clock import clock_seconds as _shared_clock_seconds

SCORE_PAIR_RE = re.compile(r"^(\d{1,3})\s?[-–:]\s?(\d{1,3})$")  # "2-0"
NUMBER_RE = re.compile(r"^\d{1,3}$")
CLOCK_RE = re.compile(r"^\d{1,3}\s?[:.,]\s?\d{2}$")
LOGO_RE = re.compile(r"^[A-Za-z]{0,2}\d\s?\+\s?\d$")  # channel logos such as "L1+3"
TEAM_RE = re.compile(r"^[^\W\d_]{2,}(?:[ .'\-][^\W\d_]+)*$")   # letters only, at least two ("PSG", "OHIO STATE")
ORDINAL_RE = re.compile(r"^[A-Za-z0-9]?(st|nd|rd|th|sr)$", re.I)   # "4th" and "1st" as OCR mangles them (Ath, Ist, Znd, Isr, Jrd)
TEAM_PAIR_ITEM_RE = re.compile(r"^([^\W\d_]{2,4})\s+(\d{1,2})\s?[-–]\s?(\d{1,2})(?:\s+[^\W\d_]{2,4})?$")   # a code and the score in one item ("FRA 0-0", "FRA 0-0 BEL")
CLOCK_AND_TEAM_RE = re.compile(r"^(\d{1,3}\s?[:.,]\s?\d{2})\s+\W?([A-Za-z]{2,4})\W?$")   # "24.47 RMA": the clock and a team code in one item
CODE_SCORE_RE = re.compile(r"^([A-Za-z]{2,4})\s?[|!Il/\]\[}{)(E]\s?(\d{1,3})$")   # a team code and its score in one item ("PHI| 14")
CHANNEL_LABEL_RE = re.compile(r"^LIVE\s?\d{1,2}$", re.I)   # "LIVE 3": a channel label; CODE_SCORE_RE would read it as the code LIV and the digit 3
SLASH_PAIR_RE = re.compile(r"^(\d{1,3})\s?[/|]\s?(\d{1,3})$")   # two scores merged by OCR ("10/20"); also passing stats

NAME_NUMBER_RE = re.compile(r"^([^\W\d_]{3,}(?:[ .'-][^\W\d_]+)*)\s+(\d{1,3})$")   # "NAVY 58": team and score in one item
STAT_GRAPHIC_RE = re.compile(r"comparison|possession|conversions?|\bconv\b|this season|receptions|leaders|turnovers|negative yards|half\s?time|mi-temps|\bdef\b|\btop ?\d\b|statistics?|statistiques?|ballons|\btirs?\b|\bshots?\b", re.I)   # overlays full of numbers
RANKED_TEAM_RE = re.compile(r"^\d{1,2}\s+[^\W\d_]{3,}(?:[ .'\-][^\W\d_]+)*$")   # "2 Georgia", "6 ALABAMA": a ranking and a name
DIGIT_WITH_MARKS_RE = re.compile(r"^(\d{1,3})\s?[:=;|!]+$")   # the score digit read together with the dashes drawn beside it ("0 =")
MERGED_PAIR_RE = re.compile(r"^(\d{1,3})\s+(\d{1,3})$")   # two scores read as ONE item ("28 9", "10 24")
MERGED_VS_LABEL = 1.5   # such a pair must be at least this much taller than the team names (measured 1.8-2.25 on real boards)
NOT_A_TEAM = {"timeouts", "timeout", "fouls", "foul", "down", "quarter", "qtr", "period", "round", "set", "game", "shots",
              "corners", "yards", "yds", "pts", "points", "bonus", "rec", "rushing", "passing", "ot", "half", "minutes", "min", "periode", "période", "tirs", "tir"}
CARD_QUARTER_RE = re.compile(r"^[A-Za-z0-9]{1,3}\s?QTR$", re.I)   # '3RD QTR' (and its OCR mangles) on a full-screen score card
CARD_WEEK_RE = re.compile(r"^WEEK\s?\d{1,2}$", re.I)
CARD_CLOCK_RE = re.compile(r"^\d{1,2}\s?[:.,;]\s?\d{2}$")
CARD_VS_LABEL = 2.0   # the two scores of a card are at least this much taller than its quarter text
MAX_PAIR_VALUE = 9   # "a-b" tokens above this are more likely a misread clock than a score
MAX_RISE = 10
RECORD_ROW = 0.8   # a "4-0" pair counts as a score only when its team names are on its own row (records sit below the names)
PANEL_QUARTER_RE = re.compile(r"^(q[1-4]|[1-4]q)$", re.I)
PLAY_CLOCK_VS_PANEL = 1.3   # a number right of the clock / quarter text and at most this much taller than it is the play clock, not a score
SCORE_VS_LABEL = 2.0   # score digits are at least this much taller than the team names beside them
CLOCK_SLACK_S = 20   # allowed clock movement beyond twice the elapsed time before it counts as a jump
CLOCK_TEXT_RE = re.compile(r"^(\d{1,3}):(\d{2})$")
HISTORY = 5          # readings that vote
NEEDED = 3           # votes a value needs to count
PRE_RESET_MEMORY_S = 300   # the score known before a baseline reset is remembered this long: a baseline that rose across it is an event
QUARTER_END_S = 10        # a clock jump to this many seconds or less of an American football quarter is the quarter ending, not a rewind
PEAK_MEMORY_S = 120  # a score only goes up: a lower reading is a misread (a logo read as 0), unless it lasts this long
PEAK_HOLD_S = 25     # a score above the ceiling becomes the new ceiling only after it has held this long
FIRED_MEMORY_S = 60   # the same score is not announced twice inside this time (a misread flicker around a goal)
TOUCHDOWN_POINTS = 4   # a gain of at least this many points is a touchdown (6, 7 or 8 with the kick): a full event
READING_S = 3        # assumed time between readings when the caller gives no clock


def _items(texts: list[str], clocks: list[str]) -> list[str]:
    skip = set(clocks)
    return [t.strip() for t in texts
            if t.strip() and t.strip() not in skip and not CLOCK_RE.match(t.strip()) and not LOGO_RE.match(t.strip())]


def _small_pair(item: str) -> tuple[int, int] | None:
    m = SCORE_PAIR_RE.match(item)
    if m and max(int(m.group(1)), int(m.group(2))) <= MAX_PAIR_VALUE:
        return int(m.group(1)), int(m.group(2))
    return None


def find_score(texts: list[str], clocks: list[str], anchored_only: bool = False) -> tuple[int, int] | None:
    """The (left, right) score found in the OCR text items, or None. `anchored_only` keeps just the readings tied to team
    names: the loose ones (any small "a-b", two numbers side by side) pick up team records ("4-0", read as "40")."""
    items = _items(texts, clocks)
    is_team = [bool(TEAM_RE.match(t)) and not ORDINAL_RE.match(t) and t.casefold() not in NOT_A_TEAM for t in items]

    # 1. "TEAM a-b TEAM" (any size for "a/b": merged scores such as "10/20" are only trusted between team names,
    #    because the same text shows passing stats elsewhere)
    for i, item in enumerate(items):
        if 0 < i < len(items) - 1 and is_team[i - 1] and is_team[i + 1]:
            pair = _small_pair(item)
            if pair:
                return pair
            m = SLASH_PAIR_RE.match(item)
            if m:
                return int(m.group(1)), int(m.group(2))

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

    # 2b. two items that each hold a team and its score ("ARMY 12", "NAVY 58"); a number BEFORE the name is usually
    #     a ranking ("#21 UCLA") and is not used
    named = []
    for item in items:
        m = NAME_NUMBER_RE.match(item)
        if m and m.group(1).strip().lower() not in NOT_A_TEAM and m.group(1).strip().casefold().split()[-1] not in NOT_A_TEAM:
            named.append((m.group(1).strip().casefold(), int(m.group(2))))
    if len(named) >= 2 and named[0][0] != named[1][0]:
        return named[0][1], named[1][1]

    # 2c. one item with a team code and a small pair ("FRA 0-0")
    for item in items:
        m = TEAM_PAIR_ITEM_RE.match(item)
        if m and m.group(1).isupper() and m.group(1).casefold() not in NOT_A_TEAM and max(int(m.group(2)), int(m.group(3))) <= MAX_PAIR_VALUE:
            return int(m.group(2)), int(m.group(3))

    if anchored_only:
        return None

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


def find_score_card(items: list[dict], clocks: list[str], last: tuple[int, int] | None) -> tuple[int, int] | None:
    """The full-screen score card an American football broadcast shows after a score: no team names are read, only 'WEEK 5', ONE
    item holding both scores ('14 10') much taller than the labels, and the quarter ('3RD QTR'), maybe with a clock. It counts only
    when a scorebug score was voted before (`last`, the order of the numbers is the scorebug's) and the card is consistent with it:
    neither number lower than before, none more than MAX_RISE above. A stats table, a results ticker (no quarter text) or a record
    ('2-2' has a dash) are not cards."""
    if last is None:
        return None
    texts = [it["text"].strip() for it in items]
    quarter = [it for it in items if CARD_QUARTER_RE.match(it["text"].strip())]
    if not quarter or not (any(CARD_WEEK_RE.match(t) for t in texts) or clocks or any(CARD_CLOCK_RE.match(t) for t in texts)):
        return None
    pairs = [it for it in items if MERGED_PAIR_RE.match(it["text"].strip())]
    if len(pairs) != 1:
        return None
    pair = pairs[0]
    label = max(it["y1"] - it["y0"] for it in quarter)
    if pair["y1"] - pair["y0"] < CARD_VS_LABEL * label:
        return None
    m = MERGED_PAIR_RE.match(pair["text"].strip())
    score = (int(m.group(1)), int(m.group(2)))
    if all(0 <= n - o <= MAX_RISE for o, n in zip(last, score)):
        return score
    return None


def _numbers_read_as_a_score(score: tuple[int, int], items: list[dict]) -> bool:
    """False when the text-only reader's two numbers are separate items that cannot be the two scores of a scoreboard: each must be
    beside a team name on its own row, and the two on one row or two neighbours of one column (no third number between them: a column
    of stats beside player names, a table of shots per period). Numbers that are not items of their own ("FRA 0-0", "NAVY 58") are not
    checked."""
    numbers = [it for it in items if NUMBER_RE.match(it["text"].strip())]
    first = [it for it in numbers if int(it["text"]) == score[0]]
    second = [it for it in numbers if int(it["text"]) == score[1]]
    if not first or not second:
        return True
    teams = [it for it in items if TEAM_RE.match(it["text"].strip()) and len(it["text"].strip()) >= 2 and not ORDINAL_RE.match(it["text"].strip())
             and it["text"].strip().casefold() not in NOT_A_TEAM and not STAT_GRAPHIC_RE.search(it["text"])]

    def beside_a_team(n) -> bool:
        h = n["y1"] - n["y0"]
        return any(abs((t["y0"] + t["y1"]) / 2 - (n["y0"] + n["y1"]) / 2) <= max(h, t["y1"] - t["y0"])
                   and (n["x0"] - t["x1"] if t["x1"] <= n["x0"] else t["x0"] - n["x1"]) <= 6 * h for t in teams)

    for a in first:
        for b in second:
            if a is b or not (beside_a_team(a) and beside_a_team(b)):
                continue
            h = max(a["y1"] - a["y0"], b["y1"] - b["y0"])
            ya, yb = (a["y0"] + a["y1"]) / 2, (b["y0"] + b["y1"]) / 2
            if abs(ya - yb) <= 0.6 * h:
                return True   # one row
            low, high = sorted((ya, yb))
            if abs(a["x0"] - b["x0"]) <= 0.6 * h and not any(
                    n is not a and n is not b and low < (n["y0"] + n["y1"]) / 2 < high and abs(n["x0"] - a["x0"]) <= 0.6 * h for n in numbers):
                return True   # stacked neighbours
    return False


def clock_seconds(text: str) -> float | None:
    return _shared_clock_seconds(text)   # the shared parser (`daemon/clock.py`)


WIDE_PAIR_VS_HEIGHT = 2.2   # two digits are about 1.5 text heights wide; wider than this there is a crest between them


def _wide_pair_halves(it: dict) -> list[dict]:
    """Two scores read as ONE item with a crest between them ("03" spread over 85 px at 32 px tall, Liverpool-City 0-3): the two digits, each
    at its own end of the box. Any other item comes back alone."""
    text = it["text"].strip()
    h = it["y1"] - it["y0"]
    if len(text) != 2 or not text.isdigit() or h <= 0 or it["x1"] - it["x0"] < WIDE_PAIR_VS_HEIGHT * h:
        return [it]
    w = 0.75 * h
    return [dict(it, text=text[0], x1=it["x0"] + w), dict(it, text=text[1], x0=it["x1"] - w)]


WIDE_CODE_VS_NATURAL = 1.35   # a code + two digits item this many times wider than its characters has a crest between its digits


def _wide_code_scores(it: dict) -> list[dict]:
    """"MCI10" over 183 px at 44 px tall (Man City-Sunderland: 1, the crest, 0): five characters take about 132 px, so the item holds a code and TWO
    one-digit scores, not the score ten. Any other item comes back alone."""
    m = re.fullmatch(r"([A-Za-z]{2,4})(\d)(\d)", it["text"].strip())
    h = it["y1"] - it["y0"]
    if not m or h <= 0 or it["x1"] - it["x0"] < WIDE_CODE_VS_NATURAL * 0.6 * h * len(it["text"].strip()):
        return [it]
    w = 0.6 * h
    code_x1 = it["x0"] + 0.6 * h * len(m.group(1))
    return [dict(it, text=m.group(1), x1=code_x1), dict(it, text=m.group(2), x0=code_x1, x1=code_x1 + w), dict(it, text=m.group(3), x0=it["x1"] - w)]


def _without_glued_crest(it: dict) -> dict:
    """A score never starts with a zero: "04" is a real 0 with the crest beside it read as a 4 ("0" + Premier League crest, Liverpool-City
    bug). Keep the first digit and cut the box to its share of the item."""
    text = it["text"].strip()
    if len(text) > 1 and text[0] == "0" and text.isdigit():
        return dict(it, text="0", x1=it["x0"] + (it["x1"] - it["x0"]) / len(text))
    return it


def find_score_geo(items: list[dict], clocks: list[str]) -> tuple[int, int] | None:
    """Score from the size and position of the text (each item has text, x0, y0, x1, y1).

    Score digits are the biggest numbers on the scoreboard, two of them side by side on one line with similar
    height, with a team name nearby. Play clocks, records, jersey numbers and "2nd & 10" are smaller or alone.
    """
    skip = set(clocks)
    items = [dict(it, text=m.group(1)) if (m := DIGIT_WITH_MARKS_RE.match(it["text"].strip())) else it for it in items]
    items = [part for it in items for part in _wide_code_scores(it)]
    items = [half for it in items for half in _wide_pair_halves(it)]
    items = [_without_glued_crest(it) for it in items]
    split = []
    for n, it in enumerate(items):
        c = CLOCK_AND_TEAM_RE.match(it["text"].strip())
        if c:   # "24.47 RMA": keep the team code as an item of its own, at its own place in the box
            width = (it["x1"] - it["x0"]) / len(it["text"].strip())
            split.append(dict(it, text=c.group(2), x0=it["x0"] + width * (it["text"].strip().index(c.group(2)) - 0.5), x1=it["x1"]))
            continue
        cs = CODE_SCORE_RE.match(it["text"].strip())
        if cs and CHANNEL_LABEL_RE.match(it["text"].strip()):
            cs = None   # "LIVE 3" is the channel label, not "LIV" + "3"
        if cs:   # "PHI| 14": the code and the digits become two items, cut at the separator
            char = (it["x1"] - it["x0"]) / len(it["text"].strip())
            split.append(dict(it, text=cs.group(1), x1=it["x0"] + char * len(cs.group(1))))
            split.append(dict(it, text=cs.group(2), x0=it["x0"] + char * it["text"].strip().index(cs.group(2))))
            continue
        m = MERGED_PAIR_RE.match(it["text"].strip())
        if m:   # two scores merged by OCR: cut the box at the space into two numbers, remembered as halves of the same item
            a, b = m.group(1), m.group(2)
            char = (it["x1"] - it["x0"]) / (len(a) + 1 + len(b))
            split.append(dict(it, text=a, x1=it["x0"] + len(a) * char, merged=n))
            split.append(dict(it, text=b, x0=it["x1"] - len(b) * char, merged=n))
        else:
            split.append(it)
    items = split
    live = [it for it in items if it["text"].strip() not in skip and not CLOCK_RE.match(it["text"].strip())
            and not LOGO_RE.match(it["text"].strip())]

    def height(it):
        return it["y1"] - it["y0"]

    def cy(it):
        return (it["y0"] + it["y1"]) / 2

    teams = [it for it in live if (TEAM_RE.match(it["text"].strip()) or RANKED_TEAM_RE.match(it["text"].strip()))
             and len(it["text"].strip()) >= 3 and not ORDINAL_RE.match(it["text"].strip())]

    def teams_near(line_cy, line_h, x0, x1) -> int:
        reach = 4 * line_h
        return sum(1 for t in teams if abs(cy(t) - line_cy) <= 2.2 * line_h and t["x1"] >= x0 - reach and t["x0"] <= x1 + reach)

    def inline(left, right, line_h) -> bool:
        reach = 3.0 * line_h
        before = any(t["x1"] <= left["x0"] + 0.5 * line_h and left["x0"] - t["x1"] <= reach and abs(cy(t) - cy(left)) <= 0.6 * line_h for t in teams)
        after = any(t["x0"] >= right["x1"] - 0.5 * line_h and t["x0"] - right["x1"] <= reach and abs(cy(t) - cy(right)) <= 0.6 * line_h for t in teams)
        return before and after

    def name_left(left, right, line_h) -> bool:
        # the layout "PHI 14  JAX 7": each number has its own team name right before it, on its row
        reach = 1.5 * line_h
        return all(any(t["x1"] <= n["x0"] + 0.5 * line_h and n["x0"] - t["x1"] <= reach and abs(cy(t) - cy(n)) <= 0.6 * line_h for t in teams)
                   for n in (left, right))

    best, best_key = None, None

    # one item holding both scores ("2-0", "10/20") with a team name on each side of it
    tokens = [it for it in live if SCORE_PAIR_RE.match(it["text"].strip()) or SLASH_PAIR_RE.match(it["text"].strip())]
    for it in tokens:
        text = it["text"].strip()
        m = SCORE_PAIR_RE.match(text) or SLASH_PAIR_RE.match(text)
        if not m:
            continue
        if sum(1 for o in tokens if abs(cy(o) - cy(it)) <= 0.5 * height(it)) >= 2:
            continue   # two such tokens on one row: the records shown under each team ("5-0" and "5-0"), not a score
        a, b = int(m.group(1)), int(m.group(2))
        if SCORE_PAIR_RE.match(text) and max(a, b) > MAX_PAIR_VALUE:
            continue
        h = height(it)
        left = any(t["x1"] <= it["x0"] + h and abs(cy(t) - cy(it)) <= RECORD_ROW * h for t in teams)   # the names' own row: a record
        right = any(t["x0"] >= it["x1"] - h and abs(cy(t) - cy(it)) <= RECORD_ROW * h for t in teams)   # ("4-0") sits below them
        if left and right:
            key = (h, 2)
            if best_key is None or key > best_key:
                best, best_key = (a, b), key

    # two separate numbers on one line, similar height, a team name close by
    panel = [it for it in items if CLOCK_RE.match(it["text"].strip()) or it["text"].strip() in skip or ORDINAL_RE.match(it["text"].strip())
             or PANEL_QUARTER_RE.match(it["text"].strip())]

    def is_play_clock(n) -> bool:
        """The play clock is printed right of the game clock / quarter text in the same size; the big digits of a centre-panel scorebug
        are much taller than the panel text (1.4x to 2.7x on the logs), the play clock about 1.0x."""
        for p in panel:
            h = p["y1"] - p["y0"]
            if (n["x0"] >= p["x1"] - 5 and abs((n["y0"] + n["y1"]) / 2 - (p["y0"] + p["y1"]) / 2) <= 0.6 * h and n["x0"] - p["x1"] <= 4 * h
                    and (n["y1"] - n["y0"]) <= PLAY_CLOCK_VS_PANEL * h
                    and not any(t["x0"] >= p["x1"] - 5 and t["x1"] <= n["x0"] + 5 and abs(cy(t) - cy(p)) <= 0.8 * h for t in teams)   # a team code between them: a score, not a play clock (stacked LaLiga bug)
                    and not any(m is not n and NUMBER_RE.match(m["text"].strip()) and 0.7 <= height(m) / max(height(n), 1) <= 1.43 and abs(m["x0"] - n["x0"]) <= 0.6 * height(n)
                                and 0.8 * height(n) <= abs(cy(m) - cy(n)) <= 3.0 * height(n) for m in live)):   # an equal number stacked right above or below it: the two scores of a stacked scorebug
                return True
        return False

    numbers = [it for it in live if NUMBER_RE.match(it["text"].strip()) and not is_play_clock(it)]
    pairs = []   # (min height, text items between the two, teams near, left, right)
    for i, a in enumerate(numbers):
        for b in numbers[i + 1:]:
            ha, hb = height(a), height(b)
            if ha < 12 or hb < 12 or abs(cy(a) - cy(b)) > 0.5 * max(ha, hb) or not 0.7 <= ha / hb <= 1.43:
                continue
            left, right = (a, b) if a["x0"] <= b["x0"] else (b, a)
            if right["x0"] - left["x1"] < 0.3 * min(ha, hb):
                continue
            near = teams_near((cy(a) + cy(b)) / 2, max(ha, hb), left["x0"], right["x1"])
            if near == 0:
                continue
            mid, line_h = (cy(a) + cy(b)) / 2, max(ha, hb)
            beside = [t for t in teams if abs(cy(t) - mid) <= 2.2 * line_h and t["x1"] >= left["x0"] - 4 * line_h
                      and t["x0"] <= right["x1"] + 4 * line_h]
            slack = 0.5 * line_h
            if len(beside) >= 2 and not name_left(left, right, line_h) and (left["x0"] < min(t["x0"] for t in beside) - slack or right["x1"] > max(t["x1"] for t in beside) + slack):
                continue   # a number outside the span of the team names: a jersey number, not the score
            labels = [height(t) for t in teams if abs(cy(t) - cy(a)) <= 2.2 * max(ha, hb) and t["x1"] >= left["x0"] - 4 * max(ha, hb)
                      and t["x0"] <= right["x1"] + 4 * max(ha, hb)]
            if labels and min(ha, hb) < SCORE_VS_LABEL * max(labels):
                # As small as the team labels: usually a record ("4-0" read as 40), a down and distance, a quarter. Except a
                # compact scorebug (USC 7 ... 7 PENN STATE) whose digits sit on the names' own row, right beside each name.
                halves = left.get("merged") is not None and left.get("merged") == right.get("merged")
                if not inline(left, right, line_h) and not name_left(left, right, line_h) and not (halves and min(ha, hb) >= MERGED_VS_LABEL * max(labels)):
                    continue
            between = sum(1 for it in live if not NUMBER_RE.match(it["text"].strip()) and left["x1"] <= it["x0"] and it["x1"] <= right["x0"]
                          and abs(cy(it) - cy(a)) <= 1.2 * max(ha, hb))
            pairs.append((min(ha, hb), between, near, left, right, name_left(left, right, line_h)))
    # two numbers stacked in a column, each right beside its own team (ATM 2 / RMA 1)
    for i, a in enumerate(numbers):
        for b in numbers[i + 1:]:
            top, bottom = (a, b) if cy(a) <= cy(b) else (b, a)
            ha, hb = height(top), height(bottom)
            if ha < 12 or hb < 12 or not 0.7 <= ha / hb <= 1.43:
                continue
            h = max(ha, hb)
            if abs(top["x0"] - bottom["x0"]) > 0.6 * h or abs(top["x1"] - bottom["x1"]) > 0.6 * h or not 0.8 * h <= cy(bottom) - cy(top) <= 3.0 * h:
                continue
            if all(any(t["x1"] <= n["x0"] + 0.5 * h and n["x0"] - t["x1"] <= 4 * h and abs(cy(t) - cy(n)) <= 0.7 * h for t in teams) for n in (top, bottom)):
                key = (min(ha, hb), 2)
                if best_key is None or key > best_key:
                    best, best_key = (int(top["text"]), int(bottom["text"])), key
            elif any(c["x1"] <= min(top["x0"], bottom["x0"]) + 0.5 * h and min(top["x0"], bottom["x0"]) - c["x1"] <= 8 * h
                     and cy(top) - 0.5 * h <= cy(c) <= cy(bottom) + 0.5 * h for c in panel if CLOCK_RE.match(c["text"].strip())):
                # a crest-only scorebug (no readable team text, the DAZN LaLiga bug): the game clock right beside two stacked digits is enough
                key = (min(ha, hb), 1)
                if best_key is None or key > best_key:
                    best, best_key = (int(top["text"]), int(bottom["text"])), key
    if any(p[5] for p in pairs):
        pairs = [p for p in pairs if p[5]]   # each number with its own team name before it (PHI 14  JAX 7) beats a number from elsewhere on the row
    if pairs:
        # The scores are the biggest pair. Among pairs about as big as the biggest, the one with the centre panel
        # (down and distance, clock) between its numbers wins: a team logo read as a digit ("0" for an O) forms a pair
        # with the real score next to it, with nothing between them.
        biggest = max(p[0] for p in pairs)
        size, _, near, left, right, _ = max((p for p in pairs if p[0] >= 0.85 * biggest), key=lambda p: (p[1], p[0], p[2]))
        key = (size, near)
        if best_key is None or key > best_key:
            best, best_key = (int(left["text"]), int(right["text"])), key
    if best is None:
        # one item holds a team code and the score pair ("FRA 0-0": a crest-less TF1 bug whose second code was lost); the same rule as the
        # text reader's, so the passes that wait for a geometry score do not run on a board that is already read
        for it in live:
            m = TEAM_PAIR_ITEM_RE.match(it["text"].strip())
            if (m and m.group(1).isupper() and m.group(1).casefold() not in NOT_A_TEAM and max(int(m.group(2)), int(m.group(3))) <= MAX_PAIR_VALUE
                    and not STAT_GRAPHIC_RE.search(it["text"])):
                return int(m.group(2)), int(m.group(3))
    return best


def _vote(values) -> int | None:
    value, count = Counter(values).most_common(1)[0]
    return value if count >= NEEDED else None


def _no_scoreboard(texts: list[str], clocks: list[str]) -> bool:
    """True when the clocks are the only scoreboard-like thing on screen (see `GameTracker`: an ad countdown)."""
    from daemon.game_state import has_scoreboard_text
    return bool(texts) and not has_scoreboard_text(texts, clocks)


class ScoreWatcher:
    """Follows one video area. `update` returns True on the reading where a score change becomes stable."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()   # event sizes (field goal, extra point window); shared, so popup edits apply at once
        self.strength = 1.0   # size of the event `update` last reported: 1.0 = goal / touchdown, less = field goal or safety
        self._scored_at = [float("-inf"), float("-inf")]   # when each side last scored a touchdown (American football)
        self._history: deque[tuple[int, int]] = deque(maxlen=HISTORY)
        self._stable: tuple[int, int] | None = None
        self._peak: tuple[int, int] | None = None      # highest value seen per side
        self._below_since: float | None = None          # since when the vote has been under the peak
        self._above_since: float | None = None          # since when the vote has been above the peak
        self._fired: tuple[tuple[int, int], float] | None = None   # the last score announced and when
        self._before_reset: tuple[tuple[int, int], float, tuple[int, float] | None] | None = None   # ceiling when the baseline was last reset, when, and the game clock then
        self._recent_clock: tuple[int, float] | None = None   # the latest game clock read at all (even a jumped one) and when
        self._readings = 0
        self.candidate = False   # the last reading was a score above the ceiling that the vote has not confirmed (yet)
        self._clock: tuple[int, float] | None = None   # last plausible game clock and when it was read
        self._alt: tuple[int, float] | None = None     # an implausible clock seen once, waiting for confirmation

    @property
    def score(self) -> tuple[int, int] | None:
        """The score the watcher currently believes (voted over several readings), or None."""
        return self._stable

    def _reset(self, now: float | None = None) -> None:
        if self._peak is not None and now is not None:
            # the ceiling only rises after a score has held 25 s, so the voted score counts too
            self._before_reset = (tuple(max(p, s) for p, s in zip(self._peak, self._stable or self._peak)), now, self._clock)
        self._history.clear()
        self._stable = self._peak = self._below_since = self._above_since = self._fired = None

    def _event_size(self, current: tuple[int, int], previous: tuple[int, int], t: float, sport: str | None) -> float:
        """How big a score change is, 0..1. Only American football has point tiers: a touchdown (4+ points) is a full event,
        a field goal / safety (2 or 3) and a lone extra point (1) are `field_goal_strength`, and an extra point or two-point
        conversion within `extra_point_s` of that side's touchdown is no new event (0)."""
        gains = [min(n - p, n - o) if n > o else 0 for o, n, p in zip(self._peak, current, previous)]
        if (sport or "").strip().lower() != "american football":
            return 1.0
        side = max(range(2), key=lambda i: gains[i])
        gain = gains[side]
        if gain >= TOUCHDOWN_POINTS:
            self._scored_at[side] = t
            return 1.0
        if gain <= 2 and t - self._scored_at[side] <= self.config.extra_point_s:
            return 0.0
        return self.config.field_goal_strength

    @staticmethod
    def _same_game_clock(before: tuple[int, float] | None, after: tuple[int, float] | None) -> bool:
        """The clock read now continues the one read before the reset: it moved by no more than twice the time that passed (plus slack)."""
        if before is None or after is None:
            return False
        return abs(after[0] - before[0]) <= 2 * max(after[1] - before[1], 0.0) + 2 * CLOCK_SLACK_S

    @staticmethod
    def _quarter_ends(clocks: list[str], sport: str | None) -> bool:
        """American football: a clock at 0:00-0:10 is a quarter ending (the clock of the new period is not far from it), not a rewind."""
        if (sport or "").strip().lower() != "american football":
            return False
        seconds = [s for s in map(clock_seconds, clocks) if s is not None]
        return bool(seconds) and min(seconds) <= QUARTER_END_S

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

    def _is_news(self, score: tuple[int, int]) -> bool:
        """During a replay only a score ABOVE the one already counted can be a new goal: neither side lower than the ceiling, at least one
        side higher by 1..MAX_RISE (the usual order of the sides). The replay of an old goal shows the same or an older score."""
        return (all(n >= p for n, p in zip(score, self._peak)) and any(1 <= n - p <= MAX_RISE for n, p in zip(score, self._peak)))

    def update(self, texts: list[str], clocks: list[str], now: float | None = None, items: list[dict] | None = None,
               replay: bool = False, sport: str | None = None) -> bool:
        # A countdown with no scoreboard beside it is the player's ad countdown: it is not a game clock and must not look like a
        # clock jump (that threw the score away at every commercial break, so a touchdown before the break was never seen).
        self.candidate = False
        if replay and self._stable is None:
            return False   # a replay shows old action with its own graphics: with no score known there is nothing to compare it with
        game_clocks = [] if clocks and _no_scoreboard(texts, clocks) else clocks
        if now is not None and not replay and (read := [s for s in map(clock_seconds, game_clocks) if s is not None]):
            self._recent_clock = (read[0], now)
        if now is not None and not replay and self._clock_jumped(game_clocks, now) and not self._quarter_ends(game_clocks, sport):   # a replay's clock is an old moment's: no jump
            self._reset(now)
        if any(STAT_GRAPHIC_RE.search(t) for t in texts):
            return False   # a stat graphic or halftime banner: its numbers are not the score, and must not become the baseline
        score = find_score_geo(items, clocks) if items else None
        if score is None:
            score = find_score(texts, clocks, anchored_only=bool(items))
            if score is not None and items and not _numbers_read_as_a_score(score, items):
                score = None   # "BARCOLA 5 ... Cherki 2": player names with a column of stats, not teams with scores
        if score is None and items:
            score = find_score_card(items, clocks, self._stable)
        if score is None:
            return False
        if replay and not self._is_news(score):
            return False   # a replay re-shows old action (the same or an older score): not a reading at all
        self._readings += 1
        t = now if now is not None else self._readings * READING_S
        # A live reading above the ceiling by 1..MAX_RISE (neither side lower) is a goal CANDIDATE before the vote confirms it.
        self.candidate = (not replay and self._stable is not None and self._is_news(score)
                          and not (self._fired is not None and self._fired[0] == score and t - self._fired[1] < FIRED_MEMORY_S))   # not the goal just announced
        self._history.append(score)
        left, right = _vote(p[0] for p in self._history), _vote(p[1] for p in self._history)
        if left is None or right is None:
            return False
        current = (left, right)
        if self._stable is None:
            before, self._before_reset = self._before_reset, None
            self._stable = self._peak = current
            rises = [n - p for n, p in zip(current, before[0])] if before is not None else []
            if before is not None and t - before[1] <= PRE_RESET_MEMORY_S and sorted(rises)[0] == 0 and 1 <= sorted(rises)[1] <= MAX_RISE                     and self._same_game_clock(before[2], self._recent_clock):
                # ONE side rose by 1..MAX_RISE and the other did not move across a baseline reset (a clock jump swallowed the change), and the
                # game clock fits the one before the reset: it is an event after all. Another game in the slot has other scores and a
                # clock from somewhere else (Como-Roma at 90' -> Espanyol-Elche at 28': a false 0-2).
                self._peak = before[0]
                self.strength = self._event_size(current, before[0], t, sport)
                self._peak = current
                if self.strength > 0.0:
                    self._fired = (current, t)
                    return True
            return False
        if all(c <= p for c, p in zip(current, self._peak)):
            # not above the ceiling: a restored score, or a misread; a drop that lasts is believed in the end
            self._above_since = None
            if current != self._peak:
                self._below_since = t if self._below_since is None else self._below_since
                if t - self._below_since >= PEAK_MEMORY_S:
                    self._peak, self._below_since = current, None
            else:
                self._below_since = None
            self._stable = current
            return False
        # Above the ceiling: an event, but the ceiling rises only once the score has held for a while, so a false
        # high reading (a graphic, a record) cannot hide the real goal that follows it.
        self._below_since = None
        fired = False
        previous = self._stable
        if self._above_since is None or current != self._stable:
            self._above_since = t
            fired = any(1 <= n - o <= MAX_RISE for o, n in zip(self._peak, current))
            if fired and self._fired is not None and self._fired[0] == current and t - self._fired[1] < FIRED_MEMORY_S:
                fired = False
            if fired:
                self.strength = self._event_size(current, previous, t, sport)
                fired = self.strength > 0.0
            if fired:
                self._fired = (current, t)
        self._stable = current
        if t - self._above_since >= PEAK_HOLD_S:
            self._peak = tuple(max(c, p) for c, p in zip(current, self._peak))
            self._above_since = None
        return fired
