import pytest

from daemon.buildup import BuildUp


def test_cues_add_up_instead_of_replacing_each_other():
    b = BuildUp(window_s=40.0)
    b.note(1, 0.0, 0.25)
    b.note(1, 10.0, 0.25)
    # at 10 s: the first cue faded to 75%, the second is fresh
    assert b.level(1, 10.0, support=0.0) == pytest.approx(0.25 * 0.75 + 0.25)


def test_cues_fade_out_of_the_window():
    b = BuildUp(window_s=40.0)
    b.note(1, 0.0, 0.25)
    assert b.level(1, 39.0, 0.0) == pytest.approx(0.25 * 1 / 40)
    assert b.level(1, 40.0, 0.0) == 0.0
    assert b.cues(1, 41.0) == []


def test_support_counts_only_beside_a_cue():
    b = BuildUp()
    assert b.level(1, 5.0, support=0.6) == 0.0        # loudness alone is no build-up
    b.note(1, 0.0, 0.2)
    assert b.level(1, 0.0, support=0.6) == pytest.approx(0.2 + 0.6)


def test_streams_are_separate_and_removed_streams_forget():
    b = BuildUp()
    b.note(1, 0.0, 0.3)
    assert b.level(2, 0.0, 0.5) == 0.0
    b.remove(1)
    assert b.level(1, 0.0, 0.5) == 0.0


def test_would_take_needs_level_and_gap_over_main():
    b = BuildUp()
    b.note(1, 0.0, 0.25)
    assert b.would_take(1, 0.0, support=0.1, main_score=0.0, min_level=0.40, min_gap=0.15) is None      # level 0.35: too low
    assert b.would_take(1, 0.0, support=0.3, main_score=0.45, min_level=0.40, min_gap=0.15) is None     # 0.55 but only 0.10 above MAIN
    assert b.would_take(1, 0.0, support=0.3, main_score=0.20, min_level=0.40, min_gap=0.15) == pytest.approx(0.55)


def test_would_take_reports_once_per_cooldown():
    b = BuildUp(cooldown_s=30.0)
    b.note(1, 0.0, 0.5)
    assert b.would_take(1, 0.0, 0.0, 0.0, 0.40, 0.15) is not None
    assert b.would_take(1, 5.0, 0.0, 0.0, 0.40, 0.15) is None
    b.note(1, 33.0, 0.5)
    assert b.would_take(1, 33.0, 0.0, 0.0, 0.40, 0.15) is not None


def test_a_zero_cue_is_ignored():
    b = BuildUp()
    b.note(1, 0.0, 0.0)
    assert b.cues(1, 1.0) == []


def test_surge_note_fires_at_the_threshold_not_below_and_once_per_cooldown_per_stream():
    b = BuildUp(cooldown_s=30.0)
    assert not b.surge_note_due(1, 0.0, 0.79, 0.8)
    assert b.surge_note_due(1, 1.0, 0.8, 0.8)
    assert not b.surge_note_due(1, 20.0, 1.0, 0.8)     # inside the cooldown
    assert b.surge_note_due(2, 20.0, 1.0, 0.8)         # other stream is independent
    assert b.surge_note_due(1, 31.0, 1.0, 0.8)         # cooldown passed
    b.remove(1)
    assert b.surge_note_due(1, 32.0, 1.0, 0.8)


def test_corner_plus_chance_plus_surge_reaches_the_level_but_a_corner_alone_does_not():
    from daemon.cues import tracker_note_weight
    from daemon.scoring import Weights
    w = Weights()
    corner = tracker_note_weight("corner", "football", w, 1.0)
    b = BuildUp(window_s=45.0)
    b.note(1, 0.0, corner)
    assert b.level(1, 0.0, 0.10) < 0.40
    b.note(1, 0.0, w.cue_chance)
    assert b.level(1, 0.0, 0.10) == pytest.approx(0.10 + 0.25 + 0.10)
    assert b.level(1, 0.0, 0.10) >= 0.40


def test_crowd_note_needs_two_consecutive_readings_at_the_threshold():
    b = BuildUp(cooldown_s=30.0)
    assert not b.crowd_note_due(1, 0.0, 0.25, 0.25, readings=2)              # a single reading does not fire
    assert b.crowd_note_due(1, 2.0, 0.25, 0.25, readings=2)                  # the second one in a row does (>= threshold)
    b2 = BuildUp()
    assert not b2.crowd_note_due(1, 0.0, 0.24, 0.25, readings=2) and not b2.crowd_note_due(1, 2.0, 0.24, 0.25, readings=2)   # below: never


def test_crowd_note_gap_resets_the_run():
    b = BuildUp()
    assert not b.crowd_note_due(1, 0.0, 0.5, 0.25, readings=2)
    assert not b.crowd_note_due(1, 2.0, 0.1, 0.25, readings=2)               # a low reading in between
    assert not b.crowd_note_due(1, 4.0, 0.5, 0.25, readings=2)               # starts again at 1
    assert b.crowd_note_due(1, 6.0, 0.5, 0.25, readings=2)


def test_crowd_note_is_per_stream_and_once_per_cooldown():
    b = BuildUp(cooldown_s=30.0)
    b.crowd_note_due(1, 0.0, 0.5, 0.25, readings=2)
    assert b.crowd_note_due(1, 2.0, 0.5, 0.25, readings=2)
    assert not b.crowd_note_due(1, 4.0, 0.5, 0.25, readings=2)               # cooldown
    assert not b.crowd_note_due(2, 4.0, 0.5, 0.25, readings=2)               # stream 2 has its own run: first reading
    assert b.crowd_note_due(2, 6.0, 0.5, 0.25, readings=2)
    assert b.crowd_note_due(1, 32.0, 0.5, 0.25, readings=2)                  # cooldown passed
    b.remove(1)
    assert not b.crowd_note_due(1, 34.0, 0.5, 0.25, readings=2)              # forgotten: the run starts over


def test_would_take_without_cooldown_can_fire_again_until_marked():
    b = BuildUp(cooldown_s=30.0)
    b.note(1, 0.0, 0.5)
    assert b.would_take(1, 0.0, 0.0, 0.0, 0.32, 0.10, start_cooldown=False) is not None
    assert b.would_take(1, 2.0, 0.0, 0.0, 0.32, 0.10, start_cooldown=False) is not None   # not started
    b.mark_fired(1, 2.0)
    assert b.would_take(1, 4.0, 0.0, 0.0, 0.32, 0.10, start_cooldown=False) is None


def test_blocked_log_is_due_once_per_ten_seconds_per_stream():
    b = BuildUp()
    assert b.blocked_log_due(1, 0.0)
    assert not b.blocked_log_due(1, 9.0)
    assert b.blocked_log_due(2, 9.0)
    assert b.blocked_log_due(1, 10.0)


def test_spoken_age_is_the_age_of_the_youngest_spoken_cue():
    b = BuildUp(window_s=60.0)
    b.note(1, 0.0, 0.10, "surge_note")
    assert b.spoken_age(1, 5.0) is None
    b.note(1, 2.0, 0.20, "spoken:post area")
    b.note(1, 0.0, 0.25, "spoken:chance")
    b.note(1, 4.0, 0.5, "review")
    assert b.spoken_age(1, 6.0) == pytest.approx(4.0)       # post area at 2.0 (a banner review does not count as spoken)
    assert b.spoken_age(1, 70.0) is None
# ---- Main keeps its seat (2026-10-13): Main's own fresh cue / review holds it against a challenger ----
from daemon.scoring import Config


def _cfg(**kw):
    return Config(**kw)


def test_would_take_compares_with_the_larger_of_main_score_and_main_level():
    b = BuildUp(window_s=60.0)
    b.note(1, 0.0, 0.30)
    assert b.would_take(1, 0.0, 0.10, main_score=0.09, min_level=0.32, min_gap=0.10, start_cooldown=False) == pytest.approx(0.40)
    # Main's own build-up level 0.35 is the bar, not its plain score 0.09
    assert b.would_take(1, 0.0, 0.10, main_score=0.09, min_level=0.32, min_gap=0.10, start_cooldown=False, main_level=0.35) is None
    assert b.would_take(1, 0.0, 0.10, main_score=0.09, min_level=0.32, min_gap=0.10, start_cooldown=False, main_level=0.25) == pytest.approx(0.40)


def test_main_hold_after_main_own_spoken_cue():
    b = BuildUp(window_s=60.0)
    b.note(1, 0.0, 0.25, "spoken:chance")           # Main's chance cue
    cfg = _cfg()
    # 17 s later a challenger with 0.397 vs Main's level 0.26: gap 0.137 is not enough while the cue is fresh
    assert b.main_hold(1, 2, 17.0, 0.397, 0.26, cfg) is not None
    assert "17" in b.main_hold(1, 2, 17.0, 0.397, 0.26, cfg)
    # a clearly higher level takes it
    assert b.main_hold(1, 2, 17.0, 0.50, 0.26, cfg) is None
    # after the hold (25 s) the cue no longer protects
    assert b.main_hold(1, 2, 26.0, 0.397, 0.26, cfg) is None


def test_main_hold_ignores_small_notes_and_can_be_switched_off():
    b = BuildUp(window_s=60.0)
    b.note(1, 0.0, 0.10, "surge_note")
    b.note(1, 0.0, 0.10, "note:corner")
    assert b.main_hold(1, 2, 5.0, 0.36, 0.12, _cfg()) is None     # only minor notes: no hold
    b.note(1, 0.0, 0.25, "spoken:chance")
    assert b.main_hold(1, 2, 5.0, 0.36, 0.2, _cfg(buildup_hold_s=0.0)) is None


def test_main_hold_review_holds_longer_with_a_higher_margin():
    b = BuildUp(window_s=60.0)
    b.note(1, 0.0, 0.50, "review")                  # VAR check on Main
    cfg = _cfg()
    assert b.main_hold(1, 2, 28.0, 0.35, 0.29, cfg) is not None     # 28 s: cue hold (25 s) is over, the review hold is not
    assert "review" in b.main_hold(1, 2, 28.0, 0.35, 0.29, cfg)
    assert b.main_hold(1, 2, 28.0, 0.60, 0.29, cfg) is not None     # +0.31 is still below the review margin 0.35
    assert b.main_hold(1, 2, 28.0, 0.70, 0.29, cfg) is None
    assert b.main_hold(1, 2, 61.0, 0.35, 0.05, cfg) is None         # review hold over (60 s = the window)


def test_challenger_with_its_own_fresh_review_cue_takes_a_main_with_only_a_chance():
    b = BuildUp(window_s=60.0)
    b.note(1, 0.0, 0.25, "spoken:chance")           # Main: chance 9 s ago
    b.note(2, 1.0, 0.50, "review")                  # challenger: VAR check
    assert b.main_hold(1, 2, 9.0, 0.44, 0.32, _cfg()) is None
    # ...but not a Main that is itself under review
    b.note(1, 5.0, 0.50, "review")
    assert b.main_hold(1, 2, 9.0, 0.44, 0.32, _cfg()) is not None


def test_main_hold_without_cues_is_none():
    assert BuildUp().main_hold(1, 2, 10.0, 0.4, 0.0, _cfg()) is None
    assert BuildUp().main_hold(None, 2, 10.0, 0.4, 0.0, _cfg()) is None


def test_hold_settings_are_per_sport_overridable_and_in_the_layout():
    from daemon.scoring import OVERRIDABLE_SCORING
    for k in ("buildup_hold_s", "buildup_hold_cue_min", "buildup_hold_margin", "buildup_review_hold_s", "buildup_review_min", "buildup_review_margin"):
        assert k in OVERRIDABLE_SCORING


def test_crowd_note_default_is_three_readings_at_the_one_second_tick():
    b = BuildUp(cooldown_s=30.0)
    assert not b.crowd_note_due(1, 0.0, 0.5, 0.25) and not b.crowd_note_due(1, 1.0, 0.5, 0.25)
    assert b.crowd_note_due(1, 2.0, 0.5, 0.25)       # the same 2 s hold as two readings at the old 2 s tick


def test_the_default_build_up_level_to_take_main_is_038():
    # user decision 2026-10-18 (option A): 0.32 let weak surge notes flip-flop Main between two quiet football games
    from daemon.scoring import Config
    assert Config().buildup_min == pytest.approx(0.38)
