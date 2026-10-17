"""Stage 1 of the per-tile sport work (docs/per-tile-sport.md): one source of truth for a tile's sport, the sport of the audible tile
for the scorer and the speech loop, and the reset when the sound moves to another tile."""
import logging

import numpy as np

from daemon.buildup import moves_main
from daemon.cues import speech_cue_weight
from daemon.server import Server
from daemon.speech import find_speech_cues

PENALTY_TEXT = "pénalty sifflé"


def _server():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 1})
    return server


def _rects(audible, n=2):
    return [{"index": i, "audible": i == audible, "left": 0, "top": 0, "width": 700, "height": 480} for i in range(n)]


def _report(server, audible, n=2, tab=1):
    server.handle_message({"type": "video_rect", "tab_id": tab, "rects": _rects(audible, n), "visible": True, "fullscreen": False})


def _mixed():
    server = _server()
    server.sport_choice_seen[(1, 0)] = ("American football", "hard")
    server.sport_choice_seen[(1, 1)] = ("Football", "hard")
    return server


def test_tile_sport_is_the_choose_sport_result_and_the_popup_wins():
    server = _server()
    server.sport_choice_seen[(1, 0)] = ("American football", "feed")   # the feed league, no hard cue
    assert server.tile_sport(1, 0) == "american football"
    server.sport_seen[(1, 0)] = "Football"                          # the plain detection alone would say otherwise
    assert server.tile_sport(1, 0) == "american football"
    server.sports[1] = "Football"                                     # the popup beats all
    assert server.tile_sport(1, 0) == "football"
    assert server.tile_sport(1, 5) == "football"                      # ...for every tile of the tab


def test_single_tile_tab_sport_uses_sources_beyond_the_hard_detection():
    server = _server()
    _report(server, 0, n=1)
    assert server._tab_sport(1) is None                               # nothing known: as before
    server.sport_choice_seen[(1, 0)] = ("American football", "memory")   # no hard detection: only the remembered sport
    assert server._tab_sport(1) == "american football"
    assert server.scorer.sport_of(1) == "american football"


def test_single_tile_tab_golden_same_as_the_hard_detection_alone():
    """Where the hard detection already gave the sport (or the popup did), the tab sport is exactly what it was."""
    for popup, hard in ((None, "Football"), (None, "American football"), ("American football", None), ("Football", "American football"), (None, None)):
        server = _server()
        _report(server, 0, n=1)
        server.sports[1] = popup
        server.sport_seen[(1, 0)] = hard
        if hard:
            server.sport_choice_seen[(1, 0)] = (popup or hard, "popup" if popup else "hard")
        old = popup or hard
        assert server._tab_sport(1) == (old.lower() if old else None)


def test_mixed_tab_reads_the_sport_of_the_audible_tile():
    server = _mixed()
    _report(server, 1)                                                # the football (soccer) tile has the sound
    assert server._tab_sport(1) == "football" and server.scorer.sport_of(1) == "football"
    cues = find_speech_cues(PENALTY_TEXT, server._tab_sport(1))
    assert "penalty" in " ".join(cues)
    assert speech_cue_weight(cues[0], "football", server.scorer.config.weights, 1.0) is not None
    assert moves_main(server._tab_sport(1))
    _report(server, 0)                                                # the NFL tile has the sound
    assert server._tab_sport(1) == "american football"
    assert not moves_main(server._tab_sport(1))
    assert find_speech_cues(PENALTY_TEXT, server._tab_sport(1)) == find_speech_cues(PENALTY_TEXT, "american football")
    assert find_speech_cues(PENALTY_TEXT, "american football") != find_speech_cues(PENALTY_TEXT, "football")


def test_unknown_audible_tile_of_a_multiview_counts_as_football_but_a_single_video_stays_unknown():
    server = _server()
    server.sport_choice_seen[(1, 0)] = ("American football", "hard")
    _report(server, 1)                                                # tile 1 has the sound and no sport yet
    assert server._tab_sport(1) == "football"
    one = _server()
    _report(one, 0, n=1)
    assert one._tab_sport(1) is None


def test_popup_sport_keeps_winning_on_every_tile_of_a_mixed_tab():
    server = _mixed()
    server.sports[1] = "American football"
    for audible in (0, 1):
        _report(server, audible)
        assert server._tab_sport(1) == "american football"


def test_audible_change_clears_audio_buffers_marks_tags_stale_and_logs(caplog):
    server = _mixed()
    _report(server, 0)
    server.speech_buffers[1] = np.zeros(100, dtype=np.float32)
    server.buffers[1] = np.zeros(100, dtype=np.float32)
    server.tags[1] = np.zeros(3)
    server.scorer.set_tags(1, 0.9, 0.1, 0.0, 5.0)
    _report(server, 0)                                                # same tile again: nothing happens
    assert 1 in server.speech_buffers and 1 in server.buffers
    with caplog.at_level(logging.INFO, logger="daemon"):
        _report(server, 1)
    assert 1 not in server.speech_buffers and 1 not in server.buffers and 1 not in server.tags
    assert server.scorer.streams[1].tags_at == float("-inf")
    assert "audible tile tab 1: tile 0 -> 1 (sport american football -> football)" in caplog.text


def test_expand_swap_carries_the_per_tile_sport_stores_and_the_audible_record():
    server = _server()
    big = {"index": 0, "width": 1900, "height": 1000}
    small = {"index": 2, "width": 600, "height": 330}
    server.sport_choice_seen[(1, 0)], server.sport_choice_seen[(1, 2)] = ("Football", "hard"), ("American football", "hard")
    server.held_change[(1, 2)] = (0.5, 1.0)
    server.fed[(1, 0)] = 7.0
    server.replay_signal[(1, 2, "bug")] = True
    server.audible_seen[1] = (2, "american football")
    server._expand_swap(1, [big, small], 2)
    assert server.sport_choice_seen[(1, 0)][0] == "American football" and server.sport_choice_seen[(1, 2)][0] == "Football"
    assert (1, 0) in server.held_change and (1, 2) not in server.held_change
    assert (1, 2) in server.fed and (1, 0) not in server.fed
    assert server.replay_signal == {(1, 0, "bug"): True}
    assert server.audible_seen[1] == (0, "american football")        # the audible stream moved to the big slot: not a change of sound
