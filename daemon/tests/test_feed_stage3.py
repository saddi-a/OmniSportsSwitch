import dataclasses
import json
import pathlib

from daemon import feed
from daemon.feed_gate import FeedGate
from daemon.feed_map import FeedMapper
from daemon.scoring import Config
from daemon.settings import LAYOUT

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def snap():
    return feed.parse_scoreboard(json.loads((FIXTURES / "feed_soccer_scoreboard.json").read_text(encoding="utf-8")), "ita.1")[0]


def goal(s, clock_s=3180.0, key="g1", kind="goal"):
    return feed.FeedEvent(s.game_id, (key,), kind, None, clock_s, "53'", "ROMA", "Goal")


def gate(s, lead=10.0):
    g = FeedGate(lead_s=lead)
    g.fresh = {s.game_id}
    return g


def see(g, s, clock, now, tab=1, tile=0, period=None, replay=False):
    return g.observe(tab, tile, s.game_id, [clock], period, replay, now)


# ---------------------------------------------------------------- the event key
def test_the_same_goal_with_a_longer_text_is_one_event():
    teams = {"1": feed.Team("1", "COM", "Como"), "2": feed.Team("2", "ROM", "Roma")}
    one = {"type": {"text": "Goal"}, "scoringPlay": True, "team": {"id": "2"}, "clock": {"value": 3180.0, "displayValue": "53'"}}
    two = {"type": {"text": "Goal - Volley"}, "scoringPlay": True, "team": {"id": "2"}, "clock": {"value": 3180.0, "displayValue": "53'"}}
    a = feed._soccer_events("9", [one], teams)[0]
    b = feed._soccer_events("9", [two], teams)[0]
    assert a.key == b.key
    penalty = dict(one, penaltyKick=True)
    assert feed._soccer_events("9", [penalty], teams)[0].key == a.key          # same goal, better described
    other_team = dict(one, team={"id": "1"})
    assert feed._soccer_events("9", [other_team], teams)[0].key != a.key
    later = dict(one, clock={"value": 3300.0, "displayValue": "55'"})
    assert feed._soccer_events("9", [later], teams)[0].key != a.key
    red = dict(one, scoringPlay=False, redCard=True)
    assert feed._soccer_events("9", [red], teams)[0].key != a.key


def test_a_score_note_carries_the_score_before():
    s = snap()
    t = feed.FeedTracker()
    t.update([s])
    notes = t.update([dataclasses.replace(s, home_score=1)])
    score = [n for n in notes if n.kind == "score"][0]
    assert score.before == (0, 0)


# ---------------------------------------------------------------- one feed goal from the score change and the event
def test_score_change_first_then_the_event_makes_one_feed_goal_with_the_exact_clock():
    s = snap()
    g = gate(s)
    g.add_score(dataclasses.replace(s, home_score=1, clock_s=3190.0), (0, 0), 0.0)
    assert len(g.pending[s.game_id]) == 1 and g.pending[s.game_id][0].elapsed == 3190.0
    g.add(goal(s, 3180.0), s, 8.0)
    assert len(g.pending[s.game_id]) == 1 and g.pending[s.game_id][0].elapsed == 3180.0


def test_event_first_then_the_score_change_adds_nothing():
    s = snap()
    g = gate(s)
    g.add(goal(s), s, 0.0)
    g.add_score(dataclasses.replace(s, away_score=1), (0, 0), 11.0)
    assert len(g.pending[s.game_id]) == 1


def test_a_second_goal_later_is_a_second_feed_goal():
    s = snap()
    g = gate(s)
    g.add(goal(s), s, 0.0)
    g.add_score(dataclasses.replace(s, away_score=2, clock_s=3800.0), (0, 1), 200.0)
    assert len(g.pending[s.game_id]) == 2


def test_a_score_going_down_or_a_one_point_change_is_not_a_goal():
    s = snap()
    g = gate(s)
    g.add_score(dataclasses.replace(s, home_score=0), (1, 0), 0.0)
    assert g.pending == {}


def test_a_football_score_change_is_typed_by_its_size():
    s = dataclasses.replace(snap(), sport="american football", league="nfl", period=4, clock_s=200.0)
    g = gate(s)
    g.add_score(dataclasses.replace(s, home_score=7), (0, 0), 0.0)
    g.add_score(dataclasses.replace(s, home_score=10, clock_s=100.0), (7, 0), 100.0)
    assert [p.event.kind for p in g.pending[s.game_id]] == ["touchdown", "field goal good"]


# ---------------------------------------------------------------- release: ahead, behind, strength, cap
def fire_goal(g, s, tile=0, start=1.0, clock_s=3180.0, key="g1", kind="goal"):
    g.add(goal(s, clock_s, key, kind), s, start)
    see(g, s, "52:55", start, tile=tile)
    return see(g, s, "52:56", start + 1, tile=tile)


def test_the_feed_ahead_fires_and_queues_an_action_once():
    s = snap()
    g = gate(s)
    lines = fire_goal(g, s)
    assert len(lines) == 1 and "would-fire" in lines[0]
    fired = g.drain_fired()
    assert len(fired) == 1 and (fired[0].tab, fired[0].tile, fired[0].kind, fired[0].strength) == (1, 0, "goal", 1.0)
    assert g.drain_fired() == []


def test_the_feed_behind_the_stream_never_fires_and_only_referees():
    s = snap()
    g = gate(s)
    g.add(goal(s), s, 0.0)
    lines = see(g, s, "53:03", 1)
    assert g.drain_fired() == []
    assert len(lines) == 1 and "behind" in lines[0] and "would-fire" not in lines[0]


def test_a_feed_event_far_behind_is_logged_as_behind():
    s = snap()
    g = gate(s)
    g.add(goal(s), s, 0.0)
    lines = see(g, s, "54:30", 1)
    assert len(lines) == 1 and "behind" in lines[0] and g.drain_fired() == []


def test_strengths_follow_the_event_kind():
    s = dataclasses.replace(snap(), sport="american football", league="nfl")
    g = gate(s)
    g.field_goal_strength, g.big_play_strength = 0.6, 0.7
    assert g.strength("touchdown") == 1.0 and g.strength("goal") == 1.0 and g.strength("field goal good") == 0.6
    assert g.strength("red card") == 0.7 and g.strength("penalty") == 0.7


def clock_text(c):
    return f"{int(c // 60)}:{int(c % 60):02d}"


def test_at_most_four_actions_per_tile_in_ten_minutes():
    s = snap()
    g = gate(s)
    last = []
    for i in range(5):
        clock = 3000.0 + 1000.0 * i                                # five goals, far apart on the game clock
        g.add(goal(s, clock, f"g{i}"), s, 10.0 * i)
        see(g, s, clock_text(clock - 5), 10.0 * i)
        last = see(g, s, clock_text(clock - 4), 10.0 * i + 1)
    assert len(g.drain_fired()) == 4
    assert len(last) == 1 and "would-fire" in last[0] and "cap" in last[0]


def test_the_fire_note_hook_is_appended_to_the_line():
    s = snap()
    g = gate(s)
    g.fire_note = lambda tab, tile, kind, strength: f" | swap would be LEFT -> MAIN ({kind} {strength})"
    lines = fire_goal(g, s)
    assert lines[0].endswith("| swap would be LEFT -> MAIN (goal 1.0)")


# ---------------------------------------------------------------- reconciling with the OCR change
def test_feed_acted_first_then_the_ocr_change_is_confirmed_and_counted():
    s = snap()
    g = gate(s)
    fire_goal(g, s)
    g.drain_fired()
    change = g.real_change(1, 0, 5.0)
    assert change.verdict == "confirmed" and change.counted


def test_feed_behind_confirms_the_ocr_change_without_having_counted():
    s = snap()
    g = gate(s)
    see(g, s, "53:10", 0.0)
    g.add(goal(s), s, 1.0)
    change = g.real_change(1, 0, 2.0)
    assert change.verdict == "confirmed" and not change.counted


def test_an_ocr_change_waiting_for_the_feed_blocks_a_late_action_for_the_same_goal():
    s = snap()
    g = gate(s)
    see(g, s, "53:00", 0.0)
    assert g.real_change(1, 0, 0.5).verdict == "ocr-only"
    g.add(goal(s), s, 5.0)                                   # the feed catches up
    see(g, s, "53:03", 5.0)
    see(g, s, "53:04", 6.0)
    assert g.drain_fired() == []
    assert g.confirmations(7.0) == [(1, 0)]


def test_a_feed_only_goal_is_logged_when_no_ocr_change_follows():
    s = snap()
    g = gate(s)
    fire_goal(g, s)
    lines = see(g, s, "55:30", 200.0)
    assert any("feed-only" in l for l in lines)


# ---------------------------------------------------------------- rewind
def test_a_clock_that_jumps_back_is_a_rewind_not_an_ocr_only_change():
    s = snap()
    g = gate(s)
    see(g, s, "53:00", 0.0)
    see(g, s, "53:03", 3.0)
    assert see(g, s, "40:00", 6.0) == []                      # one low reading may be a misread
    lines = see(g, s, "40:03", 9.0)
    assert len(lines) == 1 and "rewind" in lines[0]
    change = g.real_change(1, 0, 9.5)
    assert change.verdict == "rewind" and "rewind" in change.lines[0]
    see(g, s, "53:05", 12.0)                                   # back at the live edge
    assert g.real_change(1, 0, 12.5).verdict == "ocr-only"


def test_nothing_fires_during_a_rewind():
    s = snap()
    g = gate(s)
    see(g, s, "53:30", 0.0)
    see(g, s, "40:00", 3.0)
    see(g, s, "40:03", 6.0)
    g.add(goal(s, 2420.0), s, 7.0)                             # a feed goal at 40:20, ahead of the rewound clock
    see(g, s, "40:12", 8.0)
    see(g, s, "40:14", 9.0)
    assert g.drain_fired() == []


# ---------------------------------------------------------------- the referee
def test_ten_disagreeing_readings_log_one_referee_line():
    s = snap()
    g = gate(s)
    lines = []
    for i in range(9):
        lines += g.referee(1, 0, (0, 0), {(1, 0)}, False, 61 * 60.0)
    assert lines == []
    lines += g.referee(1, 0, (0, 0), {(1, 0)}, False, 61 * 60.0)
    assert len(lines) == 1 and "feed referee" in lines[0] and "OCR 0-0" in lines[0] and "feed 1-0" in lines[0]
    assert g.referee(1, 0, (0, 0), {(1, 0)}, False, 61 * 60.0) == []     # once per episode


def test_agreement_or_a_missing_score_resets_the_referee():
    g = FeedGate()
    for i in range(9):
        g.referee(1, 0, (0, 0), {(1, 0)}, False, 100.0)
    g.referee(1, 0, (1, 0), {(1, 0)}, False, 100.0)          # agrees
    for i in range(9):
        assert g.referee(1, 0, (0, 0), {(1, 0)}, False, 100.0) == []
    g.referee(1, 0, None, {(1, 0)}, False, 100.0)             # no OCR score
    for i in range(9):
        assert g.referee(1, 0, (0, 0), {(1, 0)}, False, 100.0) == []
    assert g.referee(1, 0, (0, 0), None, False, 100.0) == []  # the feed cannot tell


def test_the_referee_accepts_either_side_order_and_ignores_replays():
    g = FeedGate()
    for i in range(12):
        assert g.referee(1, 0, (0, 1), {(1, 0)}, False, 100.0) == []
        assert g.referee(1, 0, (5, 5), {(1, 0)}, True, 100.0) == []


def test_the_mapper_gives_the_scores_the_feed_had_around_a_stream_moment():
    s = snap()
    m = FeedMapper()
    m.set_games([dataclasses.replace(s, clock_s=3000.0, home_score=0, away_score=0)])
    m.set_games([dataclasses.replace(s, clock_s=3300.0, home_score=0, away_score=1)])
    assert m.allowed_scores(s.game_id, 3000.0, None) == {(0, 0)}
    assert m.allowed_scores(s.game_id, 3320.0, None) == {(0, 1)}
    assert m.allowed_scores(s.game_id, 3290.0, None) >= {(0, 0), (0, 1)}      # the goal may be a few seconds off on either side
    assert m.allowed_scores(s.game_id, 100.0, None) is None                   # before the recording began: cannot tell
    assert m.allowed_scores("nope", 3000.0, None) is None


# ---------------------------------------------------------------- settings
def test_feed_event_weight_defaults_to_shadow_and_is_in_the_popup():
    assert Config().feed_event_weight == 0.0
    shown = {key for _, rows in LAYOUT for key, _, _ in rows}
    assert "scoring.feed_event_weight" in shown


# ---------------------------------------------------------------- through the server
def test_the_server_wires_the_referee_and_the_feed_goal(caplog):
    import logging
    from daemon.server import Server
    s = snap()
    server = Server()
    server.handle_message({"type": "register", "tab_id": 5})
    server.feed_map.set_games([dataclasses.replace(s, clock_s=2800.0, home_score=0, away_score=0)])
    server.feed_map.set_games([dataclasses.replace(s, clock_s=3000.0, home_score=1, away_score=0)])
    server.feed_gate.fresh = {s.game_id}
    with caplog.at_level(logging.INFO, logger="daemon"):
        for i in range(16):
            server._update_game_states({(5, 0): {"texts": ["COMO", "0", "ROMA", "0"], "clocks": [f"50:{10 + i:02d}"]}}, 100.0 + 3 * i)
    assert server.feed_map.mapping(5, 0) is not None
    assert "feed referee tab 5 tile 0: OCR 0-0, feed 1-0" in caplog.text
