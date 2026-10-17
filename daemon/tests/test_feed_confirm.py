import json
import pathlib

from daemon import feed
from daemon.feed_gate import FeedGate
from daemon.scoring import Config
from daemon.settings import LAYOUT

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def snap():
    return feed.parse_scoreboard(json.loads((FIXTURES / "feed_soccer_scoreboard.json").read_text(encoding="utf-8")), "ita.1")[0]


def goal(s, clock_s=3180.0, key="g1"):
    return feed.FeedEvent(s.game_id, (key,), "goal", None, clock_s, "53'", "ROMA", "Goal")


def live_gate(s, alive=True):
    """A gate whose tile (1, 0) is mapped to the game and has read the stream clock 53:00."""
    g = FeedGate(lead_s=10.0)
    g.confirm_s = 30.0
    if alive:
        g.fresh = {s.game_id}
    g.observe(1, 0, s.game_id, ["53:00"], None, False, 100.0)
    return g


def test_mapped_and_no_feed_event_is_ocr_only():
    s = snap()
    g = live_gate(s)
    change = g.real_change(1, 0, 100.0)
    assert change.verdict == "ocr-only" and "ocr-only" in change.lines[0]


def test_matching_feed_event_first_is_confirmed():
    s = snap()
    g = live_gate(s)
    g.add(goal(s), s)                                    # the feed had the goal at 53:00 already
    change = g.real_change(1, 0, 101.0)
    assert change.verdict == "confirmed" and "confirmed" in change.lines[0]


def test_a_feed_event_that_fired_in_shadow_is_confirmed():
    s = snap()
    g = FeedGate(lead_s=10.0)
    g.fresh = {s.game_id}
    g.add(goal(s), s)
    g.observe(1, 0, s.game_id, ["52:55"], None, False, 1.0)
    assert "would-fire" in g.observe(1, 0, s.game_id, ["52:56"], None, False, 2.0)[0]
    assert g.real_change(1, 0, 14.0).verdict == "confirmed"


def test_the_same_feed_event_confirms_only_one_change():
    s = snap()
    g = live_gate(s)
    g.add(goal(s), s)
    assert g.real_change(1, 0, 101.0).verdict == "confirmed"
    assert g.real_change(1, 0, 102.0).verdict == "ocr-only"


def test_a_feed_event_arriving_10_s_after_the_ocr_change_confirms_it_later():
    s = snap()
    g = live_gate(s)
    assert g.real_change(1, 0, 100.0).verdict == "ocr-only"
    assert g.confirmations(105.0) == []
    g.add(goal(s), s)                                    # the feed catches up 10 s later
    assert g.confirmations(110.0) == [(1, 0)]
    assert any("confirmed later" in m for m in g.messages)
    assert g.confirmations(111.0) == []                  # reported once


def test_an_unconfirmed_change_stays_reduced_and_is_forgotten_after_confirm_s():
    s = snap()
    g = live_gate(s)
    assert g.real_change(1, 0, 100.0).verdict == "ocr-only"
    assert g.confirmations(131.0) == []
    g.add(goal(s), s)
    assert g.confirmations(132.0) == []                  # too late, and the entry is gone
    assert any("not confirmed" in m for m in g.messages)


def test_a_real_goal_is_never_reduced_for_longer_than_confirm_s():
    """Whatever the order, a goal the feed has within confirm_s of the OCR change ends up confirmed."""
    s = snap()
    for delay in (-20.0, 0.0, 5.0, 29.0):                # the feed event before / with / after the OCR change
        g = live_gate(s)
        if delay < 0:
            g.add(goal(s), s)
        verdict = g.real_change(1, 0, 100.0).verdict
        if delay >= 0:
            g.add(goal(s), s)
            g.confirmations(100.0 + delay)
            assert verdict == "ocr-only" and g.waiting == {}, delay
        else:
            assert verdict == "confirmed"


def test_unmapped_stale_feed_off_and_no_clock_are_unknown():
    s = snap()
    g = live_gate(s)
    assert g.real_change(2, 0, 100.0).verdict == "unknown"                       # tile never mapped
    assert live_gate(s, alive=False).real_change(1, 0, 100.0).verdict == "unknown"   # feed off or stale
    g = live_gate(s)
    g.observe(1, 0, None, ["53:00"], None, False, 101.0)                          # mapping lost
    assert g.real_change(1, 0, 102.0).verdict == "unknown"
    g = live_gate(s)
    assert g.real_change(1, 0, 130.0).verdict == "unknown"                       # the clock was last read 30 s ago
    g = FeedGate()
    g.fresh = {s.game_id}
    g.observe(1, 0, s.game_id, [], None, False, 1.0)                              # no readable clock
    assert g.real_change(1, 0, 2.0).verdict == "unknown"


def test_a_replay_clock_is_not_trusted():
    s = snap()
    g = FeedGate()
    g.fresh = {s.game_id}
    g.observe(1, 0, s.game_id, ["53:00"], None, True, 1.0)
    assert g.real_change(1, 0, 2.0).verdict == "unknown"


def test_the_settings_exist_and_are_shown_in_the_popup():
    c = Config()
    assert c.feed_unconfirmed_factor == 0.8 and c.feed_confirm_s == 30.0
    shown = {key for _, rows in LAYOUT for key, _, _ in rows}
    assert {"scoring.feed_unconfirmed_factor", "scoring.feed_confirm_s"} <= shown
