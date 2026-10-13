import numpy as np

from daemon.scoring import Config, Scorer
from director_training import features as F
from director_training import fit

from .synth import built_rows

TODAY = {n: 0.0 for n in F.FEATURE_NAMES}
TODAY.update(surge=0.3, crowd=1.0, speech=-0.2, music=-0.5, shout=0.0, event=0.8, cue=1.0, fav=0.4, lev=0.3)


def test_feature_mapping_is_documented_and_complete():
    assert [(f.name, f.section, f.key) for f in F.FEATURES] == [
        ("surge", "weights", "surge"), ("crowd", "weights", "crowd"), ("speech", "weights", "speech"), ("music", "weights", "music"),
        ("shout", "weights", "shout"), ("event", "weights", "event"), ("cue", "weights", "anticipation"), ("fav", "weights", "favourite"),
        ("lev", "scoring", "leverage_weight")]
    from daemon.scoring import OVERRIDABLE_SCORING, Weights
    for f in F.FEATURES:                              # every mapped setting really exists and can be overridden per sport
        assert hasattr(Weights, f.key) if f.section == "weights" else f.key in OVERRIDABLE_SCORING
    assert "[weights.<sport>]" in F.__doc__ and "leverage_weight" in F.__doc__


def test_score_from_row_reproduces_the_daemon_score():
    cfg = Config()
    sc = Scorer(cfg)
    sc.register(1, weight=1.0)
    for i in range(30):
        sc.add_audio(1, 0.05, i * 0.5)
    sc.add_audio(1, 0.12, 15.0)
    sc.set_tags(1, 0.4, 0.5, 0.1, 15.0, shout=0.2)
    sc.set_leverage(1, 0.6)
    sc.set_anticipation(1, 14.0, 0.25, strong=False)
    sc.set_event(1, 13.0, 0.5)
    for now in (15.0, 15.5):
        c = sc.components(1, now, warmup=False)
        stream = sc.streams[1]
        row = {"surge": c["surge"], "crowd": c["crowd"], "speech": c["speech"], "music": c["music"], "shout": c["shout"], "event": c["event"],
               "cue_fade": c["anticipation"], "cue": {"value": stream.anticipation_value, "strong": stream.anticipation_strong},
               "fav": c["fav"], "lev": c["lev"], "state": c["status"], "weight": 1, "ad": False, "on_ad": False}
        assert abs(F.score_from_row(row, cfg) - c["score"]) < 1e-9
        assert F.score_from_row(dict(row, on_ad=True), cfg) == -cfg.ad_penalty and F.score_from_row(dict(row, ad=True), cfg) == -cfg.ad_penalty / 2
    assert F.score_from_row({"surge": None, "crowd": None}, cfg) is None


def test_linear_score_matches_score_from_row_without_the_cue_cap():
    cfg = Config()
    row = {"surge": 0.5, "crowd": 0.6, "speech": 0.3, "music": 0.1, "shout": 0.0, "event": 0.2, "cue": {"value": 0.4, "strong": True}, "cue_fade": 0.5,
           "cue_part": 0.0, "fav": 1.5, "lev": 0.2, "state": "live", "weight": 1}
    cue_part = cfg.weights.anticipation * 0.4 * 0.5 * (1 + cfg.cue_leverage_gain * 0.2)
    row["cue_part"] = cue_part
    x = F.feature_row(row, anticipation_today=cfg.weights.anticipation)
    values = {n: float(getattr(cfg.weights if f.section == "weights" else cfg, f.key)) for n, f in F.BY_NAME.items()}
    talk_free = F.linear_score(x, values) - values["speech"] * 0.3           # a strong cue: the commentary does not count against it
    assert abs(F.score_from_row(row, cfg) - talk_free) < 1e-9


def test_event_counting_merges_neighbouring_rows_of_one_event():
    rows = built_rows(2, events_per_game=2)
    sel = fit.select_rows(rows, "football", "y_t12_60")
    assert sum(s["y"] for s in sel) > 20 and fit.count_events(sel) == 4          # 2 events x 2 games, not 30 rows each


def test_refuses_below_the_guard_and_says_why():
    rows = built_rows(3, events_per_game=2)
    res = fit.fit_sport(rows, "football", TODAY, min_games=8, min_events=25)
    p = res["primary"]
    assert res["status"] == "insufficient-data" and "need 8" in res["reason"] and "need 25" in res["reason"]
    assert p["games"] == 3 and p["positive_events"] == 6 and "candidate" not in p and res["stamp"] is None


def test_enough_games_and_events_fit_without_a_stamp():
    rows = built_rows(9, events_per_game=3)
    res = fit.fit_sport(rows, "football", TODAY, min_games=8, min_events=25)
    p = res["primary"]
    assert res["status"] == "ok" and p["games"] == 9 and p["positive_events"] == 27 and res["stamp"] is None
    assert p["cv"]["candidate_auc"] > 0.9 and len(p["folds"]) == 9
    assert p["coefficients"]["crowd"] > 0 and "fav" in p["constant_features"] and p["candidate"]["settings"]["weights"]["crowd"] > 0
    assert "min_score" in p["candidate"]["settings"]["scoring"] and p["candidate"]["settings"]["scoring"]["min_score"] >= 0.2


def test_dry_run_ignores_the_guard_and_marks_the_result():
    rows = built_rows(3, events_per_game=2)
    res = fit.fit_sport(rows, "football", TODAY, min_games=8, min_events=25, dry_run=True)
    assert res["status"] == "ok" and res["stamp"] == "DRY RUN - do not ship" and res["primary"]["stamp"] == fit.DRY_RUN_STAMP
    assert res["primary"]["guard_ignored"] and "DRY RUN - do not ship" in fit.report({"football": res})


def test_dry_run_still_needs_two_games_and_both_classes():
    one = fit.fit_sport(built_rows(1), "football", TODAY, dry_run=True)
    assert one["status"] == "insufficient-data" and "2 games" in one["reason"]
    no_pos = fit.fit_sport(built_rows(3, events_per_game=0), "football", TODAY, dry_run=True)
    assert no_pos["status"] == "insufficient-data" and "one class" in no_pos["reason"]
    none = fit.fit_sport([], "american football", TODAY)
    assert none["status"] == "insufficient-data" and none["primary"]["rows"] == 0


def test_leave_one_game_out_never_trains_on_the_held_out_game(monkeypatch):
    rows = built_rows(5, events_per_game=3)
    for r in rows:                                    # tag every row with its game in the integer part of a feature
        r["surge"] = int(r["game_key"][1:]) * 10 + r["surge"] * 0.5
    seen = []

    class Recorder:
        def __init__(self, c, seed):
            from sklearn.linear_model import LogisticRegression
            self.m = LogisticRegression(C=c, class_weight="balanced", max_iter=2000, random_state=seed)

        def fit(self, X, y, sample_weight=None):
            seen.append(sorted({int(v // 10) for v in X[:, 0]}))
            self.m.fit(X, y, sample_weight=sample_weight)
            self.coef_, self.intercept_ = self.m.coef_, self.m.intercept_
            return self

        def decision_function(self, X):
            return self.m.decision_function(X)

    monkeypatch.setattr(fit, "_model", lambda c, seed: Recorder(c, seed))
    res = fit.fit_target(rows, "football", "y_t12_60", TODAY, 2, 2, False)
    assert res["status"] == "ok"
    games = sorted(int(g[1:]) for g in res["game_ids"])
    assert len(seen) == len(games) + 1                                    # one fit per fold + the final fit
    for i, g in enumerate(games):
        assert g not in seen[i] and sorted(seen[i] + [g]) == games        # the fold's training set = every other game, not this one
    assert seen[-1] == games                                              # only the final fit (for the weights) sees everything


def test_a_holdout_game_is_kept_out_of_the_fit():
    rows = built_rows(5, events_per_game=3)
    res = fit.fit_target(rows, "football", "y_t12_60", TODAY, 2, 2, False, exclude_games=["g0"])
    assert "g0" not in res["game_ids"] and res["games"] == 4


def test_scaling_rule_keeps_the_median_positive_score_of_today():
    rng = np.random.default_rng(0)
    X = np.zeros((200, len(F.FEATURE_NAMES)))
    X[:, 0] = rng.uniform(0, 1, 200)                 # surge
    X[:, 1] = rng.uniform(0, 1, 200)                 # crowd
    X[:, 2] = rng.uniform(0, 1, 200)                 # speech
    X[:, 4] = rng.uniform(0, 0.2, 200)               # shout: NOT fitted below, keeps today's value
    y = (X[:, 1] > 0.6).astype(int)
    coef = {"surge": 0.2, "crowd": 6.0, "speech": -1.0}
    oof = X[:, [0, 1, 2]] @ np.array([0.2, 6.0, -1.0]) - 2.0
    today = dict(TODAY, shout=0.5)
    cand = fit.candidate_from_fit(coef, -2.0, X, y, today, oof, y)
    pos = y == 1
    new = np.array([cand["weights_by_feature"][n] for n in F.FEATURE_NAMES])
    old = np.array([today[n] for n in F.FEATURE_NAMES])
    m_new, m_old = np.median(np.maximum(X[pos] @ new, 0)), np.median(np.maximum(X[pos] @ old, 0))
    assert abs(m_new - m_old) < 0.02 * m_old + 0.01                       # same median positive-row score as today
    assert cand["weights_by_feature"]["shout"] == 0.5 and "shout" not in cand["settings"]["weights"]      # not fitted: today's value, not patched
    assert cand["settings"]["weights"]["crowd"] == cand["weights_by_feature"]["crowd"] and cand["alpha"] > 0
    assert 0.2 <= cand["min_score"] <= 0.8 and cand["settings"]["scoring"]["min_score"] == cand["min_score"]
    assert cand["weights_by_feature"]["crowd"] / cand["weights_by_feature"]["surge"] > 20          # the ratios of the coefficients survive


def test_leverage_maps_to_the_scoring_table_and_cue_to_anticipation():
    rng = np.random.default_rng(1)
    X = np.zeros((100, len(F.FEATURE_NAMES)))
    X[:, 6] = rng.uniform(0, 0.4, 100)               # cue
    X[:, 8] = rng.uniform(-1, 1, 100)                # lev
    y = (X[:, 6] > 0.2).astype(int)
    coef = {"cue": 5.0, "lev": 1.0}
    cand = fit.candidate_from_fit(coef, -1.0, X, y, TODAY, np.full(100, np.nan), y)
    assert "anticipation" in cand["settings"]["weights"] and "leverage_weight" in cand["settings"]["scoring"]
    assert "min_score" not in cand["settings"]["scoring"]                  # no out-of-fold predictions: no operating point
