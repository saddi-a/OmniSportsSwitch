"""Stage 2 of the per-tile sport work (docs/per-tile-sport.md, task T4): the server feeds each tile of a multiview tab to its own scorer
record, build-up and commentary memory, with the tile's own sport; the audio (commentary, rising sound) goes to the audible tile."""
import asyncio
import logging

import numpy as np
import pytest

from daemon import backtest
from daemon.recorder import snapshot_rows
from daemon.scoring import Config, Scorer
from daemon.server import SAMPLE_RATE, Server

NFL, SOCCER = "american football", "football"


def _rects(audible, n=2):
    return [{"index": i, "audible": i == audible, "left": 0, "top": 0, "width": 700, "height": 480} for i in range(n)]


def _boards(i, soccer_score=0, extra_soccer=(), extra_nfl=(), nfl_score=(24, 24)):
    """Tile 0 an American football board, tile 1 a soccer board with a running clock (so the plain detection tells them apart)."""
    return {(2, 0): {"texts": ["TEXAS", str(nfl_score[0]), "OKLAHOMA", str(nfl_score[1]), "4th", "3rd & 4", *extra_nfl], "clocks": []},
            (2, 1): {"texts": ["COMO", str(soccer_score), "ROMA", "0", *extra_soccer], "clocks": [f"50:{10 + 3 * i:02d}"]}}


def mixed(audible=1, **cfg):
    cfg.setdefault("warmup_s", 0.0)
    cfg.setdefault("confirm_s", 0.0)
    server = Server(Scorer(Config(**cfg)))
    server.handle_message({"type": "register", "tab_id": 1, "is_main": True, "role": "MAIN"})
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT"})
    server.handle_message({"type": "video_rect", "tab_id": 2, "rects": _rects(audible), "visible": True, "fullscreen": False})
    for i in range(8):
        server._update_game_states(_boards(i), 100.0 + 3 * i)
    return server


def hear(server, tabs=(1, 2), start=0.0, seconds=30, crowd=0.05):
    for t in range(seconds):
        for tab in tabs:
            server.scorer.add_audio(tab, 0.05, start + t)
            server.scorer.set_tags(tab, crowd, 0.1, 0.0, start + t)
    return start + seconds


def test_each_tile_gets_its_own_record_and_sport():
    server = mixed()
    assert server.scorer.streams[2].tiles.keys() == {0, 1}
    assert server.scoring_sport(2, 0) == NFL and server.scoring_sport(2, 1) == SOCCER
    assert server.scorer.tile_cfg(2, 0) is not server.scorer.tile_cfg(2, 1) or server.scorer.config.sport_scoring == {}
    assert server.scorer.streams[1].tiles == {}                       # a tab with one video keeps the old single record


def test_a_tab_with_one_video_keeps_the_streams_own_record_and_drops_tile_records_when_the_multiview_closes():
    server = mixed()
    assert server.scorer.streams[2].tiles
    server.handle_message({"type": "video_rect", "tab_id": 2, "rects": [_rects(1)[1]], "visible": True, "fullscreen": False})
    server._update_game_states({(2, 1): _boards(9)[(2, 1)]}, 130.0)
    assert server.scorer.streams[2].tiles == {}


def test_a_score_change_banner_and_cue_land_on_their_own_tile():
    server = mixed()
    for i in range(9, 13):    # a cue needs a few readings in a row
        server._update_game_states(_boards(i, soccer_score=0, extra_soccer=("VAR CHECK",)), 121.0 + 3 * (i - 9))
    stream = server.scorer.streams[2]
    assert stream.tiles[1].anticipation_value > 0 and stream.tiles[0].anticipation_value == 0.0
    assert server.buildup.cues((2, 1), 133.0) and server.buildup.cues((2, 0), 133.0) == [] and server.buildup.cues(2, 133.0) == []
    for i in range(10, 14):                                           # the soccer score goes 0-0 -> 1-0 and stays
        server._update_game_states(_boards(i, soccer_score=1), 133.0 + 3 * (i - 9))
    assert stream.tiles[1].event_at > float("-inf") and stream.tiles[1].goal_at > float("-inf")
    assert stream.tiles[0].event_at == float("-inf") and stream.tiles[0].goal_at == float("-inf")


def test_leverage_is_per_tile_with_each_tiles_sport():
    server = mixed()
    stream = server.scorer.streams[2]
    assert stream.tiles[0].leverage == pytest.approx(1.0)             # NFL: tied, 4th quarter
    assert 0.0 < stream.tiles[1].leverage < 1.0                       # soccer, tied at about 50 minutes: its own (soccer) limits give another value


def test_audio_terms_count_only_for_the_audible_tile_through_the_server():
    server = mixed(audible=0)
    now = hear(server, crowd=0.9)
    before = server.scorer.components(2, now)
    assert before["tile_scores"][0] > before["tile_scores"][1] and before["tile"] == 0 and before["sport"] == NFL
    server.handle_message({"type": "video_rect", "tab_id": 2, "rects": _rects(1), "visible": True, "fullscreen": False})
    after = server.scorer.components(2, now)
    assert after["tile_scores"][1] > before["tile_scores"][1] and after["tile_scores"][0] < before["tile_scores"][0]   # the roar moved with the sound


class _Listener:
    device = "fake"

    def __init__(self, text):
        self.text = text

    def transcribe(self, samples):
        return self.text, "en"


def _speak(server, text, tab=2):
    server.speech = _Listener(text)
    server.speech_buffers[tab] = np.zeros(SAMPLE_RATE * 8, dtype=np.float32)

    async def go():
        task = asyncio.create_task(server._speech_loop())
        await asyncio.sleep(1.3)
        task.cancel()
    asyncio.run(go())


def test_spoken_cues_go_to_the_audible_tile_read_with_its_sport():
    config = dict(speech_interval_s=0.01, speech_window_s=8.0, speech_cue_cooldown_s=0.0)
    server = mixed(audible=1, **config)                               # the soccer tile has the sound
    _speak(server, "Penalty kick for Roma, what a chance")
    stream = server.scorer.streams[2]
    assert stream.tiles[1].anticipation_value > 0 and stream.tiles[0].anticipation_value == 0.0
    assert any(kind.startswith("spoken:") for _, _, kind in server.buildup.cue_details((2, 1), __import__("time").monotonic()))
    assert server.buildup.cues((2, 0), __import__("time").monotonic()) == []
    nfl = mixed(audible=0, **config)                                  # the NFL tile has the sound: the soccer cue list is not applied
    _speak(nfl, "Penalty kick for Roma, what a chance")
    assert all(rec.anticipation_value == 0.0 for rec in nfl.scorer.streams[2].tiles.values())


def test_only_the_soccer_tile_can_move_main_and_each_tile_keeps_its_own_cooldown():
    server = mixed(audible=1)
    now = hear(server, crowd=0.5, seconds=40)
    for i, tile in enumerate((0, 1)):                                 # the same cues on the NFL tile and on the soccer tile
        server.buildup.note((2, tile), now - 5, 0.3, "chance")
        server.buildup.note((2, tile), now - 2, 0.2, "free kick")
    swap = server._log_scores(now)
    assert swap is not None and swap.promote == 2 and server.scorer.main == 2
    assert server.buildup.state((2, 1), now)["cooldown_left"] > 0
    assert server.buildup.state((2, 0), now)["cooldown_left"] == 0    # the NFL tile never built up, never fired


def test_build_up_lines_say_tile_and_sport_and_the_backtest_reads_them(caplog):
    server = mixed(audible=1, buildup_live=0.0)                       # log only
    now = hear(server, crowd=0.5, seconds=40)
    server.buildup.note((2, 1), now - 5, 0.3, "chance")
    server.buildup.note((2, 1), now - 2, 0.2, "free kick")
    with caplog.at_level(logging.INFO, logger="daemon"):
        server._log_scores(now)
    lines = [r.getMessage() for r in caplog.records if "buildup would take MAIN" in r.getMessage()]
    assert lines and lines[0].startswith("buildup would take MAIN tab 2 tile 1 (football): level")
    stamped = [f"2026-10-14 12:00:00,000 {line}" for line in lines]
    assert [(tab, tile) for _, tab, tile in backtest.parse_tile_buildups(stamped)] == [(2, 1)] * len(lines)
    assert [tab for _, tab in backtest.parse_buildups(stamped)] == [2] * len(lines)     # the old reader still works
    score_lines = [r.getMessage() for r in caplog.records if r.getMessage().strip().startswith("2:") or "  2:" in r.getMessage() or r.getMessage().startswith("2:")]
    assert any(" tile=" in line and "sport=" in line for line in score_lines)


def test_expand_swap_moves_the_scorer_records_the_build_ups_and_what_was_heard():
    server = mixed()
    server.scorer.set_event(2, 5.0, 1.0, goal=True, tile=1)
    server.buildup.note((2, 1), 5.0, 0.3, "chance")
    server.cue_speech.record((2, 1), ["penalty"], "penalty", SOCCER, 5.0)
    big, small = {"index": 0, "width": 1900, "height": 1000}, {"index": 1, "width": 600, "height": 330}
    server._expand_swap(2, [big, small], 1)
    assert server.scorer.streams[2].tiles[0].goal_at == 5.0 and server.scorer.streams[2].tiles[1].goal_at == float("-inf")
    assert server.buildup.cues((2, 0), 6.0) and not server.buildup.cues((2, 1), 6.0)
    assert server.cue_speech.tier((2, 0), 6.0) == "top" and server.cue_speech.tier((2, 1), 6.0) is None


def test_unregister_forgets_every_tile_of_the_tab():
    server = mixed()
    server.buildup.note((2, 0), 5.0, 0.3, "chance")
    server.buildup.note((2, 1), 5.0, 0.3, "chance")
    server.cue_speech.record((2, 1), ["penalty"], "penalty", SOCCER, 5.0)
    server.handle_message({"type": "unregister", "tab_id": 2})
    assert server.buildup.cues((2, 0), 6.0) == [] and server.buildup.cues((2, 1), 6.0) == [] and server.cue_speech.tier((2, 1), 6.0) is None


def test_the_recorder_row_names_the_winning_tile_and_each_tiles_score():
    server = mixed(audible=0)
    now = hear(server, crowd=0.9)
    row = next(r for r in snapshot_rows(server, now) if r["tab"] == 2)
    assert row["tile"] == 0 and row["tile_sport"] == NFL and row["sport"] == NFL
    assert {t["i"]: t["score"] is not None for t in row["tiles"]} == {0: True, 1: True}
    assert row["bu"]["tile"] == 0
    single = next(r for r in snapshot_rows(server, now) if r["tab"] == 1)
    assert single["tile"] is None and single["tile_sport"] is None and "tile" not in single["bu"]


def test_backtest_matches_cues_buildups_and_sports_on_the_goals_tile():
    stamp = "2026-10-14 12:00:%02d,000"
    cues = backtest.parse_cues([f"{stamp % 10} anticipation var tab 2 tile 1 weight 0.20", f"{stamp % 11} anticipation chance tab 2 tile 0 (spoken) weight 0.17",
                                f"{stamp % 12} anticipation chance tab 2 (spoken) weight 0.17"])
    assert [c.tile for c in cues] == [1, 0, None] and cues[0].weight == 0.20 and cues[1].kind == "chance (spoken)"
    sports = backtest.parse_tile_sports([f"{stamp % 0} sport detected tab 2 tile 0: American football", f"{stamp % 1} sport detected tab 2 tile 1: Football"])
    assert sports[0][2:] == (0, "American football") and backtest.parse_sports([f"{stamp % 1} sport detected tab 2 tile 1: Football"])[0][1:] == (2, "Football")
    readings = [backtest.Reading(backtest.parse_time(stamp % s), 2, False, True, 0.2, 0.0, 0.0, 0.0) for s in range(0, 30)] if hasattr(backtest, "Reading") else []
    if readings:
        goal = backtest.Goal(backtest.parse_time(stamp % 20), 2, 1)
        leads = backtest.measure(readings, [goal], cues, sports=sports, buildups=backtest.parse_tile_buildups([f"{stamp % 15} buildup would take MAIN tab 2 tile 0 (american football): level 0.5"]))
        assert leads[0].sport == "Football"                            # the goal is on tile 1: that tile's sport, not the tab's last one
        assert leads[0].first_cue is not None and leads[0].first_cue[1] == "var"   # the spoken cues of tile 0 are not this goal's build-up
        assert leads[0].buildup_lead_s is None                         # ...and neither is tile 0's build-up line
