import pytest

from daemon.leverage import leverage
from daemon.scoring import Config

CFG = Config()


def test_a_tied_game_in_the_fourth_quarter_is_the_most_interesting():
    assert leverage("american football", (24, 24), 4, None, CFG) == pytest.approx(1.0)


def test_a_blowout_late_is_the_least_interesting():
    assert leverage("american football", (16, 45), 4, None, CFG) == pytest.approx(-1.0)


def test_one_score_apart_late_is_still_close():
    assert leverage("american football", (17, 24), 4, None, CFG) == pytest.approx(1.0)


def test_closeness_matters_a_little_early_and_a_blowout_early_is_neutral():
    early_close = leverage("american football", (7, 7), 1, None, CFG)
    early_blowout = leverage("american football", (0, 28), 1, None, CFG)
    assert 0.2 < early_close < 0.4
    assert early_blowout == pytest.approx(0.0)


def test_in_between_gaps_give_in_between_values():
    two_scores = leverage("american football", (10, 26), 4, None, CFG)     # 16 points apart
    assert -0.9 < two_scores < 0.9
    assert leverage("american football", (10, 26), 4, None, CFG) < leverage("american football", (10, 17), 4, None, CFG)


def test_later_is_more_interesting_when_close():
    values = [leverage("american football", (14, 14), q, None, CFG) for q in (1, 2, 3, 4)]
    assert values == sorted(values) and values[-1] > values[0]


def test_overtime_counts_as_the_latest_moment():
    assert leverage("american football", (27, 27), 5, None, CFG) == pytest.approx(1.0)


def test_soccer_uses_its_own_scale():
    assert leverage("football", (1, 1), None, 85, CFG) > 0.8                 # soccer by the minute
    assert leverage("football", (0, 4), None, 85, CFG) < -0.8
    assert leverage("football", (1, 1), None, 10, CFG) < 0.5


def test_unknown_information_gives_no_effect():
    assert leverage("american football", None, 4, None, CFG) == 0.0          # score not read
    assert leverage("american football", (24, 24), None, None, CFG) == 0.0   # period not read
    assert leverage(None, (24, 24), 4, None, CFG) == 0.0
    assert leverage("generic", (24, 24), 4, None, CFG) == 0.0
    assert leverage("tennis", (1, 2), 4, None, CFG) == 0.0


def test_a_tab_takes_the_best_of_its_tiles_and_zero_when_nothing_is_known():
    from daemon.leverage import tab_leverage
    tiles = [("american football", (16, 45), 4, None), ("american football", (24, 24), 4, None)]
    assert tab_leverage(tiles, CFG) == pytest.approx(1.0)
    assert tab_leverage([], CFG) == 0.0
    assert tab_leverage([("american football", None, None, None)], CFG) == 0.0
