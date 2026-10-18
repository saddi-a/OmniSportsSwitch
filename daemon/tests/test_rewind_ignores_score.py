"""A score change on a tile whose stream is rewinding (feed gate rewind) is a replayed goal: no event, goal hold or swap
bonus (`scoring.rewind_ignores_score`), unless the feed shows that very score at the stream clock (a stale rewind flag)."""
import dataclasses
import json
import logging
import pathlib

from daemon import feed
from daemon.scoring import Config
from daemon.server import Server
from daemon.settings import LAYOUT

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TAB = 5


def _snap():
    return feed.parse_scoreboard(json.loads((FIXTURES / "feed_soccer_scoreboard.json").read_text(encoding="utf-8")), "ita.1")[0]


def _mapped_server(setting=1.0):
    s = _snap()
    server = Server()
    server.handle_message({"type": "register", "tab_id": TAB})
    server.scorer.config.rewind_ignores_score = setting
    server.feed_map.set_games([dataclasses.replace(s, clock_s=2800.0, home_score=0, away_score=0)])
    server.feed_map.set_games([dataclasses.replace(s, clock_s=3000.0, home_score=1, away_score=0)])
    server.feed_gate.fresh = {s.game_id}
    state = {"i": 0}

    def read(score, n=8):
        for _ in range(n):
            i = state["i"]
            state["i"] += 1
            server._update_game_states({(TAB, 0): {"texts": ["COMO", str(score[0]), "ROMA", str(score[1])], "clocks": [f"50:{10 + i:02d}"]}}, 100.0 + 3 * i)
        return 100.0 + 3 * state["i"]
    read((0, 0), 16)
    assert server.feed_map.mapping(TAB, 0) is not None
    return server, read


def _rewind(server):
    server.feed_gate.rewind[(TAB, 0)].active = True
    server.feed_gate.rewind[(TAB, 0)].since = 0.0
    server.feed_gate.rewind[(TAB, 0)].high = 5000.0   # the clock is far behind it: the rewind stays on
    server.feed_gate.rewind[(TAB, 0)].since = 100.0


def _goal_counted(server, now):
    return server.scorer.goal_hold_left(TAB, now) > 0


def test_rewind_active_and_unconfirmed_score_makes_no_event(caplog):
    server, read = _mapped_server()
    _rewind(server)
    with caplog.at_level(logging.INFO, logger="daemon"):
        now = read((0, 2))
    assert "the stream is replaying its past" in caplog.text
    assert not _goal_counted(server, now) and "ocr-only" not in caplog.text
    assert server.feed_gate.is_rewinding(TAB, 0)


def test_control_no_rewind_counts():
    server, read = _mapped_server()
    now = read((0, 2))
    assert _goal_counted(server, now)


def test_setting_zero_keeps_the_old_behaviour():
    server, read = _mapped_server(setting=0.0)
    _rewind(server)
    now = read((0, 2))
    assert _goal_counted(server, now)


def test_rewind_flag_but_the_feed_shows_this_score_counts_and_clears_the_stale_flag(caplog):
    server, read = _mapped_server()
    _rewind(server)
    with caplog.at_level(logging.INFO, logger="daemon"):
        now = read((1, 0))                    # the feed has 1-0 at this stream clock: a real goal
    assert _goal_counted(server, now)
    assert not server.feed_gate.is_rewinding(TAB, 0) and "the high point was stale" in caplog.text


def test_held_change_during_a_rewind_is_dropped(caplog):
    server, read = _mapped_server()
    _rewind(server)
    server.held_change[(TAB, 0)] = (1.0, 100.0 + 3 * 16)
    with caplog.at_level(logging.INFO, logger="daemon"):
        now = read((0, 0), 2)
    assert not _goal_counted(server, now) and "counted late" not in caplog.text
    assert (TAB, 0) not in server.held_change and "replaying its past (rewind)" in caplog.text


def test_held_change_without_a_rewind_counts_late(caplog):
    server, read = _mapped_server()
    server.held_change[(TAB, 0)] = (1.0, server.scorer.config.held_change_s and 100.0 + 3 * 16)
    with caplog.at_level(logging.INFO, logger="daemon"):
        now = read((0, 0), 2)
    assert "counted late" in caplog.text and _goal_counted(server, now)


def test_unmapped_tile_is_unknown_and_unchanged():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 9})
    for i in range(14):
        score = "0" if i < 6 else "1"
        server._update_game_states({(9, 0): {"texts": ["AAA", "0", "BBB", score], "clocks": [f"50:{10 + i:02d}"]}}, 100.0 + 3 * i)
    assert server.feed_gate.real_change(9, 0, 200.0).verdict == "unknown"
    assert server.scorer.goal_hold_left(9, 100.0 + 3 * 14) > 0


def test_is_rewinding_and_the_setting_in_the_popup():
    server, _ = _mapped_server()
    assert not server.feed_gate.is_rewinding(TAB, 0) and not server.feed_gate.is_rewinding(1, 1)
    assert Config().rewind_ignores_score == 1.0
    assert "scoring.rewind_ignores_score" in {key for _, rows in LAYOUT for key, _, _ in rows}
