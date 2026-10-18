"""Slot-by-slot score reading (daemon/score_slots.py): window geometry and voting, with a fake recogniser (no image engine needed)."""
import numpy as np

from daemon.score_slots import read_slot, read_slots, windows

LEFT = {"text": "ARIZONA ST", "x0": 100, "x1": 300, "y0": 400, "y1": 440}    # a 40 px tall name
RIGHT = {"text": "TEXAS TECH", "x0": 600, "x1": 800, "y0": 402, "y1": 440}
IMAGE = np.zeros((900, 1000, 3), dtype=np.uint8)
N_LEFT = len(windows(LEFT, RIGHT, "left"))


def answers(*pairs):
    """A recogniser that returns the given (text, confidence) answers one after the other (the last one repeats)."""
    state = {"i": 0}

    def rec(crop):
        a = pairs[min(state["i"], len(pairs) - 1)]
        state["i"] += 1
        return a
    return rec


def test_each_slots_windows_stay_on_their_own_side_of_the_midpoint():
    mid, h = (LEFT["x1"] + RIGHT["x0"]) / 2, 40
    assert windows(LEFT, RIGHT, "left") and windows(LEFT, RIGHT, "right")
    assert all(x1 <= mid + 0.3 * h + 1e-9 for _, x1 in windows(LEFT, RIGHT, "left"))
    assert all(x0 >= mid - 0.3 * h - 1e-9 for x0, _ in windows(LEFT, RIGHT, "right"))


def test_the_left_windows_start_at_the_left_name_and_the_right_ones_end_at_the_right_name():
    assert min(x0 for x0, _ in windows(LEFT, RIGHT, "left")) == 300 - 0.2 * 40
    assert max(x1 for _, x1 in windows(LEFT, RIGHT, "right")) == 600 + 0.2 * 40


def test_a_slot_is_the_confident_reading_with_the_most_weight():
    rec = answers(("17", 0.95), ("17", 0.9), ("1", 0.81), ("177", 0.5), ("", 0.0))
    votes = read_slot(IMAGE, LEFT, RIGHT, "left", rec)
    assert votes.most_common(1)[0][0] == "17"
    assert "177" not in votes                               # below the confidence floor


def test_read_slots_returns_both_scores_when_both_slots_are_read():
    rec = answers(*([("17", 0.97)] * N_LEFT + [("43", 0.98)]))   # the left windows are read first, then the right ones
    assert read_slots(IMAGE, LEFT, RIGHT, rec) == (17, 43)


def test_no_score_when_a_slot_has_no_confident_reading():
    rec = answers(*([("17", 0.97)] * N_LEFT + [("43", 0.5)]))    # the right slot is never sure
    assert read_slots(IMAGE, LEFT, RIGHT, rec) is None


def test_no_score_when_the_names_leave_no_room_for_two_scores():
    close = dict(RIGHT, x0=305)
    assert read_slots(IMAGE, LEFT, close, lambda crop: ("17", 0.99)) is None


def test_text_that_is_not_a_number_is_ignored():
    assert not read_slot(IMAGE, LEFT, RIGHT, "left", lambda crop: ("TEXAS", 0.99))
    assert not read_slot(IMAGE, LEFT, RIGHT, "right", lambda crop: ("12:30", 0.99))


# ---- stacked names (Ligue 1 bug: LOSC above HAC, each score right of its own name) --------------------------------

from daemon.score_slots import read_stacked, stacked_windows   # noqa: E402

TOP = {"text": "LOSC", "x0": 103, "x1": 137, "y0": 34, "y1": 49}      # real boxes of the second reader
BOTTOM = {"text": "HAC", "x0": 103, "x1": 132, "y0": 56, "y1": 72}
STACK_IMAGE = np.zeros((200, 400, 3), dtype=np.uint8)


def test_the_windows_of_a_stacked_slot_start_at_the_right_edge_of_its_own_name_and_never_go_left_of_it():
    top = stacked_windows(TOP)
    assert top and min(x0 for x0, _ in top) == TOP["x1"] - 0.2 * 15 and all(x1 > x0 for x0, x1 in top)
    assert max(x0 for x0, _ in top) - TOP["x1"] <= 4 * 15        # a slot is never far from its name


def test_read_stacked_returns_the_top_and_the_bottom_score():
    n_top = len(stacked_windows(TOP))
    rec = answers(*([("1", 0.95)] * n_top + [("0", 0.9)]))        # the top slot is read first
    assert read_stacked(STACK_IMAGE, TOP, BOTTOM, rec) == (1, 0)


def test_read_stacked_needs_both_slots():
    assert read_stacked(STACK_IMAGE, TOP, BOTTOM, answers(("", 0.0))) is None
    n_top = len(stacked_windows(TOP))
    assert read_stacked(STACK_IMAGE, TOP, BOTTOM, answers(*([("1", 0.95)] * n_top + [("x", 0.9)]))) is None   # the bottom slot reads no number


def test_a_slot_the_readers_already_found_is_not_read_again():
    # the Ligue 1 frame: the full read found the bottom 0 (0.95) but not the top 1; the sweep reads only the top slot
    rec = answers(("1", 0.95))
    assert read_stacked(STACK_IMAGE, TOP, BOTTOM, rec, known=(None, 0)) == (1, 0)
    assert read_stacked(STACK_IMAGE, TOP, BOTTOM, answers(("", 0.0)), known=(None, 0)) is None     # the missing slot is still needed
    assert read_stacked(STACK_IMAGE, TOP, BOTTOM, answers(("", 0.0)), known=(2, 0)) == (2, 0)       # both known: nothing to read
