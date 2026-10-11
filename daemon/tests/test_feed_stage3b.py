import dataclasses
import json
import pathlib

from daemon import feed
from daemon.feed_gate import FeedGate

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def snap():
    return feed.parse_scoreboard(json.loads((FIXTURES / "feed_soccer_scoreboard.json").read_text(encoding="utf-8")), "ita.1")[0]


def goal(s, clock_s=3180.0, key="g1", kind="goal"):
    return feed.FeedEvent(s.game_id, (key,), kind, None, clock_s, "53'", "ROMA", "Goal")


def gate(s):
    g = FeedGate(lead_s=10.0)
    g.fresh = {s.game_id}
    return g


def see(g, s, clock, now):
    return g.observe(1, 0, s.game_id, [clock] if clock else [], None, False, now)


# ---------------------------------------------------------------- held: no readable stream clock
def test_a_goal_with_no_readable_clock_is_reported_held_once_after_20_s():
    s = snap()
    g = gate(s)
    see(g, s, "52:00", 0.0)                       # the tile is mapped and had a clock
    g.add(goal(s), s, 10.0)
    assert see(g, s, None, 20.0) == []
    assert see(g, s, None, 29.0) == []
    lines = see(g, s, None, 31.0)
    assert len(lines) == 1 and "held" in lines[0] and "no readable stream clock" in lines[0] and "goal 53'" in lines[0]
    assert see(g, s, None, 40.0) == []            # once per goal


def test_no_held_line_while_the_clock_keeps_being_read():
    s = snap()
    g = gate(s)
    see(g, s, "52:00", 0.0)
    g.add(goal(s, 4000.0), s, 10.0)               # far ahead of the stream: waits quietly
    for i in range(1, 12):
        assert see(g, s, f"52:{i:02d}", 10.0 + 3 * i) == []


def test_old_goals_are_not_reported_held_when_a_tile_is_mapped_late():
    s = snap()
    g = gate(s)
    g.add(goal(s), s, 0.0)
    assert see(g, s, None, 400.0) == []


def test_non_scoring_events_are_never_held():
    s = snap()
    g = gate(s)
    see(g, s, "52:00", 0.0)
    g.add(goal(s, kind="red card"), s, 10.0)
    assert see(g, s, None, 60.0) == []


# ---------------------------------------------------------------- feed-only when the game is over
def test_a_feed_goal_with_no_ocr_change_is_feed_only_when_the_tile_turns_over():
    s = snap()
    g = gate(s)
    see(g, s, "52:00", 0.0)
    g.add(goal(s), s, 10.0)
    lines = g.game_over(1, 0, 60.0)
    assert len(lines) == 1 and "feed-only" in lines[0] and "over" in lines[0] and "goal 53'" in lines[0]
    assert g.game_over(1, 0, 61.0) == []          # once


def test_game_over_says_nothing_when_the_ocr_confirmed_the_goal_or_the_tile_is_unmapped():
    s = snap()
    g = gate(s)
    see(g, s, "53:10", 0.0)
    g.add(goal(s), s, 1.0)
    assert g.real_change(1, 0, 2.0).verdict == "confirmed"
    assert g.game_over(1, 0, 60.0) == []
    assert g.game_over(9, 9, 60.0) == []          # never mapped


def test_game_over_says_nothing_when_an_ocr_change_happened_around_the_goal():
    s = snap()
    g = gate(s)
    see(g, s, "52:00", 0.0)
    assert g.real_change(1, 0, 5.0).verdict == "ocr-only"      # the screen did change (the feed is simply late)
    g.add(goal(s, 4500.0), s, 10.0)                           # a different moment of the game clock
    assert g.game_over(1, 0, 60.0) == []


def test_old_feed_goals_are_not_reported_at_game_over():
    s = snap()
    g = gate(s)
    see(g, s, "52:00", 0.0)
    g.add(goal(s), s, 10.0)
    assert g.game_over(1, 0, 10.0 + 3000.0) == []


# ---------------------------------------------------------------- feed lag against the picture
def test_confirmed_later_says_how_far_the_feed_was_behind_the_picture():
    s = snap()
    g = gate(s)
    see(g, s, "53:00", 100.0)
    assert g.real_change(1, 0, 100.0).verdict == "ocr-only"
    g.add(goal(s), s, 117.0)
    g.confirmations(120.0)
    assert any("feed lag +17 s (the feed was behind the picture)" in m for m in g.messages)


def test_confirmed_by_an_earlier_feed_event_says_how_far_the_feed_was_ahead():
    s = snap()
    g = gate(s)
    see(g, s, "53:10", 90.0)
    g.add(goal(s), s, 90.0)
    change = g.real_change(1, 0, 102.0)
    assert change.verdict == "confirmed" and "feed lag -12 s (the feed was ahead of the picture)" in change.lines[0]


def test_a_would_fire_confirmation_also_carries_the_lag():
    s = snap()
    g = gate(s)
    g.add(goal(s), s, 1.0)
    see(g, s, "52:55", 1.0)
    see(g, s, "52:56", 2.0)
    change = g.real_change(1, 0, 14.0)
    assert change.verdict == "confirmed" and "feed lag -13 s" in change.lines[0]


def test_the_server_reports_feed_only_when_the_tile_turns_over(caplog):
    import logging
    from daemon.server import Server
    s = snap()
    server = Server()
    server.handle_message({"type": "register", "tab_id": 5})
    server.feed_map.set_games([dataclasses.replace(s, clock_s=2800.0, home_score=0, away_score=0)])
    server.feed_gate.fresh = {s.game_id}
    from daemon.feed_map import Mapping, _Tile
    tile = _Tile()
    tile.lock = Mapping(s.game_id, "ita.1", "COMO-ROMA", "ocr", True, 3, 3)      # the tile is locked to the game
    server.feed_map.tiles[(5, 0)] = tile
    server.feed_gate.add(goal(s), s, 100.0)
    with caplog.at_level(logging.INFO, logger="daemon"):
        for i in range(3):                                   # the tracker needs a second reading to call the game over
            server._update_game_states({(5, 0): {"texts": ["FINAL", "COMO", "1", "ROMA", "0", "FULL TIME"], "clocks": []}}, 110.0 + 3 * i)
    assert server.scorer.streams[5].auto_state == "over"
    assert caplog.text.count("feed-only tab 5 tile 0") == 1
