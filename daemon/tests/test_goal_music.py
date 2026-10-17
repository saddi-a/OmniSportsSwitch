"""Goal-celebration music is not a sound ad (live: STR-PSG 2026-10-17 21:19:40, MAIN left a goal for the music after it).

Music right after a goal looks like an ad (music high, no crowd). It is exempt only when a goal signal (`Scorer.set_event(goal=True)`:
a voted score change, a GOAL banner, a feed goal) is within `audio_ad_goal_s` of the music, in either order. A loud moment alone is
not enough, and a real ad with no goal around it stays an ad."""
from daemon.scoring import Config, Scorer

from .test_scoring import ad, roar, warm_up


def make(**cfg):
    cfg.setdefault("confirm_s", 0.0)
    s = Scorer(Config(**cfg))
    s.register(1, is_main=True)
    s.register(2)
    s.register(3)
    return s


def music(s, tab_id, start, seconds=5):
    return ad(s, tab_id, start, seconds)


def test_an_ad_after_a_loud_moment_with_no_goal_stays_an_ad():
    s = make()
    now = warm_up(s)
    now = roar(s, 1, now, seconds=5)               # a loud moment, no score change
    now = music(s, 1, now)
    c = s.components(1, now)
    assert c["ad"] and c["score"] < 0


def test_music_after_a_confirmed_goal_is_not_an_ad():
    s = make()
    now = warm_up(s)
    now = roar(s, 1, now, seconds=5)
    s.set_event(1, now, goal=True)                 # the score change was voted
    now = music(s, 1, now + 1)
    c = s.components(1, now)
    assert not c["ad"] and c["score"] >= 0


def test_a_late_goal_confirmation_cancels_the_ad_flag_that_was_already_set():
    s = make()
    now = warm_up(s)
    now = roar(s, 1, now, seconds=5)
    now = music(s, 1, now)                         # music first: flagged as an ad
    assert s.components(1, now)["ad"]
    s.set_event(1, now + 1, goal=True)             # the OCR confirms the score a moment later
    c = s.components(1, now + 1)
    assert not c["ad"]
    assert s.streams[1].ad_at == float("-inf")      # and the stream is not 'recently on an ad' for the idle-MAIN hold-off


def test_music_long_after_the_goal_is_an_ad_again():
    s = make()
    now = warm_up(s)
    s.set_event(1, now, goal=True)
    now = music(s, 1, now + 40, seconds=6)         # a commercial break 40 s after the goal
    assert s.components(1, now)["ad"]


def test_a_goal_on_another_stream_does_not_exempt_this_one():
    s = make()
    now = warm_up(s)
    s.set_event(2, now, goal=True)
    now = music(s, 1, now + 1)
    assert s.components(1, now)["ad"]


def test_a_picture_ad_is_never_exempt():
    s = make()
    now = warm_up(s)
    s.set_event(1, now, goal=True)
    s.set_on_ad(1, True, now + 1)
    assert s.components(1, now + 2)["ad"]


def test_the_exemption_can_be_switched_off():
    s = make(audio_ad_goal_s=0.0)
    now = warm_up(s)
    s.set_event(1, now, goal=True)
    now = music(s, 1, now + 1)
    assert s.components(1, now)["ad"]


def test_main_does_not_leave_a_goal_for_the_celebration_music():
    s = make(min_dwell_s=0)
    now = warm_up(s)
    s.set_event(1, now, goal=True)
    now = music(s, 1, now + 1)                     # MAIN plays the goal music
    assert not s.components(1, now)["ad"]
    assert s.components(1, now)["score"] >= 0      # not pushed under the others by the ad penalty
