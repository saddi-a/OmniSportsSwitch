"""The second OCR reader inside Server._ocr_pass: used only when a scorebug is on screen and the score or clock is missing, never fatal."""
import numpy as np
import pytest

from daemon import server as server_module
from daemon.scoring import Config, Scorer
from daemon.server import Server


def item(text, x0, y0, x1, y1, conf=0.9):
    return {"text": text, "conf": conf, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


class FirstReader:
    """The first reader: always the same items (names and a clock, but no score digits)."""
    def __init__(self, items):
        self.items = items

    def read_items(self, image):
        return list(self.items)


class SecondReader:
    available = True

    def __init__(self, extra=None, fail=False):
        self.extra, self.fail, self.calls = extra or [], fail, 0

    def rescue(self, image, items):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")      # a reader that cannot protect itself must still not be called twice in a row
        return list(items) + self.extra


@pytest.fixture
def frame(monkeypatch):
    monkeypatch.setattr(server_module, "grab", lambda rect: np.zeros((400, 1000, 3), dtype=np.uint8))


NAMES = [item("ARIZONA ST", 100, 300, 300, 330), item("TEXAS TECH", 560, 300, 760, 330), item("9:34", 380, 340, 440, 360)]
DIGITS = [item("17", 310, 290, 340, 340), item("43", 520, 290, 550, 340)]
JOB = [(1, 0, {"left": 0, "top": 0, "width": 1000, "height": 400})]


def make(first_items, second, **cfg):
    return Server(Scorer(Config(**cfg)), screen_reader=FirstReader(first_items), rapid_reader=second)


def test_the_second_reader_completes_a_scorebug_with_no_score(frame):
    second = SecondReader(DIGITS)
    server = make(NAMES, second)
    result = server._ocr_pass(JOB)[(1, 0)]
    assert second.calls == 1
    assert {"17", "43"} <= set(result["texts"])


def test_the_second_reader_is_not_called_when_the_first_reader_found_everything(frame):
    second = SecondReader(DIGITS)
    server = make(NAMES + DIGITS, second)
    server._ocr_pass(JOB)
    assert second.calls == 0


def test_the_second_reader_is_not_called_without_a_scorebug(frame):
    second = SecondReader(DIGITS)
    server = make([item("Follow", 0, 0, 80, 20)], second)
    server._ocr_pass(JOB)
    assert second.calls == 0


def test_the_second_reader_can_be_switched_off(frame):
    second = SecondReader(DIGITS)
    server = make(NAMES, second, ocr_assist=0.0)
    result = server._ocr_pass(JOB)[(1, 0)]
    assert second.calls == 0 and "17" not in result["texts"]


def test_it_looks_again_only_after_the_retry_time_when_it_found_nothing(frame):
    second = SecondReader([])                 # finds nothing
    server = make(NAMES, second, ocr_assist_retry_s=3.0)
    server._ocr_pass(JOB)
    server._ocr_pass(JOB)                     # immediately again: throttled
    assert second.calls == 1
    server.rapid_try_at[(1, 0)] -= 10         # ...and again once the retry time has passed
    server._ocr_pass(JOB)
    assert second.calls == 2


def test_once_it_finds_the_score_it_reads_on_every_pass(frame):
    second = SecondReader(DIGITS)
    server = make(NAMES, second)
    server._ocr_pass(JOB)
    server._ocr_pass(JOB)
    assert second.calls == 2                   # not throttled while it is what makes the reading complete


def test_no_second_reader_at_all_changes_nothing(frame):
    server = make(NAMES, None)
    result = server._ocr_pass(JOB)[(1, 0)]
    assert result["texts"] == [i["text"] for i in NAMES]


def test_a_second_reader_that_raises_does_not_break_the_ocr_pass(frame):
    second = SecondReader(fail=True)
    server = make(NAMES, second)
    result = server._ocr_pass(JOB)[(1, 0)]
    assert result["texts"] == [i["text"] for i in NAMES]       # the first reader's reading comes back unchanged
