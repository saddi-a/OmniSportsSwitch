from daemon.game_state import GameTracker, combine


def run(tracker, start, seconds, clock_fn, texts=(), step=3.0):
    """Feed one reading every `step` seconds; clock_fn(t) returns the clock string or None."""
    state = None
    t = start
    while t < start + seconds:
        c = clock_fn(t)
        state = tracker.update(t, list(texts), [c] if c else [])
        t += step
    return state


def ticking(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def test_running_clock_is_live():
    g = GameTracker("football")
    assert run(g, 0, 120, ticking) == "live"


def test_soccer_clock_frozen_becomes_break_quickly():
    g = GameTracker("football")
    run(g, 0, 60, ticking)
    assert run(g, 60, 15, lambda t: "00:59") == "live"
    assert run(g, 75, 30, lambda t: "00:59") == "break"


def test_american_football_stopped_clock_between_plays_stays_live():
    g = GameTracker("American football")
    run(g, 0, 60, ticking)
    assert run(g, 60, 120, lambda t: "01:00") == "live"  # 2 minutes of stopped clock is normal
    assert run(g, 180, 150, lambda t: "01:00") == "break"  # but 4+ minutes is a real break


def test_clock_restarting_ends_the_break():
    g = GameTracker("football")
    run(g, 0, 30, ticking)
    run(g, 30, 60, lambda t: "00:29")
    assert g.state == "break"
    assert run(g, 90, 9, lambda t: ticking(t - 30)) == "live"


def test_half_time_caption_is_an_immediate_break():
    g = GameTracker("football")
    assert run(g, 0, 6, ticking, texts=["PSG", "MI-TEMPS"]) == "break"


def test_final_caption_means_over_even_with_clock_running():
    g = GameTracker("american football")
    assert run(g, 0, 6, ticking, texts=["FINAL"]) == "over"


def test_caption_memory_expires():
    g = GameTracker("football")
    run(g, 0, 6, ticking, texts=["HALF TIME"])
    assert run(g, 200, 30, ticking) == "live"


def test_no_clock_for_a_long_time_is_a_break():
    g = GameTracker("football")
    run(g, 0, 30, ticking)
    assert run(g, 30, 20, lambda t: None) == "live"   # a short gap (overlay) is tolerated
    assert run(g, 50, 60, lambda t: None) == "break"  # a long one is an ad or studio segment


def test_never_seeing_a_clock_becomes_break_after_the_grace_period():
    g = GameTracker("basketball")
    assert run(g, 0, 30, lambda t: None) == "live"
    assert run(g, 30, 60, lambda t: None) == "break"


def test_unknown_sport_uses_generic_rules():
    g = GameTracker(None)
    run(g, 0, 60, ticking)
    assert run(g, 60, 60, lambda t: "01:00") == "live"
    assert run(g, 120, 90, lambda t: "01:00") == "break"


def test_combine_tab_state_from_tiles():
    assert combine(["break", "live", "over"]) == "live"
    assert combine(["break", "over"]) == "break"
    assert combine(["over", "over"]) == "over"
    assert combine([]) == "live"


def test_caption_inside_a_sentence_is_ignored():
    g = GameTracker("football")
    assert run(g, 0, 30, ticking, texts=["Pret pour la mi-temps ?", "Final countdown", "PSG"]) == "live"


def test_caption_must_appear_on_consecutive_readings():
    g = GameTracker("football")
    g.update(0, ["FINAL"], ["00:01"])
    assert g.update(3, ["PSG"], ["00:04"]) == "live"  # appeared once only (e.g. a popup flashing past)
    g.update(6, ["FINAL"], ["00:07"])
    assert g.update(9, ["FINAL"], ["00:10"]) == "over"
