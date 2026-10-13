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
    assert not b.crowd_note_due(1, 0.0, 0.25, 0.25)              # a single reading does not fire
    assert b.crowd_note_due(1, 2.0, 0.25, 0.25)                  # the second one in a row does (>= threshold)
    b2 = BuildUp()
    assert not b2.crowd_note_due(1, 0.0, 0.24, 0.25) and not b2.crowd_note_due(1, 2.0, 0.24, 0.25)   # below: never


def test_crowd_note_gap_resets_the_run():
    b = BuildUp()
    assert not b.crowd_note_due(1, 0.0, 0.5, 0.25)
    assert not b.crowd_note_due(1, 2.0, 0.1, 0.25)               # a low reading in between
    assert not b.crowd_note_due(1, 4.0, 0.5, 0.25)               # starts again at 1
    assert b.crowd_note_due(1, 6.0, 0.5, 0.25)


def test_crowd_note_is_per_stream_and_once_per_cooldown():
    b = BuildUp(cooldown_s=30.0)
    b.crowd_note_due(1, 0.0, 0.5, 0.25)
    assert b.crowd_note_due(1, 2.0, 0.5, 0.25)
    assert not b.crowd_note_due(1, 4.0, 0.5, 0.25)               # cooldown
    assert not b.crowd_note_due(2, 4.0, 0.5, 0.25)               # stream 2 has its own run: first reading
    assert b.crowd_note_due(2, 6.0, 0.5, 0.25)
    assert b.crowd_note_due(1, 32.0, 0.5, 0.25)                  # cooldown passed
    b.remove(1)
    assert not b.crowd_note_due(1, 34.0, 0.5, 0.25)              # forgotten: the run starts over


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
