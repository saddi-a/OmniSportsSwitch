"""The rising-sound cue (soccer, medium, heard before the commentator) and the small bonus of a goal in a decided game."""
import logging

import pytest

from daemon.cues import SurgeRiseWatch, surge_rise_weight
from daemon.scoring import Config, Scorer, Weights
from daemon.server import Server
from daemon.tests.test_scoring import make, quiet_play, warm_up

CFG = Config(surge_rise_votes=2.0)   # these tests feed readings 2 s apart, the old tick; the default is 3 votes at the 1 s tick (test below)


# ---- SurgeRiseWatch ---------------------------------------------------------------------------------------------------------------------

def feed(watch, readings, tab=1, cfg=CFG):
    """readings = [(time, level, surge)]; returns the times at which the watch fired."""
    return [t for t, level, surge in readings if watch.reading(tab, t, level, surge, cfg)]


def test_a_rise_on_two_readings_in_a_row_fires_once():
    # quiet, then the level climbs (0.05 -> 0.10 -> 0.14 -> 0.18) with the surge well above the baseline
    readings = [(0, 0.05, 0.0), (2, 0.05, 0.0), (4, 0.06, 0.1), (6, 0.10, 0.6), (8, 0.14, 0.9), (10, 0.18, 1.0)]
    assert feed(SurgeRiseWatch(), readings) == [8]    # t=6 is the first vote, t=8 the second; t=10 is inside the cooldown


def test_default_is_three_readings_one_second_apart():
    # the same 2 s hold as two readings 2 s apart at the old tick
    readings = [(0, 0.05, 0.0), (1, 0.05, 0.0), (2, 0.05, 0.0), (3, 0.06, 0.1), (4, 0.10, 0.6), (5, 0.14, 0.9), (6, 0.16, 1.0), (7, 0.18, 1.0)]
    assert feed(SurgeRiseWatch(), readings, cfg=Config()) == [6]


def test_one_reading_is_never_enough():
    readings = [(0, 0.05, 0.0), (2, 0.05, 0.0), (4, 0.05, 0.0), (6, 0.12, 0.8), (8, 0.05, 0.0), (10, 0.05, 0.0)]
    assert feed(SurgeRiseWatch(), readings) == []


def test_a_steady_loud_crowd_is_not_a_rise():
    readings = [(t, 0.15, 1.0) for t in range(0, 30, 2)]     # loud from the start: no growth against 3 to 6 s ago
    assert feed(SurgeRiseWatch(), readings) == []


def test_a_rise_below_the_surge_minimum_is_ignored():
    readings = [(0, 0.020, 0.0), (2, 0.020, 0.0), (4, 0.025, 0.2), (6, 0.032, 0.3), (8, 0.040, 0.4), (10, 0.050, 0.45)]
    assert feed(SurgeRiseWatch(), readings) == []


def test_a_slow_growth_is_not_a_fast_rise():
    readings = [(0, 0.10, 0.6), (2, 0.105, 0.6), (4, 0.11, 0.6), (6, 0.115, 0.6), (8, 0.12, 0.6), (10, 0.125, 0.6)]
    assert feed(SurgeRiseWatch(), readings) == []        # +25 % in 6 s is below the ratio 1.5


def test_the_cooldown_and_each_stream_apart():
    w = SurgeRiseWatch()
    up = [(0, 0.05, 0.0), (2, 0.05, 0.0), (4, 0.06, 0.1), (6, 0.10, 0.6), (8, 0.14, 0.9)]
    assert feed(w, up) == [8]
    # a second rise on the same stream 20 s later is inside the 40 s cooldown...
    again = [(20, 0.05, 0.0), (22, 0.05, 0.0), (24, 0.06, 0.0), (26, 0.10, 0.6), (28, 0.14, 0.9)]
    assert feed(w, again) == []
    # ...but another stream is not affected, and the same stream fires after the cooldown
    assert feed(w, up, tab=2) == [8]
    later = [(t + 60, lv, sg) for t, lv, sg in up]
    assert feed(w, later) == [68]
    w.remove(1)
    assert 1 not in w._history


def test_the_thresholds_are_read_from_the_config():
    cfg = Config(surge_rise_votes=1.0, surge_rise_ratio=1.2)
    readings = [(0, 0.05, 0.0), (4, 0.05, 0.0), (6, 0.07, 0.6)]
    assert feed(SurgeRiseWatch(), readings, cfg=cfg) == [6]
    assert feed(SurgeRiseWatch(), readings) == []


def test_weight_is_soccer_only_and_zero_is_off():
    w = Weights()
    assert surge_rise_weight("football", w) == w.cue_surge_rise == 0.20 == w.cue_review_mid     # a medium cue
    assert surge_rise_weight(None, w) == 0.20 and surge_rise_weight("", w) == 0.20
    for sport in ("american football", "basketball", "generic"):
        assert surge_rise_weight(sport, w) is None
    assert surge_rise_weight("football", Weights(cue_surge_rise=0.0)) is None


# ---- the server wiring ------------------------------------------------------------------------------------------------------------------

def _server(sport="football"):
    server = Server()
    server.scorer.config.surge_rise_votes = 2.0
    for t in (1, 2):
        server.handle_message({"type": "register", "tab_id": t, "is_main": t == 1})
    if sport:
        server.sports[2] = sport
    return server


def _c(level, surge, **over):
    c = {"level": level, "surge": surge, "crowd": 0.4, "ad": False, "status": "live"}
    c.update(over)
    return c


def _rise(server, tab=2, start=0.0, **over):
    fired = []
    for dt, level, surge in ((0, 0.05, 0.0), (2, 0.05, 0.0), (4, 0.06, 0.1), (6, 0.10, 0.6), (8, 0.14, 0.9)):
        fired.append(server._check_surge_rise(tab, _c(level, surge, **over), start + dt))
    return fired


def test_a_rise_in_soccer_sets_a_medium_cue_notes_the_build_up_and_logs(caplog):
    server = _server()
    with caplog.at_level(logging.INFO):
        assert _rise(server) == [False, False, False, False, True]
    s = server.scorer.streams[2]
    assert s.anticipation_value == pytest.approx(0.20) and s.anticipation_at == 8.0
    assert [(round(v, 2), kind) for _, v, kind in server.buildup.cue_details(2, 8.0)] == [(0.20, "surge_rise")]
    assert "anticipation surge rise tab 2" in caplog.text


def test_the_rise_cue_is_recorded_for_director_training():
    events = []
    server = _server()
    server._rec_event = lambda kind, tab_id=None, tile=None, **f: events.append((kind, tab_id, f))
    _rise(server)
    assert [(k, t) for k, t, _ in events] == [("surge_rise", 2)]
    assert events[0][2]["weight"] == 0.2 and events[0][2]["surge"] == 0.9 and "crowd" in events[0][2] and "level" in events[0][2]


@pytest.mark.parametrize("sport", ["american football", "basketball", "generic"])
def test_other_sports_get_no_rise_cue(sport):
    server = _server(sport)
    assert not any(_rise(server))
    assert server.scorer.streams[2].anticipation_value == 0.0 and server.buildup.cue_details(2, 8.0) == []


def test_no_rise_cue_on_an_ad_a_break_a_replay_or_when_switched_off():
    for over in ({"ad": True}, {"status": "break"}):
        assert not any(_rise(_server(), **over))
    server = _server()
    server.scorer.streams[2].replay = True
    assert not any(_rise(server))
    server = _server()
    server.scorer.config.weights.cue_surge_rise = 0.0
    assert not any(_rise(server))


def test_the_weight_can_be_overridden_for_football_only():
    server = _server()
    server.scorer.config.sport_weights = {"football": {"cue_surge_rise": 0.3}}
    assert any(_rise(server))
    assert server.scorer.streams[2].anticipation_value == pytest.approx(0.3)


# ---- a goal in a decided game ----------------------------------------------------------------------------------------------------------

def blowout_case(**cfg):
    """Tab 2 has just scored in a decided game (leverage -1, leverage weight so big that the goal alone is worth little); the rest is quiet."""
    cfg.setdefault("leverage_weight", 0.6)
    s = make(**cfg)
    now = warm_up(s)
    for tab in (1, 2, 3):
        quiet_play(s, tab, now)
    now += 5
    s.set_leverage(2, -1.0)
    s.set_event(2, now, 1.0, goal=True)
    return s, now


def test_without_the_bonus_a_goal_in_a_decided_game_does_not_win():
    s, now = blowout_case(blowout_goal_bonus=0.0)
    assert s.components(2, now)["score"] < s.config.min_score
    assert s.decide(now) is None


def test_the_bonus_lets_it_win_when_every_other_stream_is_quiet():
    s, now = blowout_case(blowout_goal_bonus=0.5)
    assert s.blowout_goal_bonus(2, s.components(2, now), now) == 0.5
    swap = s.decide(now)
    assert swap is not None and swap.promote == 2 and "blowout goal +0.50" in swap.reason


def test_default_bonus_is_small_and_conservative():
    c = Config()
    assert 0 < c.blowout_goal_bonus <= 0.3 and c.blowout_goal_quiet <= 0.25


def test_any_cue_or_activity_elsewhere_takes_the_bonus_away():
    s, now = blowout_case(blowout_goal_bonus=0.5)
    s.set_anticipation(3, now, 0.10)                      # a minor cue on another stream
    assert s.blowout_goal_bonus(2, s.components(2, now), now) == 0.0
    assert s.decide(now) is None

    s, now = blowout_case(blowout_goal_bonus=0.5)
    s.set_anticipation(1, now, 0.10)                      # ...on MAIN counts too
    assert s.blowout_goal_bonus(2, s.components(2, now), now) == 0.0

    s, now = blowout_case(blowout_goal_bonus=0.5, blowout_goal_quiet=0.01)
    assert s.components(3, now)["score"] >= 0.0
    s.set_event(3, now, 1.0)                              # another stream with a score change of its own
    assert s.blowout_goal_bonus(2, s.components(2, now), now) == 0.0


def test_an_ad_or_a_break_elsewhere_is_not_activity():
    s, now = blowout_case(blowout_goal_bonus=0.5)
    s.set_anticipation(3, now, 0.10)
    s.set_on_ad(3, True, now)
    assert s.blowout_goal_bonus(2, s.components(2, now), now) == 0.5


def test_no_bonus_for_a_close_game_an_old_goal_or_a_change_that_is_not_a_goal():
    s, now = blowout_case(blowout_goal_bonus=0.5)
    s.set_leverage(2, -0.2)                                 # not decided yet (limit is minus 0.3)
    assert s.blowout_goal_bonus(2, s.components(2, now), now) == 0.0
    s.set_leverage(2, -1.0)
    assert s.blowout_goal_bonus(2, s.components(2, now + 30), now + 30) == 0.0     # event faded below 0.5
    s2, now2 = blowout_case(blowout_goal_bonus=0.5)
    s2.streams[2].goal_at = float("-inf")                   # a big play, not a goal
    assert s2.blowout_goal_bonus(2, s2.components(2, now2), now2) == 0.0


def test_bonus_is_read_per_sport():
    s, now = blowout_case(blowout_goal_bonus=0.0)
    s.sport_of = lambda tab: "football" if tab == 2 else None
    s.config.sport_scoring = {"football": {"blowout_goal_bonus": 0.5}}
    assert s.blowout_goal_bonus(2, s.components(2, now), now) == 0.5
    s.sport_of = lambda tab: "basketball" if tab == 2 else None
    assert s.blowout_goal_bonus(2, s.components(2, now), now) == 0.0


def test_the_new_keys_may_be_overridden_per_sport():
    from daemon.scoring import OVERRIDABLE_SCORING
    for key in ("surge_rise_min", "surge_rise_ratio", "surge_rise_window_s", "surge_rise_votes", "surge_rise_cooldown_s",
                "blowout_goal_bonus", "blowout_goal_lev", "blowout_goal_event", "blowout_goal_quiet"):
        assert key in OVERRIDABLE_SCORING
