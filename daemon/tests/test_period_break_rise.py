"""A score that rose across a commercial break / period break (TENN-UK 2026-11-08 01:22): the break's countdown ('1:53') was read as a game clock jump,
the watcher reset, and the first board after the break (3-10, clock 7:16) became a baseline because 7:16 does not 'continue' from 1:53.
Option C (user, 2026-11-08): American football only, the clock before the break under 3:00, the new clock later (a new period or time passed),
and the same team names before and after."""
from daemon.score_watch import ScoreWatcher


def boxes(rows):
    return [{"text": t, "x0": x, "y0": y, "x1": x + h * 0.6 * len(t), "y1": y + h} for t, h, x, y in rows]


def board(left, right, clock, names=("6 KENTUCKY", "13 TENNESSEE")):
    return boxes([(names[0], 30, 100, 800), (str(left), 60, 420, 790), (str(right), 60, 520, 790), (names[1], 30, 620, 800),
                  ("2ND", 30, 900, 800), (clock, 40, 1000, 795)])


def run(w, items, clock, start, n=4, sport="american football"):
    fired = []
    for i in range(n):
        texts = [it["text"] for it in items]
        if w.update(texts, [clock], start + i * 3.0, items, sport=sport):
            fired.append(start + i * 3.0)
    return fired


BREAK = ["LIVE", "#6 Kentucky vs. #13 Tennessee", "COMMERCIAL BREAK IN PROGRESS.", "1:53", "7"]


def after_the_break(names=("6 KENTUCKY", "13 TENNESSEE"), old_clock="0:15", sport="american football", break_clock="1:53"):
    w = ScoreWatcher()
    assert run(w, board(3, 7, old_clock), old_clock, 0.0, 5, sport) == []
    assert w.score == (3, 7)
    for i in range(3):                                   # the break countdown reads as a clock far from the game's
        w.update(BREAK[:3] + [break_clock, "7"], [break_clock], 20.0 + i * 5.0, None, sport=sport)
    assert w.score is None                               # reset
    return w


def test_a_field_goal_across_a_period_break_is_an_event():
    w = after_the_break()
    fired = run(w, board(3, 10, "7:16"), "7:16", 80.0, 5)
    assert len(fired) == 1 and w.score == (3, 10) and w.strength < 1.0     # a field goal, not a touchdown


def test_the_same_rise_with_other_team_names_is_a_baseline():
    w = after_the_break()
    assert run(w, board(3, 10, "7:16", ("22 OHIO STATE", "4 PENN STATE")), "7:16", 80.0, 5) == []
    assert w.score == (3, 10)


def test_other_sports_and_a_late_old_clock_keep_the_old_rule():
    w = after_the_break(sport="football")
    assert run(w, board(3, 10, "7:16"), "7:16", 80.0, 5, "football") == []
    w = after_the_break(old_clock="9:30", break_clock="11:53")   # the game clock was 9:30 / the break clock 11:53: no period end
    assert run(w, board(3, 10, "7:16"), "7:16", 80.0, 5) == []


def test_a_lower_or_equal_baseline_is_still_no_event():
    for left, right in ((3, 7), (3, 6), (2, 7)):
        w = after_the_break()
        assert run(w, board(left, right, "7:16"), "7:16", 80.0, 5) == [], (left, right)
