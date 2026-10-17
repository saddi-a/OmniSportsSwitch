"""Command line of Director Training (scripts/director_training.py): build, fit, evaluate, ship, status.

Every command except `status` makes a numbered run folder under <logs>/director_training/ (see runs.py). All are deterministic (fixed seeds) and
write nothing outside that folder, except `ship --apply --target FILE`, which writes FILE (after a backup) and nothing else.
Exit codes: 0 done (also `insufficient-data`, which is a result), 1 refused (a ship gate failed with --apply, a bad reference), 2 bad arguments.
"""
import argparse
import datetime
import json
import pathlib
import shutil

from daemon import dataset_builder as db

from . import data, evaluate, features as F, fit, runs, ship

REPO = pathlib.Path(__file__).resolve().parent.parent
SPORTS = ("football", "american football")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="director_training", description="Offline learning of the per-sport weights (docs/director-training.md).")
    p.add_argument("--logs-dir", default=str(REPO / "logs"), help="the logs folder (read-only except <logs>/director_training)")
    p.add_argument("--settings", default=str(REPO / "settings.toml"), help="the settings file that defines 'today' (read-only)")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="build the labelled dataset into a run folder")
    b.add_argument("days", nargs="*", help="YYYY-MM-DD (default: every recorded day)")
    b.add_argument("--holdout", default="", help="comma separated game ids to mark as hold-out in the rows")

    f = sub.add_parser("fit", help="fit per-sport logistic regression (refuses below the data guard)")
    f.add_argument("--sport", default="all", choices=list(SPORTS) + ["all"])
    f.add_argument("--rows", help="rows.jsonl of a build run (default: the newest build run, else <logs>/dataset_built/rows.jsonl)")
    f.add_argument("--min-games", type=int, default=fit.DEFAULT_MIN_GAMES)
    f.add_argument("--min-events", type=int, default=fit.DEFAULT_MIN_EVENTS)
    f.add_argument("--dry-run", action="store_true", help="ignore the data guard ONLY to exercise the pipeline; the result is stamped 'DRY RUN - do not ship'")
    f.add_argument("--c", type=float, default=1.0, help="inverse L2 strength of the logistic regression")
    f.add_argument("--seed", type=int, default=0)
    f.add_argument("--holdout", default="", help="comma separated game ids kept OUT of the fit (for the shadow evaluation)")

    e = sub.add_parser("evaluate", help="shadow evaluation of a fit run's candidate on recorded rows")
    e.add_argument("--fit-run", required=True)
    e.add_argument("days", nargs="*")
    e.add_argument("--holdout", default="", help="comma separated game ids to evaluate on (default: games the fit did not see; none = in-sample)")
    e.add_argument("--exogenous", default=",".join(evaluate.DEFAULT_EXOGENOUS), help="sources of recorded swaps replayed as they happened")

    s = sub.add_parser("ship", help="patch for the per-sport overrides (dry unless --apply)")
    s.add_argument("--fit-run")
    s.add_argument("--shadow-run")
    s.add_argument("--sport", choices=SPORTS)
    s.add_argument("--target", help="the settings.toml to patch (required with --apply; without it the project's settings.toml is only read for the diff)")
    s.add_argument("--apply", action="store_true", help="write the target after a backup (only if the gate passes)")
    s.add_argument("--rollback", metavar="RUN", help="restore the backup made by that ship run")
    s.add_argument("--force", action="store_true", help="with --rollback: restore even if the target changed since")
    s.add_argument("--lead-tolerance-s", type=float, default=ship.DEFAULT_LEAD_TOLERANCE_S)
    s.add_argument("--min-judged", type=int, default=ship.DEFAULT_MIN_JUDGED)

    t = sub.add_parser("status", help="list the runs and the latest numbers per sport (read-only)")
    t.add_argument("--sport", choices=SPORTS)
    return p


def _csv(text: str) -> list[str]:
    return [x for x in (text or "").split(",") if x]


def main(argv=None, out=print, now_fn=None, repo=REPO) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    logs = pathlib.Path(args.logs_dir)
    runs_dir = logs / "director_training"
    now_fn = now_fn or datetime.datetime.now
    ctx = {"args": args, "logs": logs, "runs_dir": runs_dir, "now_fn": now_fn, "repo": repo, "out": out}
    try:
        return {"build": cmd_build, "fit": cmd_fit, "evaluate": cmd_evaluate, "ship": cmd_ship, "status": cmd_status}[args.command](ctx)
    except Exception as e:   # a crash still leaves a finished run (status error) in the experiment log, never a half-written one
        run = ctx.get("run")
        if run is not None and not run.finished:
            run.finish({"headline": {}, "error": repr(e)}, f"# {run.command}\n\nFAILED: {e!r}\n", status="error", summary=f"error: {e!r}")
        out(f"error: {e!r}")
        return 1


def _new_run(ctx, command, params, sport=None, inputs=()):
    ctx["run"] = runs.Run.create(ctx["runs_dir"], command, params, sport=sport, inputs=inputs, repo=ctx["repo"], now_fn=ctx["now_fn"])
    return ctx["run"]


def _params(args) -> dict:
    return {k: v for k, v in vars(args).items() if k not in ("command",)}


# ---------------------------------------------------------------------------------------------------------------------------------------
def cmd_build(ctx) -> int:
    args, out = ctx["args"], ctx["out"]
    inputs = data.load_inputs(ctx["logs"], args.days)
    run = _new_run(ctx, "build", _params(args), inputs=inputs["files"])
    if not inputs["rows"]:
        run.finish({"headline": {}}, "# Build\n\nNo recorded rows for " + (", ".join(inputs["days"]) or "any day") + ".\n", status="insufficient-data",
                          summary="no dataset rows")
        out(f"{run.name}: no dataset rows")
        return 0
    result = data.build_from_inputs(inputs, _csv(args.holdout))
    written = db.write_outputs(run.dir, result)
    run.artifacts += [p.name for p in written]
    summary = result["summary"]
    games = sorted({r.get("game_key") for r in result["rows"]})
    headline = {sp: {"rows": s["rows"], "labelled": s["aligned_rows"], "y_t12_60": s["y_t12_60"], "y_goal_30": s["y_goal_30"], "swaps": s["swaps"],
                     "regrets": s["regrets"], "regret_per_10min": s["regret_per_10min"], "misses": s["misses"]} for sp, s in summary.items()}
    report = "# Build report\n\nDays: " + ", ".join(inputs["days"]) + f"\n\n{len(result['rows'])} rows, {len(result['swaps'])} swaps, games: {', '.join(map(str, games))}\n\n```\n" \
             + db.format_summary(summary) + "\n```\n"
    metrics = {"headline": headline, "summary": summary, "games": games, "days": inputs["days"]}
    run.finish(metrics, report, summary=f"{len(result['rows'])} rows, {len(games)} games")
    out(f"{run.name}: {len(result['rows'])} rows, {len(games)} games -> {run.dir}")
    out(db.format_summary(summary))
    return 0


# ---------------------------------------------------------------------------------------------------------------------------------------
def cmd_fit(ctx) -> int:
    args, out = ctx["args"], ctx["out"]
    rows_path = pathlib.Path(args.rows) if args.rows else data.latest_rows_path(ctx["logs"], ctx["runs_dir"])
    rows = data.read_rows(rows_path) if rows_path.exists() else []
    missing = [c for c in ("event", "cue_part") if rows and c not in rows[0]]
    sports = list(SPORTS) if args.sport == "all" else [args.sport]
    for sport in sports:
        run = _new_run(ctx, "fit", _params(args), sport=sport, inputs=[rows_path])
        today = F.today_values(args.settings, sport)
        run.config["today"] = today
        run.write_json("config.json", run.config)
        if not rows:
            result = {"sport": sport, "status": "insufficient-data", "reason": f"no rows in {rows_path}", "dry_run": args.dry_run, "stamp": None,
                      "primary": {"sport": sport, "rows": 0, "positive_rows": 0, "games": 0, "positive_events": 0, "guard": {"min_games": args.min_games, "min_events": args.min_events}}}
        else:
            result = fit.fit_sport(rows, sport, today, args.min_games, args.min_events, args.dry_run, args.c, args.seed, _csv(args.holdout))
        p = result["primary"]
        text = fit.report({sport: result}) if rows else f"# Fit report\n\n## {sport}\n\nNo rows in {rows_path}. Run `build` first.\n"
        if missing:
            text += f"\nNote: {rows_path.name} has no column(s) {', '.join(missing)} (built by an older builder): read as 0. Run `build` again.\n"
        headline = {"rows": p.get("rows"), "games": p.get("games"), "positive_events": p.get("positive_events")}
        if result["status"] == "ok":
            cv = p["cv"]
            headline.update(candidate_auc=cv.get("candidate_auc"), today_auc=cv.get("today_auc"))
            weights = {"stamp": result["stamp"], "sport": sport, "dry_run": result["dry_run"], "status": "ok", "fit_games": p["game_ids"],
                       "settings": p["candidate"]["settings"], "weights_by_feature": p["candidate"]["weights_by_feature"], "alpha": p["candidate"]["alpha"]}
            run.write_json("weights.json", weights)
        else:
            run.write_json("weights.json", {"stamp": result["stamp"], "sport": sport, "dry_run": result["dry_run"], "status": result["status"], "reason": result["reason"]})
        line = run.finish({"headline": headline, "sports": {sport: result}, "rows_file": str(rows_path)}, text, status="ok" if result["status"] == "ok" else "insufficient-data",
                          summary=(result["stamp"] + ": " if result["stamp"] else "") + (result["reason"] or "fitted"), dry_run=bool(args.dry_run))
        out(f"{run.name} [{sport}]: {line['status']} - {line['summary']}")
        if result["status"] == "ok":
            out(f"    games {p['games']}, rows {p['rows']}, events {p['positive_events']}; pooled leave-one-game-out AUC candidate {fit._f(p['cv'].get('candidate_auc'))} "
                f"vs today's {fit._f(p['cv'].get('today_auc'))}")
    return 0


# ---------------------------------------------------------------------------------------------------------------------------------------
def cmd_evaluate(ctx) -> int:
    args, out = ctx["args"], ctx["out"]
    fit_dir = runs.find_run(ctx["runs_dir"], args.fit_run, "fit")
    if fit_dir is None:
        out(f"no fit run {args.fit_run!r} under {ctx['runs_dir']}")
        return 1
    fit_run = runs.load_run(fit_dir)
    sport = fit_run["config"].get("sport")
    result = (fit_run["metrics"].get("sports") or {}).get(sport) or {}
    inputs = data.load_inputs(ctx["logs"], args.days)
    run = _new_run(ctx, "evaluate", dict(_params(args), fit_run_number=fit_run["config"]["run"]), sport=sport, inputs=inputs["files"])
    dry = bool(fit_run["metrics"].get("dry_run"))
    base = {"sport": sport, "fit_run": fit_run["config"]["run"], "dry_run": dry, "stamp": fit.DRY_RUN_STAMP if dry else None}
    if result.get("status") != "ok":
        shadow = dict(base, status="insufficient-data", reason="the fit run has no fitted weights: " + str(result.get("reason")), hold_out=False, games_seen_by_fit=[],
                      games_evaluated=[], rows=0, approximations=list(evaluate.APPROXIMATIONS))
    elif not inputs["rows"]:
        shadow = dict(base, status="insufficient-data", reason="no recorded rows", hold_out=False, games_seen_by_fit=[], games_evaluated=[], rows=0,
                      approximations=list(evaluate.APPROXIMATIONS))
    else:
        built = data.build_from_inputs(inputs)
        fit_games = result["primary"]["game_ids"]
        all_games = sorted({r.get("game_key") for r in built["rows"] if evaluate.sport_of_row(r) == sport})
        held = _csv(args.holdout) or [g for g in all_games if g not in set(fit_games)]
        today_cfg = F.sport_config(args.settings, sport)
        candidate = result["primary"]["candidate"]["settings"]
        shadow = dict(base, **evaluate.evaluate(built, inputs["events"], sport, today_cfg, candidate, held, fit_games, _csv(args.exogenous), inputs["marks"]))
    run.write_json("shadow.json", shadow)
    cmp = shadow.get("comparison") or {}
    m = shadow.get("metrics") or {}
    headline = {"hold_out": shadow.get("hold_out"), "games": len(shadow.get("games_evaluated") or []),
                "regret_per_10min_today": (m.get("today_sim") or {}).get("regret_per_10min"), "regret_per_10min_candidate": (m.get("candidate_sim") or {}).get("regret_per_10min")}
    text = evaluate.report(shadow)
    run.finish({"headline": headline, "comparison": cmp, "fit_run": base["fit_run"], "sport": sport, "hold_out": shadow.get("hold_out"), "status_detail": shadow.get("reason")},
               text, status="ok" if shadow["status"] == "ok" else "insufficient-data", summary=("IN-SAMPLE " if not shadow.get("hold_out") else "") + (shadow.get("reason") or "evaluated"),
               dry_run=dry)
    out(f"{run.name} [{sport}]: {shadow['status']}" + (f" - {shadow.get('reason')}" if shadow.get("reason") else ""))
    if shadow["status"] == "ok":
        for k in ("recorded", "recorded_with_marks", "today_sim", "candidate_sim"):
            h = m[k]
            out(f"    {k:20s} swaps {h['swaps']}, regrets {h['regrets']}, regret/10min {fit._f(h['regret_per_10min'], '.2f')}, misses {h['misses']}, median lead {fit._f(h['median_lead_last_s'], '.1f')}")
        out("    hold-out: " + ("yes" if shadow["hold_out"] else "NO (in-sample)"))
    return 0


# ---------------------------------------------------------------------------------------------------------------------------------------
def cmd_ship(ctx) -> int:
    args, out = ctx["args"], ctx["out"]
    if args.rollback:
        return _rollback(ctx)
    if not (args.fit_run and args.shadow_run):
        out("ship needs --fit-run and --shadow-run (or --rollback RUN)")
        return 2
    if args.apply and not args.target:
        out("--apply needs an explicit --target FILE (there is no default file to write)")
        return 2
    fit_dir = runs.find_run(ctx["runs_dir"], args.fit_run, "fit")
    shadow_dir = runs.find_run(ctx["runs_dir"], args.shadow_run, "evaluate")
    if fit_dir is None or shadow_dir is None:
        out(f"cannot find fit run {args.fit_run!r} or evaluate run {args.shadow_run!r} under {ctx['runs_dir']}")
        return 1
    fit_run, shadow_run = runs.load_run(fit_dir), runs.load_run(shadow_dir)
    sport = args.sport or fit_run["config"].get("sport")
    shadow = json.loads((shadow_dir / "shadow.json").read_text(encoding="utf-8")) if (shadow_dir / "shadow.json").exists() else {}
    ok, why = ship.gate(fit_run["metrics"], shadow, fit_run["config"]["run"], shadow_run["config"]["params"].get("fit_run_number"), sport, args.lead_tolerance_s, args.min_judged)
    target = pathlib.Path(args.target) if args.target else pathlib.Path(args.settings)
    run = _new_run(ctx, "ship", dict(_params(args), fit_run_number=fit_run["config"]["run"], shadow_run_number=shadow_run["config"]["run"]), sport=sport,
                   inputs=[target, fit_dir / "weights.json", shadow_dir / "shadow.json"])
    result = (fit_run["metrics"].get("sports") or {}).get(sport) or {}
    text = target.read_bytes().decode("utf-8") if target.exists() else ""
    patch = {"weights": {}, "scoring": {}}
    diff, problems = "", []
    if result.get("status") == "ok":
        patch = ship.candidate_patch(result["primary"], F.sport_config(args.settings, sport))
        new = ship.apply_text(text, sport, patch)
        diff = ship.unified_diff(text, new, target.name)
        problems = ship.validate_text(new, sport, patch, run.dir / "validate")
        shutil.rmtree(run.dir / "validate", ignore_errors=True)
    stamp_txt = result.get("stamp") or ""
    run.write_text("patch.toml", ship.patch_toml(patch, sport, f"Director Training candidate for {sport} (fit run {fit_run['config']['run']}, shadow run {shadow_run['config']['run']}). "
                                                 f"Paste into settings.toml; needs a daemon restart. {stamp_txt}".strip()))
    run.write_text("patch.diff", diff)
    applied = None
    status, exit_code = "ok", 0
    if args.apply:
        if not ok or problems:
            status, exit_code = "refused", 1
        elif not target.exists():
            status, exit_code, why = "refused", 1, why + [f"target {target} does not exist"]
        else:
            stamp = ctx["now_fn"]().strftime("%Y%m%d-%H%M%S")
            applied = ship.apply_to_file(target, ship.apply_text(text, sport, patch), run.dir / "backup", stamp)
            run.artifacts.append("backup/" + pathlib.Path(applied["backup"]).name)
    lines = [f"# Ship report ({sport})", "", f"Mode: {'APPLY' if args.apply else 'DRY (nothing written except this run folder)'}. Target: {target}", "",
             "Where overrides can live: only in settings.toml tables `[weights.<sport>]` / `[scoring.<sport>]`; settings.local.json (the popup's file) has no per-sport ids.", "",
             f"Gate: **{'PASS' if ok else 'FAIL'}**"] + [f"- {w}" for w in why] + ([f"- patch problems: {p}" for p in problems])
    if stamp_txt:
        lines += ["", f"**{stamp_txt}**"]
    if applied:
        lines += ["", f"Applied. Backup: {applied['backup']}. Undo: `ship --rollback {run.number}`."]
    elif args.apply:
        lines += ["", "NOT applied."]
    lines += ["", "Patch:", "", "```toml", run.dir.joinpath("patch.toml").read_text(encoding="utf-8").rstrip(), "```", "", "Diff:", "", "```diff", diff.rstrip(), "```"]
    metrics = {"headline": {"gate": "pass" if ok else "fail", "applied": bool(applied)}, "gate_ok": ok, "gate_reasons": why, "patch": patch, "problems": problems, "applied": applied,
               "fit_run": fit_run["config"]["run"], "shadow_run": shadow_run["config"]["run"], "sport": sport, "target": str(target)}
    run.finish(metrics, "\n".join(lines), status=status, summary=("applied" if applied else ("refused: " + "; ".join(why + problems) if status == "refused" else "dry run, not applied")),
               dry_run=bool(stamp_txt))
    out(f"{run.name} [{sport}]: gate {'PASS' if ok else 'FAIL'}" + ("" if ok else ": " + "; ".join(why)))
    if diff:
        out(diff.rstrip())
    out("applied; backup " + applied["backup"] if applied else ("NOT applied" + (" (refused)" if status == "refused" else " (dry)")))
    return exit_code


def _rollback(ctx) -> int:
    args, out = ctx["args"], ctx["out"]
    src = runs.find_run(ctx["runs_dir"], args.rollback, "ship")
    if src is None:
        out(f"no ship run {args.rollback!r}")
        return 1
    prev = runs.load_run(src)
    info = prev["metrics"].get("applied")
    run = _new_run(ctx, "ship", {"rollback_of": prev["config"]["run"], "force": args.force}, sport=prev["config"].get("sport"))
    if not info:
        ok, msg = False, f"run {prev['config']['run']} did not apply anything"
    else:
        ok, msg = ship.rollback_file(info, args.force)
    run.finish({"headline": {"rollback": ok}, "rollback_of": prev["config"]["run"], "message": msg}, f"# Rollback of run {prev['config']['run']}\n\n{msg}\n",
               status="ok" if ok else "refused", summary=("rolled back run %d: " % prev["config"]["run"]) + msg)
    out(msg)
    return 0 if ok else 1


# ---------------------------------------------------------------------------------------------------------------------------------------
def cmd_status(ctx) -> int:
    args, out = ctx["args"], ctx["out"]
    lines, latest = runs.status_table(ctx["runs_dir"])
    if args.sport:
        lines = [ln for ln in lines if ln.get("sport") in (args.sport, None)]
    if not lines:
        out(f"no runs yet under {ctx['runs_dir']}")
        return 0
    out("Runs:")
    for ln in lines:
        out(f"  {ln.get('name', ln.get('run')):24s} {str(ln.get('sport') or '-'):18s} {ln.get('status'):18s} {'[DRY RUN] ' if ln.get('dry_run') else ''}{ln.get('summary', '')}")
    out("Latest per command and sport:")
    for (cmd, sport), ln in sorted(latest.items(), key=lambda kv: (str(kv[0][1]), str(kv[0][0]))):
        if args.sport and sport not in (args.sport, None):
            continue
        out(f"  {cmd:9s} {str(sport or '-'):18s} {ln.get('name')}: {ln.get('headline')}")
    return 0
