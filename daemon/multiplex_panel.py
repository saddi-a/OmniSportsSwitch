"""The results panel of the CANAL+ "+SPORT 360 MULTIPLEX" channel (soccer): one video on the left with its own scorebug, and on the right a
column of every game of the night, one row per game: `SPO 1-0 LASK`, `BAY 2-1 ARS` (a channel tag and crests between the rows).

Pure logic. `find_panel` finds the rows in one OCR reading (at least `MIN_ROWS` rows of `CODE n-n CODE` stacked in the right part of the
frame; anything else, RedZone included, gives None) and splits the reading into the panel and the rest (the scorebug side), so game
identity and feed mapping can look at the scorebug only. `PanelWatch` votes over the readings and reports a game whose score went up by one
goal: a goal was scored in SOME game of the channel, usually before the director cuts to it."""
import dataclasses
import re

from daemon.game_state import NOT_TEAM_CODES, TEAM_CODE_RE

MIN_ROWS = 3
ROW_RE = re.compile(r"^([A-Z]{2,4})\s*(\d{1,2})\s*[-–:]\s*(\d{1,2})\s*([A-Z]{2,4})$")
ROW_GAP = 0.7        # items whose vertical centres are this many text heights apart or less are one row
PANEL_SIDE = 0.6     # a row starts in the right part of the frame: x0 >= this x the right edge of the reading
CUT_HEIGHTS = 3.0    # the panel's own words (channel tags, crests) start up to this many text heights left of its rows


@dataclasses.dataclass(frozen=True)
class Row:
    left: str
    right: str
    a: int
    b: int
    x0: float
    y: float

    @property
    def key(self) -> tuple[str, str]:
        return (self.left, self.right)

    @property
    def label(self) -> str:
        return f"{self.left}-{self.right}"


@dataclasses.dataclass(frozen=True)
class Panel:
    rows: tuple
    cut_x: float
    rest: tuple      # the items left of the panel (the scorebug side), as given


def _row_text(group: list[dict]) -> str:
    return re.sub(r"\s+", " ", " ".join(re.sub(r"[^\w\s–:-]", "", it["text"]).strip() for it in group)).strip()


def _row_of(group: list[dict]) -> Row | None:
    """The row in one line of items: the longest run of neighbouring items that reads `CODE n-n CODE` (a stray item of the picture, such as
    a misread advertising board, may share the line)."""
    group = sorted(group, key=lambda it: it["x0"])
    for size in range(len(group), 0, -1):
        for start in range(len(group) - size + 1):
            part = group[start:start + size]
            m = ROW_RE.match(_row_text(part))
            if m is None:
                continue
            left, a, b, right = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            if left == right or any(c in NOT_TEAM_CODES or not TEAM_CODE_RE.fullmatch(c) for c in (left, right)):
                continue
            ys = [(it["y0"] + it["y1"]) / 2 for it in part]
            return Row(left, right, a, b, part[0]["x0"], sum(ys) / len(ys))
    return None


def find_panel(items: list[dict] | None) -> Panel | None:
    """The results panel of one reading, or None (fewer than `MIN_ROWS` aligned rows in the right part of the frame)."""
    if not items:
        return None
    right_edge = max(it["x1"] for it in items)
    cand = sorted((it for it in items if it["x0"] >= PANEL_SIDE * right_edge), key=lambda it: (it["y0"] + it["y1"]))
    groups: list[list[dict]] = []
    for it in cand:
        h = max(it["y1"] - it["y0"], 1)
        yc = (it["y0"] + it["y1"]) / 2
        if groups and abs(yc - sum((g["y0"] + g["y1"]) / 2 for g in groups[-1]) / len(groups[-1])) <= ROW_GAP * h:
            groups[-1].append(it)
        else:
            groups.append([it])
    rows = [r for r in (_row_of(g) for g in groups) if r is not None]
    if len(rows) < MIN_ROWS:
        return None
    x_min = min(r.x0 for r in rows)
    if max(r.x0 for r in rows) - x_min > 0.15 * right_edge:
        return None                                   # not one column
    height = sorted(it["y1"] - it["y0"] for it in items)[len(items) // 2]
    cut = x_min - CUT_HEIGHTS * max(height, 1)
    rest = tuple(it for it in items if it["x0"] < cut)
    return Panel(tuple(rows), cut, rest)


class PanelWatch:
    """Votes over the panel's rows: a game's score is believed after `readings` identical readings, a new score that goes up by exactly
    one goal on one side after `readings` identical readings is a goal; any other change (down, several goals, a misread digit that
    stuck) is only a new baseline, never an event. A game seen for the first time is a baseline too (nothing fires on a tab that was
    just tracked)."""

    def __init__(self, readings: int = 2):
        self.readings = max(int(readings), 1)
        self.base: dict[tuple, tuple[int, int]] = {}
        self._cand: dict[tuple, tuple[tuple[int, int], int]] = {}

    def update(self, panel: Panel | None) -> list[tuple[str, tuple[int, int], tuple[int, int]]]:
        """One reading. Returns [(game label, old score, new score)] for the goals confirmed on this reading."""
        if panel is None:
            return []
        goals = []
        for row in panel.rows:
            score = (row.a, row.b)
            base = self.base.get(row.key)
            if score == base:
                self._cand.pop(row.key, None)
                continue
            cand, n = self._cand.get(row.key, (None, 0))
            n = n + 1 if cand == score else 1
            if n < self.readings:
                self._cand[row.key] = (score, n)
                continue
            self._cand.pop(row.key, None)
            self.base[row.key] = score
            if base is not None and ((score[0] == base[0] + 1 and score[1] == base[1]) or (score[1] == base[1] + 1 and score[0] == base[0])):
                goals.append((row.label, base, score))
        return goals
