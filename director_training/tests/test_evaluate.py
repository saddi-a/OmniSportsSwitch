from daemon import dataset_builder as db
from daemon.scoring import Config
from director_training import evaluate as E
from director_training import features as F
from daemon.tests import test_dataset_builder as tdb

from .synth import T0, signal_rows


def loud_tab2(tab, off):
    return 0.9 if (tab == 2 and off >= 100) else 0.1


def day(goal_on="gA"):
    rows, events = signal_rows(loud_tab2)
    result = db.build(rows, events, tdb.feed(goals=[(goal_on, 110, "goal")]))
    return result, events


def run(candidate, holdout=(), fit_games=(), **kw):
    result, events = day(**kw)
    return E.evaluate(result, events, "football", Config(), candidate, holdout, fit_games)


def test_candidate_equal_to_todays_weights_reproduces_the_recorded_regret_numbers():
    cfg = Config()
    same = {"weights": {"crowd": cfg.weights.crowd, "surge": cfg.weights.surge}, "scoring": {"min_score": cfg.min_score}}
    res = run(same)
    m = res["metrics"]
    assert res["status"] == "ok"
    assert res["swap_counts"] == {"recorded": 1, "today_sim": 1, "candidate_sim": 1}
    assert res["fidelity"] == {"recorded_decide_swaps": 1, "simulated_decide_swaps": 1, "matched": 1}
    for key in ("today_sim", "candidate_sim"):                     # the same regret numbers as the recorded sequence
        assert m[key]["regrets"] == m["recorded"]["regrets"] == 1
        assert m[key]["regret_weight"] == m["recorded"]["regret_weight"] == 2
        assert abs(m[key]["regret_per_10min"] - m["recorded"]["regret_per_10min"]) < 0.5
        assert m[key]["swaps"] == m["recorded"]["swaps"] == 1
    assert res["comparison"]["regrets"] == 0
    assert res["score_reproduction"]["median_abs_diff"] is None        # the synthetic rows carry no recorded score


def test_a_candidate_that_ignores_the_crowd_makes_no_swap_and_no_regret():
    res = run({"weights": {"crowd": 0.0}, "scoring": {}})
    assert res["swap_counts"]["candidate_sim"] == 0 and res["metrics"]["candidate_sim"]["regrets"] == 0
    assert res["metrics"]["today_sim"]["regrets"] == 1 and res["comparison"]["regrets"] == -1


def test_a_swap_to_the_stream_with_the_goal_is_not_regret_in_any_sequence():
    res = run({"weights": {}, "scoring": {}}, goal_on="gB")
    assert [res["metrics"][k]["regrets"] for k in ("recorded", "today_sim", "candidate_sim")] == [0, 0, 0]


def test_hold_out_flag_follows_the_games_the_fit_saw():
    seen = run({"weights": {}, "scoring": {}}, fit_games=["gA", "gB"])
    assert seen["hold_out"] is False and seen["games_seen_by_fit"] == ["gA", "gB"]
    fresh = run({"weights": {}, "scoring": {}}, fit_games=["other"])
    assert fresh["hold_out"] is True
    only_b = run({"weights": {}, "scoring": {}}, holdout=["gB"], fit_games=["gA"])
    assert only_b["games_evaluated"] == ["gB"] and only_b["status"] == "insufficient-data" and "2 streams" in only_b["reason"]


def test_no_rows_of_the_sport_is_insufficient_data():
    result, events = day()
    res = E.evaluate(result, events, "american football", Config(), {"weights": {}, "scoring": {}})
    assert res["status"] == "insufficient-data" and res["hold_out"] is False


def test_user_and_buildup_swaps_are_replayed_as_recorded():
    rows, events = signal_rows(lambda tab, off: 0.1)                 # nobody is loud: the rule would never swap
    events = [dict(tdb.swap(60, 2, 1, source="buildup"))]
    result = db.build(rows, events, tdb.feed())
    res = E.evaluate(result, events, "football", Config(), {"weights": {}, "scoring": {}})
    assert res["swap_counts"] == {"recorded": 1, "today_sim": 1, "candidate_sim": 1}
    assert [s["source"] for s in E.simulate([r for r in result["rows"]], Config(), events)[0]] == ["buildup"]
    assert E.simulate(result["rows"], Config(), events, exogenous=())[0] == []


def test_dwell_and_confirm_apply():
    cfg = Config()
    rows, events = signal_rows(lambda tab, off: 0.9 if (tab == 2 and 100 <= off < 101) else 0.1)      # a single loud reading: not confirmed
    result = db.build(rows, [], tdb.feed())
    assert E.simulate(result["rows"], cfg, [])[0] == []
    rows, _ = signal_rows(lambda tab, off: 0.9 if (tab == 2 and off >= 100) else 0.1)
    swaps, timeline = E.simulate(db.build(rows, [], tdb.feed())["rows"], cfg, [])
    assert len(swaps) == 1 and swaps[0]["t"] == T0 + 102 and swaps[0]["promote"] == 2 and timeline[-1] == (T0 + 102, 2)


def test_report_lists_what_the_replay_ignores():
    res = run({"weights": {}, "scoring": {}}, fit_games=["gA"])
    text = E.report(dict(res, stamp=None))
    assert "What the replay ignores" in text and "IN-SAMPLE" in text and "hot-game" in text and len(res["approximations"]) >= 5


def test_a_candidate_view_changes_only_what_it_names():
    cv = F.CandidateView(Config(), {"crowd": 2.0}, {"min_score": 0.5})
    assert cv.weights.crowd == 2.0 and cv.weights.surge == Config().weights.surge and cv.min_score == 0.5 and cv.threshold == Config().threshold
