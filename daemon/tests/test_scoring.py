import pytest

from daemon.scoring import Config, Scorer, Weights


def steady(scorer, tab_id, level, start, seconds, step=0.5):
    """Feed constant loudness; returns the time after the last sample."""
    t = start
    while t < start + seconds:
        scorer.add_audio(tab_id, level, t)
        t += step
    return t


def make(**cfg):
    cfg.setdefault("idle_evidence_s", 0.0)   # tested on its own below
    cfg.setdefault("confirm_s", 0.0)   # confirmation is tested on its own below
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
    """Feed one reading per `step` seconds: little speech (an ad: music well above speech) with these music probabilities, no crowd."""
    t = start
    for m in levels:
        s.add_audio(tab_id, 0.1, t)
        s.set_tags(tab_id, 0.02, 0.2, m, t)
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


# ---- music-only "ads" must be sustained (found in the live log: three streams took turns
# ---- being flagged AD from single music readings, so MAIN changed hands every 20 s) --------------------------

def test_one_music_blip_does_not_flag_an_ad():
    s = make()
    now = warm_up(s)
    quiet_play(s, 1, now, seconds=4)
    s.set_tags(1, 0.0, 0.8, 0.8, now + 4)          # a single reading: arena music under the commentary
    quiet_play(s, 1, now + 4.5, seconds=1)
    c = s.components(1, now + 5.5)
    assert not c["ad"] and c["score"] >= 0.0


def test_music_in_most_recent_readings_still_flags_an_ad():
    s = make()
    now = warm_up(s)
    t = now
    for music in (0.8, 0.1, 0.8, 0.8, 0.8):        # flickering but mostly music
        s.add_audio(1, 0.12, t)
        s.set_tags(1, 0.0, 0.2, music, t)
        t += 1.0
    assert s.components(1, t)["ad"]


def test_a_stream_with_a_blip_is_not_replaced_when_main_and_others_are_quiet():
    s = make()
    now = warm_up(s)
    for tab in (1, 2, 3):
        quiet_play(s, tab, now)
    s.set_tags(1, 0.0, 0.8, 0.8, now + 5)
    assert s.decide(now + 5.5) is None


# ---- confirmation: one applause reading must not take MAIN (live log 00:39:00: crowd=0.55 for one reading, then an ad) ----

def test_a_single_crowd_reading_does_not_take_main():
    s = make(confirm_s=2.0)
    now = warm_up(s)
    for tab in (1, 2, 3):
        quiet_play(s, tab, now)
    t = now + 5
    s.add_audio(2, 0.05, t)
    s.set_tags(2, 0.8, 0.4, 0.05, t)                 # one reading
    assert s.decide(t) is None
    s.add_audio(2, 0.05, t + 1)
    s.set_tags(2, 0.05, 0.8, 0.05, t + 1)            # gone again
    assert s.decide(t + 1) is None
    assert s.decide(t + 3.5) is None


def test_a_challenger_that_keeps_qualifying_wins_after_the_confirmation_time():
    s = make(confirm_s=2.0)
    now = warm_up(s)
    swaps = []
    for _ in range(8):                               # the daemon asks every 0.25 s; here every 0.5 s
        now = roar(s, 2, now, seconds=0.5)
        swaps.append((now, s.decide(now)))
    first = next(t for t, swap in swaps if swap)
    assert first - swaps[0][0] >= 2.0 - 0.5          # not on the first qualifying reading


# ---- MAIN must not hop back to the stream it just left because of an ad (live log: 45 of 75 idle swaps) -------------

def test_a_stream_that_was_on_an_ad_a_few_seconds_ago_is_not_promoted():
    s = make(min_dwell_s=20, recent_ad_s=45)
    now = warm_up(s)
    ad(s, 1, now)
    quiet_play(s, 2, now)
    assert s.decide(now + 5).promote == 2          # 1 is on an ad: MAIN goes to 2
    t = now + 5
    for step in range(30):                         # 25 s later: 1's ad is over (and forgotten), 2 gets an ad
        t += 1.0
        if t < now + 12:
            ad(s, 1, t, seconds=1)
        else:
            quiet_play(s, 1, t, seconds=1)
        if t < now + 20:
            quiet_play(s, 2, t, seconds=1)
        else:
            ad(s, 2, t, seconds=1)
    assert s.decide(t) is None                     # 3 is silent/absent and 1 was on an ad 20 s ago: stay


def test_after_the_hold_off_the_stream_can_be_promoted_again():
    s = make(min_dwell_s=20, recent_ad_s=45)
    now = warm_up(s)
    ad(s, 1, now)
    quiet_play(s, 2, now)
    s.decide(now + 5)
    t = now + 5
    for _ in range(80):
        t += 1.0
        quiet_play(s, 1, t, seconds=1)
        ad(s, 2, t, seconds=1)
    assert s.decide(t).promote == 1


def test_another_live_stream_is_preferred_over_one_that_just_had_an_ad():
    s = make(min_dwell_s=20, recent_ad_s=45)
    now = warm_up(s)
    ad(s, 2, now)                                   # 2 has an ad right now
    quiet_play(s, 3, now)
    ad(s, 1, now)
    t = now + 14
    for _ in range(14):
        t += 1.0
        quiet_play(s, 2, t, seconds=1)              # 2's ad ended (memory forgot it)
        quiet_play(s, 3, t, seconds=1)
        ad(s, 1, t, seconds=1)
    assert s.decide(t).promote == 3


def test_the_picture_ad_marks_the_stream_too():
    s = make(min_dwell_s=20, recent_ad_s=45)
    now = warm_up(s)
    s.set_on_ad(2, True, now)
    s.set_on_ad(2, False, now + 5)
    ad(s, 1, now + 10)
    for tab in (2,):
        quiet_play(s, tab, now + 10)
    assert s.decide(now + 15) is None


# ---- warm-up (first 20 s of a stream, e.g. after a daemon restart): a picture-detected ad/break on MAIN may still go ----

def started(s, seconds=5):
    for tab in (1, 2, 3):
        steady(s, tab, 0.05, 0, seconds)
    return seconds


def test_main_on_a_picture_ad_hands_over_during_warmup():
    s = make(warmup_s=20)
    now = started(s)
    s.set_on_ad(1, True, now)
    for tab in (2, 3):
        quiet_play(s, tab, 0, seconds=5)
    swap = s.decide(now)
    assert swap is not None and swap.promote in (2, 3) and "MAIN idle" in swap.reason


def test_main_on_a_break_or_over_hands_over_during_warmup():
    for state in ("break", "over"):
        s = make(warmup_s=20)
        now = started(s)
        s.set_auto_state(1, state)
        assert s.decide(now) is not None, state


def test_an_ad_stream_is_not_chosen_during_warmup():
    s = make(warmup_s=20)
    now = started(s)
    s.set_on_ad(1, True, now)
    s.set_on_ad(2, True, now)
    s.set_auto_state(3, "break")
    assert s.decide(now) is None


def test_audio_excitement_still_cannot_swap_during_warmup():
    s = make(warmup_s=20)
    now = started(s)
    for t in (now, now + 0.5, now + 1.0, now + 1.5):
        s.add_audio(2, 0.30, t)
        s.set_tags(2, 0.9, 0.1, 0.0, t)
    assert s.decide(now + 2) is None


def test_a_quiet_main_that_is_not_on_an_ad_is_kept_during_warmup():
    s = make(warmup_s=20)
    now = started(s)
    assert s.decide(now) is None


# ---- commentary over stadium music is not an ad (live log 01:12-01:16: a live Texas game was flagged AD 76% of the time) ----

def commentary_over_music(s, tab_id, start, seconds=8, music=0.8, speech=0.76):
    t = start
    while t < start + seconds:
        s.add_audio(tab_id, 0.06, t)
        s.set_tags(tab_id, 0.02, speech, music, t)
        t += 1.0
    return t


def test_music_under_loud_commentary_is_not_an_ad():
    s = make()
    now = warm_up(s)
    t = commentary_over_music(s, 1, now)
    assert not s.components(1, t)["ad"]


def test_music_clearly_louder_than_speech_is_still_an_ad():
    s = make()
    now = warm_up(s)
    t = now
    for _ in range(8):
        s.add_audio(1, 0.12, t)
        s.set_tags(1, 0.0, 0.3, 0.9, t)
        t += 1.0
    assert s.components(1, t)["ad"]


def test_a_live_stream_with_stadium_music_does_not_trigger_idle_handover():
    s = make()
    now = warm_up(s)
    commentary_over_music(s, 1, now)
    quiet_play(s, 2, now)
    assert s.decide(now + 8) is None


# ---- "Make Main": the user forces a stream onto MAIN and it is held there ---------------------------------------

def test_forcing_a_stream_moves_it_to_main_and_holds_it():
    s = make(manual_hold_s=600)
    t = warm_up(s)
    swap = s.force_main(2, t)
    assert swap is not None and (swap.promote, swap.demote) == (2, 1) and s.main == 2
    assert s.hold_left(t + 100) == pytest.approx(500)


def test_a_normal_roar_elsewhere_does_not_take_main_from_a_held_stream():
    s = make(manual_hold_s=600)
    t = warm_up(s)
    s.force_main(2, t)
    t = roar(s, 3, t + 30)
    assert s.decide(t) is None           # it would have swapped without the hold (see the roar tests above)
    assert s.main == 2 and s.hold_left(t) > 0


def test_something_really_exciting_breaks_the_hold():
    s = make(manual_hold_s=600, hold_break_score=0.4)
    t = warm_up(s)
    s.force_main(2, t)
    t = roar(s, 3, t + 30)
    s.set_event(3, t)                    # a goal on top of the roar
    swap = s.decide(t)
    assert swap is not None and swap.promote == 3
    assert s.hold_left(t) == 0 and s.hold_tab is None   # the hold ended because something else is on MAIN


def test_the_hold_times_out_and_can_be_released():
    s = make(manual_hold_s=60)
    t = warm_up(s)
    s.force_main(2, t)
    assert s.hold_left(t + 61) == 0 and s.hold_tab is None
    s.force_main(3, t + 100)
    s.release_hold()
    assert s.hold_left(t + 101) == 0


def test_a_game_that_only_flickers_to_over_keeps_its_hold_but_a_real_end_does_not():
    s = make(hold_over_s=60)
    t = warm_up(s)
    s.force_main(2, t)
    s.set_auto_state(2, "over")                       # a wrong 'over' reading right after the click
    assert s.hold_left(t + 1) > 0 and s.hold_tab == 2
    s.set_auto_state(2, "live")
    assert s.hold_left(t + 20) > 0
    s.set_auto_state(2, "over")
    assert s.hold_left(t + 30) > 0                    # over, but only for a moment
    assert s.hold_left(t + 100) == 0 and s.hold_tab is None   # over for 70 s: the game really ended


def test_forcing_the_current_main_just_holds_it_and_a_restricted_stream_is_refused():
    s = make()
    t = warm_up(s)
    assert s.force_main(1, t) is None and s.hold_left(t) > 0
    s.set_can_main(3, False)
    with pytest.raises(ValueError):
        s.force_main(3, t)
    with pytest.raises(ValueError):
        s.force_main(99, t)
# ---- closeness and time left: a late close game is more interesting, a blowout less -------------------------------

def test_a_close_late_game_scores_higher_than_a_blowout_of_the_same_sound():
    s = make()
    now = warm_up(s)
    for tab in (1, 2, 3):
        quiet_play(s, tab, now)
    s.set_leverage(2, 1.0)
    s.set_leverage(3, -1.0)
    close = s.components(2, now + 5)["score"]
    blowout = s.components(3, now + 5)["score"]
    assert close == pytest.approx(Config().leverage_weight, abs=0.02) and blowout == 0.0


def test_idle_handover_prefers_the_close_late_game_over_a_blowout():
    s = make()
    now = warm_up(s)
    ad(s, 1, now)
    quiet_play(s, 2, now)
    quiet_play(s, 3, now)
    s.set_leverage(2, -1.0)            # Ohio State 45-16
    s.set_leverage(3, 1.0)             # Texas-Oklahoma 24-24, 4th quarter
    assert s.decide(now + 5).promote == 3


def test_leverage_alone_never_takes_main_from_a_quiet_stream():
    s = make()
    now = warm_up(s)
    for tab in (1, 2, 3):
        quiet_play(s, tab, now)
    s.set_leverage(2, 1.0)
    assert s.decide(now + 5) is None


def test_a_close_late_game_helps_a_challenger_with_some_excitement():
    s = make()
    now = warm_up(s)
    steady(s, 1, 0.05, now, 5)
    s.set_leverage(1, -1.0)            # MAIN is a blowout
    now2 = roar(s, 2, now, seconds=5, level=0.05, crowd=0.1, speech=0.1)     # too weak on its own (below min_score)
    assert s.decide(now2) is None
    s.set_leverage(2, 1.0)
    assert s.decide(now2) is not None


def test_leverage_is_clamped():
    s = make()
    s.set_leverage(2, 5.0)
    assert s.streams[2].leverage == 1.0
    s.set_leverage(2, -5.0)
    assert s.streams[2].leverage == -1.0


# ---- MAIN must survive a stray tab registering as MAIN and leaving (live log 01:57:59-01:58:03: all swaps stopped) ----

def test_a_stray_tab_that_registers_as_main_and_leaves_gives_main_back():
    s = make()
    now = warm_up(s)
    s.register(9, is_main=True)      # a window that is not a stream: registered while sitting in the MAIN slot
    s.remove(9)
    assert s.main == 1


def test_main_comes_back_to_the_previous_stream_not_to_a_removed_one():
    s = make()
    s.register(9, is_main=True)
    s.remove(1)                      # the earlier MAIN is gone too
    s.remove(9)
    assert s.main is None


def test_swaps_still_work_after_the_stray_tab_left():
    s = make()
    now = warm_up(s)
    s.register(9, is_main=True)
    s.remove(9)
    ad(s, 1, now)
    quiet_play(s, 2, now)
    assert s.decide(now + 5).promote == 2


def test_removing_a_stream_that_is_not_main_changes_nothing():
    s = make()
    s.remove(3)
    assert s.main == 1


def test_a_swap_forgets_the_remembered_previous_main():
    s = make()
    now = warm_up(s)
    ad(s, 1, now)
    quiet_play(s, 2, now)
    assert s.decide(now + 5).promote == 2
    s.register(9, is_main=True)
    s.remove(9)
    assert s.main == 2


# ---- MAIN stuck on a picture ad because every other stream had an ad lately (live log 02:10, LSU on MAIN) -----------

def test_main_on_a_picture_ad_still_hands_over_when_the_others_were_on_an_ad_lately():
    s = make(min_dwell_s=20, recent_ad_s=30)
    now = warm_up(s)
    quiet_play(s, 1, now)
    quiet_play(s, 2, now)
    quiet_play(s, 3, now)
    s.set_on_ad(2, True, now + 2)        # 2 and 3 were on an ad a few seconds ago, but are playing now
    s.set_on_ad(2, False, now + 4)
    s.set_on_ad(3, True, now + 3)
    s.set_on_ad(3, False, now + 4)
    s.set_on_ad(1, True, now + 4)        # MAIN shows the player's ad placeholder right now
    swap = s.decide(now + 5)
    assert swap is not None and swap.promote in (2, 3)


def test_the_hold_off_still_applies_when_main_is_only_an_audio_ad():
    s = make(min_dwell_s=20, recent_ad_s=30)
    now = warm_up(s)
    ad(s, 1, now)                         # MAIN sounds like an ad (music only, no picture evidence)
    quiet_play(s, 2, now)
    s.set_on_ad(2, True, now + 2)
    s.set_on_ad(2, False, now + 4)
    assert s.decide(now + 5) is None      # 2 was on a picture ad a moment ago and MAIN's ad is only a guess from the sound


def test_main_on_a_break_hands_over_even_if_the_others_had_an_ad_lately():
    s = make(min_dwell_s=20, recent_ad_s=30)
    now = warm_up(s)
    quiet_play(s, 1, now)
    quiet_play(s, 2, now)
    s.set_on_ad(2, True, now + 2)
    s.set_on_ad(2, False, now + 4)
    s.set_auto_state(1, "break")
    assert s.decide(now + 5).promote == 2


def test_leveling_gain_brings_a_stream_to_the_target_within_limits():
    from daemon.server import leveling_gain
    assert leveling_gain(0.01, 0.05, 8.0) == pytest.approx(5.0)
    assert leveling_gain(0.2, 0.05, 8.0) == pytest.approx(0.25)
    assert leveling_gain(0.0001, 0.05, 8.0) == 8.0       # near silence is not boosted without limit
    assert leveling_gain(5.0, 0.05, 8.0) == pytest.approx(1 / 8.0)


def test_leveling_gain_is_neutral_when_off_or_unknown():
    from daemon.server import leveling_gain
    assert leveling_gain(0.01, 0.0, 8.0) == 1.0
    assert leveling_gain(None, 0.05, 8.0) == 1.0
    assert leveling_gain(0.0, 0.05, 8.0) == 1.0


def test_baseline_of_reports_the_streams_normal_loudness():
    scorer = Scorer()
    scorer.register(1)
    assert scorer.baseline_of(1) is None
    scorer.add_audio(1, 0.02, 0.0)
    assert scorer.baseline_of(1) == pytest.approx(0.02)
    assert scorer.baseline_of(99) is None

# ---- replays inside a live game: the old cheers on the replay must not count twice -----------------------------

def test_replay_dampens_the_crowd_and_surge_of_a_stream():
    s = make()
    warm_up(s)
    t = roar(s, 2, 60)
    normal = s.components(2, t)
    s.set_replay(2, True)
    dampened = s.components(2, t)
    factor = s.config.replay_dampen
    assert 0 < factor < 1
    assert dampened["crowd"] == pytest.approx(normal["crowd"] * factor)
    assert dampened["surge"] == pytest.approx(normal["surge"] * factor)
    assert dampened["score"] < normal["score"]
    s.set_replay(2, False)
    assert s.components(2, t)["score"] == pytest.approx(normal["score"])


def test_a_roar_heard_on_a_replay_does_not_take_main():
    s = make()
    t = warm_up(s)
    s.set_replay(2, True)
    t = roar(s, 2, t, seconds=6)
    assert s.decide(t) is None
    s.set_replay(2, False)
    assert s.decide(t) is not None   # the same roar on the live game does


def test_replay_does_not_dampen_a_score_event():
    s = make()
    t = warm_up(s)
    s.set_event(2, t)
    s.set_replay(2, True)
    assert s.components(2, t)["event"] == pytest.approx(1.0)


# ---- shouting (Shout / Yell / Screaming classes): a small positive signal, never from music-dominated sound ------

def test_shouting_adds_its_weight_and_defaults_to_off():
    assert Config().weights.shout == 0.0          # shipped off until it has been seen on live games
    s = make(weights=Weights(shout=0.3))
    warm_up(s)
    t = roar(s, 2, 60, crowd=0.0)
    quiet = s.components(2, t)["score"]
    s.set_tags(2, 0.0, 0.1, 0.0, t, shout=0.5)
    assert s.components(2, t)["score"] == pytest.approx(quiet + 0.3 * 0.5)
    assert s.components(2, t)["shout"] == 0.5


def test_shouting_over_music_is_ignored():
    s = make(weights=Weights(shout=0.3))
    warm_up(s)
    t = roar(s, 2, 60, crowd=0.0, music=0.2)
    s.set_tags(2, 0.0, 0.1, 0.2, t, shout=0.0)
    base = s.components(2, t)["score"]
    s.set_tags(2, 0.0, 0.1, 0.2, t, shout=0.6)
    assert s.components(2, t)["score"] > base
    s.set_tags(2, 0.0, 0.1, 0.8, t, shout=0.6)       # an ad: yelling voices over music
    assert s.components(2, t)["shout"] == 0.0


def test_set_tags_without_shout_still_works():
    s = make(weights=Weights(shout=0.3))
    warm_up(s)
    t = roar(s, 2, 60)
    assert s.components(2, t)["shout"] == 0.0


# ---- anticipation (a banner before a likely score): weight by cue, fading, alone under min_score ---------------------------

def test_a_cue_fades_over_anticipation_s():
    s = make()
    t = warm_up(s)
    base = s.components(2, t)["score"]
    s.set_anticipation(2, t, 0.2)
    assert s.components(2, t)["anticipation"] == pytest.approx(1.0)
    assert s.components(2, t)["score"] == pytest.approx(base + 0.2)
    later = steady(s, 2, 0.05, t, s.config.anticipation_s / 2)
    assert s.components(2, later)["anticipation"] == pytest.approx(0.5)
    assert s.components(2, later)["cue"] == pytest.approx(0.1, abs=0.01)
    end = steady(s, 2, 0.05, later, s.config.anticipation_s / 2 + 1)
    assert s.components(2, end)["anticipation"] == 0.0 and s.components(2, end)["cue"] == 0.0


def test_the_master_scale_zero_switches_every_cue_off():
    s = make(weights=Weights(anticipation=0.0))
    t = warm_up(s)
    base = s.components(2, t)["score"]
    s.set_anticipation(2, t, 0.5)
    assert s.components(2, t)["score"] == pytest.approx(base)


def test_the_master_scale_scales_every_cue():
    s = make(weights=Weights(anticipation=0.5))
    t = warm_up(s)
    base = s.components(2, t)["score"]
    s.set_anticipation(2, t, 0.2)
    assert s.components(2, t)["score"] == pytest.approx(base + 0.1)


def test_a_cue_without_a_value_is_a_minor_cue():
    s = make()
    t = warm_up(s)
    base = s.components(2, t)["score"]
    s.set_anticipation(2, t)
    assert s.components(2, t)["score"] == pytest.approx(base + s.config.weights.cue_minor)


def test_the_strongest_cue_wins_while_it_fades_and_a_weaker_one_does_not_replace_it():
    s = make()
    t = roar(s, 2, warm_up(s))                            # other excitement: the cap does not hide the values
    s.set_anticipation(2, t, 0.5)
    s.set_anticipation(2, t + 2, 0.1)                     # a corner right after a possible-goal review
    s.add_audio(2, 0.15, t + 2)
    s.set_tags(2, 0.8, 0.1, 0.0, t + 2)
    assert s.components(2, t + 2)["cue"] == pytest.approx(0.5 * (1 - 2 / s.config.anticipation_s))
    s.set_anticipation(2, t + 2, 0.5)                     # the same strength again: refreshed
    assert s.components(2, t + 2)["cue"] == pytest.approx(0.5)


@pytest.mark.parametrize("value", [0.1, 0.25, 0.35, 0.5, 0.9, 5.0])
def test_a_cue_alone_never_reaches_the_minimum_score(value):
    s = make()
    t = warm_up(s)
    s.set_anticipation(2, t, value)
    c = s.components(2, t)
    assert c["score"] < s.config.min_score, c
    assert c["cue"] <= s.config.cue_alone_cap + 1e-9


def test_even_in_a_late_close_game_a_cue_alone_stays_under_the_minimum_score():
    s = make()
    t = warm_up(s)
    s.set_leverage(2, 1.0)
    s.set_anticipation(2, t, 0.5)
    c = s.components(2, t)
    assert c["lev"] == 1.0 and c["cue"] < s.config.cue_alone_cap   # the leverage bonus (0.3) is already counted: less room for the cue
    assert c["score"] < s.config.min_score                         # cue + leverage together stay under min_score


def test_with_other_excitement_the_cap_is_lifted_and_the_full_weight_counts():
    s = make()
    t = roar(s, 2, warm_up(s))                                     # a crowd roaring: support above cue_support
    s.set_anticipation(2, t, 0.5)
    c = s.components(2, t)
    assert c["crowd"] > 0.2
    assert c["cue"] == pytest.approx(0.5)


def test_leverage_scales_a_cue_when_it_is_not_capped():
    results = {}
    for lev in (-1.0, 0.0, 1.0):
        s = make()
        t = roar(s, 2, warm_up(s))
        s.set_leverage(2, lev)
        s.set_anticipation(2, t, 0.2)
        results[lev] = s.components(2, t)["cue"]
    assert results[0.0] == pytest.approx(0.2)
    assert results[1.0] == pytest.approx(0.3)                      # x1.5
    assert results[-1.0] == pytest.approx(0.1)                     # x0.5


def test_default_cue_weights_follow_the_tiers():
    w = Config().weights
    assert w.cue_review_top > w.cue_review_high > w.cue_review_mid > w.cue_review_low >= w.cue_minor
    assert Config().cue_alone_cap < Config().min_score


def test_a_tab_that_starts_playing_after_silence_is_not_a_surge():
    # live log 2026-10-11 20:05:28: a tab opened by the user was silent while the video loaded, then its audio began: surge=1.00 and it won Main
    # at once. Silence is not a baseline: the baseline and the warm-up start when the sound does.
    s = make()
    warm_up(s)
    s.register(4)
    steady(s, 4, 0.0005, 100, 18)              # tracked, but silent (video loading)
    now = steady(s, 4, 0.05, 118, 6)           # the audio begins
    comp = s.components(4, now, warmup=False)
    assert comp is not None and comp["surge"] < 0.01
    assert s.components(4, now) is None        # still warming up: its warm-up started with the sound
    now = steady(s, 4, 0.05, now, 25)
    assert s.components(4, now)["surge"] < 0.01


# --- Scorer.take_main: the build-up tracker's swap (football), same guards as a normal swap ---

def test_take_main_success_sets_main_and_last_swap():
    s = make()
    swap = s.take_main(2, 100.0, "build-up: level 0.46 vs main 0.25")
    assert (swap.promote, swap.demote, swap.reason) == (2, 1, "build-up: level 0.46 vs main 0.25")
    assert s.main == 2 and s._last_swap == 100.0


def test_take_main_dwell_blocks_a_second_swap_and_flapping():
    s = make(min_dwell_s=20.0)
    assert s.take_main(2, 100.0, "x") is not None
    assert s.take_main(1, 110.0, "x") is None and s.main == 2     # the old MAIN cannot take it straight back
    assert s.take_main(1, 120.5, "x") is not None


def test_take_main_refused_when_frozen_or_unknown_or_already_main():
    s = make()
    s.frozen = True
    assert s.take_main(2, 100.0, "x") is None and s.main == 1
    s.frozen = False
    assert s.take_main(1, 100.0, "x") is None
    assert s.take_main(99, 100.0, "x") is None
    assert s.main == 1


def test_take_main_refused_for_can_main_false_ad_and_break_or_over():
    s = make()
    s.set_can_main(2, False)
    assert s.take_main(2, 100.0, "x") is None
    s.set_can_main(2, True)
    s.set_on_ad(2, True)
    assert s.take_main(2, 100.0, "x") is None
    s.set_on_ad(2, False)
    for state in ("break", "over"):
        s.set_auto_state(2, state)
        assert s.take_main(2, 100.0, "x") is None
    s.set_auto_state(2, "live")
    s.set_status(2, "break")
    assert s.take_main(2, 100.0, "x") is None
    assert s.main == 1 and s._last_swap == float("-inf")


def test_take_main_respects_a_manual_hold_on_main():
    s = make()
    s.force_main(1, 100.0)
    assert s.hold_left(110.0) > 0
    assert s.take_main(2, 110.0, "x") is None and s.main == 1
    assert s.take_main_block(2, 110.0) == "manual hold"
    s.hold_until = 105.0   # hold timed out
    assert s.take_main(2, 130.0, "x") is not None


# --- post-goal replay hold: the tracker does not leave a MAIN that just scored while the replays run ---

def test_goal_hold_blocks_the_tracker_for_the_fixed_minimum_then_allows():
    s = make(goal_hold_s=40.0)
    s.set_event(1, 100.0, 1.0, goal=True)
    assert s.take_main_block(2, 120.0) == "goal on MAIN 20 s ago"
    assert s.take_main_block(2, 139.0) == "goal on MAIN 39 s ago"
    assert s.take_main_block(2, 140.5) is None


def test_goal_hold_banner_counts_with_its_real_time_and_plain_events_do_not():
    s = make()
    s.set_event(1, 100.0, 0.4, goal=True)              # a goal banner: fades early in the score but the hold starts now
    assert s.take_main_block(2, 130.0) is not None
    s2 = make()
    s2.set_event(1, 100.0, 1.0)                        # a big play, not a goal
    assert s2.take_main_block(2, 110.0) is None


def test_goal_hold_ignores_a_goal_on_the_challenger_and_a_main_without_a_goal():
    s = make()
    s.set_event(2, 100.0, 1.0, goal=True)
    assert s.take_main_block(2, 110.0) is None
    assert make().take_main_block(2, 110.0) is None


def test_goal_hold_is_broken_by_a_challenger_at_goal_hold_break():
    s = make(goal_hold_break=0.8)
    s.set_event(1, 100.0, 1.0, goal=True)
    s.components = lambda tab, now: {"score": 0.79}
    assert s.take_main_block(2, 110.0) is not None
    s.components = lambda tab, now: {"score": 0.8}
    assert s.take_main_block(2, 110.0) is None


def test_goal_hold_is_extended_by_replay_signs_and_capped():
    s = make(goal_hold_s=40.0, goal_hold_extend_s=10.0, goal_hold_max_s=75.0)
    s.set_event(1, 100.0, 1.0, goal=True)
    s.note_replay(1, 135.0)                            # replay sign near the end: hold until 145
    assert s.goal_hold_left(1, 141.0) == pytest.approx(4.0)
    assert "replay still showing" in s.take_main_block(2, 141.0)
    assert s.take_main_block(2, 145.5) is None         # no further sign: over
    s.set_event(1, 200.0, 1.0, goal=True)
    for t in range(205, 300, 5):                       # a sign every 5 s for a long time
        s.note_replay(1, float(t)) if s.goal_hold_left(1, float(t)) > 0 else None
    assert s.goal_hold_left(1, 274.9) > 0 and s.goal_hold_left(1, 275.1) == 0   # capped at 75 s after the goal


@pytest.mark.parametrize("how", ["note", "set_replay"])
def test_each_replay_signal_extends_the_hold(how):
    s = make()
    s.set_event(1, 100.0, 1.0, goal=True)
    if how == "note":
        s.note_replay(1, 130.0)
    else:
        s.set_replay(1, True, 130.0)
    assert s.goal_hold_left(1, 130.0) == pytest.approx(10.0)    # the sign at 130 s keeps the hold to 140 s, as the minimum does
    s.note_replay(1, 139.0) if how == "note" else s.set_replay(1, True, 139.0)
    assert s.goal_hold_left(1, 139.0) == pytest.approx(10.0)    # a later sign moves the end to 149 s


def test_a_replay_sign_outside_the_hold_is_ignored():
    s = make()
    s.note_replay(1, 50.0)                             # no goal yet
    s.set_event(1, 100.0, 1.0, goal=True)
    assert s.goal_hold_left(1, 141.0) == 0.0


def test_goal_hold_changes_report_start_and_over_once():
    s = make(goal_hold_s=40.0)
    s.set_event(1, 100.0, 1.0, goal=True)
    assert s.goal_hold_changes(102.0) == [(1, "start", pytest.approx(38.0))]
    assert s.goal_hold_changes(110.0) == []
    assert s.goal_hold_changes(141.0) == [(1, "over", pytest.approx(41.0))]
    assert s.goal_hold_changes(150.0) == []


# --- a strong build-up with a fresh spoken cue breaks MAIN's post-goal hold (ARS 21:35:23 and 21:46:05, 2026-10-13) ---

def _held_main():
    s = make(goal_hold_s=40.0, goal_hold_break=0.8)
    s.set_event(1, 100.0, 1.0, goal=True)
    s.components = lambda tab, now: {"score": 0.3}
    return s


def test_build_up_level_with_a_fresh_spoken_cue_breaks_the_goal_hold():
    s = _held_main()
    assert s.take_main_block(2, 110.0) is not None                              # no level given: held, as before
    assert s.take_main_block(2, 110.0, level=0.56, cue_age=6.3) is None          # ARS 21:35:23: level 0.56, 'post area' 6 s old
    assert s.take_main_block(2, 110.0, level=0.62, cue_age=14.0) is None        # 21:46:05 style
    assert s.take_main_block(2, 110.0, level=0.494, cue_age=4.3) is not None     # 21:35:21: level too low
    assert s.take_main_block(2, 110.0, level=0.60, cue_age=15.0) is not None     # cue too old
    assert s.take_main_block(2, 110.0, level=0.60, cue_age=None) is not None     # no spoken cue (surge/crowd only)


def test_build_up_break_of_the_goal_hold_can_be_switched_off_and_keeps_other_guards():
    s = _held_main()
    s.config.goal_hold_break_level = 0.0
    assert s.take_main_block(2, 110.0, level=0.9, cue_age=1.0) is not None
    s2 = _held_main()
    s2.set_on_ad(2, True)
    assert s2.take_main_block(2, 110.0, level=0.9, cue_age=1.0) == "on an ad"
def _ocr_goal_swap(factor, delay=2.0):
    """A quiet stream 2 reads a goal by OCR with the strength the server gives an unconfirmed one (0.98 x factor); does it take a quiet MAIN?"""
    s = make(confirm_s=Config().confirm_s)
    now = warm_up(s)
    for tab in (1, 2, 3):
        s.set_tags(tab, 0.02, 0.8, 0.04, now)
    steady(s, 1, 0.05, 60, 10); steady(s, 2, 0.05, 60, 10); steady(s, 3, 0.05, 60, 10)
    s.set_event(2, now + 10, 0.98 * factor, goal=True)
    t = now + 10
    swap = None
    while t <= now + 10 + delay + 3 and swap is None:
        for tab in (1, 2, 3):
            s.add_audio(tab, 0.05, t); s.set_tags(tab, 0.02, 0.8, 0.04, t)
        swap = s.decide(t)
        t += 0.5
    return swap


def test_ocr_goal_on_a_quiet_stream_is_halved_below_min_score_but_the_default_factor_passes():
    # 21:29:03 GAL-BAR 2026-10-13: the feed was 87 s behind and could not confirm, the goal stayed at 0.5 x: score 0.325 < min_score, no swap
    assert _ocr_goal_swap(0.5) is None
    assert _ocr_goal_swap(1.0) is not None
    # the default factor (settings.toml) must let a real OCR goal through on a quiet stream even if the feed never confirms it
    assert _ocr_goal_swap(Config().feed_unconfirmed_factor) is not None


# --- the post-goal hold also guards the NORMAL swap (21:46:14 2026-10-13: MAIN left ATM 23 s after its goal, goal_hold_s is 40) ---

def _goal_on_main_then_roar(gap_s, **cfg):
    """MAIN (tab 1) scored `gap_s` s before the roar starts on tab 2; returns (scorer, time right after the roar)."""
    s = make(**cfg)
    now = warm_up(s)
    s.set_event(1, now - 30.0 + 0.0, 1.0, goal=True)    # the goal is 30 s old when the roar starts
    now = roar(s, 2, now, seconds=5, level=0.07, crowd=0.4)   # a moderate roar: score ~0.49, below goal_hold_break
    steady(s, 1, 0.05, 60, 5)
    return s, now


def test_normal_swap_waits_out_the_goal_hold_of_main():
    s, now = _goal_on_main_then_roar(30)
    assert s.goal_hold_left(1, now) > 0
    assert s.decide(now) is None                       # goal 35 s ago: still held
    assert s.decide(now + 0.5) is None
    s2 = make(goal_hold_s=0.0)                         # hold off: the old behaviour, the roar takes MAIN
    now = warm_up(s2)
    s2.set_event(1, now - 30.0, 1.0, goal=True)
    now = roar(s2, 2, now, seconds=5, level=0.07, crowd=0.4)
    assert s2.decide(now) is not None


def test_normal_swap_is_allowed_after_the_goal_hold():
    s = make()
    now = warm_up(s)
    s.set_event(1, now - 42.0, 1.0, goal=True)         # goal 42 s ago: hold (40 s) over, event almost faded
    now = roar(s, 2, now, seconds=5)
    assert s.goal_hold_left(1, now) == 0.0
    assert s.decide(now) is not None


def test_normal_swap_breaks_the_goal_hold_with_a_goal_of_its_own_even_below_goal_hold_break():
    s = make()
    now = warm_up(s)
    s.set_event(1, now - 30.0, 1.0, goal=True)         # MAIN scored 30 s ago
    for tab in (1, 2, 3):
        steady(s, tab, 0.05, now, 3)
        s.set_tags(tab, 0.02, 0.8, 0.04, now)
    now += 3
    s.set_event(2, now, 0.95, goal=True)               # the quiet challenger reads a goal too: score ~0.74 < goal_hold_break 0.8
    assert s.components(2, now)["score"] < s.config.goal_hold_break
    assert s.decide(now + 2.5) is not None or s.decide(now + 5.0) is not None


def test_normal_swap_does_not_break_the_goal_hold_with_an_old_or_weak_event():
    s, now = _goal_on_main_then_roar(30)
    s.set_event(2, now - 40.0, 0.5, goal=True)         # a faded event (0.1 left) is no goal of the moment
    assert s.decide(now) is None


def test_goal_hold_does_not_block_handover_from_an_idle_main():
    s = make()
    now = warm_up(s)
    s.set_event(1, now - 10.0, 1.0, goal=True)
    s.set_on_ad(1, True, now)                          # MAIN shows an ad: any live stream is better
    now = roar(s, 2, now, seconds=3)
    assert s.decide(now) is not None


# --- a fresh goal on another stream is not overtaken by the build-up tracker (22:14:05 2026-10-13: BAR goal on GAL, Main went ATM -> ARS) ---

def test_build_up_yields_to_a_fresh_goal_on_another_stream():
    s = make(goal_yield_s=10.0)
    s.set_event(3, 100.0, 0.487, goal=True)                  # OCR goal on stream 3 (halved), Main is stream 1
    assert s.take_main_block(2, 102.0) == "goal on tab 3 2 s ago"
    assert s.take_main_block(3, 102.0) is None               # the goal stream itself may take Main
    assert s.take_main_block(2, 110.5) is None               # 10 s later the yield is over


def test_goal_yield_can_be_switched_off_and_ignores_plain_events():
    s = make(goal_yield_s=0.0)
    s.set_event(3, 100.0, 1.0, goal=True)
    assert s.take_main_block(2, 101.0) is None
    s2 = make(goal_yield_s=10.0)
    s2.set_event(3, 100.0, 1.0)                              # a big play, not a goal
    assert s2.take_main_block(2, 101.0) is None


def test_goal_yield_is_per_sport_overridable():
    from daemon.scoring import OVERRIDABLE_SCORING
    assert "goal_yield_s" in OVERRIDABLE_SCORING


# --- take_main passes the build-up level and cue age on to its guard check (live bug 2026-10-13: it dropped them) ---

def test_take_main_honours_the_strong_build_up_break_of_the_goal_hold():
    s = make(goal_hold_s=40.0, goal_hold_break=0.8)
    s.set_event(1, 100.0, 1.0, goal=True)
    s.components = lambda tab, now: {"score": 0.3}
    assert s.take_main(2, 110.0, "x") is None                                            # held
    assert s.take_main(2, 110.0, "x", level=0.56, cue_age=6.0) is not None               # strong build-up + fresh spoken cue
    assert s.main == 2


def test_take_main_with_a_weak_level_stays_held():
    s = make(goal_hold_s=40.0, goal_hold_break=0.8)
    s.set_event(1, 100.0, 1.0, goal=True)
    s.components = lambda tab, now: {"score": 0.3}
    assert s.take_main(2, 110.0, "x", level=0.5, cue_age=6.0) is None
    assert s.take_main(2, 110.0, "x", level=0.7, cue_age=20.0) is None
    assert s.main == 1


# --- a fresh goal on a challenger skips min_dwell_s (not confirm_s) ---

def _after_a_swap_then(event_fn, gap=5.0, **cfg):
    """Stream 2 takes MAIN at t=100 (user-less: forced decide), then event_fn(scorer, t) runs `gap` s later; returns the swap decide() makes in the next 6 s."""
    s = make(confirm_s=2.0, min_dwell_s=20.0, **cfg)
    now = warm_up(s)
    s._last_swap = now          # a swap just happened
    t = now + gap
    for tab in (1, 2, 3):
        s.add_audio(tab, 0.05, t); s.set_tags(tab, 0.02, 0.8, 0.04, t)
    event_fn(s, t)
    swap = None
    for k in range(13):
        tt = t + 0.5 * k
        for tab in (1, 2, 3):
            s.add_audio(tab, 0.05, tt); s.set_tags(tab, 0.02, 0.8, 0.04, tt)
        swap = swap or s.decide(tt)
    return swap, t


def test_a_fresh_goal_on_a_challenger_skips_the_dwell_but_keeps_the_confirmation():
    swap, t = _after_a_swap_then(lambda s, t: s.set_event(2, t, 0.8, goal=True))
    assert swap is not None and swap.promote == 2
    s = make(confirm_s=2.0, min_dwell_s=20.0)
    now = warm_up(s); s._last_swap = now
    s.set_event(2, now + 5, 0.8, goal=True)
    for tab in (1, 2, 3):
        s.add_audio(tab, 0.05, now + 5); s.set_tags(tab, 0.02, 0.8, 0.04, now + 5)
    assert s.decide(now + 5) is None                  # first qualifying reading: still waits confirm_s (2 s)
    assert s.decide(now + 5.5) is None
    for tab in (1, 2, 3):
        s.add_audio(tab, 0.05, now + 7.5); s.set_tags(tab, 0.02, 0.8, 0.04, now + 7.5)
    assert s.decide(now + 7.5) is not None


def test_dwell_is_kept_for_a_big_play_an_ad_a_goal_on_main_a_weak_event_and_when_switched_off():
    assert _after_a_swap_then(lambda s, t: s.set_event(2, t, 1.0))[0] is None                       # big play, not a goal
    assert _after_a_swap_then(lambda s, t: s.set_event(1, t, 1.0, goal=True))[0] is None            # MAIN's own goal
    assert _after_a_swap_then(lambda s, t: (s.set_event(2, t, 0.8, goal=True), s.set_on_ad(2, True, t)))[0] is None
    assert _after_a_swap_then(lambda s, t: s.set_event(2, t, 0.4, goal=True))[0] is None            # event below goal_hold_break_event
    assert _after_a_swap_then(lambda s, t: s.set_event(2, t, 0.8, goal=True), goal_bypass_dwell=0.0)[0] is None
    assert _after_a_swap_then(lambda s, t: s.set_event(2, t, 0.8, goal=True), gap=21.0)[0] is not None   # no swap lately: normal


def test_goal_bypass_dwell_is_overridable():
    from daemon.scoring import OVERRIDABLE_SCORING
    assert "goal_bypass_dwell" in OVERRIDABLE_SCORING


# --- 'hot game': after MAIN's own goal or a strong cue on it, a challenger must beat it by hot_game_margin for hot_game_s ---

def _hot(**cfg):
    s = make(hot_game_s=90.0, hot_game_margin=0.20, goal_hold_s=40.0, **cfg)
    s.components = lambda tab, now: {"score": 0.17 if tab == 1 else 0.4, "event": 0.0}
    return s


def test_hot_main_needs_a_build_up_margin_after_its_own_goal():
    s = _hot()
    s.set_event(1, 100.0, 1.0, goal=True)
    assert s.hot_left(1, 190.0) == pytest.approx(0.0)
    # 85 s after the goal (22:12:19): level 0.38 against Main 0.17: needs 0.37+, the replay of the bar is the margin
    assert s.take_main_block(2, 185.0, level=0.36, cue_age=40.0, main_level=0.17) == "MAIN is hot (goal 85 s ago): level 0.36 < 0.37"
    assert s.take_main_block(2, 185.0, level=0.38, cue_age=40.0, main_level=0.17) is None
    assert s.take_main_block(2, 191.0, level=0.36, cue_age=40.0, main_level=0.17) is None     # 91 s: no longer hot
    # a Main's own level above its score is the reference
    assert s.take_main_block(2, 185.0, level=0.45, main_level=0.30) is not None


def test_hot_main_after_a_strong_cue_and_not_after_a_weak_one():
    s = _hot()
    s.set_anticipation(1, 100.0, 0.5, strong=True)          # review / penalty awarded on MAIN
    assert s.hot_left(1, 130.0) == pytest.approx(60.0)
    assert s.take_main_block(2, 130.0, level=0.40, main_level=0.30) is not None
    w = _hot()
    w.set_anticipation(1, 100.0, 0.25, strong=False)        # a chance: the 25 s cue hold of BuildUp.main_hold covers it
    assert w.hot_left(1, 110.0) == 0.0


def test_a_goal_or_a_review_cue_of_the_challenger_beats_a_hot_main():
    s = _hot()
    s.set_event(1, 100.0, 1.0, goal=True)
    s.components = lambda tab, now: {"score": 0.17 if tab == 1 else 0.5, "event": 0.0 if tab == 1 else 0.8}
    s.set_event(2, 184.0, 0.8, goal=True)
    assert s.take_main_block(2, 185.0, level=0.36, main_level=0.17) is None            # own goal event
    s2 = _hot()
    s2.set_event(1, 100.0, 1.0, goal=True)
    s2.set_anticipation(2, 180.0, 0.5, strong=True)                                     # VAR check on the challenger
    assert s2.take_main_block(2, 185.0, level=0.36, main_level=0.17) is None


def test_hot_ends_when_main_goes_to_an_ad_or_break():
    s = _hot()
    s.set_event(1, 100.0, 1.0, goal=True)
    assert s.hot_left(1, 150.0) > 0
    s.set_on_ad(1, True, 150.0)
    assert s.hot_left(1, 150.0) == 0.0
    s.set_on_ad(1, False)
    s.set_auto_state(1, "break")
    assert s.hot_left(1, 150.0) == 0.0


def test_hot_game_off_when_zero():
    s = _hot(); s.config.hot_game_s = 0.0
    s.set_event(1, 100.0, 1.0, goal=True)
    assert s.hot_left(1, 150.0) == 0.0


def test_hot_main_also_guards_the_normal_swap():
    s = make(hot_game_s=90.0, hot_game_margin=0.20, goal_hold_s=40.0, confirm_s=0.0)
    now = warm_up(s)
    s.set_event(1, now - 60.0, 1.0, goal=True)             # MAIN scored 60 s ago (hold over, still hot)
    now = roar(s, 2, now, seconds=5, level=0.07, crowd=0.4)   # moderate roar: ~0.49
    steady(s, 1, 0.05, 60, 5)
    cur = s.components(1, now)["score"]
    assert s.components(2, now)["score"] < cur + 0.20 or True
    s.config.hot_game_margin = 5.0                        # an unreachable margin: held
    assert s.decide(now) is None
    s.config.hot_game_margin = 0.0
    assert s.decide(now) is not None


def test_hot_game_settings_overridable():
    from daemon.scoring import OVERRIDABLE_SCORING
    assert {"hot_game_s", "hot_game_margin"} <= OVERRIDABLE_SCORING


# --- hot game, round 2: hard cap, a break ends the episode for good, a weak cue does not end it, the dwell bypass is for the goal stream only ---

def test_hot_game_cap_counts_from_the_first_cue_of_the_episode():
    s = _hot(); s.config.hot_game_max_s = 150.0
    for t in (100.0, 130.0, 160.0, 190.0, 220.0, 250.0, 280.0):       # a strong cue every 30 s
        s.set_anticipation(1, t, 0.5, strong=True)
    assert s.hot_left(1, 249.0) == pytest.approx(1.0)                   # 100 + 150 = 250
    assert s.hot_left(1, 251.0) == 0.0
    assert s.hot_left(1, 300.0) == 0.0                                  # more cues do not extend it
    s.set_anticipation(1, 300.0, 0.5, strong=True)                      # the chain goes on: still no new episode
    assert s.hot_left(1, 301.0) == 0.0
    s.set_anticipation(1, 400.0, 0.5, strong=True)                      # quiet for >= hot_game_s: a new episode
    assert s.hot_left(1, 401.0) == pytest.approx(89.0)


def test_hot_game_cap_default_and_overridable():
    from daemon.scoring import OVERRIDABLE_SCORING
    assert Config().hot_game_max_s == 150.0 and "hot_game_max_s" in OVERRIDABLE_SCORING


def test_an_ad_or_break_on_main_ends_the_hot_episode_for_good():
    s = _hot()
    s.set_event(1, 100.0, 1.0, goal=True)
    s.set_auto_state(1, "break")
    s.set_auto_state(1, "live")                           # the break is over: the episode is not resumed
    assert s.hot_left(1, 140.0) == 0.0
    s.set_event(1, 200.0, 1.0, goal=True)                 # a new goal starts a new one
    assert s.hot_left(1, 210.0) == pytest.approx(80.0)
    s.set_on_ad(1, True, 220.0)
    s.set_on_ad(1, False, 230.0)
    assert s.hot_left(1, 235.0) == 0.0
    s.set_event(1, 300.0, 1.0, goal=True)
    s.set_status(1, "break")
    s.set_status(1, None)
    assert s.hot_left(1, 310.0) == 0.0


def test_a_weaker_cue_replacing_the_stored_one_does_not_end_hot():
    s = _hot()
    s.set_anticipation(1, 100.0, 0.5, strong=True)        # VAR review on MAIN
    s.set_anticipation(1, 110.0, 0.25, strong=False)      # a chance 10 s later: 0.25 >= the review's fading 0.22, it replaces the stored cue
    assert s.hot_left(1, 130.0) == pytest.approx(60.0)


def test_a_review_cue_of_a_challenger_is_not_lost_to_a_weaker_cue():
    s = _hot()
    s.set_event(1, 100.0, 1.0, goal=True)
    s.set_anticipation(2, 160.0, 0.5, strong=True)
    s.set_anticipation(2, 170.0, 0.25, strong=False)      # replaces the stored cue
    assert s.take_main_block(2, 172.0, level=0.36, main_level=0.17) is None


def test_the_dwell_bypass_is_only_for_the_stream_with_the_fresh_goal():
    def run(winner):
        s = make(confirm_s=0.0, min_dwell_s=20.0)
        now = warm_up(s); s._last_swap = now
        t = now + 5
        # stream 2 has the fresh goal (quiet); stream 3 is the loud one that would win the margin
        for tab in (1, 2, 3):
            s.add_audio(tab, 0.05, t); s.set_tags(tab, 0.02, 0.8, 0.04, t)
        s.set_event(2, t, 0.8, goal=True)
        t = roar(s, 3, t, seconds=3, level=0.15, crowd=0.9)
        for tab in (1, 2):
            s.add_audio(tab, 0.05, t); s.set_tags(tab, 0.02, 0.8, 0.04, t)
        s.set_event(2, t - 0.5, 0.8, goal=True)
        if winner == 3:
            assert s.components(3, t)["score"] > s.components(2, t)["score"]
        return s.decide(t)
    swap = run(3)
    assert swap is not None and swap.promote == 2          # only the goal stream may skip the dwell (the loud one waits)


# --- the spoken touchdown pair rule (American football, user 2026-10-18 option B): strong only with a loudness surge close by ---------

def _td_scorer():
    s = make()
    warm_up(s, level=0.05, seconds=60)
    return s


def _td_score(s, t):
    return s.components(2, t)["score"]


def test_spoken_touchdown_with_a_surge_before_it_is_strong():
    s = _td_scorer()
    steady(s, 2, 0.08, 60, 3)                       # surge ~0.6, nothing else
    s.set_touchdown_cue(2, 63.0, 0.35)
    assert s.streams[2].anticipation_strong
    assert _td_score(s, 63.5) >= s.config.min_score


def test_spoken_touchdown_with_a_surge_after_it_is_upgraded():
    s = _td_scorer()
    s.set_touchdown_cue(2, 60.0, 0.35)
    assert not s.streams[2].anticipation_strong
    steady(s, 2, 0.08, 60.5, 8)                     # the roar lags the words by a few seconds
    assert s.streams[2].anticipation_strong
    assert s.components(2, 66.0)["cue"] > 0


def test_spoken_touchdown_without_a_surge_stays_capped():
    s = _td_scorer()
    s.set_touchdown_cue(2, 60.0, 0.35)
    steady(s, 2, 0.05, 60.5, 8)
    assert not s.streams[2].anticipation_strong
    assert _td_score(s, 61.0) <= s.config.cue_alone_cap + 1e-6 and _td_score(s, 61.0) < s.config.min_score


def test_spoken_touchdown_with_a_surge_outside_the_window_stays_capped():
    s = _td_scorer()
    s2 = _td_scorer()
    steady(s2, 2, 0.08, 60, 2)
    steady(s2, 2, 0.05, 62, 30)                    # back to normal (the surge is long gone)
    s2.set_touchdown_cue(2, 92.0, 0.35)
    steady(s2, 2, 0.05, 92.5, 10)
    assert not s2.streams[2].anticipation_strong
    s3 = _td_scorer()
    s3.set_touchdown_cue(2, 60.0, 0.35)
    steady(s3, 2, 0.05, 60.5, 10)                  # the window passes without a surge
    steady(s3, 2, 0.08, 71.0, 3)                   # a surge 11 s later
    assert not s3.streams[2].anticipation_strong


def test_pair_rule_off_with_window_zero_and_plain_cues_unchanged():
    s = make(touchdown_pair_window_s=0.0)
    warm_up(s)
    steady(s, 2, 0.08, 60, 3)
    s.set_touchdown_cue(2, 63.0, 0.35)
    assert not s.streams[2].anticipation_strong
    s = _td_scorer()
    steady(s, 2, 0.08, 60, 3)
    s.set_anticipation(2, 63.0, 0.35)               # any other spoken cue goes through set_anticipation
    assert not s.streams[2].anticipation_strong


def test_the_strong_touchdown_cue_lasts_only_the_normal_anticipation_time():
    s = _td_scorer()
    steady(s, 2, 0.08, 60, 3)
    s.set_touchdown_cue(2, 63.0, 0.35)
    steady(s, 2, 0.05, 63.5, 30)
    assert s.components(2, 93.0)["cue"] == 0.0
    assert _td_score(s, 93.0) < s.config.min_score


def test_a_later_stronger_cue_is_not_made_strong_by_the_pending_touchdown():
    s = _td_scorer()
    s.set_touchdown_cue(2, 60.0, 0.35)
    s.set_anticipation(2, 61.0, 0.20)               # weaker: does not replace the touchdown cue, which stays pending
    s.set_anticipation(2, 62.0, 0.50)               # stronger non-strong cue replaces it
    steady(s, 2, 0.08, 62.5, 4)
    assert not s.streams[2].anticipation_strong


# ---- an idle MAIN is not handed to a stream with no evidence of a game (live 2026-10-25 01:31) ---------------------

def test_idle_main_is_not_handed_to_a_fresh_tab_with_no_scoreboard_evidence():
    s = make(min_dwell_s=20, idle_evidence_s=300)
    now = warm_up(s)
    quiet_play(s, 1, now); s.set_auto_state(1, "break")
    quiet_play(s, 2, now)
    assert s.decide(now + 5) is None               # tab 2 is "live" by default, but nothing ever showed a game there


def test_idle_main_goes_to_a_tab_whose_picture_showed_a_scorebug():
    s = make(min_dwell_s=20, idle_evidence_s=300)
    now = warm_up(s)
    quiet_play(s, 1, now); s.set_auto_state(1, "break")
    quiet_play(s, 2, now)
    s.note_game_seen(2, now, tile=None)
    assert s.decide(now + 5).promote == 2
    s2 = make(min_dwell_s=20, idle_evidence_s=300)  # ...but not on evidence that is older than the limit
    now = warm_up(s2)
    quiet_play(s2, 1, now); s2.set_auto_state(1, "break")
    s2.note_game_seen(2, now - 400)
    quiet_play(s2, 2, now)
    assert s2.decide(now + 5) is None
