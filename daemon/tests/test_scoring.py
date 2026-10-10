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
    assert c["surge"] == pytest.approx(1.0)  # capped: loudness alone cannot dominate the score
    assert c["score"] > 0


def test_fresh_score_change_boosts_a_stream_and_fades():
    s = make()
    now = warm_up(s)
    steady(s, 2, 0.05, now, 5)
    s.set_event(2, now + 5)
    fresh = s.components(2, now + 5)
    assert fresh["event"] == pytest.approx(1.0) and fresh["score"] > 0.5
    steady(s, 2, 0.05, now + 5, 30)
    assert 0 < s.components(2, now + 35)["event"] < 0.5
    steady(s, 2, 0.05, now + 35, 20)
    assert s.components(2, now + 55)["event"] == 0.0


def test_score_change_alone_can_trigger_a_swap():
    s = make()
    now = warm_up(s)
    steady(s, 1, 0.05, now, 5)
    steady(s, 2, 0.05, now, 5)
    s.set_event(2, now + 4.5)
    assert s.decide(now + 5).promote == 2


def test_stream_detected_as_over_cannot_win():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now)
    s.set_auto_state(2, "over")
    steady(s, 1, 0.05, 60, 5)
    assert s.decide(now) is None


def test_break_counts_a_quarter():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now)
    live = s.score(2, now)
    s.set_auto_state(2, "break")
    assert s.score(2, now) == pytest.approx(live * 0.25)


def test_manual_status_overrides_automatic_detection():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now)
    s.set_auto_state(2, "over")
    s.set_status(2, "live")
    assert s.components(2, now)["status"] == "live"
    s.set_status(2, None)  # back to automatic
    assert s.components(2, now)["status"] == "over"


def test_stream_restricted_away_from_main_is_skipped_for_promotion():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now)
    roar(s, 3, now - 5)
    s.set_can_main(2, False)
    steady(s, 1, 0.05, 60, 5)
    swap = s.decide(now)
    assert swap is not None and swap.promote == 3


def test_no_swap_while_main_is_still_warming_up():
    s = make(warmup_s=20)
    steady(s, 1, 0.05, 40, 5)               # MAIN only just started sending audio
    for tab in (2, 3):
        steady(s, tab, 0.05, 0, 45)
    now = roar(s, 2, 40)
    assert s.components(1, now) is None     # warming up
    assert s.decide(now) is None


def test_loudness_alone_is_not_enough_to_win():
    s = make()
    now = warm_up(s)
    steady(s, 2, 0.20, now, 5)              # 4x louder, but no crowd sound and no score change
    steady(s, 1, 0.05, now, 5)
    assert s.decide(now + 5) is None


def ad(s, tab_id, start, seconds=5, level=0.12):
    """Loud music, no crowd: an ad break."""
    t = start
    while t < start + seconds:
        s.add_audio(tab_id, level, t)
        s.set_tags(tab_id, 0.0, 0.3, 0.9, t)
        t += 0.5
    return t


def quiet_play(s, tab_id, start, seconds=5):
    t = start
    while t < start + seconds:
        s.add_audio(tab_id, 0.05, t)
        s.set_tags(tab_id, 0.02, 0.8, 0.04, t)
        t += 0.5
    return t


def test_ad_on_main_hands_over_to_a_quiet_live_stream():
    s = make()
    now = warm_up(s)
    ad(s, 1, now)
    quiet_play(s, 2, now)
    quiet_play(s, 3, now)
    swap = s.decide(now + 5)
    assert swap is not None and swap.promote in (2, 3) and "MAIN idle" in swap.reason


def test_quiet_play_on_main_is_not_replaced_by_other_quiet_streams():
    s = make()
    now = warm_up(s)
    for tab in (1, 2, 3):
        quiet_play(s, tab, now)
    assert s.decide(now + 5) is None


def test_ads_everywhere_means_no_swap():
    s = make()
    now = warm_up(s)
    for tab in (1, 2, 3):
        ad(s, tab, now)
    assert s.decide(now + 5) is None


def test_main_on_a_break_hands_over_to_a_live_stream():
    s = make()
    now = warm_up(s)
    quiet_play(s, 1, now)
    quiet_play(s, 2, now)
    s.set_auto_state(1, "break")
    assert s.decide(now + 5).promote == 2


def test_idle_handover_still_respects_the_minimum_gap_between_swaps():
    s = make(min_dwell_s=20)
    now = warm_up(s)
    ad(s, 1, now)
    quiet_play(s, 2, now)
    assert s.decide(now + 5) is not None
    ad(s, 2, now + 5)                      # the new MAIN gets an ad right away
    quiet_play(s, 1, now + 5)
    assert s.decide(now + 10) is None      # too soon after the last swap


# ---- favourite teams -------------------------------------------------------------------------------------

def test_a_favourite_on_a_side_screen_takes_main_from_a_quiet_stream():
    s = make()
    now = warm_up(s)
    s.set_favourite(2, 2.0)
    for tab in (1, 2, 3):
        quiet_play(s, tab, now)
    swap = s.decide(now + 5)
    assert swap is not None and swap.promote == 2


def test_a_favourite_on_main_stays_while_nothing_exciting_happens():
    s = make()
    now = warm_up(s)
    s.set_favourite(1, 2.0)
    for tab in (1, 2, 3):
        quiet_play(s, tab, now)
    assert s.decide(now + 5) is None


def test_a_goal_elsewhere_drops_the_favourite_from_main():
    s = make()
    now = warm_up(s)
    s.set_favourite(1, 2.0)
    quiet_play(s, 1, now)
    quiet_play(s, 3, now)
    s.set_event(2, now + 4.5)           # a goal in another game
    quiet_play(s, 2, now)
    swap = s.decide(now + 5)
    assert swap is not None and swap.promote == 2


def test_a_weak_moment_elsewhere_does_not_drop_the_favourite():
    s = make()
    now = warm_up(s)
    s.set_favourite(1, 2.0)
    quiet_play(s, 1, now)
    steady(s, 2, 0.12, now, 5)          # louder than usual, no crowd, no score change
    assert s.decide(now + 5) is None


def test_a_favourite_on_a_break_does_not_hold_main():
    s = make()
    now = warm_up(s)
    s.set_favourite(1, 2.0)
    quiet_play(s, 1, now)
    quiet_play(s, 2, now)
    s.set_auto_state(1, "break")
    assert s.decide(now + 5).promote == 2


def test_no_favourite_means_no_bonus():
    s = make()
    now = warm_up(s)
    quiet_play(s, 1, now)
    assert s.components(1, now + 5)["fav"] == 1.0
    s.set_favourite(1, 0.3)             # anything below 1 is treated as no bonus
    assert s.components(1, now + 5)["fav"] == 1.0


def test_replacing_a_target_slot_leaves_main_alone_and_ignores_the_freeze():
    s = make()
    s.frozen = True
    now = warm_up(s)
    now = roar(s, 3, now)
    assert s.decide(now) is None                       # normal swaps are blocked while frozen
    swap = s.decide(now, target=2)                     # but the LEFT stream (tab 2) can be replaced
    assert (swap.promote, swap.demote) == (3, 2)
    assert s.main == 1


def test_the_main_stream_is_never_a_challenger_for_the_target_slot():
    s = make()
    now = warm_up(s)
    now = roar(s, 1, now)                              # MAIN itself is the exciting one
    assert s.decide(now, target=2) is None


# ---- negative scores for ads and breaks -----------------------------------------------------------------

def test_a_stream_showing_an_ad_has_a_negative_score_whatever_its_audio_says():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now)                              # loud, crowd-like audio (the ad's soundtrack)
    s.set_event(2, now)
    s.set_on_ad(2, True)
    c = s.components(2, now)
    assert c["score"] == pytest.approx(-0.5) and c["ad"] is True


def test_music_without_a_crowd_is_a_milder_negative():
    s = make()
    now = warm_up(s)
    now = ad(s, 2, now)
    c = s.components(2, now)
    assert c["score"] == pytest.approx(-0.25) and c["ad"] is True


def test_normal_play_is_not_marked_as_an_ad():
    s = make()
    now = warm_up(s)
    now = quiet_play(s, 2, now)
    c = s.components(2, now)
    assert c["ad"] is False and c["score"] >= 0


def test_a_stream_on_an_ad_never_wins_a_swap():
    s = make()
    now = warm_up(s)
    now = roar(s, 2, now)
    s.set_on_ad(2, True)
    quiet_play(s, 1, now - 5)
    assert s.decide(now) is None


def test_main_on_an_ad_gives_way_to_a_quiet_live_stream():
    s = make()
    now = warm_up(s)
    quiet_play(s, 1, now)
    quiet_play(s, 2, now)
    s.set_on_ad(1, True)
    s.set_auto_state(1, "break")
    swap = s.decide(now + 5)
    assert swap is not None and swap.promote == 2
    assert s.components(1, now + 5)["score"] < 0


# ---- weaker events: big plays -------------------------------------------------------------------------

def test_a_big_play_counts_as_two_thirds_to_three_quarters_of_a_goal():
    s = make()
    now = warm_up(s)
    steady(s, 2, 0.05, now, 5)
    steady(s, 3, 0.05, now, 5)
    s.set_event(2, now + 5, 1.0)       # a goal
    s.set_event(3, now + 5, s.config.big_play_strength)
    goal, big = s.components(2, now + 5)["event"], s.components(3, now + 5)["event"]
    assert goal == pytest.approx(1.0) and big == pytest.approx(0.7)
    assert 0.66 <= big / goal <= 0.75


def test_a_big_play_can_win_a_swap_on_its_own_but_by_less_than_a_goal():
    s = make()
    now = warm_up(s)
    for tab in (1, 2):
        steady(s, tab, 0.05, now, 5)
    s.set_event(2, now + 4.5, 0.7)
    swap = s.decide(now + 5)
    assert swap is not None and swap.promote == 2
    assert 0.5 < s.components(2, now + 5)["score"] < 0.8      # a goal would give about 0.8


def test_a_goal_after_a_big_play_takes_over_at_full_strength_and_a_later_big_play_does_not_weaken_it():
    s = make()
    now = warm_up(s)
    steady(s, 2, 0.05, now, 5)
    s.set_event(2, now + 5, 0.7)
    s.set_event(2, now + 6, 1.0)
    assert s.components(2, now + 6)["event"] == pytest.approx(1.0)
    s.set_event(2, now + 7, 0.7)                                # weaker, later: must not reduce the goal's boost
    assert s.components(2, now + 7)["event"] >= 1.0 - 1 / 45 - 1e-9


def test_a_big_play_fades_faster_than_a_goal():
    s = make()
    now = warm_up(s)
    steady(s, 2, 0.05, now, 40)
    steady(s, 3, 0.05, now, 40)
    s.set_event(2, now, 1.0)
    s.set_event(3, now, 0.7)
    assert s.components(3, now + 31.5)["event"] == pytest.approx(0.0, abs=0.01)
    assert s.components(2, now + 31.5)["event"] > 0.25


# ---- the FIFA+ ad on the UCLA-Oregon multiview: the music probability flickered and one dip let it take over ----

def flicker(s, tab_id, start, levels, step=1.0):
    """Feed one reading per `step` seconds: loud speech with these music probabilities, no crowd."""
    t = start
    for m in levels:
        s.add_audio(tab_id, 0.1, t)
        s.set_tags(tab_id, 0.02, 0.8, m, t)
        t += step
    return t


def test_a_stream_that_sounded_like_an_ad_stays_flagged_through_a_dip():
    s = make()
    now = warm_up(s)
    now = flicker(s, 2, now, [0.77, 0.81, 0.83, 0.55, 0.17])        # the last reading dips below the ad threshold
    c = s.components(2, now)
    assert c["music"] < 0.5 and c["ad"] is True and c["score"] < 0
    later = flicker(s, 2, now, [0.1] * 14)                            # clean audio for 14 s: the memory fades
    assert s.components(2, later)["ad"] is False


def test_idle_main_does_not_hand_over_to_a_stream_that_was_just_an_ad():
    s = make()
    now = warm_up(s)
    quiet_play(s, 3, now, seconds=8)
    ad(s, 1, now, seconds=8)                                          # MAIN is on an ad
    flicker(s, 2, now, [0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.2])      # tab 2: an ad too, but the last reading dips
    swap = s.decide(now + 8)
    assert swap is not None and swap.promote == 3                     # the clean stream, not the flickering ad


def test_main_that_may_no_longer_be_there_is_handed_over_at_once():
    s = make()
    t = warm_up(s)
    s.set_can_main(1, False)
    swap = s.evict_main(t)
    assert swap is not None and swap.demote == 1 and swap.promote in (2, 3)
    assert s.main == swap.promote
    assert s.evict_main(t + 1) is None   # nothing more to do (and not retried within 5 s)


def test_nothing_is_evicted_while_frozen_or_when_main_is_allowed():
    s = make()
    t = warm_up(s)
    assert s.evict_main(t) is None
    s.set_can_main(1, False)
    s.frozen = True
    assert s.evict_main(t) is None
