import datetime
import json

from daemon import backtest as bt
from daemon.feed_compare import parse_feed

T0 = datetime.datetime(2026, 10, 12, 22, 0, 0)


def at(seconds):
    return T0 + datetime.timedelta(seconds=seconds)


def stamp(t):
    return t.strftime("%Y-%m-%d %H:%M:%S") + ",000"


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.000") + "+02:00"


def snap(t, score, game="1", league="eng.1", home="COV", away="NEW"):
    return json.dumps({"t": iso(t), "type": "snap", "league": league, "game": game, "home": home, "away": away, "state": "in", "score": score})


def event(t, kind, game="1", league="eng.1", home="COV", away="NEW", team="NEW", text="x"):
    return json.dumps({"t": iso(t), "type": "event", "league": league, "game": game, "home": home, "away": away, "kind": kind,
                       "team": team, "text": text, "clock_s": 100.0, "display": "70'"})


def feed_events(lines):
    return bt.parse_feed_events(parse_feed(lines))


def map_line(t, tab=5, tile=1, game="eng.1 COV-NEW"):
    return f"{stamp(t)} feed map tab {tab} tile {tile} -> {game} (via ocr, 2/4 readings fit the feed)"


def ocr_line(t, tab=5, tile=1):
    return f"{stamp(t)} score change tab {tab} tile {tile}: ['COV', '2', '1', 'NEW']"


def readings(tab=5, seconds=range(-120, 400, 2), score=0.1):
    return [bt.Reading(at(s), tab, False, True, score, 0.0, 0.0, 0.0) for s in seconds]


def test_timezone_offset_is_dropped_so_feed_and_log_times_compare_directly():
    goals, _ = feed_events([event(at(0), "goal")])
    assert goals[0].event_t == at(0)
    assert goals[0].t == at(0) and goals[0].sport == "Football"


def test_goal_time_is_the_snapshot_score_rise_when_it_came_before_the_event_row():
    goals, _ = feed_events([snap(at(-60), [2, 0]), snap(at(-20), [2, 1]), event(at(0), "goal")])
    assert goals[0].t == at(-20) and goals[0].event_t == at(0)


def test_repeated_event_rows_are_one_goal_and_sports_are_split():
    rows = [event(at(0), "goal"), event(at(75), "goal"), event(at(102), "goal"), event(at(1000), "goal"),
            event(at(5), "touchdown", league="nfl", game="2", home="SEA", away="SF", team="SF"),
            event(at(6), "field goal good", league="nfl", game="2", home="SEA", away="SF", team="SF"),
            event(at(7), "goal", league="nba", game="3", home="A", away="B")]
    goals, _ = feed_events(rows)
    assert [(g.sport, g.kind) for g in goals] == [("Football", "goal"), ("American football", "touchdown"), ("American football", "field goal good"),
                                                  ("Football", "goal")]


def test_non_goal_events_are_collected_and_basketball_has_no_goals():
    goals, others = feed_events([event(at(0), "red card"), event(at(1), "interception", league="nfl", home="SEA", away="SF", game="2"),
                                 event(at(2), "sack", league="nfl", game="2")])
    assert goals == [] and sorted((o.sport, o.kind) for o in others) == [("American football", "interception"), ("Football", "red card")]


def test_mapping_holds_until_the_next_map_line_of_the_tile_for_another_game_or_a_drop():
    lines = [map_line(at(0)), map_line(at(100)), map_line(at(200), game="eng.1 LIV-MNC"), map_line(at(50), tab=6),
             f"{stamp(at(300))} feed map tab 5 tile 1 dropped eng.1 LIV-MNC: score disagrees for 6 readings"]
    maps = bt.parse_mappings(lines)
    mine = [(m.label, m.start, m.end) for m in maps if m.tab == 5]
    assert mine == [("eng.1 COV-NEW", at(0), at(200)), ("eng.1 LIV-MNC", at(200), at(300))]
    assert maps[1].tab == 6 or any(m.tab == 6 for m in maps)
    open_end = next(m for m in maps if m.tab == 6)
    assert open_end.end == at(50) + datetime.timedelta(seconds=bt.MAP_MAX_OPEN_S) and open_end.sport == "Football"


def run(feed_rows, log_lines, **kw):
    goals, others = feed_events(feed_rows)
    ocr = bt.parse_goals([x for x in log_lines if "score change" in x])
    return bt.measure_feed(goals, others, bt.parse_mappings(log_lines), ocr, kw.pop("readings", readings()), **kw)


def test_detected_goal_has_a_positive_feed_lead_and_is_anchored_on_the_ocr_change():
    report = run([event(at(0), "goal")], [map_line(at(-100)), ocr_line(at(25))])
    row = report.rows[0]
    assert row.detected and row.feed_lead_s == 25 and row.anchor == "OCR change - 12 s"
    assert row.picture_t == at(13) and row.feed_ahead_s == 13       # the feed knew it 13 s before the goal on the picture
    assert row.ahead("feed") == 13 and report.ocr_only == []


def test_picture_ahead_of_the_feed_gives_a_negative_lead():
    row = run([event(at(0), "goal")], [map_line(at(-100)), ocr_line(at(-7))]).rows[0]
    assert row.feed_lead_s == -7 and row.feed_ahead_s == -19


def test_missed_goal_uses_the_median_lag_of_the_tab_and_ocr_only_changes_are_counted():
    rows = [event(at(0), "goal", game="1"), event(at(500), "goal", game="1")]
    log = [map_line(at(-100)), ocr_line(at(30)), ocr_line(at(900)), map_line(at(-100), tab=6, tile=0, game="eng.1 LIV-MNC"), ocr_line(at(40), tab=6, tile=0)]
    report = run(rows, log, readings=readings() + readings(tab=6))
    first, second = report.rows
    assert first.detected and not second.detected
    assert second.anchor == "feed + median lag of the tab" and second.picture_t == at(500 + 30 - 12)
    assert [(o.tab, o.t) for o, _, _ in report.ocr_only] == [(5, at(900)), (6, at(40))]       # nothing matches them: no feed goal at that time / other game


def test_goal_with_no_lag_known_has_no_anchor_and_a_goal_outside_the_mapping_is_ignored():
    report = run([event(at(0), "goal"), event(at(20000), "goal")], [map_line(at(-100))])
    assert len(report.rows) == 1 and report.rows[0].anchor == "none" and report.rows[0].feed_ahead_s is None
    assert "missed by the OCR" in bt.summarise_feed(report)


def test_ocr_change_outside_the_window_is_not_the_goal():
    report = run([event(at(0), "goal")], [map_line(at(-100)), ocr_line(at(200))])
    assert not report.rows[0].detected and len(report.ocr_only) == 1


def test_one_ocr_change_explains_one_goal_only():
    report = run([event(at(0), "goal", team="COV"), event(at(20), "goal", team="NEW")], [map_line(at(-100)), ocr_line(at(30))])
    assert [r.detected for r in report.rows] == [True, False]


def test_measures_use_the_picture_anchor():
    log = [map_line(at(-100)), ocr_line(at(60))]
    cues = bt.parse_cues([f"{stamp(at(10))} anticipation var tab 5 tile 1 weight 0.20"])
    buildups = bt.parse_buildups([f"{stamp(at(20))} buildup would take MAIN tab 5: level 0.6"])
    row = run([event(at(0), "goal")], log, cues=cues, buildups=buildups).rows[0]
    assert row.ahead("cue") == 50 - 12 and row.ahead("buildup") == 40 - 12
    assert row.ahead("swap") is None


def test_logged_feed_lag_is_carried_for_the_cross_check():
    lags = bt.parse_feed_lags([f"{stamp(at(70))} feed confirmed tab 5 tile 1 eng.1 COV-NEW: goal 70', 15 s after the OCR change, feed lag -30 s (x)"])
    assert run([event(at(0), "goal")], [map_line(at(-100)), ocr_line(at(30))], feed_lags=lags).rows[0].logged_lag_s == 30


def test_base_rate_needs_three_events_and_counts_goals_within_120_s():
    rows = [event(at(0), "red card", team="A"), event(at(1000), "yellow card", team="A"), event(at(2000), "yellow card", team="B"),
            event(at(3000), "yellow card", team="C"), event(at(50), "goal")]
    goals, others = feed_events(rows)
    rates = bt.base_rates(goals, others)
    assert rates["Football"]["red card"] == (1, 1) and rates["Football"]["yellow card"] == (3, 0)
    text = bt.summarise_feed(bt.FeedReport([], [], goals, others))
    assert "feed event red card: 1, too few events" in text and "feed event yellow card: 3, 0 followed by a goal within 120 s" in text


def test_summary_per_sport_numbers():
    rows = [event(at(0), "goal"), event(at(500), "goal")]
    log = [map_line(at(-100)), ocr_line(at(30))]
    text = bt.summarise_feed(run(rows, log), detail=False)
    assert "Football (feed): 2 feed goals on mapped tiles, 1 detected by the OCR, 1 missed by the OCR, 0 OCR score changes" in text
    assert "median +30 s" in text and "the feed: 1 of 1" in text
    assert "no goal of a mapped game" in bt.summarise_feed(bt.FeedReport([], [], [], []))
