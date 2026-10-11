import asyncio
import logging
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
    config = Config(min_dwell_s=0, warmup_s=0, window_s=1.0, confirm_s=0)
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
    config = Config(warmup_s=0, min_dwell_s=0, window_s=1.0, confirm_s=0)
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


def test_a_takeaway_banner_boosts_the_stream_and_its_tile_by_the_big_play_strength():
    config = Config(warmup_s=0)
    server = Server(Scorer(config))
    server.handle_message({"type": "register", "tab_id": 12})
    now = time.monotonic()
    for t in range(5):
        server.scorer.add_audio(12, 0.05, now - 5 + t)
    server._update_game_states({(12, 0): {"texts": ["TAKEAWAY!", "UCLA", "10", "6", "OREGON"], "clocks": []}}, now)
    assert server.scorer.components(12, now)["event"] == pytest.approx(config.big_play_strength, abs=0.01)
    assert (12, 0) in server.event_at
    fresh = 1 - (now - server.event_at[(12, 0)]) / config.event_s
    assert fresh == pytest.approx(config.big_play_strength, abs=0.01)


# ---- expand confirmation: slots keep their size, the streams move; the sound shows what really happened ------

def _tiles(audible):
    sizes = {0: (1272, 577), 1: (622, 282), 2: (622, 282)}
    return [{"index": i, "audible": i == audible, "left": 0, "top": 0, "width": sizes[i][0], "height": sizes[i][1]} for i in (0, 1, 2)]


def _report(server, tab, audible):
    server.handle_message({"type": "video_rect", "tab_id": tab, "rects": _tiles(audible), "visible": True, "fullscreen": True})


def test_a_confirmed_expand_swaps_the_records_of_the_two_slots():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 20})
    server.trackers[(20, 0)], server.trackers[(20, 2)] = "main-game", "tile2-game"
    server.pending_expand[20] = (2, time.monotonic() + 8)
    _report(server, 20, audible=0)                      # the sound is on the big slot: the stream moved there
    assert server.trackers[(20, 0)] == "tile2-game" and server.trackers[(20, 2)] == "main-game"
    assert 20 not in server.pending_expand


def test_a_click_that_only_selected_the_tile_swaps_nothing_and_warns(caplog):
    server = Server()
    server.handle_message({"type": "register", "tab_id": 20})
    server.trackers[(20, 0)], server.trackers[(20, 2)] = "main-game", "tile2-game"
    server.pending_expand[20] = (2, time.monotonic() + 8)
    with caplog.at_level(logging.WARNING):
        _report(server, 20, audible=2)                  # the sound went to the clicked tile: only selected
    assert server.trackers[(20, 0)] == "main-game" and server.trackers[(20, 2)] == "tile2-game"
    assert 20 not in server.pending_expand and "only selected" in caplog.text


def test_an_unclear_report_keeps_waiting_until_the_deadline():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 20})
    server.pending_expand[20] = (2, time.monotonic() + 8)
    _report(server, 20, audible=1)                      # the sound is on a third tile: neither case
    assert 20 in server.pending_expand


def test_team_rule_limits_slots_and_keeps_the_tabs_own_choice_on_a_clash():
    server = Server(scorer=Scorer())
    rules = [{"name": "PSG", "mult": 1, "slots": ["MAIN", "LEFT"]}]
    server.handle_message({"type": "register", "tab_id": 1, "teams": "PSG vs Lyon", "favourites": rules})
    server.handle_message({"type": "register", "tab_id": 2, "teams": "Napoli", "favourites": rules})
    server.handle_message({"type": "register", "tab_id": 3, "teams": "PSG", "allowed": ["RIGHT_BOT"], "favourites": rules})
    assert server.limits[1] == {"MAIN", "LEFT"}
    assert server.limits[2] is None
    assert server.limits[3] == {"RIGHT_BOT"}


def test_the_popup_can_read_change_and_reset_settings(tmp_path):
    from daemon.settings import load_settings

    settings = load_settings(tmp_path / "settings.toml")
    server = Server(scorer=Scorer(settings.scoring), settings=settings)
    reply = server.handle_settings_message({"type": "get_settings"})
    assert reply["type"] == "settings" and reply["groups"] and reply["errors"] == {}
    reply = server.handle_settings_message({"type": "set_settings", "changes": {"scoring.min_score": 0.5, "scoring.bad": 1}})
    assert server.scorer.config.min_score == 0.5            # the running scorer sees it at once
    assert list(reply["errors"]) == ["scoring.bad"]
    server.handle_settings_message({"type": "reset_settings"})
    assert server.scorer.config.min_score == Config().min_score
    assert server.handle_settings_message({"type": "audio"}) is None
    assert Server().handle_settings_message({"type": "get_settings"})["error"]


def test_a_score_change_is_ignored_while_the_game_is_on_a_break():
    server = Server(Scorer(Config(warmup_s=0)))
    server.handle_message({"type": "register", "tab_id": 13})
    board = lambda a, extra=(): {(13, 0): {"texts": ["HOUSTON", str(a), "K-STATE", "3", *extra], "clocks": []}}
    for i in range(5):
        server._update_game_states(board(7), 100.0 + i * 3)
    for i in range(5):                                                   # numbers change under an ad / break screen
        server._update_game_states(board(10, ["COMMERCIAL BREAK IN PROGRESS"]), 120.0 + i * 3)
    assert server.trackers[(13, 0)].state == "break"
    assert (13, 0) not in server.event_at


def test_make_main_from_the_popup_arranges_the_windows_and_reports_the_hold():
    server = Server(scorer=Scorer(Config(confirm_s=0.0)))
    sent = []

    async def capture(obj):
        sent.append(obj)

    server._broadcast = capture
    server.handle_message({"type": "register", "tab_id": 1, "role": "MAIN", "is_main": True})
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT"})
    reply = asyncio.run(server.handle_control_message({"type": "force_main", "tab_id": 2}))
    assert reply["error"] is None and reply["tab_id"] == 2 and reply["left_s"] > 0
    assert sent == [{"type": "arrange", "moves": [{"tab_id": 2, "role": "MAIN"}, {"tab_id": 1, "role": "LEFT"}]}]
    assert server.scorer.main == 2
    assert asyncio.run(server.handle_control_message({"type": "release_hold"}))["left_s"] == 0
    assert asyncio.run(server.handle_control_message({"type": "get_hold"}))["tab_id"] is None
    assert asyncio.run(server.handle_control_message({"type": "audio"})) is None


def test_make_main_is_refused_by_the_slot_rules_and_main_stays():
    server = Server(scorer=Scorer(Config(confirm_s=0.0)))

    async def capture(obj):
        pass

    server._broadcast = capture
    server.handle_message({"type": "register", "tab_id": 1, "role": "MAIN", "is_main": True})
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT", "allowed": ["LEFT"]})
    reply = asyncio.run(server.handle_control_message({"type": "force_main", "tab_id": 2}))
    assert "not allowed" in reply["error"] and server.scorer.main == 1 and reply["tab_id"] is None


def test_a_late_close_game_gets_positive_leverage_and_a_late_blowout_negative():
    server = Server(scorer=Scorer())
    server.handle_message({"type": "register", "tab_id": 13, "sport": "American football"})
    server.handle_message({"type": "register", "tab_id": 14, "sport": "American football"})
    for i in range(6):
        now = 100.0 + 3 * i
        results = {(13, 0): {"texts": ["TEXAS", "24", "OKLAHOMA", "24", "4th", "3rd & 4"], "clocks": []},
                   (14, 0): {"texts": ["MARYLAND", "16", "OHIO STATE", "45", "4th", "1st & 10"], "clocks": []}}
        server._update_game_states(results, now)
    assert server.scorer.streams[13].leverage == pytest.approx(1.0)      # tied, 4th quarter
    assert server.scorer.streams[14].leverage < 0                         # 29 points apart, 4th quarter


def test_a_stray_main_tab_leaving_does_not_leave_the_daemon_without_a_main():
    server = Server(scorer=Scorer())
    server.handle_message({"type": "register", "tab_id": 1, "role": "MAIN", "is_main": True})
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT"})
    server.handle_message({"type": "register", "tab_id": 9, "role": "MAIN", "is_main": True})   # a stray window in the MAIN slot
    server.handle_message({"type": "unregister", "tab_id": 9})
    assert server.scorer.main == 1


def test_the_safety_net_adopts_the_tab_in_the_main_window_when_there_is_no_main():
    server = Server(scorer=Scorer())
    server.handle_message({"type": "register", "tab_id": 1, "role": "MAIN"})   # no is_main flag: the scorer has no main
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT"})
    assert server.scorer.main is None
    server._ensure_main()
    assert server.scorer.main == 1
    server.scorer.main = 2
    server._ensure_main()
    assert server.scorer.main == 2    # an existing MAIN is never changed


def _football_board(a, b=3):
    return {(14, 0): {"texts": ["HOUSTON", str(a), "K-STATE", str(b), "1ST & GOAL"], "clocks": ["10:45"]}}


def test_a_field_goal_counts_less_than_a_touchdown_in_american_football():
    config = Config(warmup_s=0)
    server = Server(Scorer(config))
    server.handle_message({"type": "register", "tab_id": 14, "sport": "american football"})
    for i in range(5):
        server._update_game_states(_football_board(7), 100.0 + i * 3)
    for i in range(5):
        server._update_game_states(_football_board(10), 120.0 + i * 3)      # +3: a field goal
    for t in range(6):
        server.scorer.add_audio(14, 0.05, 127.0 + t)
    server.scorer.set_tags(14, 0.0, 0.0, 0.0, 132.0)
    event = server.scorer.components(14, 132.0)["event"]
    assert 0.4 < event <= config.field_goal_strength + 0.01      # a touchdown would be about 1.0


def test_a_replay_banner_over_a_live_scorebug_flags_the_stream_and_a_score_in_it_is_ignored():
    server = Server(Scorer(Config(warmup_s=0)))
    server.handle_message({"type": "register", "tab_id": 14, "sport": "american football"})
    for i in range(5):
        server._update_game_states(_football_board(7), 100.0 + i * 3)
    replay = {(14, 0): {"texts": ["HOUSTON", "14", "K-STATE", "3", "1ST & GOAL", "REPLAY"], "clocks": ["10:45"]}}
    for i in range(5):
        server._update_game_states(replay, 120.0 + i * 3)
    assert server.scorer.streams[14].replay is True
    assert (14, 0) not in server.event_at


# ---- commentary listener (measuring only) ----------------------------------------------------------------------------

def test_the_speech_cues_are_found_in_english_and_french_transcripts():
    from daemon.speech import find_speech_cues, to_whisper_rate

    assert find_speech_cues("He is in the red zone, first and goal at the four yard line.") == ["red zone"]
    assert find_speech_cues("Penalty kick for Roma, the referee points to the spot.") == ["penalty"]
    assert find_speech_cues("Corner kick for Como, and the flag is up for offside.") == ["flag", "corner", "offside"]
    assert find_speech_cues("Coup franc aux vingt mètres, quelle occasion, le poteau !") == ["free kick", "post", "chance"]
    assert find_speech_cues("Goaaaal! Touchdown!") == ["touchdown", "goal"]
    assert find_speech_cues("The weather is nice today.") == []
    assert len(to_whisper_rate(np.ones(100, dtype=np.float32))) == 50


@pytest.mark.asyncio
async def test_the_commentary_loop_logs_the_words_and_each_cue_once_per_cooldown(caplog):
    class FakeListener:
        device = "fake"

        def transcribe(self, samples):
            return "Penalty for Roma, what a corner", "en"

    config = Config(speech_interval_s=0.01, speech_window_s=1.0, speech_cue_cooldown_s=60.0)
    server = Server(Scorer(config), speech=FakeListener())
    server.speech_buffers[5] = np.zeros(SAMPLE_RATE_FOR_TEST, dtype=np.float32)
    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(server._speech_loop())
        await asyncio.sleep(1.2)       # the loop never sleeps less than one second
        task.cancel()
    heard = [r.getMessage() for r in caplog.records if "speech heard" in r.getMessage()]
    cues = [r.getMessage() for r in caplog.records if "speech cue" in r.getMessage()]
    assert heard and "tab 5 [en]" in heard[0]
    assert sorted(m.split(" tab ")[0] for m in cues) == ["speech cue corner", "speech cue penalty"]   # each once


def test_audio_is_buffered_for_the_listener_only_when_there_is_one():
    frame = struct.pack("<I", 7) + (np.ones(3200, dtype="<i2") * 1000).tobytes()
    plain = Server(Scorer())
    plain.scorer.register(7)
    plain.handle_audio(frame)
    assert plain.speech_buffers == {}
    class Quiet:
        device = "fake"
    listening = Server(Scorer(Config(speech_window_s=0.05)), speech=Quiet())
    listening.scorer.register(7)
    listening.handle_audio(frame)
    assert len(listening.speech_buffers[7]) == int(0.05 * 32000)      # only the last window is kept


SAMPLE_RATE_FOR_TEST = 32000


def test_a_french_post_is_found_with_any_article():
    from daemon.speech import find_speech_cues

    assert find_speech_cues("le ballon a rebondi sur son poteau") == ["post"]
    assert find_speech_cues("il touche la transversale") == ["post"]


def test_a_near_chance_is_a_spoken_cue():
    from daemon.speech import find_speech_cues

    assert find_speech_cues("Juste à côté de la base du poteau droit.") == ["post", "chance"]
    assert find_speech_cues("quelle occasion, quelle action !") == ["chance"]
    assert find_speech_cues("What a save! So close.") == ["chance"]
    assert find_speech_cues("The pass goes to the left wing.") == []


def test_goal_words_include_opening_the_scoring_and_equalising():
    from daemon.speech import find_speech_cues

    assert find_speech_cues("Roma ouvre le score !") == ["goal"]
    assert find_speech_cues("Como égalise juste avant la pause") == ["goal"]
    assert find_speech_cues("Les poteaux ont sauvé le gardien") == ["post"]


# ---- semantic reading of the commentary (measuring only) -------------------------------------------------------------

def test_the_commentary_is_cut_into_pieces_and_the_level_needs_a_margin_over_ordinary_talk():
    from daemon.semantic import level, pieces

    assert pieces("Quelle occasion, quelle action ! Oh la la.") == ["Quelle occasion", "quelle action", "Oh la la", "Quelle occasion, quelle action ! Oh la la."]
    assert pieces("Oui.") == []   # one word is not worth a reading
    assert level({"high": 0.7, "mid": 0.4, "low": 0.3}, 0.55, 0.05) == "high"
    assert level({"high": 0.7, "mid": 0.4, "low": 0.68}, 0.55, 0.05) == ""        # as close to ordinary talk: no
    assert level({"high": 0.4, "mid": 0.6, "low": 0.3}, 0.55, 0.05) == "mid"
    assert level({"high": 0.4, "mid": 0.4, "low": 0.3}, 0.55, 0.05) == ""


@pytest.mark.asyncio
async def test_the_loop_logs_the_semantic_reading_and_a_level_once_per_cooldown(caplog):
    class FakeListener:
        device = "fake"

        def transcribe(self, samples):
            return "il touche la barre", "fr"

    class FakeMatcher:
        def match(self, text):
            return {"high": 0.95, "mid": 0.3, "low": 0.2, "phrase": "il touche la barre", "piece": text}

    config = Config(speech_interval_s=0.01, speech_window_s=1.0, speech_cue_cooldown_s=60.0)
    server = Server(Scorer(config), speech=FakeListener(), semantic=FakeMatcher())
    server.speech_buffers[5] = np.zeros(SAMPLE_RATE_FOR_TEST, dtype=np.float32)
    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(server._speech_loop())
        await asyncio.sleep(2.3)
        task.cancel()
    messages = [r.getMessage() for r in caplog.records]
    assert sum(m.startswith("semantic heard") for m in messages) >= 2
    assert sum(m.startswith("semantic high") for m in messages) == 1


def test_semantic_level_uses_the_best_ordinary_talk_as_base_and_a_stricter_mid_minimum():
    from daemon.semantic import level

    ordinary = {"high": 0.78, "mid": 0.4, "low": 0.5, "neutral": 0.74}
    assert level(ordinary, 0.75, 0.10) == ""                                   # as close to neutral talk as to the phrase
    assert level({"high": 0.95, "mid": 0.5, "low": 0.5, "neutral": 0.6}, 0.75, 0.10) == "high"
    assert level({"high": 0.5, "mid": 0.78, "low": 0.4, "neutral": 0.4}, 0.75, 0.10, 0.80) == ""   # below the mid minimum
    assert level({"high": 0.5, "mid": 0.85, "low": 0.4, "neutral": 0.4}, 0.75, 0.10, 0.80) == "mid"


def test_a_sending_off_is_a_card_cue_in_french_and_english():
    from daemon.speech import find_speech_cues

    assert find_speech_cues("sans l'emputeur, Daniel Malin exclu.") == ["card"]
    assert find_speech_cues("He has been sent off, a straight red.") == ["card"]
    assert find_speech_cues("Le joueur est expulsé après le tackle") == ["card", "foul"]
    assert find_speech_cues("Il ne faut pas exclure cette option") == []        # a verb, not a sending off


def test_a_replay_announcement_and_a_foul_are_speech_cues():
    from daemon.speech import find_speech_cues

    assert find_speech_cues("Alors, revoyons-le là, il manque de maîtrise sur ce tacle.") == ["replay", "foul"]
    assert find_speech_cues("On joue à dix maintenant.") == ["card"]
    assert find_speech_cues("The replay shows a late tackle.") == ["replay", "foul"]


def test_the_tests_never_touch_the_real_sports_file(tmp_path):
    import hashlib

    from daemon import logsetup
    real = logsetup.LOG_DIR / "sports.json"
    before = hashlib.md5(real.read_bytes()).hexdigest() if real.exists() else None
    server = Server()
    assert server.sport_memory.path is not None and logsetup.LOG_DIR not in server.sport_memory.path.parents
    server.handle_message({"type": "register", "tab_id": 8})
    for i in range(3):
        server._update_game_states({(8, 0): {"texts": ["HOUSTON", "0", "K-STATE", "0", "1ST & GOAL"], "clocks": ["10:45"]}}, 100.0 + i * 3)
    assert server.sport_memory.get(8, time.time()) == "American football"   # remembered, but in the test's own folder
    after = hashlib.md5(real.read_bytes()).hexdigest() if real.exists() else None
    assert before == after


def test_the_memory_file_can_be_given_to_the_server(tmp_path):
    path = tmp_path / "mine.json"
    server = Server(sport_memory_path=path)
    server.handle_message({"type": "register", "tab_id": 8})
    for i in range(3):
        server._update_game_states({(8, 0): {"texts": ["HOUSTON", "0", "K-STATE", "0", "1ST & GOAL"], "clocks": ["10:45"]}}, 100.0 + i * 3)
    assert path.exists()
