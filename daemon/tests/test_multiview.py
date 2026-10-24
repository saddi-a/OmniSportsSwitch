from daemon.multiview import TileFocuser


def tiles(**overrides):
    base = [
        {"index": 0, "audible": True, "state": "live", "event": 0.0, "crowd": 0.1},
        {"index": 1, "audible": False, "state": "live", "event": 0.0},
        {"index": 2, "audible": False, "state": "live", "event": 0.0},
    ]
    for index, changes in overrides.items():
        base[int(index.lstrip("t"))].update(changes)
    return base


def test_quiet_multiview_leaves_focus_alone():
    assert TileFocuser().decide(1, 100, tiles()) is None


def test_fresh_score_change_on_another_tile_takes_focus():
    assert TileFocuser().decide(1, 100, tiles(t2={"event": 1.0})) == 2


def test_focused_tile_with_crowd_roar_keeps_focus():
    f = TileFocuser()
    assert f.decide(1, 100, tiles(t0={"crowd": 0.9}, t2={"event": 0.5})) is None


def test_break_on_focused_tile_hands_focus_to_a_live_tile():
    assert TileFocuser().decide(1, 100, tiles(t0={"state": "break", "crowd": 0.0})) in (1, 2)


def test_over_tile_never_gets_focus():
    f = TileFocuser()
    assert f.decide(1, 100, tiles(t2={"state": "over", "event": 1.0})) is None


def test_min_dwell_prevents_rapid_switching():
    f = TileFocuser()
    assert f.decide(1, 100, tiles(t2={"event": 1.0})) == 2
    focused_now_2 = tiles(t0={"audible": False}, t2={"audible": True, "event": 0.0}, t1={"event": 1.0})
    assert f.decide(1, 110, focused_now_2) is None   # 10 s after the last switch
    assert f.decide(1, 140, focused_now_2) == 1      # 40 s after


def test_dwell_is_tracked_per_tab():
    f = TileFocuser()
    assert f.decide(1, 100, tiles(t2={"event": 1.0})) == 2
    assert f.decide(2, 101, tiles(t1={"event": 1.0})) == 1


def test_single_video_is_not_multiview():
    assert TileFocuser().decide(1, 100, tiles()[:1]) is None


def test_no_audible_tile_does_not_spam_focus_commands():
    f = TileFocuser()
    silent = tiles(t0={"audible": False}, t2={"event": 1.0})
    assert f.decide(1, 100, silent) == 2
    assert f.decide(1, 103, silent) is None
    assert f.decide(1, 106, silent) is None
    assert f.decide(1, 131, silent) == 2


def test_a_tile_on_an_ad_has_negative_interest_and_loses_focus():
    f = TileFocuser()
    assert f.tile_score({"index": 0, "ad": True, "state": "break"}) < 0
    ad_on_focused = tiles(t0={"ad": True, "state": "break", "crowd": 0.0})
    assert f.decide(1, 100, ad_on_focused) in (1, 2)


def test_a_tile_on_an_ad_never_takes_focus():
    f = TileFocuser()
    assert f.decide(1, 100, tiles(t2={"ad": True, "event": 1.0})) is None


def test_the_dwell_is_skipped_when_the_focused_tile_is_on_an_ad_or_a_break():
    # user decision 2026-10-25: leave an ad or a break at once (a short pause of break_dwell_s stays so a failing click is no loop)
    for bad in ({"ad": True, "state": "break"}, {"state": "break"}, {"state": "over"}):
        f = TileFocuser()
        assert f.decide(1, 100, tiles(t2={"event": 1.0})) == 2
        stuck = tiles(t0={"audible": False}, t2={"audible": True, "event": 0.0, **bad}, t1={"event": 1.0})
        assert f.decide(1, 105, stuck) is None            # 5 s after the last switch: still too soon
        assert f.decide(1, 112, stuck) == 1               # 12 s after: no need to wait the full 30 s


def test_the_dwell_stays_when_the_focused_tile_is_live():
    f = TileFocuser()
    assert f.decide(1, 100, tiles(t2={"event": 1.0})) == 2
    live = tiles(t0={"audible": False}, t2={"audible": True, "event": 0.0}, t1={"event": 1.0})
    assert f.decide(1, 112, live) is None
