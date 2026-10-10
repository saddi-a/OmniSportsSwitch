from daemon.scoring import Config, Scorer


def feed(scorer, tab_id, rms, start, seconds, step=0.5):
    t = start
    while t < start + seconds:
        scorer.add_audio(tab_id, rms, t)
        t += step
    return t


def make(**cfg):
    s = Scorer(Config(**cfg))
    s.register(1, is_main=True)
    s.register(2)
    s.register(3)
    return s


def test_no_swap_when_challenger_not_loud_enough():
    s = make()
    feed(s, 1, 0.10, 0, 10)
    feed(s, 2, 0.13, 0, 10)  # only +30%
    assert s.decide(10) is None


def test_swap_when_challenger_beats_threshold():
    s = make()
    feed(s, 1, 0.10, 0, 10)
    feed(s, 2, 0.15, 0, 10)  # +50%
    swap = s.decide(10)
    assert (swap.promote, swap.demote) == (2, 1)
    assert s.main == 2


def test_priority_weight_changes_outcome():
    s = make()
    s.set_weight(2, 2.0)
    feed(s, 1, 0.10, 0, 10)
    feed(s, 2, 0.08, 0, 10)  # quieter, but weighted x2 = 0.16
    assert s.decide(10).promote == 2


def test_picks_loudest_challenger():
    s = make()
    feed(s, 1, 0.05, 0, 10)
    feed(s, 2, 0.10, 0, 10)
    feed(s, 3, 0.20, 0, 10)
    assert s.decide(10).promote == 3


def test_min_dwell_blocks_immediate_second_swap():
    s = make(min_dwell_s=20)
    feed(s, 1, 0.10, 0, 10)
    feed(s, 2, 0.20, 0, 10)
    assert s.decide(10) is not None
    feed(s, 1, 0.50, 10, 5)  # old main now very loud
    assert s.decide(15) is None
    feed(s, 1, 0.50, 15, 10)
    assert s.decide(25) is None  # only 15s since the swap at t=10


def test_dwell_expires():
    s = make(min_dwell_s=20)
    feed(s, 1, 0.10, 0, 10)
    feed(s, 2, 0.20, 0, 10)
    s.decide(10)
    feed(s, 2, 0.05, 10, 25)
    feed(s, 1, 0.50, 10, 25)
    assert s.decide(35).promote == 1


def test_single_spike_is_smoothed_out():
    s = make(window_s=3.0)
    feed(s, 1, 0.10, 0, 10)
    feed(s, 2, 0.05, 0, 9)
    s.add_audio(2, 0.30, 9.5)  # one loud blip
    assert s.decide(10) is None


def test_silent_challenger_never_wins():
    s = make(min_score=0.01)
    feed(s, 1, 0.0, 0, 10)
    feed(s, 2, 0.005, 0, 10)
    assert s.decide(10) is None


def test_stale_stream_is_ignored():
    s = make(stale_s=5.0)
    feed(s, 1, 0.10, 0, 20)
    feed(s, 2, 0.50, 0, 5)  # went silent/disconnected at t=5
    assert s.decide(20) is None


def test_freeze_blocks_swaps():
    s = make()
    s.frozen = True
    feed(s, 1, 0.10, 0, 10)
    feed(s, 2, 0.50, 0, 10)
    assert s.decide(10) is None
    s.frozen = False
    assert s.decide(10) is not None


def test_no_main_registered_means_no_swap():
    s = Scorer()
    s.register(2)
    feed(s, 2, 0.5, 0, 10)
    assert s.decide(10) is None
