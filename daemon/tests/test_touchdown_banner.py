"""RedZone's TOUCHDOWN banner as a score event: the strict matcher, the votes and cooldown, which tabs get the extra look, and the server path
(a fake reader returns the texts; the real frames are never committed)."""
import numpy as np
import pytest

from daemon.rapid_reader import RapidReader
from daemon.scoring import Config, Scorer
from daemon.server import Server
from daemon.touchdown_banner import TouchdownBannerWatch, has_banner, has_doubt, is_banner_text

NFL = "american football"


@pytest.mark.parametrize("text", ["TOUCHDOWN", "ATOUChDOWN", "KTOUCHDOWN", "TOUCHDO", "T0UCHD0WN", "touchdown", " TOUCHDOWN "])
def test_strict_match_accepts(text):
    assert is_banner_text(text)


@pytest.mark.parametrize("text", ["TOUCHDOWNS", "TOTAL TOUCHDOWNS", "3RD DOWN", "JeoDOWN", "YARDS/TOUCH", "REVIEW: TOUCHDOWN", "NO TOUCHDOWN",
                                  "DOWN", "TOUCH", "TOUCHDOWN PASS", "ABTOUCHDOWN", "", "TD"])
def test_strict_match_rejects(text):
    assert not is_banner_text(text)


def test_doubt_words():
    assert has_doubt(["TOUCHDOWN", "REVIEW"]) and has_doubt(["POSSIBLE", "x"]) and has_doubt(["CALLED BACK"]) and has_doubt(["Challenge"])
    assert not has_doubt(["TOUCHDOWN", "3rd & 4"])
    assert has_banner(["a", "TOUCHDOWN"]) and not has_banner(["TOUCHDOWNS"])


def test_votes_window_and_cooldown():
    w = TouchdownBannerWatch()
    assert not w.update(0.0, True)            # one read is not enough
    assert w.update(2.0, True)                # the second inside 6 s
    assert not w.update(4.0, True) and not w.update(6.0, True)   # the banner lingers: cooldown
    assert not w.update(33.0, True)               # 31 s after the accept it may fire again, but needs its second vote
    w = TouchdownBannerWatch()
    assert not w.update(0.0, True) and not w.update(10.0, True)  # too far apart
    assert w.update(12.0, True)
    w = TouchdownBannerWatch()
    assert not w.update(0.0, True) and not w.update(1.0, False) and not w.update(2.0, True)   # a look without the banner resets the votes
    assert not w.update(3.0, None) and w.update(4.0, True)       # not looking changes nothing
    assert not w.update(40.0, True) and w.update(41.0, True)     # after the cooldown it can fire again


class FakeEngine:
    def __init__(self, raw):
        self.raw, self.shapes = raw, []

    def __call__(self, img, **kw):
        self.shapes.append(img.shape)
        return self.raw, None


def test_reader_band_is_lower_rows_at_half_size():
    engine = FakeEngine([([[0, 0], [1, 0], [1, 1], [0, 1]], "TOUCHDOWN", 0.98), ([[0, 0]], " ", 0.5)])
    reader = RapidReader(engine=engine)
    assert reader.read_banner_band(np.zeros((1000, 1600, 3), np.uint8)) == ["TOUCHDOWN"]
    assert engine.shapes[0][:2] == (200, 800)     # rows 60-100 % of 1000 = 400 rows, halved
    bad = RapidReader(engine=None)
    assert bad.read_banner_band(np.zeros((10, 10, 3), np.uint8)) == []


class FakeBanner:
    available = True

    def __init__(self, texts):
        self.texts, self.calls = texts, 0

    def read_banner_band(self, image):
        self.calls += 1
        return list(self.texts)


def make(flag=True, sport="American football", reader=None, **cfg):
    cfg.setdefault("warmup_s", 0.0)
    server = Server(Scorer(Config(**cfg)), rapid_reader=reader)
    server.handle_message({"type": "register", "tab_id": 1, "is_main": True, "role": "MAIN"})
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT", "sport": sport, "multiplex": flag})
    return server


IMG = np.zeros((100, 160, 3), np.uint8)
BOARD = ["KCC", "7", "BUF", "3", "3rd & 4", "2nd"]


def test_pass_runs_only_on_flagged_football_tabs():
    reader = FakeBanner(["TOUCHDOWN"])
    server = make(True, reader=reader)
    assert server._banner_pass(2, 0, IMG, BOARD) == ["TOUCHDOWN"]
    assert server._banner_pass(2, 0, IMG, BOARD) is None                    # interval 2 s
    server.td_try_at.clear()
    assert server._banner_pass(2, 0, IMG, BOARD + ["TOUCHDOWN"]) is None   # the normal reading already has it
    assert make(False, reader=FakeBanner(["x"]))._banner_pass(2, 0, IMG, BOARD) is None   # not flagged
    assert make(False, reader=FakeBanner(["x"]), touchdown_banner_all_tabs=1.0)._banner_pass(2, 0, IMG, BOARD) == ["x"]
    assert make(True, sport="Soccer", reader=FakeBanner(["x"]))._banner_pass(2, 0, IMG, BOARD) is None   # not football
    assert make(True, reader=FakeBanner(["x"]), touchdown_banner_interval_s=0.0)._banner_pass(2, 0, IMG, BOARD) is None   # off
    assert make(True, reader=None)._banner_pass(2, 0, IMG, BOARD) is None


def board(extra=None, texts=BOARD):
    r = {"texts": list(texts), "clocks": ["9:30"]}
    if extra is not None:
        r["banner_texts"] = extra
    return r


def run(server, readings, start=100.0, step=2.0):
    for i, r in enumerate(readings):
        server._update_game_states({(2, 0): r}, start + step * i)
    return start + step * len(readings)


def event_time(server):
    return server.scorer.streams[2].event_at


def test_banner_needs_two_reads_then_sets_a_goal_event():
    server = make()
    before = event_time(server)
    run(server, [board([]), board(["TOUCHDOWN"])])
    assert event_time(server) == before and server.scorer.goal_hold_left(2, 103.0) == 0   # one read: nothing
    run(server, [board(["ATOUChDOWN"])], start=104.0)
    assert event_time(server) > before and server.scorer.goal_hold_left(2, 104.0) > 0
    assert server.event_at[(2, 0)] > float("-inf")
    later = server.event_at[(2, 0)]
    server.scorer.set_event(2, 106.0, 1.0, goal=True)     # a real score change after the banner takes the max, no second event
    assert event_time(server) == 106.0 and server.event_at[(2, 0)] == later


def test_banner_cooldown_and_flicker():
    server = make()
    run(server, [board(["TOUCHDOWN"]), board(["TOUCHDOWN"])])
    first = event_time(server)
    run(server, [board(["TOUCHDOWN"]), board(["TOUCHDOWN"])], start=104.0)   # still on screen: cooldown
    assert event_time(server) == first
    server2 = make()
    run(server2, [board(["TOUCHDOWN"]), board([]), board(["TOUCHDOWN"])])   # a look without the banner between: no vote
    assert event_time(server2) == server2.scorer.streams[2].event_at and server2.scorer.goal_hold_left(2, 106.0) == 0


@pytest.mark.parametrize("texts", [BOARD + ["REVIEW"], BOARD + ["POSSIBLE", "NO TOUCHDOWN"], BOARD + ["CALLED BACK"]])
def test_review_wording_blocks_the_banner(texts):
    server = make()
    run(server, [board(["TOUCHDOWN"], texts)] * 3)
    assert server.scorer.goal_hold_left(2, 106.0) == 0


def test_banner_ignored_in_a_replay_or_off_football_or_when_strength_zero():
    server = make()
    run(server, [board(["TOUCHDOWN"], BOARD + ["REPLAY"])] * 4)   # a replay banner over a live scorebug
    assert server.scorer.goal_hold_left(2, 108.0) == 0
    soccer = make(sport="Soccer")
    run(soccer, [board(["TOUCHDOWN"])] * 3)
    assert soccer.scorer.goal_hold_left(2, 106.0) == 0
    quiet = make(touchdown_banner_strength=0.0)
    run(quiet, [board(["TOUCHDOWN"])] * 3)
    assert quiet.scorer.goal_hold_left(2, 106.0) == 0


def test_banner_found_by_the_normal_reading_votes_too():
    server = make()
    run(server, [board(None, BOARD + ["TOUCHDOWN"]), board(None, BOARD + ["TOUCHDOWN"])])
    assert server.scorer.goal_hold_left(2, 103.0) > 0


def test_banner_off_when_not_the_sport():
    server = make(sport="Soccer")
    assert server._banner_pass(2, 0, IMG, BOARD) is None


def test_split_banner_halves_in_neighbouring_items():
    assert has_banner(["TOUCHDO", "OWN"]) and has_banner(["x", "TOUCHO", "OWN", "NFL"]) and has_banner(["ETOUCHI", "WN"])   # real RedZone readings
    assert not has_banner(["OWN", "TOUCHD0W"]) and not has_banner(["TOUCH", "x", "OWN"]) and not has_banner(["3RD", "DOWN"])
    assert not has_banner(["YARDS", "TOUCH", "TOTAL", "DOWNS"]) and not has_banner(["TOTAL", "TOUCHDOWNS"]) and not has_banner(["TOUCH", "DOWNS"])
