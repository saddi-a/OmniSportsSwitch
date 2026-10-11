"""Replay detected from the game clock: it goes back 10-30 s while the scorebug is on screen (DAZN shows no REPLAY text, but its
replays show the scorebug of the moment being replayed; seen live 2026-10-11, Como-Roma: 53:04 then 52:44)."""
from daemon.game_state import GameTracker
from daemon.scoring import Config

BUG = ["DA", "COM", "0-0", "ROM", "ZN"]


def mmss(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def run(g, readings):
    """readings: [(time, clock seconds or None)]. Returns [(time, note)] for the readings that carried a clock-replay note."""
    notes = []
    for t, c in readings:
        g.update(float(t), list(BUG), [mmss(c)] if c is not None else [])
        if g.clock_replay_note:
            notes.append((t, g.clock_replay_note))
    return notes


def running(start_t, start_clock, seconds, step=2.5, sign=1):
    """A clock that runs in real time (sign +1 counts up, -1 counts down), one reading every `step` seconds."""
    out, t = [], 0.0
    while t <= seconds:
        out.append((start_t + t, start_clock + sign * t))
        t += step
    return out


def test_a_clock_that_goes_back_twenty_seconds_and_keeps_running_is_a_replay():
    g = GameTracker("football")
    live = running(0, 3180, 20)                       # 53:00 up to 53:20
    assert run(g, live) == []
    replay = running(25, 3180 + 20 - 20, 10)          # the clock jumps back 20 s (to 53:00) and runs on
    notes = run(g, replay)
    assert [n[0] for _, n in notes] == ["start"] and 17 <= notes[0][1][1] <= 23     # about 20 s back
    assert g.replay is False                          # default: log only


def test_the_real_example_with_a_hidden_scorebug_in_between():
    g = GameTracker("football")
    live = [(0, 3174), (2.5, 3176), (5, 3179), (7.5, 3181), (10, 3184)]    # 52:54 ... 53:04
    assert run(g, live) == []
    notes = run(g, [(20, 3164), (22.5, 3167), (25, 3169)])
    assert [n[0] for _, n in notes] == ["start"] and 17 <= notes[0][1][1] <= 25


def test_a_countdown_clock_that_goes_up_twenty_seconds_is_a_replay():
    g = GameTracker("American football")
    assert run(g, running(0, 521, 20, sign=-1)) == []                 # 8:41 counting down to 8:21
    notes = run(g, running(25, 521 - 20 + 20, 10, sign=-1))           # back to 8:41 (up by 20 s) and down again
    assert [n[0] for _, n in notes] == ["start"]


def test_a_stopped_clock_is_not_a_replay():
    g = GameTracker("American football")
    run(g, running(0, 521, 20, sign=-1))
    assert run(g, [(25 + 2.5 * i, 500) for i in range(12)]) == []     # the clock stops between plays


def test_a_dvr_rewind_of_minutes_is_not_a_replay():
    g = GameTracker("football")
    run(g, running(0, 3180, 20))
    assert run(g, running(25, 3180 + 20 - 180, 15)) == []              # three minutes back, then running


def test_small_and_big_steps_back_are_ignored():
    for back in (5, 40):
        g = GameTracker("football")
        run(g, running(0, 3180, 20))
        assert run(g, running(25, 3180 + 20 - back, 10)) == [], back


def test_an_odd_clock_between_two_scorebug_gaps_then_the_live_clock_is_a_replay():
    g = GameTracker("football")
    run(g, [(0, 3174), (2.5, 3176), (5, 3179), (7.5, 3181), (10, 3184)])
    notes = run(g, [(20, 3164), (41, 3215), (43.5, 3217)])   # 52:44 alone after an 10 s gap, then the live clock (53:35) 21 s later
    assert [n[0] for _, n in notes] == ["start"] and 17 <= notes[0][1][1] <= 25


def test_a_note_belongs_to_one_reading():
    g = GameTracker("football")
    run(g, running(0, 3180, 20))
    assert run(g, running(25, 3180, 5))      # the replay is reported on one of these readings...
    g.update(40.0, list(BUG), [])        # a reading without a clock: nothing to report
    assert g.clock_replay_note is None


def test_one_odd_clock_reading_is_not_enough():
    g = GameTracker("football")
    run(g, running(0, 3180, 20))
    notes = run(g, [(25, 3180 + 20 - 20), (27.5, 3180 + 25)])        # one reading 20 s back, then the live clock again
    assert notes == []


def test_nothing_is_reported_before_the_direction_of_the_clock_is_known():
    g = GameTracker("football")
    assert run(g, [(0, 3180), (2.5, 3160), (5, 3162.5), (7.5, 3165)]) == []


def test_the_replay_flag_follows_the_setting_and_ends_when_the_live_clock_returns():
    g = GameTracker("football", Config(replay_by_clock_s=15.0))
    run(g, running(0, 3180, 20))
    run(g, running(25, 3180, 10))                                       # replay detected during these readings
    assert g.replay is True
    g.update(40.0, list(BUG), [mmss(3180 + 12)])                        # replay clock running on
    assert g.replay is True
    g.update(42.5, list(BUG), [mmss(3180 + 50)])                        # the live clock is back (jump forward)
    assert g.replay is False and g.clock_replay_note[0] == "end"


def test_the_replay_flag_times_out():
    g = GameTracker("football", Config(replay_by_clock_s=8.0))
    run(g, running(0, 3180, 20))
    run(g, running(25, 3180, 5))
    assert g.replay is True
    for i in range(6):                                                  # no live-clock return: only the timeout ends it
        g.update(35.0 + 2.5 * i, list(BUG), [mmss(3180 + 10 + 2.5 * i)])
    assert g.replay is False


def test_the_thresholds_are_settings():
    g = GameTracker("football", Config(replay_clock_min_s=3.0, replay_clock_max_s=8.0))
    run(g, running(0, 3180, 20))
    notes = run(g, running(25, 3180 + 20 - 5, 10))
    assert [n[0] for _, n in notes] == ["start"]
