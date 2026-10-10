import asyncio
import json
import struct
import time

import numpy as np
import pytest
from websockets.asyncio.client import connect

from daemon.arrange import plan_swap
from daemon.scoring import Config, Scorer
from daemon.server import Server

PORT = 8799


@pytest.mark.asyncio
async def test_crowd_roar_triggers_swap_message():
    config = Config(min_dwell_s=0, warmup_s=0, window_s=1.0)
    server = Server(Scorer(config))
    task = asyncio.create_task(server.run(port=PORT))
    await asyncio.sleep(0.2)
    try:
        async with connect(f"ws://127.0.0.1:{PORT}") as ws:
            await ws.send(json.dumps({"type": "register", "tab_id": 1, "is_main": True, "role": "MAIN"}))
            await ws.send(json.dumps({"type": "register", "tab_id": 2, "role": "RIGHT_BOT"}))
            for _ in range(5):  # normal level for both
                await ws.send(json.dumps({"type": "audio", "tab_id": 1, "rms": 0.05}))
                await ws.send(json.dumps({"type": "audio", "tab_id": 2, "rms": 0.05}))
                await asyncio.sleep(0.05)
            for _ in range(8):  # tab 2 roars
                await ws.send(json.dumps({"type": "audio", "tab_id": 1, "rms": 0.05}))
                await ws.send(json.dumps({"type": "audio", "tab_id": 2, "rms": 0.20}))
                server.scorer.set_tags(2, 0.9, 0.1, 0.0, time.monotonic())
                await asyncio.sleep(0.1)
            reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert reply == {"type": "arrange", "moves": [{"tab_id": 2, "role": "MAIN"}, {"tab_id": 1, "role": "RIGHT_BOT"}]}
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_binary_audio_frame_feeds_the_scorer():
    server = Server()
    task = asyncio.create_task(server.run(port=PORT + 2))
    await asyncio.sleep(0.2)
    try:
        async with connect(f"ws://127.0.0.1:{PORT + 2}") as ws:
            await ws.send(json.dumps({"type": "register", "tab_id": 7}))
            pcm = (np.full(3200, 0.5) * 32767).astype("<i2").tobytes()  # constant level 0.5
            await ws.send(struct.pack("<I", 7) + pcm)
            await asyncio.sleep(0.2)
            level = server.scorer.streams[7].samples[-1][1]
            assert level == pytest.approx(0.5, abs=0.01)
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_bad_message_does_not_kill_connection():
    server = Server()
    task = asyncio.create_task(server.run(port=PORT + 1))
    await asyncio.sleep(0.2)
    try:
        async with connect(f"ws://127.0.0.1:{PORT + 1}") as ws:
            await ws.send("not json")
            await ws.send(json.dumps({"type": "audio"}))
            await ws.send(json.dumps({"type": "register", "tab_id": 1}))
            await asyncio.sleep(0.2)
            assert 1 in server.scorer.streams
    finally:
        task.cancel()


def test_video_rect_message_is_stored_and_cleared_on_unregister():
    server = Server()
    rect = {"left": 10, "top": 20, "width": 1280, "height": 720}
    server.handle_message({"type": "register", "tab_id": 5})
    server.handle_message({"type": "video_rect", "tab_id": 5, "rects": [rect], "visible": True, "fullscreen": False})
    assert server.rects[5]["rects"] == [rect] and server.rects[5]["visible"] is True
    server.handle_message({"type": "unregister", "tab_id": 5})
    assert 5 not in server.rects


def test_register_carries_sport_and_manual_status():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 3, "sport": "Football", "status": "break"})
    assert server.sports[3] == "Football"
    assert server.scorer.streams[3].override == "break"
    server.handle_message({"type": "register", "tab_id": 3, "sport": "Football", "status": "auto"})
    assert server.scorer.streams[3].override is None


def test_multiview_tab_gets_a_focus_command_for_the_tile_with_a_fresh_score_change():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 9})
    rects = [{"index": i, "audible": i == 0, "left": 0, "top": 0, "width": 700, "height": 480} for i in (0, 1, 2, 3)]
    server.handle_message({"type": "video_rect", "tab_id": 9, "rects": rects, "visible": True, "fullscreen": False})
    now = time.monotonic()
    assert server._focus_commands(now) == []
    server.event_at[(9, 2)] = now
    assert server._focus_commands(now) == [{"type": "focus_tile", "tab_id": 9, "index": 2}]


def test_single_video_tab_never_gets_focus_commands():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 9})
    one = [{"index": 0, "audible": True, "left": 0, "top": 0, "width": 1280, "height": 720}]
    server.handle_message({"type": "video_rect", "tab_id": 9, "rects": one, "visible": True, "fullscreen": False})
    server.event_at[(9, 0)] = time.monotonic()
    assert server._focus_commands(time.monotonic()) == []


def test_expanding_a_sidebar_tile_swaps_its_records_with_the_main_slot():
    server = Server()
    big = {"index": 0, "width": 1900, "height": 1000}
    small = lambda i: {"index": i, "width": 600, "height": 330}
    rects = [big, small(1), small(2), small(3)]
    server.trackers[(9, 0)], server.trackers[(9, 2)] = "main-stream", "tile2-stream"
    server.event_at[(9, 2)] = 123.0
    server._expand_swap(9, rects, 2)
    assert server.trackers[(9, 0)] == "tile2-stream" and server.trackers[(9, 2)] == "main-stream"
    assert server.event_at[(9, 0)] == 123.0 and (9, 2) not in server.event_at


def test_equal_grid_or_main_tile_expand_changes_nothing():
    server = Server()
    grid = [{"index": i, "width": 700, "height": 480} for i in range(4)]
    server.trackers[(9, 0)], server.trackers[(9, 1)] = "a", "b"
    server._expand_swap(9, grid, 1)
    assert server.trackers == {(9, 0): "a", (9, 1): "b"}
    sidebar = [{"index": 0, "width": 1900, "height": 1000}, {"index": 1, "width": 600, "height": 330}]
    server._expand_swap(9, sidebar, 0)
    assert server.trackers == {(9, 0): "a", (9, 1): "b"}


def test_stream_not_allowed_on_main_is_never_promoted():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 1, "role": "MAIN", "is_main": True})
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT", "allowed": ["LEFT", "RIGHT_BOT"]})
    assert server.scorer.streams[2].can_main is False
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT", "allowed": None})
    assert server.scorer.streams[2].can_main is True
    assert server.allowed[2] is None and server.roles[2] == "LEFT"


def test_favourite_is_recognised_from_the_teams_field():
    server = Server()
    favs = [{"name": "PSG", "mult": 2}]
    server.handle_message({"type": "register", "tab_id": 4, "teams": "PSG vs Lyon", "favourites": favs})
    assert server.scorer.streams[4].favourite == 2
    server.handle_message({"type": "register", "tab_id": 5, "teams": "Napoli vs Roma", "favourites": favs})
    assert server.scorer.streams[5].favourite == 1.0


def test_favourite_is_recognised_from_the_scoreboard_and_remembered_for_a_minute():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 6, "favourites": [{"name": "PSG", "mult": 2}]})
    results = {(6, 0): {"texts": ["PSG", "1", "LMFC", "0"], "clocks": []}}
    server._update_game_states(results, 100.0)
    assert server.scorer.streams[6].favourite == 2
    server._update_game_states({(6, 0): {"texts": ["1+3"], "clocks": []}}, 130.0)   # scorebug briefly gone
    assert server.scorer.streams[6].favourite == 2
    server._update_favourite(6, 200.0)                                                # more than a minute later
    assert server.scorer.streams[6].favourite == 1.0


def test_freeze_stops_tile_changes_on_main_only():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 9, "is_main": True})
    server.handle_message({"type": "register", "tab_id": 10})
    rects = [{"index": i, "audible": i == 0, "left": 0, "top": 0, "width": 700, "height": 480} for i in (0, 1, 2)]
    for tab in (9, 10):
        server.handle_message({"type": "video_rect", "tab_id": tab, "rects": rects, "visible": True, "fullscreen": False})
    now = time.monotonic()
    server.event_at[(9, 2)] = now
    server.event_at[(10, 2)] = now
    server.handle_message({"type": "freeze", "value": True})
    assert server.scorer.frozen is True
    assert server._focus_commands(now) == [{"type": "focus_tile", "tab_id": 10, "index": 2}]   # MAIN (tab 9) is locked
    server.handle_message({"type": "freeze", "value": False})
    assert {c["tab_id"] for c in server._focus_commands(now + 1)} == {9}                        # tab 10 is dwelling


def test_while_frozen_the_winner_goes_to_left_and_main_stays():
    config = Config(warmup_s=0, min_dwell_s=0, window_s=1.0)
    server = Server(Scorer(config))
    for tab, role, main in ((1, "MAIN", True), (2, "LEFT", False), (3, "RIGHT_BOT", False)):
        server.handle_message({"type": "register", "tab_id": tab, "role": role, "is_main": main})
    now = time.monotonic()
    for t in (0, 1, 2, 3, 4):
        for tab in (1, 2, 3):
            server.scorer.add_audio(tab, 0.05, now - 5 + t)
    server.scorer.set_event(3, now)                      # a goal in the RIGHT_BOT stream
    server.handle_message({"type": "freeze", "value": True})
    swap = server._next_swap(now)
    assert (swap.promote, swap.demote) == (3, 2)         # replaces the LEFT stream
    assert server.scorer.main == 1                       # MAIN untouched
    assert plan_swap(swap.promote, swap.demote, server.roles, server.allowed) == [(3, "LEFT"), (2, "RIGHT_BOT")]


def test_sport_is_detected_from_the_scoreboard_and_sets_the_break_rules():
    from daemon.game_state import profile_for
    server = Server()
    server.handle_message({"type": "register", "tab_id": 8})
    for i in range(3):
        server._update_game_states({(8, 0): {"texts": ["HOUSTON", "0", "K-STATE", "0", "1ST & GOAL"], "clocks": ["10:45"]}}, 100.0 + i * 3)
    assert server.trackers[(8, 0)].profile == profile_for("American football")


def test_a_sport_chosen_in_the_popup_wins_over_the_detected_one():
    from daemon.game_state import profile_for
    server = Server()
    server.handle_message({"type": "register", "tab_id": 8, "sport": "Basketball"})
    for i in range(3):
        server._update_game_states({(8, 0): {"texts": ["HOUSTON", "0", "K-STATE", "0", "1ST & GOAL"], "clocks": ["10:45"]}}, 100.0 + i * 3)
    assert server.trackers[(8, 0)].profile == profile_for("Basketball")


def test_a_tab_whose_player_shows_the_ad_placeholder_gets_a_negative_score():
    config = Config(warmup_s=0)
    server = Server(Scorer(config))
    server.handle_message({"type": "register", "tab_id": 11})
    now = time.monotonic()
    for t in range(5):
        server.scorer.add_audio(11, 0.05, now - 5 + t)
    ad = {(11, 0): {"texts": ["COMMERCIAL BREAK IN PROGRESS", "0:37"], "clocks": ["0:37"]}}
    server._update_game_states(ad, 100.0)
    server._update_game_states(ad, 103.0)
    assert server.scorer.streams[11].on_ad is True
    assert server.scorer.score(11, now) == pytest.approx(-0.5)
    live = {(11, 0): {"texts": ["TEXAS", "14", "7", "OKLAHOMA"], "clocks": ["18:50"]}}
    for i in range(4):
        server._update_game_states(live, 120.0 + i * 3)
    assert server.scorer.streams[11].on_ad is False
