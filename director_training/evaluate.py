"""Shadow evaluation: replay the recorded rows with candidate weights and judge the swap sequence with the dataset builder's own metric code.

The recorded rows hold every signal of every stream every ~2 s. `features.score_from_row` recomputes each stream's score under any weights with the
formula of `Scorer.components`; `simulate` then applies a SIMPLIFIED swap rule (`Scorer.decide`) to produce the swap sequence the candidate would have
made; `judge_sequence` runs `dataset_builder.judge_swaps / judge_events / summarise` on it (regret, miss, lead). Three sequences are judged on the
same rows: the RECORDED swaps (what really happened), the SIMULATION WITH TODAY'S SETTINGS and the SIMULATION WITH THE CANDIDATE. The honest comparison
is candidate-sim against today-sim (same approximation on both sides); the recorded line is for reference.

WHAT THE REPLAY IGNORES (so it is an APPROXIMATION, `APPROXIMATIONS` below is copied into every report):
"""
import bisect
import collections
import statistics

from daemon import dataset_builder as db

from . import features as F

APPROXIMATIONS = (
    "Swaps by the user and by the build-up tracker are taken from the recording as they happened and are NOT re-decided (the tracker's level is built from the recorded cues and surge, not from the candidate weights); only the normal swap rule (`decide`) is re-simulated.",
    "The cues, events, game states, ads, replay flags and loudness are the RECORDED ones: a candidate changes how they are weighed, not when they happened, and a different Main would not have changed what the streams did.",
    "Simplified rule: challenger vs Main by `threshold`, `min_gap`, `min_score`, `confirm_s` and the dwell, with the recorded goal hold and manual hold. Ignored: the hot-game margin, the goal yield, the fresh-goal dwell bypass, the hold-off of streams that were on an ad lately, slot rules beyond `can_main`, the warm-up state machine, MAIN eviction, DAZN multiview tiles.",
    "The score is recomputed from the recorded signals: the cue cap and leverage follow `Scorer.components`, but a recorded row is a 2 s sample of a decision loop that runs several times a second.",
    "The user's swap marks apply to the swaps that really happened; a candidate swap at a new time has the automatic verdict only (all three sequences are therefore also judged WITHOUT marks).",
    "A game's picture lag is estimated from the recording itself; a swap near an event whose lag is unknown is judged on the builder's wide margin (`lag assumed`).",
)
__doc__ += "\n".join(f"  - {a}" for a in APPROXIMATIONS)

DEFAULT_EXOGENOUS = ("user", "buildup")


def sport_of_row(r: dict):
    return r.get("sport_used") or r.get("sport")


def simulate(rows: list[dict], cfg, recorded_swaps: list[dict], exogenous=DEFAULT_EXOGENOUS) -> tuple:
    """(swap events, main timeline) of the simplified rule on the rows (one sport, one config view `cfg`).

    Swap events have the recorder's shape (`t`, `promote`, `demote`, `source`, `sport`). `main timeline` = [(t, main tab)] after the swap at t
    (first entry: the tab that was Main at the first tick). Recorded swaps whose source is in `exogenous` are applied at their time."""
    ticks: dict = collections.defaultdict(dict)
    for r in rows:
        ticks[round(r["t"], 2)][r["tab"]] = r
    times = sorted(ticks)
    exo = sorted((s for s in recorded_swaps if s.get("source") in exogenous), key=lambda s: s["t"])
    ei = 0
    out, timeline = [], []
    main, last_swap, cand = None, float("-inf"), None
    for t in times:
        by_tab = ticks[t]
        if main is None:
            main = next((tab for tab, r in by_tab.items() if r.get("is_main")), None)
            if main is None:
                continue
            timeline.append((t, main))
        while ei < len(exo) and exo[ei]["t"] <= t + 1e-6:
            s = exo[ei]
            ei += 1
            if s.get("promote") in by_tab and s["promote"] != main:
                out.append({"t": s["t"], "promote": s["promote"], "demote": main, "source": s.get("source"), "sport": s.get("sport"), "reason": s.get("reason")})
                main, last_swap, cand = s["promote"], s["t"], None
                timeline.append((s["t"], main))
        row_main = by_tab.get(main)
        if row_main is None or row_main.get("frozen"):
            continue
        if t - last_swap < cfg.min_dwell_s:
            continue
        current = F.score_from_row(row_main, cfg)
        current_score = current if current is not None else 0.0
        idle = bool(row_main.get("ad") or row_main.get("on_ad") or (row_main.get("state") or "live") != "live")
        best_tab, best_score, best_row = None, None, None
        for tab, r in by_tab.items():
            if tab == main or not r.get("can_main", True):
                continue
            sc = F.score_from_row(r, cfg)
            if sc is None:
                continue
            if idle and ((r.get("state") or "live") != "live" or r.get("ad") or r.get("on_ad") or (r.get("music") or 0.0) >= cfg.idle_music):
                continue
            if best_score is None or sc > best_score:
                best_tab, best_score, best_row = tab, sc, r
        if best_tab is None:
            cand = None
            continue
        ccfg = cfg
        if not idle:
            if (row_main.get("hold") or 0.0) > 0 and best_score < ccfg.hold_break_score:
                cand = None
                continue
            if (row_main.get("goal_hold") or 0.0) > 0 and not (best_score >= ccfg.goal_hold_break or float(best_row.get("event") or 0.0) >= ccfg.goal_hold_break_event):
                cand = None
                continue
            if best_score < ccfg.min_score or best_score < max(current_score * (1 + ccfg.threshold), current_score + ccfg.min_gap):
                cand = None
                continue
            if cand is None or cand[:2] != (main, best_tab):
                cand = (main, best_tab, t)
            if t - cand[2] < ccfg.confirm_s:
                continue
        out.append({"t": t, "promote": best_tab, "demote": main, "source": "decide", "sport": sport_of_row(best_row),
                    "reason": f"simulated: {best_score:.2f} vs {current_score:.2f}"})
        main, last_swap, cand = best_tab, t, None
        timeline.append((t, main))
    return out, timeline


def rows_with_main(rows: list[dict], timeline: list[tuple]) -> list[dict]:
    """Copies of the rows with `is_main` set from a main timeline (the swap sequence under judgement)."""
    if not timeline:
        return [dict(r) for r in rows]
    ts = [t for t, _ in timeline]
    out = []
    for r in rows:
        i = bisect.bisect_right(ts, round(r["t"], 2) + 1e-6) - 1
        main = timeline[i][1] if i >= 0 else timeline[0][1]
        out.append(dict(r, is_main=(r["tab"] == main)))
    return out


def judge_sequence(ctx: dict, rows: list[dict], swap_events: list[dict], marks: dict | None = None) -> dict:
    """The builder's metrics for one swap sequence on these rows: {swaps, events, summary (per sport)}."""
    p = ctx["params"]
    index = db.RowIndex(rows, p)
    swaps = db.judge_swaps(swap_events, index, ctx["tab_ev"], ctx["cues_by_tab"], ctx["sports"], p, marks, ctx["tab_ev_wide"], ctx["lags"])
    events = db.judge_events(ctx["feed_events"], rows, swaps, index, ctx["tab_ev"], p)
    minutes = db.watched_live_minutes(rows, ctx["sports"], p)
    return {"swaps": swaps, "events": events, "summary": db.summarise(rows, swaps, events, minutes)}


def headline(summary: dict, sport: str) -> dict:
    s = summary.get(sport) or {}
    last = s.get("last_leads") or []
    return {"swaps": s.get("swaps"), "judged": s.get("judged"), "regrets": s.get("regrets"), "regret_weight": s.get("regret_weight"),
            "regret_per_10min": s.get("regret_per_10min"), "regret_share": s.get("regret_share"), "misses": s.get("misses"),
            "miss_per_10min": s.get("miss_per_10min"), "goals": s.get("goals"), "goals_ahead_12s": s.get("goals_ahead_12s"),
            "median_lead_last_s": statistics.median(last) if last else None, "lead_n": len(last), "live_main_minutes": s.get("live_main_minutes"),
            "regrets_goal_left": s.get("regrets_goal_left"), "misses_left_just_before": s.get("misses_left_just_before")}


def score_reproduction(rows: list[dict], cfg) -> dict:
    """How well `score_from_row` with TODAY'S settings reproduces the recorded `score` (the check that the replay uses the daemon's formula)."""
    diffs = [abs(F.score_from_row(r, cfg) - r["score"]) for r in rows if r.get("score") is not None and F.score_from_row(r, cfg) is not None]
    if not diffs:
        return {"rows": 0, "median_abs_diff": None, "p95_abs_diff": None}
    diffs.sort()
    return {"rows": len(diffs), "median_abs_diff": diffs[len(diffs) // 2], "p95_abs_diff": diffs[min(len(diffs) - 1, int(len(diffs) * 0.95))]}


def fidelity(recorded: list[dict], simulated: list[dict], tol_s: float = 6.0) -> dict:
    """Recorded 'decide' swaps vs the simulation with TODAY's settings: how many the simulation also made (same promote, within tol_s)."""
    rec = [s for s in recorded if s.get("source") == "decide"]
    sim = [s for s in simulated if s.get("source") == "decide"]
    used, hit = set(), 0
    for s in rec:
        for i, x in enumerate(sim):
            if i not in used and x["promote"] == s["promote"] and abs(x["t"] - s["t"]) <= tol_s:
                used.add(i)
                hit += 1
                break
    return {"recorded_decide_swaps": len(rec), "simulated_decide_swaps": len(sim), "matched": hit}


def evaluate(result: dict, events_file: list[dict], sport: str, today_cfg, candidate: dict, holdout_games=(), fit_games=(), exogenous=DEFAULT_EXOGENOUS, marks=None) -> dict:
    """Shadow evaluation of `candidate` ({'weights': {...}, 'scoring': {...}} setting names -> values) for one sport.

    `result` = dataset_builder.build(...) output (with `context`); `holdout_games` = the games to evaluate on (empty = every game of the sport);
    `fit_games` = the games the fit saw (to decide whether this is a real hold-out)."""
    ctx = result["context"]
    all_rows = sorted((r for r in result["rows"] if sport_of_row(r) == sport), key=lambda r: r["t"])
    held = set(holdout_games)
    rows = [r for r in all_rows if r.get("game_key") in held] if held else all_rows
    games = sorted({r.get("game_key") for r in rows})
    seen_by_fit = sorted(set(games) & set(fit_games))
    out = {"sport": sport, "games_evaluated": games, "rows": len(rows), "hold_out": bool(rows) and not seen_by_fit,
           "games_seen_by_fit": seen_by_fit, "approximations": list(APPROXIMATIONS)}
    if not rows:
        out.update(status="insufficient-data", reason="no rows of this sport in the evaluated games")
        return out
    tabs = {r["tab"] for r in rows}
    if len(tabs) < 2:
        out.update(status="insufficient-data", reason="fewer than 2 streams: a swap needs at least two")
        return out
    recorded = [e for e in events_file if e.get("kind") == "swap" and e.get("promote") in tabs and e.get("demote") in tabs]
    cand_cfg = F.CandidateView(today_cfg, candidate.get("weights"), candidate.get("scoring"))
    sim_today, tl_today = simulate(rows, today_cfg, recorded, exogenous)
    sim_cand, tl_cand = simulate(rows, cand_cfg, recorded, exogenous)
    seqs = {
        "recorded": (rows, recorded, None),
        "recorded_with_marks": (rows, recorded, "marks"),
        "today_sim": (rows_with_main(rows, tl_today), sim_today, None),
        "candidate_sim": (rows_with_main(rows, tl_cand), sim_cand, None),
    }
    metrics = {}
    for name, (rws, sws, m) in seqs.items():
        j = judge_sequence(ctx, rws, sws, marks if m == "marks" else None)
        metrics[name] = headline(j["summary"], sport)
    out.update(status="ok", metrics=metrics, fidelity=fidelity(recorded, sim_today), score_reproduction=score_reproduction(rows, today_cfg),
               swap_counts={"recorded": len(recorded), "today_sim": len(sim_today), "candidate_sim": len(sim_cand)},
               candidate=candidate)
    out["candidate_swaps"] = [{"t": s["t"], "promote": s["promote"], "demote": s["demote"], "source": s["source"]} for s in sim_cand]
    out["comparison"] = compare(metrics["today_sim"], metrics["candidate_sim"])
    return out


def compare(today: dict, cand: dict) -> dict:
    """Candidate minus today (same approximation on both sides); None where a side has no value."""
    def d(k):
        a, b = today.get(k), cand.get(k)
        return None if a is None or b is None else b - a
    return {"regrets": d("regrets"), "regret_per_10min": d("regret_per_10min"), "misses": d("misses"), "miss_per_10min": d("miss_per_10min"),
            "median_lead_last_s": d("median_lead_last_s"), "goals_ahead_12s": d("goals_ahead_12s"), "swaps": d("swaps")}


def report(shadow: dict) -> str:
    L = ["# Shadow evaluation report", "", f"Sport: {shadow['sport']}. Games evaluated: {len(shadow['games_evaluated'])}, rows: {shadow['rows']}.", ""]
    if shadow.get("stamp"):
        L += [f"**{shadow['stamp']}**", ""]
    if not shadow["hold_out"]:
        L += ["**IN-SAMPLE: these games were also used to fit the candidate. Not a hold-out test; it cannot be shipped on this.**" if shadow["games_seen_by_fit"] else "No rows evaluated.", ""]
    if shadow["status"] != "ok":
        L += [f"RESULT: insufficient data - {shadow.get('reason')}.", ""]
    else:
        f, sr = shadow["fidelity"], shadow["score_reproduction"]
        L += [f"Replay check: the simulation with today's settings made {f['simulated_decide_swaps']} normal-rule swaps; the recording has {f['recorded_decide_swaps']}, "
              f"{f['matched']} matched (same stream, within 6 s). Recomputed score vs the recorded score: median difference {_f(sr['median_abs_diff'])} over {sr['rows']} rows.", "",
              "| | recorded | recorded + your marks | today (replayed) | candidate (replayed) |", "|---|---|---|---|---|"]
        m = shadow["metrics"]
        for label, key, spec in (("swaps", "swaps", ".0f"), ("judged swaps", "judged", ".0f"), ("regret swaps", "regrets", ".0f"), ("regret per 10 live Main min", "regret_per_10min", ".2f"),
                                 ("misses", "misses", ".0f"), ("misses per 10 min", "miss_per_10min", ".2f"), ("goals ahead >= 12 s", "goals_ahead_12s", ".0f"),
                                 ("median lead from last swap (s)", "median_lead_last_s", ".1f"), ("regrets on a stream with a goal", "regrets_goal_left", ".0f")):
            L.append(f"| {label} | " + " | ".join(_f(m[k].get(key), spec) for k in ("recorded", "recorded_with_marks", "today_sim", "candidate_sim")) + " |")
        L += ["", "The fair comparison is the last two columns (same approximation on both sides)."]
    L += ["", "## What the replay ignores (so this is an approximation)", ""] + [f"- {a}" for a in shadow["approximations"]]
    return "\n".join(L) + "\n"


def _f(x, spec=".3f"):
    return "-" if x is None else format(x, spec)
