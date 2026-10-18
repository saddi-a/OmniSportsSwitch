"""Window 2 (internal LEFT) has the same two controls as Main: Make Window 2 (a timed hold) and Freeze Window 2.
While either is on, swap planning treats Window 2 as fixed."""
import asyncio
import logging

import pytest

from daemon.scoring import Config, Scorer
from daemon.server import Server
from daemon.tests.test_scoring import make, roar, warm_up


# ---- the Scorer: hold, freeze, challengers ------------------------------------------------------------------------

def test_make_window2_holds_the_stream_for_its_own_hold_time():
    s = make(manual_hold2_s=600, manual_hold_s=60)
    assert s.force_window2(2, 100.0, occupant=2) is None   # not Main: no swap, just the hold
    assert s.hold2_left(200.0, window2_tab=2) == pytest.approx(500)   # its own setting, not manual_hold_s
    assert s.window2_fixed(200.0, 2) is True


def test_the_window2_hold_ends_by_timeout_release_or_when_something_else_sits_there():
    s = make(manual_hold2_s=60)
    s.force_window2(2, 100.0, occupant=2)
    assert s.hold2_left(161.0, 2) == 0 and s.hold2_tab is None   # timed out
    s.force_window2(2, 200.0, occupant=2)
    s.release_hold2()
    assert s.hold2_left(201.0, 2) == 0
    s.force_window2(2, 300.0, occupant=2)
    assert s.hold2_left(301.0, 3) == 0 and s.hold2_tab is None   # tab 3 now sits in Window 2: the hold is gone
    s.force_window2(2, 400.0, occupant=2)
    s.remove(2)
    assert s.hold2_left(401.0, 2) == 0                            # the stream left


def test_freeze_window2_fixes_it_without_a_stream_being_held():
    s = make()
    assert s.window2_fixed(0.0, 2) is False
    s.frozen2 = True
    assert s.window2_fixed(0.0, 2) is True and s.window2_fixed(0.0, None) is False


def test_make_window2_on_the_main_stream_swaps_main_with_window2_once_then_holds():
    s = make(manual_hold2_s=600)
    swap = s.force_window2(1, 100.0, occupant=2)
    assert (swap.promote, swap.demote) == (2, 1) and s.main == 2
    assert s.hold2_left(110.0, window2_tab=1) > 0


def test_make_window2_on_main_is_refused_when_nothing_can_take_main():
    s = make()
    with pytest.raises(ValueError):
        s.force_window2(1, 100.0, occupant=None)          # Window 2 is empty
    s.set_can_main(2, False)
    with pytest.raises(ValueError):
        s.force_window2(1, 100.0, occupant=2)             # the Window 2 stream may not be in Main
    s.set_can_main(2, True)
    s.frozen = True
    with pytest.raises(ValueError):
        s.force_window2(1, 100.0, occupant=2)             # Main is frozen
    with pytest.raises(ValueError):
        s.force_window2(99, 100.0, occupant=2)            # unknown tab
    assert s.main == 1 and s.hold2_tab is None


def test_a_fixed_window2_stream_is_never_the_challenger_even_when_it_roars():
    s = make()
    t = warm_up(s)
    t = roar(s, 2, t + 5)
    s.fixed_tabs = frozenset({2})
    assert s.decide(t) is None and s.take_main_block(2, t) == "Window 2 is held or frozen"
    s.fixed_tabs = frozenset()
    assert s.decide(t).promote == 2                        # without the hold the same roar wins


def test_the_second_best_stream_wins_while_the_best_one_is_fixed():
    s = make()
    t = warm_up(s)
    t = roar(s, 2, t + 5, level=0.20)
    roar(s, 3, t - 5, level=0.12, crowd=0.6)
    s.fixed_tabs = frozenset({2})
    swap = s.decide(t)
    assert swap is not None and swap.promote == 3


def test_evict_main_does_not_hand_main_to_the_fixed_window2_stream():
    s = make()
    s.set_can_main(1, False)
    s.fixed_tabs = frozenset({2})
    swap = s.evict_main(100.0)
    assert swap.promote == 3


# ---- the Server ---------------------------------------------------------------------------------------------------

def _server(**cfg):
    server = Server(Scorer(Config(confirm_s=0.0, **cfg)))
    sent = []

    async def capture(obj):
        sent.append(obj)

    server._broadcast = capture
    for tab, role, main in ((1, "MAIN", True), (2, "LEFT", False), (3, "RIGHT_BOT", False), (4, "RIGHT_MID", False)):
        server.handle_message({"type": "register", "tab_id": tab, "role": role, "is_main": main})
    return server, sent


def _control(server, kind, **kw):
    return asyncio.run(server.handle_control_message({"type": kind, **kw}))


def test_make_window2_moves_a_stream_from_another_window_and_holds_it():
    server, sent = _server()
    reply = _control(server, "force_window2", tab_id=3)
    assert reply["type"] == "hold2" and reply["error"] is None and reply["tab_id"] == 3 and reply["left_s"] > 0
    assert sent == [{"type": "arrange", "moves": [{"tab_id": 3, "role": "LEFT"}, {"tab_id": 2, "role": "RIGHT_BOT"}]}]
    assert server.roles[3] == "LEFT" and server.scorer.main == 1       # Main untouched
    assert _control(server, "get_hold2")["tab_id"] == 3
    assert _control(server, "release_hold2")["left_s"] == 0


def test_make_window2_on_the_stream_already_there_only_holds():
    server, sent = _server()
    reply = _control(server, "force_window2", tab_id=2)
    assert reply["tab_id"] == 2 and reply["left_s"] > 0 and sent == []


def test_make_window2_on_main_swaps_main_with_window2_once_then_holds():
    server, sent = _server()
    reply = _control(server, "force_window2", tab_id=1)
    assert reply["error"] is None and reply["tab_id"] == 1
    assert sent == [{"type": "arrange", "moves": [{"tab_id": 1, "role": "LEFT"}, {"tab_id": 2, "role": "MAIN"}]}]
    assert server.scorer.main == 2 and server.roles == {1: "LEFT", 2: "MAIN", 3: "RIGHT_BOT", 4: "RIGHT_MID"}


def test_make_window2_is_refused_by_the_slot_rules_and_changes_nothing():
    server, sent = _server()
    server.handle_message({"type": "register", "tab_id": 3, "role": "RIGHT_BOT", "allowed": ["RIGHT_BOT"]})
    reply = _control(server, "force_window2", tab_id=3)
    assert "cannot go to Window 2" in reply["error"] and reply["tab_id"] is None and sent == []
    server.scorer.frozen = True
    reply = _control(server, "force_window2", tab_id=1)
    assert "frozen" in reply["error"] and server.scorer.main == 1 and sent == []


def test_while_window2_is_held_a_main_swap_uses_another_window(caplog):
    server, sent = _server()
    server.handle_message({"type": "register", "tab_id": 1, "role": "MAIN", "is_main": True, "allowed": ["MAIN", "LEFT", "RIGHT_MID"]})
    _control(server, "force_window2", tab_id=2)
    swap = type("S", (), {"promote": 3, "demote": 1, "reason": "test"})()
    assert asyncio.run(server._perform_swap(swap, restore_main=False)) is True
    assert sent[-1]["moves"] == [{"tab_id": 3, "role": "MAIN"}, {"tab_id": 1, "role": "RIGHT_MID"}, {"tab_id": 4, "role": "RIGHT_BOT"}]
    assert server.roles[2] == "LEFT"                                    # Window 2 never moved


def test_the_swap_is_skipped_and_logged_once_per_ten_seconds_when_only_window2_could_take_the_old_main(caplog):
    server, sent = _server()
    server.handle_message({"type": "register", "tab_id": 1, "role": "MAIN", "is_main": True, "allowed": ["MAIN", "LEFT"]})
    server.scorer.frozen2 = True
    server.scorer.main = 3                                              # the scorer already moved Main to tab 3 (as decide() does)
    swap = type("S", (), {"promote": 3, "demote": 1, "reason": "test"})()
    with caplog.at_level(logging.WARNING, logger="daemon"):
        assert asyncio.run(server._perform_swap(swap, restore_main=True)) is False
        assert asyncio.run(server._perform_swap(swap, restore_main=True)) is False
    assert sent == [] and server.scorer.main == 1                       # nothing moved, Main restored
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "swap skipped" in r.getMessage()]
    assert len(warnings) == 1 and "Window 2" in warnings[0].getMessage() and "LEFT" not in warnings[0].getMessage()
    server.skip_logged_at = {k: v - 11 for k, v in server.skip_logged_at.items()}
    with caplog.at_level(logging.WARNING, logger="daemon"):
        asyncio.run(server._perform_swap(swap, restore_main=True))
    assert len([r for r in caplog.records if "swap skipped" in r.getMessage()]) == 2


def test_make_main_respects_a_fixed_window2():
    server, sent = _server()
    _control(server, "force_window2", tab_id=2)
    reply = _control(server, "force_main", tab_id=2)                    # the stream that is held in Window 2
    assert "Window 2" in reply["error"] and server.scorer.main == 1 and sent == []
    server.handle_message({"type": "register", "tab_id": 1, "role": "MAIN", "is_main": True, "allowed": ["MAIN", "LEFT"]})
    reply = _control(server, "force_main", tab_id=3)                    # the old Main may only go to Window 2: refused, not moved
    assert "Window 2" in reply["error"] and server.scorer.main == 1 and reply["tab_id"] is None and sent == []
    _control(server, "release_hold2")
    reply = _control(server, "force_main", tab_id=3)
    assert reply["error"] is None and server.scorer.main == 3


def test_freeze2_message_sets_the_flag_and_stops_tile_changes_on_window2_only():
    server, _ = _server()
    rects = [{"index": i, "audible": i == 0, "left": 0, "top": 0, "width": 700, "height": 480} for i in (0, 1, 2)]
    import time
    now = time.monotonic()
    for tab in (1, 2, 3):
        server.handle_message({"type": "video_rect", "tab_id": tab, "rects": rects, "visible": True, "fullscreen": False})
        server.event_at[(tab, 2)] = now
    server.handle_message({"type": "freeze2", "value": True})
    assert server.scorer.frozen2 is True and server.scorer.frozen is False
    assert {c["tab_id"] for c in server._focus_commands(now)} == {1, 3}   # Window 2 (tab 2) keeps its tile
    server.handle_message({"type": "freeze2", "value": False})
    assert server.scorer.frozen2 is False


def test_the_frozen_main_swap_goes_to_the_next_window_when_window2_is_fixed():
    config = Config(warmup_s=0, min_dwell_s=0, window_s=1.0, confirm_s=0)
    server = Server(Scorer(config))
    for tab, role, main in ((1, "MAIN", True), (2, "LEFT", False), (3, "RIGHT_BOT", False), (4, "RIGHT_MID", False)):
        server.handle_message({"type": "register", "tab_id": tab, "role": role, "is_main": main})
    import time
    now = time.monotonic()
    for t in (0, 1, 2, 3, 4):
        for tab in (1, 2, 3, 4):
            server.scorer.add_audio(tab, 0.05, now - 5 + t)
    server.scorer.set_event(4, now)                      # a goal in the RIGHT_MID stream
    server.handle_message({"type": "freeze", "value": True})
    server.handle_message({"type": "freeze2", "value": True})
    swap = server._next_swap(now)
    assert (swap.promote, swap.demote) == (4, 3)         # replaces Window 3, Window 2 and Main stay
    assert server.scorer.main == 1
