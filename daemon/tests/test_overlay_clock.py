"""The extension's own overlay over a stream must not feed its countdown ('0:25') to the feed gate as the game clock: on
2026-10-21 19:29 (Lille-Galatasaray, tab 485663016) it started a false 'feed rewind' (2586 s behind) that swallowed the 2-2 goal for 45 s."""
import dataclasses
import logging

from daemon.game_state import overlay_on_screen
from daemon.server import Server
from daemon.tests.test_feed_stage3 import snap

OVERLAY = ["OmniSportsSwitch", "This window is", "Main", "Freeze Main (Ctrl+Shift+P)", "Freeze Window", "(Ctrl+Shift+L)",
           "Re-track this tab (Ctrl+Shift+Y)", "Sport", "Hold Window 2", "Untrack"]


def test_overlay_texts_are_recognised_and_a_scorebug_is_not():
    assert overlay_on_screen(OVERLAY)
    assert overlay_on_screen(["43.31", "LIL", "2", "2", "GAL", "Re-track this tab (Ctrl+Shift+Y)"])
    assert not overlay_on_screen(["43.31", "LIL", "2", "2", "GAL", "#FOOT"])


def _server():
    s = snap()
    server = Server()
    server.handle_message({"type": "register", "tab_id": 5})
    server.feed_map.set_games([dataclasses.replace(s, clock_s=2800.0, home_score=0, away_score=0)])
    server.feed_map.set_games([dataclasses.replace(s, clock_s=3000.0, home_score=0, away_score=0)])
    server.feed_gate.fresh = {s.game_id}
    return server


def test_an_overlay_countdown_does_not_start_a_rewind(caplog):
    server = _server()
    board = {"texts": ["COMO", "0", "ROMA", "0"]}
    with caplog.at_level(logging.INFO, logger="daemon"):
        for i in range(10):
            server._update_game_states({(5, 0): {**board, "clocks": [f"50:{10 + i:02d}"]}}, 100.0 + 3 * i)
        for i in range(10):   # the overlay covers the clock: only its '0:25' is read
            server._update_game_states({(5, 0): {"texts": OVERLAY, "clocks": ["0:25"]}}, 130.0 + 3 * i)
        for i in range(3):
            server._update_game_states({(5, 0): {**board, "clocks": [f"50:{40 + i:02d}"]}}, 160.0 + 3 * i)
    assert "feed rewind" not in caplog.text
    assert not server.feed_gate.is_rewinding(5, 0)


def test_the_same_countdown_without_the_overlay_text_is_still_a_rewind(caplog):
    server = _server()
    board = {"texts": ["COMO", "0", "ROMA", "0"]}
    with caplog.at_level(logging.INFO, logger="daemon"):
        for i in range(10):
            server._update_game_states({(5, 0): {**board, "clocks": [f"50:{10 + i:02d}"]}}, 100.0 + 3 * i)
        for i in range(6):   # a real rewind: the scorebug stays, the clock is far lower
            server._update_game_states({(5, 0): {**board, "clocks": ["10:25"]}}, 130.0 + 3 * i)
    assert "feed rewind tab 5" in caplog.text
