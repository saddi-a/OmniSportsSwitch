"""The rewind detector's high point is capped by the feed's game clock (`scoring.rewind_high_slack_s`): a stream cannot be ahead
of the feed, so a scrubbed or misread clock never sticks as the high point (tab 485662434 tile 0, PAR-REN, 2026-10-18)."""
import dataclasses
import json
import pathlib

from daemon import feed
from daemon.feed_gate import FeedGate, _Rewind
from daemon.scoring import Config
from daemon.settings import LAYOUT

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SNAP = feed.parse_scoreboard(json.loads((FIXTURES / "feed_soccer_scoreboard.json").read_text(encoding="utf-8")), "ita.1")[0]


def _gate(feed_clock_s=2700.0, slack=None, sport="soccer", period=2):
    g = FeedGate()
    if slack is not None:
        g.rewind_high_slack_s = slack
    snap = dataclasses.replace(SNAP, sport=sport, state="in", period=period, clock_s=feed_clock_s)
    g.sport_of[snap.game_id] = sport
    g.fresh = {snap.game_id}
    g.set_feed_clocks([snap])
    return g, snap.game_id


def _steady(g, gid, clocks):
    msgs, now = [], 100.0
    for c in clocks:
        for _ in range(2):
            now += 3
            msgs += g.observe(1, 0, gid, [c], None, False, now)
    return msgs


def test_par_ren_scrub_does_not_start_a_rewind():
    g, gid = _gate(feed_clock_s=45 * 60 + 21)
    msgs = _steady(g, gid, ["37:04", "59:02", "82:11", "84:10", "45:21", "45:30"])
    assert not any("feed rewind" in m for m in msgs) and not g.is_rewinding(1, 0)


def test_control_without_the_cap_the_scrub_starts_a_rewind():
    g, gid = _gate(feed_clock_s=45 * 60 + 21, slack=0)
    msgs = _steady(g, gid, ["37:04", "59:02", "82:11", "84:10", "45:21", "45:30"])
    assert g.is_rewinding(1, 0) and any("behind its high point" in m for m in msgs)


def test_no_feed_clock_is_unchanged():
    g, gid = _gate(feed_clock_s=45 * 60 + 21)
    g.feed_now.clear()
    _steady(g, gid, ["37:04", "59:02", "82:11", "84:10", "45:21", "45:30"])
    assert g.is_rewinding(1, 0)
    g, gid = _gate(feed_clock_s=45 * 60 + 21)
    g.fresh = set()                                          # stale feed
    _steady(g, gid, ["37:04", "84:10", "45:21"])
    assert g.is_rewinding(1, 0)


def test_a_genuine_rewind_is_still_detected():
    g, gid = _gate(feed_clock_s=60 * 60)
    msgs = _steady(g, gid, ["59:40", "60:00", "40:00", "40:10"])
    assert g.is_rewinding(1, 0) and any("behind its high point" in m for m in msgs)


def test_slack_boundary():
    g, gid = _gate(feed_clock_s=3000.0)                      # cap 3120 = 52:00
    _steady(g, gid, ["52:00"])
    assert g.rewind[(1, 0)].high == 3120.0                   # on the boundary: kept
    g2, gid2 = _gate(feed_clock_s=3000.0)
    _steady(g2, gid2, ["50:00", "52:01"])
    assert g2.rewind[(1, 0)].high == 3000.0                  # one second above: ignored


def test_active_rewind_with_a_high_point_above_the_feed_clock_is_dropped():
    g, gid = _gate(feed_clock_s=2725.0)
    st = g.rewind.setdefault((1, 0), _Rewind())
    st.high, st.active, st.since = 5050.0, True, 100.0
    msgs = g.observe(1, 0, gid, ["45:25"], None, False, 110.0)
    assert not g.is_rewinding(1, 0) and any("high point was above the feed clock" in m for m in msgs)
    assert g.rewind[(1, 0)].high == 2725.0


def test_american_football_uses_the_period_scale():
    # feed: period 3, 5:00 left = 2*900 + 600 = 2400 s; the stream scrubbed to period 4 (clock 1:00 left = 3540 s), then back
    g, gid = _gate(feed_clock_s=300.0, sport="american football", period=3)
    assert g.feed_now[gid] == 2400.0
    for i, (c, p) in enumerate([("2:00", 3), ("2:00", 3), ("1:00", 4), ("1:00", 4), ("4:30", 3), ("4:29", 3)]):
        g.observe(1, 0, gid, [c], p, False, 100.0 + 3 * i)
    assert not g.is_rewinding(1, 0)
    g2, gid2 = _gate(feed_clock_s=300.0, sport="american football", period=3, slack=0)
    for i, (c, p) in enumerate([("2:00", 3), ("2:00", 3), ("1:00", 4), ("1:00", 4), ("4:30", 3), ("4:29", 3)]):
        g2.observe(1, 0, gid2, [c], p, False, 100.0 + 3 * i)
    assert g2.is_rewinding(1, 0)


def test_setting_default_and_popup():
    assert Config().rewind_high_slack_s == 120.0
    assert "scoring.rewind_high_slack_s" in {key for _, rows in LAYOUT for key, _, _ in rows}
