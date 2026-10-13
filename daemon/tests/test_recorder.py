import json
import threading
import time

import numpy as np

from daemon.recorder import DatasetRecorder, snapshot_rows
from daemon.scoring import Config, Scorer
from daemon.server import Server

ROW_FIELDS = {"v", "t", "mono", "tab", "role", "is_main", "sport", "warm", "state", "auto_state", "override", "ad", "on_ad", "can_main", "weight",
              "score", "surge", "crowd", "speech", "music", "shout", "event", "cue_fade", "cue_part", "fav", "lev", "loud", "baseline", "top", "cue",
              "bu", "tile", "tile_sport", "home", "away", "clock", "period", "replay", "goal_hold", "goal_age", "hold", "game_id", "league", "tiles", "main", "frozen",
              "last_swap_age", "swap_pending"}
BU_FIELDS = {"level", "cues_sum", "support", "cues", "min", "gap", "live", "main_score", "main_level", "hot_left", "qualifies", "cooldown_left", "surge_note_left",
             "crowd_note_left", "crowd_run", "blocked"}


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _server(tmp_path, **config):
    recorder = DatasetRecorder(tmp_path / "dataset", mode=lambda: server.scorer.config.dataset_mode)
    server = Server(Scorer(Config(warmup_s=0.0, **config)), recorder=recorder, sport_memory_path=tmp_path / "sports.json")
    return server, recorder


def _register(server, tab, now, level=0.05):
    server.handle_message({"type": "register", "tab_id": tab, "weight": 1.0, "is_main": tab == 1, "sport": "football", "role": "MAIN" if tab == 1 else "LEFT"})
    for k in range(10):
        server.scorer.add_audio(tab, level, now - 1.0 + k * 0.1)
    server.scorer.set_tags(tab, 0.4, 0.5, 0.05, now - 0.5, shout=0.1)


def test_a_server_tick_writes_one_documented_row_per_stream(tmp_path):
    server, recorder = _server(tmp_path, buildup_live=0.0)
    now = time.monotonic()
    _register(server, 1, now)
    _register(server, 2, now)
    server.buildup.note(2, now - 5, 0.3, "free kick")
    server.buildup.note(2, now - 2, 0.1, "surge_note")
    server._log_scores(now)
    server._rec_event("speech_cue", 2, cue="chance", text="quelle occasion")
    recorder.close()
    rows = _lines(next((tmp_path / "dataset").glob("????-??-??.jsonl")))
    assert sorted(r["tab"] for r in rows) == [1, 2]
    for row in rows:
        assert set(row) == ROW_FIELDS
        assert set(row["bu"]) == BU_FIELDS
        assert row["v"] == 1 and row["sport"] == "football" and row["warm"] is True
    main = next(r for r in rows if r["tab"] == 1)
    other = next(r for r in rows if r["tab"] == 2)
    assert main["is_main"] and not other["is_main"] and main["bu"]["blocked"] == "already MAIN"
    assert [c[2] for c in other["bu"]["cues"]] == ["free kick", "surge_note"]   # build-up parts with their kind, at every tick
    assert other["bu"]["level"] > 0.0 and other["bu"]["cues_sum"] > 0.0
    events = _lines(next((tmp_path / "dataset").glob("*.events.jsonl")))
    cue = next(e for e in events if e["kind"] == "speech_cue")
    assert cue["text"] == "quelle occasion" and cue["tab"] == 2 and cue["sport"] == "football"
    assert any(e["kind"] == "buildup" for e in events)   # the build-up decision (log only here) is an event too


def test_a_stream_without_audio_still_gets_a_row_with_nulls(tmp_path):
    server, recorder = _server(tmp_path)
    server.handle_message({"type": "register", "tab_id": 7, "weight": 1.0, "is_main": False, "sport": None})
    rows = snapshot_rows(server, time.monotonic())
    assert len(rows) == 1 and rows[0]["warm"] is False and rows[0]["score"] is None and rows[0]["tiles"] == []
    json.dumps(rows[0])


def test_rows_carry_tile_state_swap_context_and_cue(tmp_path):
    server, recorder = _server(tmp_path)
    now = time.monotonic()
    _register(server, 1, now)
    _register(server, 2, now)
    server.scorer.set_anticipation(2, now - 3, 0.4, strong=True)
    server.sport_choice_seen[(2, 0)] = ("football", "popup")
    from daemon.game_state import GameTracker
    server.trackers[(2, 0)] = GameTracker("football", server.scorer.config)
    server.rects[2] = {"rects": [{"index": 0, "audible": True}], "visible": True, "fullscreen": False}
    server.scorer.take_main(2, now, "test")
    row = next(r for r in snapshot_rows(server, now + 1) if r["tab"] == 2)
    assert row["is_main"] and row["last_swap_age"] == 1.0
    assert row["cue"]["value"] == 0.4 and row["cue"]["strong"] and row["cue"]["age"] == 4.0
    assert row["tiles"][0]["i"] == 0 and row["tiles"][0]["audible"] is True and row["tiles"][0]["sport_src"] == "popup"
    json.dumps(row)


def test_mode_zero_writes_nothing(tmp_path):
    server, recorder = _server(tmp_path, dataset_mode=0.0)
    now = time.monotonic()
    _register(server, 1, now)
    server._log_scores(now)
    server._rec_event("swap", 1)
    recorder.close()
    assert not (tmp_path / "dataset").exists() and recorder._thread is None and recorder.queue.empty()


def test_mode_can_be_switched_live(tmp_path):
    server, recorder = _server(tmp_path)
    now = time.monotonic()
    _register(server, 1, now)
    server.scorer.config.dataset_mode = 0.0
    server._log_scores(now)
    server.scorer.config.dataset_mode = 1.0
    server._log_scores(now + 2)
    recorder.close()
    assert len(_lines(next((tmp_path / "dataset").glob("????-??-??.jsonl")))) == 1


def test_a_full_queue_drops_instead_of_blocking(tmp_path):
    recorder = DatasetRecorder(tmp_path / "dataset", queue_size=3)
    gate = threading.Event()
    real_write = recorder._write

    def slow_write(batch):
        gate.wait(5)
        real_write(batch)

    recorder._write = slow_write
    started = time.monotonic()
    results = [recorder.add_row({"t": time.time(), "n": i}) for i in range(50)]
    assert time.monotonic() - started < 1.0   # nobody waited for the stuck writer
    assert results.count(False) >= 40 and recorder.dropped_total >= 40
    gate.set()
    recorder.close()
    written = _lines(next((tmp_path / "dataset").glob("*.jsonl")))
    assert len(written) == results.count(True)


def test_drops_are_reported_once_a_minute(tmp_path, caplog, monkeypatch):
    import logging
    import daemon.recorder as rec
    recorder = DatasetRecorder(tmp_path / "dataset", queue_size=1)
    recorder._thread = threading.Thread()   # no writer: the queue fills
    recorder._reported_at = time.monotonic() - rec.REPORT_EVERY_S - 1
    with caplog.at_level(logging.WARNING, logger="daemon.recorder"):
        for i in range(20):
            recorder.add_row({"t": 1.0})
    assert len([r for r in caplog.records if "dropped" in r.getMessage()]) == 1


def test_json_is_valid_one_object_per_line_and_files_change_by_day(tmp_path):
    recorder = DatasetRecorder(tmp_path / "dataset")
    day1 = time.mktime((2026, 10, 12, 23, 59, 58, 0, 0, -1))
    recorder.add_row({"t": day1, "text": "line\nbreak é"})
    recorder.add_row({"t": day1 + 5, "n": 2})
    recorder.add_event({"t": day1 + 5, "kind": "swap"})
    recorder.close()
    names = sorted(p.name for p in (tmp_path / "dataset").iterdir())
    assert names == ["2026-10-12.jsonl", "2026-10-13.events.jsonl", "2026-10-13.jsonl"]
    first = (tmp_path / "dataset" / "2026-10-12.jsonl").read_text(encoding="utf-8")
    assert first.count("\n") == 1 and json.loads(first)["text"] == "line\nbreak é"


def test_a_write_error_does_not_stop_the_writer(tmp_path):
    target = tmp_path / "dataset"
    target.write_text("a file where the folder should be")   # mkdir fails
    recorder = DatasetRecorder(target)
    recorder.add_row({"t": time.time()})
    recorder.close()
    assert recorder.errors >= 1


def test_the_settings_file_default_turns_the_recorder_on():
    assert Config().dataset_mode == 1.0 and Config().log_archive_days == 90.0


def test_recorded_qualifies_and_blocked_follow_main_level_bar_and_main_hold(tmp_path):
    from daemon.recorder import _buildup_part
    server, recorder = _server(tmp_path, buildup_live=1.0, min_dwell_s=0.0)
    now = time.monotonic()
    _register(server, 1, now)
    _register(server, 2, now)
    server.buildup.note(1, now - 17, 0.25, "spoken:chance")   # MAIN's own fresh chance cue
    server.buildup.note(2, now - 1, 0.34, "spoken:chance")
    part = _buildup_part(server, 2, server.scorer.components(2, now), now)
    assert part["main_level"] > 0.15 and part["level"] > part["main_level"] + 0.1   # above MAIN's level by the plain gap...
    assert part["qualifies"] is True
    assert part["blocked"] is not None and "MAIN's own cue" in part["blocked"]     # ...but not by the hold margin
    server.scorer.config.buildup_hold_s = 0.0
    assert _buildup_part(server, 2, server.scorer.components(2, now), now)["blocked"] is None
    recorder.close()


def test_recorded_hot_left_and_blocked_follow_the_hot_game_hold(tmp_path):
    from daemon.recorder import _buildup_part
    server, recorder = _server(tmp_path, buildup_live=1.0, min_dwell_s=0.0)
    now = time.monotonic()
    _register(server, 1, now)
    _register(server, 2, now)
    cold = _buildup_part(server, 2, server.scorer.components(2, now), now)
    assert cold["hot_left"] == 0.0
    server.scorer.set_event(1, now - 50.0, 1.0, goal=True)   # MAIN scored 50 s ago: goal hold over, hot for 40 s more
    server.buildup.note(1, now - 26, 0.30, "spoken:chance")   # MAIN's own level ~0.17: the hot bar is 0.17 + 0.25
    server.buildup.note(2, now - 1, 0.40, "spoken:chance")
    hot = _buildup_part(server, 2, server.scorer.components(2, now), now)
    assert 35.0 < hot["hot_left"] <= 40.0 and "MAIN is hot" in (hot["blocked"] or "")
    assert _buildup_part(server, 1, server.scorer.components(1, now), now)["hot_left"] == hot["hot_left"]
    recorder.close()
