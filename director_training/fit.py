"""Fit: one small L2 logistic regression per sport on the score components (see features.py for the feature -> setting map).

Target: `y_t12_60` (a tier-1/2 event on this stream in the next 60 s), secondary `y_goal_30`. Class-weighted ('balanced') and sample-weighted by the
builder's `label_weight`. Validation: leave-one-GAME-out (the unit is `game_key`; rows of a game are never split).

GUARD: a sport is only fitted with at least `min_games` games and `min_events` independent positive events (positive rows of one game closer than
`EVENT_GAP_S` seconds in event time count once, because 15 neighbouring rows are the same goal). Below that the result is `insufficient-data`
with the counts. `dry_run=True` skips the guard only to exercise the pipeline (it still needs two games and both classes) and stamps the
result `DRY RUN - do not ship`.

SCALING RULE (so the new score stays comparable with today's): the logistic model has logit z = b + sum(c_k * f_k). The daemon has no intercept
and its thresholds (min_score, margins) are tuned on today's scale, so the candidate weights are w_k = alpha * c_k where alpha > 0 is the one number
that makes the MEDIAN score of the positive training rows under the candidate weights equal the median score of the same rows under today's
weights. Features with no variance in the data (e.g. favourite, leverage when every row has the same value) are not fitted: they keep today's value.
The candidate `min_score` is the best-F1 operating point z* of the pooled out-of-fold predictions mapped to the score scale, alpha*(z* - b),
clipped to [MIN_SCORE_CLIP]. It is a candidate for the shadow evaluation, not a decision.
"""
import collections
import statistics

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

from . import features as F

TARGET_PRIMARY, TARGET_SECONDARY = "y_t12_60", "y_goal_30"
EVENT_GAP_S = 20.0
MIN_SCORE_CLIP = (0.20, 0.80)
DRY_RUN_STAMP = "DRY RUN - do not ship"
DEFAULT_MIN_GAMES, DEFAULT_MIN_EVENTS = 8, 25


def select_rows(rows: list[dict], sport: str, target: str, exclude_games=(), anticipation_today: float = 1.0) -> list[dict]:
    """The usable labelled rows of one sport: aligned, labelled, live, not an ad, with an audio reading. Each result is
    {game, t, x (feature vector), y, w, event_t}."""
    out = []
    excluded = set(exclude_games)
    for r in rows:
        if r.get("sport_used") != sport or not r.get("aligned") or r.get(target) is None:
            continue
        if r.get("state") not in (None, "live") or r.get("ad") or r.get("on_ad"):
            continue
        if r.get("game_key") in excluded:
            continue
        x = F.feature_row(r, anticipation_today)
        if x is None:
            continue
        nxt = r.get("secs_to_next_event")
        out.append({"game": r.get("game_key") or f"{r.get('date')}:tab{r.get('tab')}", "t": float(r["t"]), "x": x, "y": int(r[target]),
                    "w": float(r["label_weight"]) if r.get("label_weight") is not None else 1.0,
                    "event_t": float(r["t"]) + float(nxt) if (r[target] and nxt is not None) else None})
    return out


def count_events(sel: list[dict]) -> int:
    """Independent positive events: per game, positive rows whose event time is within EVENT_GAP_S of the previous one count once. A positive row
    with no known event time counts as its own event only if no other positive row of the game is within EVENT_GAP_S of its time."""
    per_game: dict = collections.defaultdict(list)
    for s in sel:
        if s["y"]:
            per_game[s["game"]].append(s["event_t"] if s["event_t"] is not None else s["t"])
    total = 0
    for times in per_game.values():
        last = None
        for t in sorted(times):
            if last is None or t - last > EVENT_GAP_S:
                total += 1
            last = t
    return total


def _model(c: float, seed: int) -> LogisticRegression:
    return LogisticRegression(C=c, class_weight="balanced", max_iter=2000, random_state=seed)


def _auc_ap(y, score):
    y = np.asarray(y)
    if len(set(y.tolist())) < 2:
        return None, None
    return float(roc_auc_score(y, score)), float(average_precision_score(y, score))


def _active_columns(X: np.ndarray) -> list[int]:
    return [j for j in range(X.shape[1]) if float(np.ptp(X[:, j])) > 1e-9]


def _best_f1_threshold(y, z):
    p, r, th = precision_recall_curve(np.asarray(y), np.asarray(z))
    if len(th) == 0:
        return None
    f1 = 2 * p[:-1] * r[:-1] / np.maximum(p[:-1] + r[:-1], 1e-12)
    best = int(np.flatnonzero(f1 >= f1.max() - 1e-12)[-1])    # the highest threshold among equals: fewer, surer swaps
    return float(th[best]), float(f1[best])


def fit_target(rows: list[dict], sport: str, target: str, today: dict, min_games: int, min_events: int, dry_run: bool, c: float = 1.0, seed: int = 0,
               exclude_games=()) -> dict:
    """The whole fit of one target for one sport. Returns a JSON-able dict; `status` is 'ok' or 'insufficient-data'."""
    sel = select_rows(rows, sport, target, exclude_games, today["cue"])
    games = sorted({s["game"] for s in sel})
    pos = sum(s["y"] for s in sel)
    events = count_events(sel)
    res = {"sport": sport, "target": target, "rows": len(sel), "positive_rows": pos, "games": len(games), "game_ids": games, "positive_events": events,
           "guard": {"min_games": min_games, "min_events": min_events}, "dry_run": bool(dry_run), "status": "ok", "reason": None,
           "stamp": DRY_RUN_STAMP if dry_run else None, "per_game": {}}
    for g in games:
        gs = [s for s in sel if s["game"] == g]
        res["per_game"][g] = {"rows": len(gs), "positive_rows": sum(s["y"] for s in gs), "events": count_events(gs)}
    why = []
    if len(games) < min_games:
        why.append(f"{len(games)} game(s) with usable labelled rows, need {min_games}")
    if events < min_events:
        why.append(f"{events} independent positive event(s), need {min_events}")
    hard = []
    if len(games) < 2:
        hard.append("fewer than 2 games: leave-one-game-out is impossible")
    if pos == 0 or pos == len(sel):
        hard.append("only one class in the data")
    if hard or (why and not dry_run):
        res["status"], res["reason"] = "insufficient-data", "; ".join(hard + why)
        return res
    if why:
        res["guard_ignored"] = why                                  # a dry run: said out loud

    X = np.array([s["x"] for s in sel], dtype=float)
    y = np.array([s["y"] for s in sel], dtype=int)
    sw = np.array([s["w"] for s in sel], dtype=float)
    game_arr = np.array([s["game"] for s in sel])
    cols = _active_columns(X)
    res["active_features"] = [F.FEATURE_NAMES[j] for j in cols]
    res["constant_features"] = [F.FEATURE_NAMES[j] for j in range(X.shape[1]) if j not in cols]
    today_vec = np.array([today[n] for n in F.FEATURE_NAMES], dtype=float)
    today_score = X @ today_vec

    folds, oof_z = [], np.full(len(sel), np.nan)
    for g in games:
        te, tr = game_arr == g, game_arr != g
        entry = {"game": g, "rows": int(te.sum()), "positive_rows": int(y[te].sum())}
        if len(set(y[tr].tolist())) < 2:
            entry["skipped"] = "training part has one class"
            folds.append(entry)
            continue
        m = _model(c, seed).fit(X[tr][:, cols], y[tr], sample_weight=sw[tr])
        z = m.decision_function(X[te][:, cols])
        oof_z[te] = z
        entry["candidate_auc"], entry["candidate_ap"] = _auc_ap(y[te], z)
        entry["today_auc"], entry["today_ap"] = _auc_ap(y[te], today_score[te])
        folds.append(entry)
    res["folds"] = folds
    seen = ~np.isnan(oof_z)
    pooled = {}
    if seen.any():
        pooled["candidate_auc"], pooled["candidate_ap"] = _auc_ap(y[seen], oof_z[seen])
        pooled["today_auc"], pooled["today_ap"] = _auc_ap(y[seen], today_score[seen])
    scored = [f for f in folds if f.get("candidate_auc") is not None and f.get("today_auc") is not None]
    pooled["folds_with_both_classes"] = len(scored)
    pooled["mean_fold_candidate_auc"] = statistics.mean(f["candidate_auc"] for f in scored) if scored else None
    pooled["mean_fold_today_auc"] = statistics.mean(f["today_auc"] for f in scored) if scored else None
    pooled["candidate_wins_auc"] = sum(1 for f in scored if f["candidate_auc"] > f["today_auc"])
    res["cv"] = pooled

    final = _model(c, seed).fit(X[:, cols], y, sample_weight=sw)
    coef = {F.FEATURE_NAMES[j]: float(final.coef_[0][i]) for i, j in enumerate(cols)}
    b = float(final.intercept_[0])
    res["coefficients"], res["intercept"], res["C"], res["seed"] = coef, b, c, seed
    cand = candidate_from_fit(coef, b, X, y, today, oof_z, y_all=y)
    res["candidate"] = cand
    res["sign_flips"] = [n for n, w in cand["weights_by_feature"].items() if today.get(n) and w and (w > 0) != (today[n] > 0)]
    return res


def candidate_from_fit(coef: dict, b: float, X: np.ndarray, y: np.ndarray, today: dict, oof_z: np.ndarray, y_all: np.ndarray) -> dict:
    """The scaling rule and the operating threshold (module docstring)."""
    names = F.FEATURE_NAMES
    fitted = [n for n in names if n in coef]
    idx = {n: i for i, n in enumerate(names)}
    pos = y == 1
    fit_part = np.zeros(len(y))
    for n in fitted:
        fit_part += coef[n] * X[:, idx[n]]
    other = np.zeros(len(y))
    for n in names:
        if n not in coef:
            other += today[n] * X[:, idx[n]]
    today_score = X @ np.array([today[n] for n in names], dtype=float)
    target = float(np.median(np.maximum(today_score[pos], 0.0))) if pos.any() else 0.0
    alpha, note = 1.0, None
    if target > 0 and pos.any():
        grid = np.exp(np.linspace(np.log(0.01), np.log(100.0), 801))
        errs = [abs(float(np.median(np.maximum(a * fit_part[pos] + other[pos], 0.0))) - target) for a in grid]
        best = min(range(len(grid)), key=lambda i: (round(errs[i], 9), abs(np.log(grid[i]))))
        alpha = float(grid[best])
    else:
        note = "today's median positive-row score is 0: no scaling possible, alpha = 1"
    weights_by_feature = {n: (alpha * coef[n] if n in coef else today[n]) for n in names}
    cand_score = X @ np.array([weights_by_feature[n] for n in names], dtype=float)
    out = {"alpha": alpha, "alpha_note": note, "weights_by_feature": {n: F.round_setting(v) for n, v in weights_by_feature.items()},
           "today_by_feature": {n: F.round_setting(today[n]) for n in names},
           "median_positive_score": {"today": target, "candidate": float(np.median(np.maximum(cand_score[pos], 0.0))) if pos.any() else None},
           "settings": {"weights": {}, "scoring": {}}, "min_score": None}
    for n in fitted:
        f = F.BY_NAME[n]
        out["settings"][f.section][f.key] = F.round_setting(weights_by_feature[n])
    seen = ~np.isnan(oof_z)
    if seen.any() and len(set(y_all[seen].tolist())) == 2:
        bt = _best_f1_threshold(y_all[seen], oof_z[seen])
        if bt:
            z_star, f1 = bt
            raw_min = alpha * (z_star - b)
            lo, hi = MIN_SCORE_CLIP
            out["operating_point"] = {"z_star": z_star, "best_f1": f1, "min_score_unclipped": F.round_setting(raw_min)}
            out["min_score"] = F.round_setting(min(max(raw_min, lo), hi))
            out["min_score_clipped"] = not (lo <= raw_min <= hi)
            out["settings"]["scoring"]["min_score"] = out["min_score"]
    return out


def fit_sport(rows: list[dict], sport: str, today: dict, min_games: int = DEFAULT_MIN_GAMES, min_events: int = DEFAULT_MIN_EVENTS, dry_run: bool = False,
              c: float = 1.0, seed: int = 0, exclude_games=()) -> dict:
    """Primary target (candidate weights) and secondary target (coefficients and CV only)."""
    primary = fit_target(rows, sport, TARGET_PRIMARY, today, min_games, min_events, dry_run, c, seed, exclude_games)
    secondary = fit_target(rows, sport, TARGET_SECONDARY, today, min_games, min_events, dry_run, c, seed, exclude_games)
    status = primary["status"]
    return {"sport": sport, "status": status, "reason": primary["reason"], "dry_run": bool(dry_run), "stamp": DRY_RUN_STAMP if dry_run else None,
            "primary": primary, "secondary": secondary}


def report(results: dict) -> str:
    """Plain-language report of `{sport: fit_sport result}`."""
    L = ["# Fit report", ""]
    for sport, r in results.items():
        p = r["primary"]
        L += [f"## {sport}", ""]
        if r["dry_run"]:
            L += [f"**{DRY_RUN_STAMP}.** The data guard was ignored only to test the pipeline; the numbers below are not meaningful.", ""]
        L += [f"- usable labelled rows: {p['rows']} ({p['positive_rows']} positive), games: {p['games']}, independent positive events: {p['positive_events']}",
              f"- guard: at least {p['guard']['min_games']} games and {p['guard']['min_events']} positive events"]
        if r["status"] != "ok":
            L += [f"- RESULT: **insufficient data** - {r['reason']}. Nothing was fitted. Collect more games and run `fit` again.", ""]
            continue
        if p.get("guard_ignored"):
            L += ["- guard ignored (dry run): " + "; ".join(p["guard_ignored"])]
        L += [f"- features fitted: {', '.join(p['active_features'])}; left at today's value (no variance in the data): {', '.join(p['constant_features']) or 'none'}", ""]
        cv = p["cv"]
        L += ["Leave-one-game-out (each game predicted by a model that never saw it):", "",
              "| | AUC | average precision |", "|---|---|---|",
              f"| candidate (pooled) | {_f(cv.get('candidate_auc'))} | {_f(cv.get('candidate_ap'))} |",
              f"| today's weights (pooled) | {_f(cv.get('today_auc'))} | {_f(cv.get('today_ap'))} |", "",
              f"- folds with both classes: {cv['folds_with_both_classes']} of {p['games']}; candidate beats today's AUC in {cv['candidate_wins_auc']} of them; "
              f"mean fold AUC {_f(cv.get('mean_fold_candidate_auc'))} vs {_f(cv.get('mean_fold_today_auc'))}", "",
              "| game | rows | positive rows | candidate AUC | today AUC |", "|---|---|---|---|---|"]
        for fo in p["folds"]:
            L.append(f"| {fo['game']} | {fo['rows']} | {fo['positive_rows']} | {_f(fo.get('candidate_auc'))} | {_f(fo.get('today_auc'))} |")
        cand = p["candidate"]
        L += ["", f"Candidate weights (scale factor alpha = {cand['alpha']:.3f}; positive-row median score {_f(cand['median_positive_score']['candidate'])} vs today's "
              f"{_f(cand['median_positive_score']['today'])}):", "", "| feature | setting | today | candidate | raw coefficient |", "|---|---|---|---|---|"]
        for n in F.FEATURE_NAMES:
            f = F.BY_NAME[n]
            L.append(f"| {n} | {f.section}.{f.key} | {cand['today_by_feature'][n]} | {cand['weights_by_feature'][n]}{'' if n in p['coefficients'] else ' (not fitted)'} | "
                     f"{_f(p['coefficients'].get(n))} |")
        if p["sign_flips"]:
            L += ["", "Sign flips against today's weights (look at these first): " + ", ".join(p["sign_flips"])]
        if cand.get("min_score") is not None:
            L += ["", f"Candidate operating threshold `min_score` = {cand['min_score']}" + (" (clipped)" if cand.get("min_score_clipped") else "")
                  + f" (best F1 {_f(cand['operating_point']['best_f1'])} on the out-of-fold predictions)."]
        s = r["secondary"]
        if s["status"] == "ok":
            L += ["", f"Secondary target `{TARGET_SECONDARY}`: pooled AUC candidate {_f(s['cv'].get('candidate_auc'))} vs today's {_f(s['cv'].get('today_auc'))} "
                  f"({s['positive_events']} events)."]
        L.append("")
    return "\n".join(L)


def _f(x, spec=".3f"):
    return "-" if x is None else format(x, spec)
