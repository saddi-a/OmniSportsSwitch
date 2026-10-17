import datetime

from daemon import backtest as bt

T0 = datetime.datetime(2026, 10, 12, 21, 30, 0)


def stamp(t: datetime.datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") + ",000"


def signal_line(t, tab, score, main=False, kind="live", surge=0.1):
    return f"{stamp(t)}   {'MAIN ' if main else ''}{tab}: {kind} fav=1.0 score={score:.2f} surge={surge:.2f} crowd=0.02 speech=0.80 music=0.05 shout=0.00 lev=0.3 top=Speech:0.80"


def at(seconds):
    return T0 + datetime.timedelta(seconds=seconds)


def run(challenger_scores, goal_s=60, main_score=0.10, cues=(), main_tab_is_goal_tab=False):
    """Two streams, readings every 2 s from 0 to goal_s; the challenger's score at second s is challenger_scores(s)."""
    lines = []
    for s in range(0, goal_s + 1, 2):
        lines.append(signal_line(at(s), 2, challenger_scores(s), main=main_tab_is_goal_tab))
        lines.append(signal_line(at(s), 1, main_score, main=not main_tab_is_goal_tab))
    goal_line = f"{stamp(at(goal_s))} score change tab 2 tile 0: ['A', '1', 'B', '0']"
    readings = bt.parse_signals(lines)
    goals = bt.parse_goals([goal_line])
    return bt.measure(readings, goals, list(cues))[0]


def test_parses_main_and_other_signal_lines_and_breaks():
    readings = bt.parse_signals([signal_line(T0, 5, 0.18, main=True), signal_line(T0, 6, 0.0, kind="break"), "garbage"])
    assert [(r.tab, r.main, r.live, r.score) for r in readings] == [(5, True, True, 0.18), (6, False, False, 0.0)]


def test_a_second_score_change_of_the_same_tab_is_the_same_goal():
    lines = [f"{stamp(at(0))} score change tab 2 tile 0: ['x']", f"{stamp(at(8))} score change tab 2 tile 0: ['x']",
             f"{stamp(at(100))} score change tab 2 tile 0: ['x']", f"{stamp(at(101))} score change tab 3 tile 0: ['x']"]
    assert [(g.tab, int((g.t - T0).total_seconds())) for g in bt.parse_goals(lines)] == [(2, 0), (2, 100), (3, 101)]


def test_parses_cues_with_and_without_spoken_and_weight():
    cues = bt.parse_cues([f"{stamp(T0)} anticipation var tab 7 tile 0 weight 0.20", f"{stamp(T0)} anticipation penalty tab 7 (spoken) weight 0.17",
                          f"{stamp(T0)} anticipation added time tab 7 tile 1"])
    assert [(c.kind, c.tab, c.weight) for c in cues] == [("var", 7, 0.2), ("penalty (spoken)", 7, 0.17), ("added time", 7, None)]


def test_feed_lag_is_positive_when_the_feed_was_ahead():
    lags = bt.parse_feed_lags([f"{stamp(T0)} feed confirmed tab 9 tile 1 nfl SEA-SF: touchdown 8:17, the real score change came 24 s after would-fire, feed lag -28 s (the feed was ahead of the picture)"])
    assert lags == [(T0, 9, 28)]


def test_swap_rule_scores():
    rule = bt.Rule()
    assert bt.qualifies(0.45, 0.10, rule)
    assert not bt.qualifies(0.30, 0.0, rule)      # under min_score
    assert not bt.qualifies(0.40, 0.35, rule)     # not far enough ahead of MAIN
    assert not bt.qualifies(0.45, 0.35, rule)     # ahead by 0.10 only: under min_gap


def test_a_stream_that_rises_early_gets_a_lead():
    lead = run(lambda s: 0.50 if s >= 30 else 0.10)
    assert not lead.already_main
    assert lead.swap_lead_s == 30                       # qualified from second 30 of 60
    assert lead.score_before[30.0] == 0.50 and lead.score_before[5.0] == 0.50
    assert lead.best_other_before[10.0] == 0.10


def test_a_stream_that_only_rises_at_the_goal_has_no_lead():
    lead = run(lambda s: 0.10 if s < 60 else 0.80)
    assert lead.swap_lead_s is None                     # one reading at the goal: not confirmed for confirm_s


def test_one_reading_is_not_a_lead():
    lead = run(lambda s: 0.60 if s == 40 else 0.10)    # a single 2 s spike: the streak is one reading, then it is broken
    assert lead.swap_lead_s is None


def test_a_stream_already_in_main_is_marked_and_has_no_swap_lead():
    lead = run(lambda s: 0.50, main_tab_is_goal_tab=True)
    assert lead.already_main and lead.swap_lead_s is None


def test_a_break_does_not_qualify():
    lines = []
    for s in range(0, 62, 2):
        lines.append(signal_line(at(s), 2, 0.50, kind="break"))
        lines.append(signal_line(at(s), 1, 0.10, main=True))
    lead = bt.measure(bt.parse_signals(lines), [bt.Goal(at(60), 2)], [])[0]
    assert lead.swap_lead_s is None


def test_earliest_cue_is_reported_in_seconds_before_the_goal():
    cues = bt.parse_cues([f"{stamp(at(20))} anticipation var tab 2 tile 0 weight 0.20", f"{stamp(at(45))} anticipation chance tab 2 (spoken) weight 0.17",
                          f"{stamp(at(10))} anticipation var tab 3 tile 0 weight 0.20"])
    lead = run(lambda s: 0.10, cues=cues)
    assert lead.first_cue == (40.0, "var")


def test_summary_counts_and_handles_no_goals():
    assert "No goals" in bt.summarise([])
    text = bt.summarise([run(lambda s: 0.50 if s >= 30 else 0.10), run(lambda s: 0.50, main_tab_is_goal_tab=True)])
    assert "unknown: 2 goals, 1 already in MAIN, 1 to take over" in text and "swap rule qualified AHEAD of the goal (>= 12 s before the confirmation): 1 of 1 (median 30 s ahead)" in text


def test_sport_of_a_goal_is_the_last_detection_before_it_and_metrics_are_split_per_sport():
    sports = bt.parse_sports([f"{stamp(at(-100))} sport detected tab 2 tile 0: American football", f"{stamp(at(10))} sport detected tab 2 tile 0: Football",
                              f"{stamp(at(10))} sport detected tab 3 tile 0: Hockey"])
    lines = []
    for s in range(0, 62, 2):
        lines += [signal_line(at(s), 2, 0.50 if s >= 30 else 0.10), signal_line(at(s), 1, 0.10, main=True)]
    leads = bt.measure(bt.parse_signals(lines), [bt.Goal(at(60), 2)], [], sports=sports)
    assert leads[0].sport == "Football"
    early = bt.measure(bt.parse_signals(lines), [bt.Goal(at(5), 2)], [], sports=sports)
    assert early[0].sport == "American football"
    text = bt.summarise(leads + early, detail=False)
    assert "Football: 1 goals" in text and "American football: 1 goals" in text and "tab 2" not in text


def test_buildup_lead_is_the_earliest_buildup_line_before_the_goal():
    lines = [f"{stamp(at(20))} buildup would take MAIN tab 2: level 0.55 (cues 0.2@3s; surge 0.8 crowd 0.0) vs main 0.10",
             f"{stamp(at(50))} buildup would take MAIN tab 2: level 0.60", f"{stamp(at(10))} buildup would take MAIN tab 3: level 0.60"]
    lead = run(lambda s: 0.10)
    built = bt.measure(bt.parse_signals([signal_line(at(s), 2, 0.1) for s in range(0, 62, 2)]), [bt.Goal(at(60), 2)], [], buildups=bt.parse_buildups(lines))[0]
    assert built.buildup_lead_s == 40.0 and lead.buildup_lead_s is None


def test_a_lead_shorter_than_the_confirmation_lag_does_not_count_as_ahead_of_the_goal():
    late = run(lambda s: 0.50 if s >= 52 else 0.10)         # qualifies 8 s before the confirmation: the goal already happened
    assert late.swap_lead_s == 8
    text = bt.summarise([late], detail=False)
    assert "(>= 12 s before the confirmation): 0 of 1" in text
