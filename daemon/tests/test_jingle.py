"""The channel goal-jingle matcher. The reference audio of the real jingle is never in the repo: these tests use a synthetic stand-in
(a repeated-note sting with the same shape: about 1.2 s of short notes, then silence) mixed into speech-like and crowd-like noise."""
import logging
import struct
import time

import numpy as np
import pytest
import scipy.signal as ss

from daemon.jingle import SAMPLE_RATE as SR, JingleMatcher, JingleWatch, build_template, load_template, save_template
from daemon.scoring import Config, Scorer
from daemon.server import Server

NOTES = (880, 1175, 880, 1175, 1319, 1175, 880, 1175, 1568, 1319, 1175, 1568, 1760, 1568)


def sting() -> np.ndarray:
    """A stand-in jingle: 14 short decaying notes 85 ms apart (about 1.2 s) followed by silence."""
    out = np.zeros(int(1.5 * SR), np.float32)
    for i, f in enumerate(NOTES):
        a = int(0.03 * SR) + i * int(0.085 * SR)
        t = np.arange(int(0.12 * SR)) / SR
        out[a:a + t.size] += (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * 2 * f * t)) * np.exp(-18 * t)
    return out


def other_sting() -> np.ndarray:
    """A different jingle-like arpeggio: other notes, other rhythm."""
    out = np.zeros(int(1.5 * SR), np.float32)
    for i, f in enumerate([523, 659, 784, 1047] * 6):
        a = int(0.05 * SR) + i * int(0.055 * SR)
        t = np.arange(int(0.05 * SR)) / SR
        out[a:a + t.size] += np.sin(2 * np.pi * f * t) * np.exp(-30 * t)
    return out


def speech(n: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    f0 = 120 + 30 * np.sin(2 * np.pi * 0.7 * t)
    ph = 2 * np.pi * np.cumsum(f0) / SR
    out = np.zeros(n, np.float32)
    for h in range(1, 40):
        fc = h * f0
        w = np.exp(-((fc - 700) / 400) ** 2) + 0.6 * np.exp(-((fc - 1800) / 600) ** 2) + 0.2 * np.exp(-((fc - 3200) / 800) ** 2)
        out += (w * np.sin(h * ph)).astype(np.float32)
    env = np.clip(np.sin(2 * np.pi * 3.5 * t + rng.uniform(0, 6)) + 0.3, 0, 1) ** 1.5
    return (out * env + 0.05 * rng.standard_normal(n) * env).astype(np.float32)


def crowd(n: int, seed: int = 2) -> np.ndarray:
    x = np.random.default_rng(seed).standard_normal(n).astype(np.float32)
    b, a = ss.butter(2, [300 / 16000, 3000 / 16000], "band")
    return (ss.lfilter(b, a, x) * (1 + 0.3 * np.sin(2 * np.pi * 0.4 * np.arange(n) / SR))).astype(np.float32)


def melody(n: int, seed: int) -> np.ndarray:
    """Other music: random notes from a scale, quarter-second steps."""
    r = np.random.default_rng(seed)
    out = np.zeros(n, np.float32)
    for i in range(int(n / SR * 4)):
        a, b = int(i * SR / 4), int((i + 1) * SR / 4)
        tt = np.arange(min(b, n) - a) / SR
        out[a:a + tt.size] += np.sin(2 * np.pi * r.choice([220, 277, 330, 392, 440, 523, 659]) * tt) * np.exp(-6 * tt)
    return out


def normalise(x: np.ndarray, rms: float = 0.05) -> np.ndarray:
    return x / max(float(np.sqrt(np.mean(x ** 2))), 1e-9) * rms


def mixed(bg: np.ndarray, clip: np.ndarray, snr_db: float, at_s: float = 0.4) -> np.ndarray:
    """`clip` placed `at_s` into the background, `snr_db` above it (power of the clip against power of the background)."""
    bg = normalise(bg)
    sig = np.zeros_like(bg)
    a = int(at_s * SR)
    sig[a:a + clip.size] = clip[:bg.size - a]
    gain = np.sqrt(np.mean(bg ** 2) / np.mean(clip ** 2) * 10 ** (snr_db / 10))
    return (bg + sig * gain).astype(np.float32)


@pytest.fixture(scope="module")
def matcher() -> JingleMatcher:
    return JingleMatcher(build_template(sting()))


def best(matcher: JingleMatcher, audio: np.ndarray) -> float:
    return matcher.best(audio)[0]


def test_template_cuts_the_silence_around_the_notes():
    template = build_template(np.concatenate([np.zeros(SR // 2, np.float32), sting(), np.zeros(SR // 2, np.float32)]))
    assert 1.1 <= template.shape[0] * 0.01 <= 1.4


@pytest.mark.parametrize("make", [speech, crowd, lambda n: speech(n) + crowd(n, 5)])
@pytest.mark.parametrize("snr", [15, 10])
def test_the_jingle_is_found_over_commentary_and_crowd(matcher, make, snr):
    n = matcher.window_samples(1.0)
    assert best(matcher, mixed(make(n), sting(), snr)) >= Config().jingle_min_score + 0.03


def test_clean_clip_matches_and_gives_its_position(matcher):
    n = matcher.window_samples(1.0)
    x = np.concatenate([np.zeros(int(0.4 * SR), np.float32), sting(), np.zeros(n)])[:n]
    score, end = matcher.best(x)
    assert score > 0.9
    assert abs(end / SR - (0.4 + 1.2)) < 0.4        # the end of the matched stretch (template = the notes, silence cut)


def test_no_match_on_speech_crowd_music_silence_or_another_arpeggio(matcher):
    n = matcher.window_samples(1.0)
    cases = {"speech": speech(n), "crowd": crowd(n), "speech+crowd": speech(n) + crowd(n, 5), "music": melody(n, 3), "music2": melody(n, 4),
             "arpeggio": mixed(speech(n), other_sting(), 5), "silence": np.zeros(n, np.float32),
             "noise floor": 1e-5 * np.random.default_rng(0).standard_normal(n).astype(np.float32)}
    scores = {name: best(matcher, x) for name, x in cases.items()}
    assert max(scores.values()) < Config().jingle_min_score - 0.1, scores


def test_too_little_audio_is_not_compared(matcher):
    assert matcher.best(np.zeros(1000, np.float32))[0] == -1.0


def test_the_matcher_is_cheap(matcher):
    x = speech(matcher.window_samples(1.0))
    t0 = time.perf_counter()
    for _ in range(10):
        matcher.best(x)
    assert (time.perf_counter() - t0) / 10 < 0.1


# ---- JingleWatch -----------------------------------------------------------------------------------------------------------------------

def test_watch_needs_votes_that_agree_and_then_waits():
    w = JingleWatch(min_score=0.4, votes=2, cooldown_s=8)
    assert w.feed(0.6, 5 * SR) is None                  # one reading is never enough
    assert w.feed(0.7, 5 * SR + 1000) == 0.7            # the second agrees on where the sting ended
    assert w.feed(0.7, 5 * SR + 2000) is None and w.feed(0.7, 5 * SR + 3000) is None   # same sting still in the window: cooldown
    assert w.feed(0.1, 6 * SR) is None
    assert w.feed(0.7, 20 * SR) is None and w.feed(0.7, 20 * SR + 500) == 0.7           # a later one counts


def test_watch_ignores_a_run_that_breaks_or_disagrees():
    w = JingleWatch(min_score=0.4, votes=2)
    assert w.feed(0.6, 5 * SR) is None and w.feed(0.1, 5 * SR + 100) is None and w.feed(0.6, 5 * SR + 200) is None   # broken run
    assert w.feed(0.6, 9 * SR) is None                  # another place: starts a new run
    assert w.feed(0.6, 9 * SR + 100) == 0.6


# ---- template file ---------------------------------------------------------------------------------------------------------------------

def test_template_round_trip_and_missing_file(tmp_path):
    template = build_template(sting())
    save_template(tmp_path / "x" / "jingle.npz", template)
    loaded = load_template(tmp_path / "x" / "jingle.npz")
    assert loaded.shape == template.shape and JingleMatcher(loaded).best(sting())[0] > 0.9
    assert load_template(tmp_path / "missing.npz") is None
    (tmp_path / "bad.npz").write_bytes(b"not an array")
    assert load_template(tmp_path / "bad.npz") is None


# ---- the server ------------------------------------------------------------------------------------------------------------------------

def frame(tab: int, x: np.ndarray) -> bytes:
    return struct.pack("<I", tab) + (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def play(server: Server, tab: int, audio: np.ndarray, now0: float = 100.0, chunk_s: float = 0.2) -> float:
    """What the extension and the loop do: small frames, and after each one a comparison and a vote. Returns the time reached."""
    step, now = int(chunk_s * SR), now0
    for a in range(0, audio.size, step):
        server.handle_audio(frame(tab, audio[a:a + step]))
        now += chunk_s
        job = server._jingle_job(tab)
        if job is not None:
            buf, total = job
            score, end = server.jingle.best(buf)
            server._jingle_vote(tab, score, total - len(buf) + end, now)
    return now


def make_server(matcher, **cfg) -> Server:
    server = Server(Scorer(Config(**cfg)), jingle=matcher)
    server.scorer.register(1)
    server.scorer.register(2)
    return server


def with_sting(snr: float, n_s: float = 8.0, at_s: float = 3.0) -> np.ndarray:
    n = int(n_s * SR)
    return mixed(speech(n) + crowd(n, 5), sting(), snr, at_s=at_s)


def heard_lines(caplog, tab: int = 1) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(f"jingle heard tab {tab} ")]


def test_a_heard_jingle_is_logged_recorded_and_a_strong_cue_on_that_tab(matcher, caplog):
    server = make_server(matcher)
    events = []
    server._rec_event = lambda kind, tab=None, tile=None, **f: events.append((kind, tab, f))
    with caplog.at_level(logging.INFO, logger="daemon"):
        play(server, 1, with_sting(10))
    heard = heard_lines(caplog)
    assert len(heard) == 1 and "(score " in heard[0]
    assert [e[0] for e in events] == ["jingle"] and events[0][1] == 1
    rec = server.scorer.streams[1]
    assert rec.anticipation_value == pytest.approx(server.scorer.config.weights.cue_review_top) and rec.anticipation_strong
    assert server.scorer.streams[2].anticipation_value == 0.0     # the other tab heard nothing


def test_log_only_mode_writes_the_line_but_no_cue(matcher, caplog):
    server = make_server(matcher, jingle_cue=0.0)
    with caplog.at_level(logging.INFO, logger="daemon"):
        play(server, 1, with_sting(10))
    assert len(heard_lines(caplog)) == 1 and "log only" in heard_lines(caplog)[0]
    assert server.scorer.streams[1].anticipation_value == 0.0


@pytest.mark.parametrize("bg", [lambda n: speech(n), lambda n: crowd(n), lambda n: melody(n, 3), lambda n: np.zeros(n, np.float32),
                                lambda n: speech(n) + crowd(n, 5)], ids=["speech", "crowd", "music", "silence", "speech+crowd"])
def test_no_hit_on_audio_without_the_jingle(matcher, caplog, bg):
    server = make_server(matcher)
    with caplog.at_level(logging.INFO, logger="daemon"):
        play(server, 1, bg(int(12 * SR)))
    assert heard_lines(caplog) == []
    assert server.scorer.streams[1].anticipation_value == 0.0


def test_no_hit_on_another_arpeggio_over_commentary(matcher, caplog):
    server = make_server(matcher)
    n = int(8 * SR)
    with caplog.at_level(logging.INFO, logger="daemon"):
        play(server, 1, mixed(speech(n) + crowd(n, 5), other_sting(), 5, at_s=3.0))
    assert heard_lines(caplog) == []


def test_a_second_sting_after_the_cooldown_counts_again(matcher, caplog):
    server = make_server(matcher)
    n = int(24 * SR)
    background = speech(n) + crowd(n, 5)
    first = mixed(background, sting(), 10, at_s=3.0)
    second = mixed(background, sting(), 10, at_s=14.0) - normalise(background)   # just the second sting, same gain
    with caplog.at_level(logging.INFO, logger="daemon"):
        play(server, 1, first + second)
    assert len(heard_lines(caplog)) == 2


def test_the_cue_survives_a_multiplex_game_change(matcher):
    server = make_server(matcher)
    now = play(server, 1, with_sting(10))
    server.multiplex[1] = True
    server._on_new_game(1, 0, frozenset({"AAA", "BBB"}), frozenset({"CCC", "DDD"}), now + 1, multi=False)
    rec = server.scorer.streams[1]
    assert rec.anticipation_value == pytest.approx(0.5) and rec.anticipation_strong


def test_a_cue_that_ran_out_does_not_return_after_a_game_change(matcher):
    server = make_server(matcher)
    now = play(server, 1, with_sting(10))
    server._on_new_game(1, 0, frozenset({"AAA", "BBB"}), frozenset({"CCC", "DDD"}), now + 60, multi=False)
    assert server.scorer.streams[1].anticipation_value == 0.0


def test_without_a_template_the_daemon_behaves_as_before(caplog):
    server = Server(Scorer(Config()))                     # no matcher: what a missing models/jingle/jingle.npz gives
    server.scorer.register(1)
    with caplog.at_level(logging.INFO, logger="daemon"):
        server.handle_audio(frame(1, with_sting(10, n_s=3.0, at_s=0.5)))
    assert server.jingle is None and server.jingle_buffers == {} and server.jingle_total == {}
    assert server._jingle_job(1) is None
    assert server.scorer.streams[1].anticipation_value == 0.0 and not caplog.records


def test_unregister_drops_the_tab_state(matcher):
    server = make_server(matcher)
    play(server, 1, with_sting(10))
    server.handle_message({"type": "unregister", "tab_id": 1})
    assert 1 not in server.jingle_buffers and 1 not in server.jingle_watch and 1 not in server.jingle_at
