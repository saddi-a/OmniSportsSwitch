"""A goal candidate (the first reading of a higher score, before the 3-of-5 vote confirms it) backs the music exemption
(`audio_ad_goal_s`) when a loud moment came just before. It lapses by itself when the vote never confirms it."""

from .test_goal_music import make, music
from .test_scoring import ad, roar, warm_up


def test_early_reading_then_confirmation_is_never_an_ad():
    s = make()
    now = warm_up(s)
    now = roar(s, 1, now, seconds=5)                # the goal roar
    s.set_goal_pending(1, now)                # the first new-score reading
    now = music(s, 1, now)                          # the celebration music
    assert not s.components(1, now)["ad"]
    s.set_event(1, now + 0.5, goal=True)            # the vote confirms
    now = music(s, 1, now + 0.5, seconds=3)
    assert not s.components(1, now)["ad"]


def test_a_flag_set_before_the_early_reading_is_cancelled_by_it():
    s = make()
    now = warm_up(s)
    now = roar(s, 1, now, seconds=5)
    now = music(s, 1, now)
    assert s.components(1, now)["ad"]
    s.set_goal_pending(1, now)
    assert not s.components(1, now)["ad"]
    assert s.streams[1].ad_at == float("-inf")


def test_early_reading_rejected_by_the_vote_restores_the_ad_rule():
    s = make()
    now = warm_up(s)
    now = roar(s, 1, now, seconds=5)
    s.set_goal_pending(1, now)
    now = music(s, 1, now)
    assert not s.components(1, now)["ad"]
    # no further candidate reading: the vote never confirmed it; after the lapse the music counts again
    now = music(s, 1, now + s.config.goal_pending_s, seconds=5)
    assert s.components(1, now)["ad"]


def test_a_lone_misread_with_no_loud_moment_changes_nothing():
    s = make()
    now = warm_up(s)
    s.set_goal_pending(1, now)                # a misread in a quiet game
    now = music(s, 1, now + 1)                      # a real ad starts
    assert s.components(1, now)["ad"]


def test_the_real_ad_case_is_unchanged():
    s = make()
    now = warm_up(s)
    now = roar(s, 1, now, seconds=5)                # a loud moment, no candidate, no goal
    now = ad(s, 1, now)
    c = s.components(1, now)
    assert c["ad"] and c["score"] < 0


def test_the_early_signal_can_be_switched_off():
    s = make(goal_pending_s=0.0)
    now = warm_up(s)
    now = roar(s, 1, now, seconds=5)
    s.set_goal_pending(1, now)
    now = music(s, 1, now)
    assert s.components(1, now)["ad"]
