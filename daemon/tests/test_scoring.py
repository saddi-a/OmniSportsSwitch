import pytest

from daemon.scoring import Config, Scorer


def steady(scorer, tab_id, level, start, seconds, step=0.5):
    """Feed constant loudness; returns the time after the last sample."""
    t = start
    while t < start + seconds:
        scorer.add_audio(tab_id, level, t)
        t += step
    return t


def make(**cfg):
    s = Scorer(Config(**cfg))
    s.register(1, is_main=True)
    s.register(2)
    s.register(3)
    return s


def warm_up(s, level=0.05, seconds=60):
    for tab in (1, 2, 3):
        steady(s, tab, level, 0, seconds)
    return seconds


def roar(s, tab_id, start, seconds=5, level=0.15, crowd=0.8, speech=0.1, music=0.0):
    """A crowd roar: louder than normal and crowd-like."""
    t = start
    while t < start + seconds:
        s.add_audio(tab_id, level, t)
        s.set_tags(tab_id, crowd, speech, music, t)
        t += 0.5
    return t


def test_steady_streams_of_different_loudness_never_swap():
    s = make()
    steady(s, 1, 0.05, 0, 90)
    steady(s, 2, 0.30, 0, 90)  # much louder stream, but always at its own normal level
    assert s.decide(90) is None


def test_crowd_roar_on_a_challenger_triggers_swap_with_reason():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now)
    steady(s, 1, 0.05, 60, 5)
    swap = s.decide(now)
    assert (swap.promote, swap.demote) == (2, 1)
    assert "crowd=" in swap.reason and s.main == 2


def test_loud_ad_with_music_does_not_win():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now, level=0.15, crowd=0.0, speech=0.4, music=0.9)  # loud, but music and no crowd
    steady(s, 1, 0.05, 60, 5)
    assert s.decide(now) is None


def test_loud_commentary_alone_does_not_win():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now, level=0.12, crowd=0.02, speech=0.9, music=0.0)
    steady(s, 1, 0.05, 60, 5)
    assert s.decide(now) is None


def test_no_swap_during_warmup():
    s = make(warmup_s=20)
    now = roar(s, 2, 0, seconds=5)
    steady(s, 1, 0.05, 0, 5)
    assert s.decide(now) is None


def test_priority_weight_changes_outcome():
    s = make()
    s.set_weight(2, 3.0)
    now = warm_up(s)
    now = roar(s, 2, now, level=0.06, crowd=0.25)  # mild crowd noise, weighted x3
    steady(s, 1, 0.05, 60, 5)
    assert s.decide(now).promote == 2


def test_picks_the_strongest_challenger():
    s = make()
    now = warm_up(s)
    roar(s, 2, now, crowd=0.4)
    now = roar(s, 3, now, crowd=0.9)
    steady(s, 1, 0.05, 60, 5)
    assert s.decide(now).promote == 3


def test_both_streams_roaring_does_not_swap():
    s = make()
    now = warm_up(s)
    roar(s, 1, now, crowd=0.8)
    now = roar(s, 2, now, crowd=0.85)  # barely different: margin not met
    assert s.decide(now) is None


def test_min_dwell_blocks_immediate_second_swap():
    s = make(min_dwell_s=20)
    now = warm_up(s)
    now = roar(s, 2, now)
    assert s.decide(now) is not None
    now = roar(s, 1, now, seconds=5)
    assert s.decide(now) is None  # only 5 s since the swap


def test_second_swap_allowed_after_dwell():
    s = make(min_dwell_s=20)
    now = warm_up(s)
    now = roar(s, 2, now)
    s.decide(now)
    for tab in (1, 2, 3):
        steady(s, tab, 0.05, now, 25)
    now += 25
    now = roar(s, 1, now)
    assert s.decide(now).promote == 1


def test_stale_stream_is_ignored():
    s = make(stale_s=5.0)
    now = warm_up(s)
    roar(s, 2, now)  # stream 2 stops sending after this
    steady(s, 1, 0.05, now, 30)
    assert s.decide(now + 30) is None


def test_freeze_blocks_swaps():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now)
    steady(s, 1, 0.05, 60, 5)
    s.frozen = True
    assert s.decide(now) is None
    s.frozen = False
    assert s.decide(now) is not None


def test_no_main_registered_means_no_swap():
    s = Scorer()
    s.register(2)
    now = steady(s, 2, 0.05, 0, 60)
    now = roar(s, 2, now)
    assert s.decide(now) is None


def test_missing_classifier_tags_fall_back_to_loudness_only():
    s = make()
    now = warm_up(s)
    steady(s, 2, 0.20, now, 5)  # 4x louder than normal, no tags at all
    c = s.components(2, now + 5)
    assert c["crowd"] == 0.0
    assert 2.0 < c["surge"] <= 3.0  # baseline drifts up a little while the stream stays loud
    assert c["score"] > 0
