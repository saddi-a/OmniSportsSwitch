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
    g.fresh = {snap.game_id}
    change = g.real_change(1, 0, 14.0)
    assert change.verdict == "confirmed" and len(change.lines) == 1 and "12 s after" in change.lines[0]
    assert g.real_change(1, 0, 20.0).verdict == "ocr-only"    # a second change has no feed event behind it


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


# ---------------------------------------------------------------- Stage 2b: end zone cue (shadow)
def nfl_snap():
    return feed.parse_scoreboard(json.loads((FIXTURES / "feed_nfl_scoreboard.json").read_text(encoding="utf-8")), "nfl")[0]


def situation_data():
    return json.loads((FIXTURES / "feed_nfl_situations.json").read_text(encoding="utf-8"))


def test_situations_are_read_in_order_with_goal_to_go_and_offense():
    snap = nfl_snap()
    sits = feed.parse_situations(situation_data(), snap)
    assert [s.yards_to_endzone for s in sits] == [66, 71, 57, 41, 28, 1]
    last = sits[-1]
    assert (last.period, last.clock_s, last.down, last.distance, last.goal_to_go, last.offense) == (4, 489.0, 1, 1, True, snap.home.abbr)
    assert not sits[0].goal_to_go


def test_timeouts_kickoffs_duplicates_and_stateless_plays_are_left_out():
    data = situation_data()
    plays = data["drives"]["previous"][0]["plays"]
    timeout = json.loads(json.dumps(plays[-1]))
    timeout.update(id="t1", type={"text": "Official Timeout"})
    kick = json.loads(json.dumps(plays[-1]))
    kick.update(id="k1", type={"text": "Kickoff"})
    nodown = json.loads(json.dumps(plays[-1]))
    nodown.update(id="n1")
    nodown["start"]["down"] = -1
    plays += [timeout, kick, nodown, json.loads(json.dumps(plays[0]))]
    assert len(feed.parse_situations(data, nfl_snap())) == 6


def test_zero_yards_to_the_end_zone_is_read_as_one():
    data = situation_data()
    data["drives"]["previous"][0]["plays"][-1]["start"]["yardsToEndzone"] = 0
    assert feed.parse_situations(data, nfl_snap())[-1].yards_to_endzone == 1


def zone_gate():
    snap = nfl_snap()
    g = FeedGate(lead_s=10.0)
    g.set_situations(snap.game_id, feed.parse_situations(situation_data(), snap))
    return g, snap


def test_end_zone_cue_is_logged_once_when_the_stream_nears_the_snap():
    g, snap = zone_gate()
    assert see(g, snap, "9:00", 1, period=4) == []
    assert see(g, snap, "8:21", 2, period=4) == []                 # 12 s before the snap: too early
    assert see(g, snap, "8:17", 3, period=4) == []                 # armed
    lines = see(g, snap, "8:16", 4, period=4)
    assert len(lines) == 1 and "feed end zone would raise cue: 1 yds, 1 & goal" in lines[0] and "JAX has the ball" in lines[0]
    assert see(g, snap, "8:10", 5, period=4) == []


def test_plays_far_from_the_end_zone_are_never_logged_and_no_period_holds():
    g, snap = zone_gate()
    for i, clock in enumerate(["9:50", "9:48", "9:45", "9:00", "8:59"]):
        assert see(g, snap, clock, i, period=4) == []             # the 41 and 28 yard plays
    assert see(g, snap, "8:15", 10) == [] and see(g, snap, "8:14", 11) == []   # no period read
    assert see(g, snap, "8:13", 12, period=4) == []                # armed only now


def test_end_zone_play_already_behind_the_stream_is_dropped_quietly():
    g, snap = zone_gate()
    assert see(g, snap, "4:00", 1, period=4) == [] and see(g, snap, "4:00", 2, period=4) == []
    assert see(g, snap, "8:12", 3, period=4) == []                 # decided for good
