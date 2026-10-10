import json
import pathlib

import pytest

from daemon.score_watch import ScoreWatcher, find_score, find_score_geo
from daemon.screen_ocr import find_clocks

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    """Real OCR output (text and box of every item) from a screenshot of a live stream."""
    items = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["items"]
    texts = [i["text"] for i in items]
    return items, texts, find_clocks(texts)


def test_college_football_big_digits_beat_jersey_numbers():
    items, texts, clocks = load("main_now")                       # Texas 14 - 7 Oklahoma
    assert find_score_geo(items, clocks) == (14, 7)
    assert find_score(texts, clocks) != (14, 7)                   # the text-only reader took jersey numbers


def test_merged_scores_between_team_names():
    items, _, clocks = load("ohio_tile")                          # Maryland 10 - 20 Ohio State
    assert find_score_geo(items, clocks) == (10, 20)


def test_small_dazn_multiview_tile():
    items, _, clocks = load("rect_multiview_tile4")               # NAP 1-0 FRO
    assert find_score_geo(items, clocks) == (1, 0)


def test_unsure_is_better_than_wrong():
    items, _, clocks = load("center_after")                       # Houston 0 - 0 K-State: only one big digit read
    assert find_score_geo(items, clocks) is None


@pytest.mark.xfail(strict=True, reason="known: a team logo read as a big '4' beats the real 0-0 on the UCF layout")
def test_ucf_layout_with_a_logo_read_as_a_digit():
    items, _, clocks = load("edge_window_1")                      # UCF 0 - 0 Oklahoma State
    assert find_score_geo(items, clocks) == (0, 0)


def test_watcher_uses_the_geometry_first_and_falls_back_to_text():
    items, texts, clocks = load("main_now")
    w = ScoreWatcher()
    for i in range(5):
        w.update(texts, clocks, 3.0 * i, items)
    assert w._stable == (14, 7)                                   # not (39, 23) from jersey numbers
    w2 = ScoreWatcher()
    for i in range(5):
        w2.update(["PSG", "1", "LMFC", "0"], [], 3.0 * i, None)   # no boxes: the text reader still works
    assert w2._stable == (1, 0)
