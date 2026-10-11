import dataclasses
import json
import pathlib

from daemon import feed
from daemon.feed_gate import CONFIRM_S, FeedGate

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def soccer_snap():
    return feed.parse_scoreboard(json.loads((FIXTURES / "feed_soccer_scoreboard.json").read_text(encoding="utf-8")), "ita.1")[0]


def goal(snap, clock_s=3180.0, kind="goal", key="g1"):
    return feed.FeedEvent(snap.game_id, (key,), kind, None, clock_s, "53'", "ROMA", "Goal")


def gate_with(event, snap):
    g = FeedGate(lead_s=10.0)
    g.add(event, snap)
    return g


def see(g, snap, clock, now, tab=1, tile=0, period=None, replay=False):
    return g.observe(tab, tile, snap.game_id, [clock], period, replay, now)


def test_nothing_fires_while_the_stream_is_far_before_the_event():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)               # goal at 53:00
    for i in range(10):
        assert see(g, snap, "50:00", i) == []


def test_fires_when_the_stream_is_lead_s_before_the_event_and_needs_two_readings():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)
    assert see(g, snap, "52:45", 1) == []         # 15 s ahead: not yet
    assert see(g, snap, "52:52", 2) == []         # 8 s ahead: armed
    lines = see(g, snap, "52:54", 3)
    assert len(lines) == 1 and "would-fire" in lines[0] and "goal 53'" in lines[0]
    assert see(g, snap, "52:56", 4) == []         # once only


def test_one_misread_clock_does_not_fire():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)
    assert see(g, snap, "52:55", 1) == []
    assert see(g, snap, "40:00", 2) == []         # the clock was misread once
    assert see(g, snap, "52:56", 3) == []         # armed again from scratch


def test_an_event_already_behind_the_stream_is_skipped_not_fired():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)
    lines = see(g, snap, "60:00", 1)
    assert len(lines) == 1 and "skipped" in lines[0] and "behind" in lines[0]
    assert see(g, snap, "52:55", 2) == []         # decided for good (a clock going back later does not fire it)


def test_unknown_clock_and_replay_hold():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)
    assert g.observe(1, 0, snap.game_id, [], None, False, 1) == []
    assert see(g, snap, "53:00", 2, replay=True) == []
    assert see(g, snap, "53:00", 3) == []         # armed only now
    assert len(see(g, snap, "53:01", 4)) == 1


def test_unmapped_tile_never_fires():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)
    assert g.observe(1, 0, None, ["53:00"], None, False, 1) == []


def test_kinds_outside_the_list_are_not_gated():
    snap = soccer_snap()
    g = gate_with(goal(snap, kind="yellow card"), snap)
    assert g.pending == {}


def test_real_score_change_after_would_fire_is_confirmed_with_the_lead():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)
    see(g, snap, "52:55", 1)
    see(g, snap, "52:56", 2)                      # fires at t=2
    lines = g.real_change(1, 0, 14.0)
    assert len(lines) == 1 and "confirmed" in lines[0] and "12 s after" in lines[0]
    assert "ocr-only" in g.real_change(1, 0, 20.0)[0]    # a second change has no feed event behind it


def test_unconfirmed_would_fire_is_reported_once_after_the_window():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)
    see(g, snap, "52:55", 1)
    see(g, snap, "52:56", 2)
    lines = see(g, snap, "60:00", 2 + CONFIRM_S + 1)
    assert len(lines) == 1 and "unconfirmed" in lines[0]
    assert see(g, snap, "60:05", 2 + CONFIRM_S + 2) == []


def test_a_red_card_fires_but_is_never_waiting_for_a_score_change():
    snap = soccer_snap()
    g = gate_with(goal(snap, kind="red card"), snap)
    see(g, snap, "52:55", 1)
    assert "would-fire" in see(g, snap, "52:56", 2)[0]
    assert see(g, snap, "70:00", 2 + CONFIRM_S + 5) == []


def test_football_needs_a_period_and_counts_down():
    snap = dataclasses.replace(soccer_snap(), sport="american football", league="nfl")
    ev = feed.FeedEvent(snap.game_id, ("td",), "touchdown", 2, 300.0, "5:00", "", "TD")   # 2nd quarter, 5:00 left
    g = gate_with(ev, snap)
    assert see(g, snap, "5:00", 1) == []          # no period read: hold
    assert see(g, snap, "5:00", 2, period=2) == []   # armed
    lines = see(g, snap, "5:02", 3, period=2)     # the stream still has 5:02 left = 2 s before the event
    assert len(lines) == 1 and "period guessed" in lines[0]


def test_football_in_the_wrong_period_is_skipped_as_behind_or_waits():
    snap = dataclasses.replace(soccer_snap(), sport="american football", league="nfl")
    ev = feed.FeedEvent(snap.game_id, ("td",), "touchdown", 3, 300.0, "5:00", "", "TD")   # 3rd quarter
    g = gate_with(ev, snap)
    assert see(g, snap, "5:00", 1, period=1) == [] and see(g, snap, "5:00", 2, period=1) == []   # still in the 1st quarter


def test_forget_clears_a_tab():
    snap = soccer_snap()
    g = gate_with(goal(snap), snap)
    see(g, snap, "52:55", 1)
    see(g, snap, "52:56", 2)
    g.forget(1)
    assert g.fires == {} and g.mapped == {}
