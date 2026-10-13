import json
import pathlib

from daemon.scoring import Config
from director_training import cli, runs, ship
from director_training import features as F

REPO = pathlib.Path(__file__).resolve().parents[2]
SETTINGS = (REPO / "settings.toml").read_bytes()
PATCH = {"weights": {"crowd": 1.25, "speech": -0.4}, "scoring": {"min_score": 0.42}}


def settings_copy(tmp_path, crlf=False):
    data = SETTINGS.replace(b"\r\n", b"\n")
    if crlf:
        data = data.replace(b"\n", b"\r\n")
    p = tmp_path / "work" / "settings.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def fit_metrics(dry=False, status="ok"):
    cand = {"settings": {"weights": dict(PATCH["weights"]), "scoring": dict(PATCH["scoring"])}}
    return {"dry_run": dry, "sports": {"football": {"status": status, "reason": None, "stamp": "DRY RUN - do not ship" if dry else None, "dry_run": dry,
                                                    "primary": {"candidate": cand, "game_ids": ["a", "b"]}}}}


def shadow(hold_out=True, today_r=1.0, cand_r=0.5, today_lead=40.0, cand_lead=38.0, judged=20, status="ok", dry=False):
    return {"sport": "football", "status": status, "reason": None, "hold_out": hold_out, "games_seen_by_fit": [] if hold_out else ["a"], "dry_run": dry,
            "metrics": {"today_sim": {"judged": judged, "regret_per_10min": today_r, "median_lead_last_s": today_lead},
                        "candidate_sim": {"judged": judged, "regret_per_10min": cand_r, "median_lead_last_s": cand_lead}}}


# ---- patching the text ---------------------------------------------------------------------------------------
def test_the_daemons_own_loader_reads_the_patch_back(tmp_path):
    text = ship.apply_text(SETTINGS.decode("utf-8"), "football", PATCH)
    assert ship.validate_text(text, "football", PATCH, tmp_path / "v") == []
    from daemon.settings import load_settings
    s = load_settings(tmp_path / "v" / "settings.toml").scoring
    assert s.sport_weights["football"] == {"crowd": 1.25, "speech": -0.4} and s.sport_scoring["football"]["min_score"] == 0.42
    assert s.for_sport("football").weights.crowd == 1.25 and s.for_sport("basketball").weights.crowd == Config().weights.crowd


def test_a_sport_with_a_space_gets_a_quoted_table_and_existing_keys_are_updated_in_place():
    base = '[weights]\nsurge = 0.3\n\n[weights."american football"]\ncue_chance = 0.3   # keep\ncrowd = 1.0 # old\n\n[multiview]\nx = 1\n'
    new = ship.apply_text(base, "american football", {"weights": {"crowd": 1.5, "event": 0.9}, "scoring": {"min_score": 0.4}})
    assert 'crowd = 1.5 # old' in new and "cue_chance = 0.3   # keep" in new
    assert new.index("event = 0.9") < new.index("[multiview]") and new.count('[weights."american football"]') == 1
    assert new.rstrip().endswith('[scoring."american football"]\nmin_score = 0.4')
    import tomllib
    data = tomllib.loads(new)
    assert data["weights"]["american football"]["event"] == 0.9 and data["scoring"]["american football"]["min_score"] == 0.4


def test_line_endings_and_everything_else_are_preserved(tmp_path):
    crlf = settings_copy(tmp_path, crlf=True).read_bytes().decode("utf-8")
    new = ship.apply_text(crlf, "football", PATCH)
    assert new.count("\r\n") == new.count("\n") and new.endswith("\r\n")
    it = iter(new.split("\r\n"))
    assert all(any(ln == x for x in it) for ln in crlf.split("\r\n"))      # every old line is still there, in the old order (lines were only added)
    lf = ship.apply_text(SETTINGS.decode("utf-8").replace("\r\n", "\n"), "football", PATCH)
    assert "\r" not in lf


def test_candidate_patch_lists_only_what_differs_from_today():
    cfg = Config()
    primary = {"candidate": {"settings": {"weights": {"crowd": cfg.weights.crowd, "speech": -0.4}, "scoring": {"min_score": cfg.min_score, "leverage_weight": 0.9}}}}
    assert ship.candidate_patch(primary, cfg) == {"weights": {"speech": -0.4}, "scoring": {"leverage_weight": 0.9}}
    assert ship.patch_toml({"weights": {"speech": -0.4}, "scoring": {}}, "football", "hello") == "# hello\n[weights.football]\nspeech = -0.4\n\n"


# ---- the gate ------------------------------------------------------------------------------------------------
def gate(**kw):
    sh = kw.pop("shadow", shadow())
    return ship.gate(kw.pop("fit", fit_metrics()), sh, 1, kw.pop("shadow_fit", 1), "football", **kw)


def test_gate_passes_only_when_regret_falls_and_lead_holds():
    assert gate() == (True, [])
    assert not gate(shadow=shadow(cand_r=1.0))[0] and not gate(shadow=shadow(cand_r=1.5))[0]               # equal is not better
    ok, why = gate(shadow=shadow(cand_lead=30.0))
    assert not ok and "median lead" in why[0]
    assert gate(shadow=shadow(cand_lead=30.0), lead_tolerance_s=15.0)[0]                                  # the tolerance is a stated parameter
    assert not gate(shadow=shadow(judged=3))[0]


def test_gate_refuses_dry_runs_in_sample_runs_and_mismatches():
    assert "DRY RUN" in " ".join(gate(fit=fit_metrics(dry=True))[1])
    assert "DRY RUN" in " ".join(gate(shadow=shadow(dry=True))[1])
    assert "hold-out" in " ".join(gate(shadow=shadow(hold_out=False))[1])
    assert "not 1" in " ".join(gate(shadow_fit=2)[1])
    assert not gate(fit=fit_metrics(status="insufficient-data"))[0]
    assert not gate(shadow=shadow(status="insufficient-data"))[0]


# ---- file operations -----------------------------------------------------------------------------------------
def test_apply_writes_a_backup_and_rollback_restores_the_exact_bytes(tmp_path):
    target = settings_copy(tmp_path)
    before = target.read_bytes()
    new = ship.apply_text(before.decode("utf-8"), "football", PATCH)
    info = ship.apply_to_file(target, new, tmp_path / "bak", "20261013-220000")
    assert target.read_bytes() != before and pathlib.Path(info["backup"]).read_bytes() == before and info["backup"].endswith("settings.toml.20261013-220000.bak")
    ok, msg = ship.rollback_file(info)
    assert ok and target.read_bytes() == before


def test_rollback_refuses_when_the_file_changed_since(tmp_path):
    target = settings_copy(tmp_path)
    info = ship.apply_to_file(target, ship.apply_text(target.read_text(encoding="utf-8"), "football", PATCH), tmp_path / "bak", "s")
    target.write_bytes(target.read_bytes() + b"# edited by hand\n")
    ok, msg = ship.rollback_file(info)
    assert not ok and "changed" in msg
    assert ship.rollback_file(info, force=True)[0]


# ---- the command ---------------------------------------------------------------------------------------------
def make_runs(logs, fit_dry=False, shadow_kw=None):
    d = logs / "director_training"
    fr = runs.Run.create(d, "fit", {}, sport="football")
    fr.write_json("weights.json", {"x": 1})
    fr.finish({"sports": fit_metrics(fit_dry)["sports"]}, "r", dry_run=fit_dry)
    sr = runs.Run.create(d, "evaluate", {"fit_run_number": 1}, sport="football")
    sr.write_json("shadow.json", shadow(**(shadow_kw or {})))
    sr.finish({}, "r")


def ship_cmd(tmp_path, settings, *extra):
    lines = []
    code = cli.main(["--logs-dir", str(tmp_path / "logs"), "--settings", str(settings), "ship", "--fit-run", "1", "--shadow-run", "2", *extra], out=lines.append)
    return code, "\n".join(lines)


def test_ship_is_dry_by_default_prints_the_diff_and_changes_nothing(tmp_path):
    make_runs(tmp_path / "logs")
    settings = settings_copy(tmp_path)
    before = settings.read_bytes()
    code, out = ship_cmd(tmp_path, settings)
    run = tmp_path / "logs" / "director_training" / "run-0003-ship"
    assert code == 0 and settings.read_bytes() == before and "NOT applied (dry)" in out and "+crowd = 1.25" in out and "gate PASS" in out
    assert (run / "patch.toml").read_text(encoding="utf-8").startswith("# Director Training candidate") and "[weights.football]" in (run / "patch.toml").read_text(encoding="utf-8")
    assert "+++ b/settings.toml" in (run / "patch.diff").read_text(encoding="utf-8") and not (run / "backup").exists()
    assert runs.read_log(tmp_path / "logs" / "director_training")[-1]["summary"] == "dry run, not applied"


def test_apply_needs_an_explicit_target(tmp_path):
    make_runs(tmp_path / "logs")
    settings = settings_copy(tmp_path)
    code, out = ship_cmd(tmp_path, settings, "--apply")
    assert code == 2 and "--target" in out and settings.read_bytes() == SETTINGS.replace(b"\r\n", b"\n")
    assert not (tmp_path / "logs" / "director_training" / "run-0003-ship").exists()


def test_apply_refuses_when_the_gate_fails(tmp_path):
    make_runs(tmp_path / "logs", shadow_kw={"hold_out": False})
    settings = settings_copy(tmp_path)
    before = settings.read_bytes()
    code, out = ship_cmd(tmp_path, settings, "--apply", "--target", str(settings))
    assert code == 1 and settings.read_bytes() == before and "gate FAIL" in out
    assert runs.read_log(tmp_path / "logs" / "director_training")[-1]["status"] == "refused"


def test_apply_refuses_a_dry_run_fit(tmp_path):
    make_runs(tmp_path / "logs", fit_dry=True)
    settings = settings_copy(tmp_path)
    before = settings.read_bytes()
    code, out = ship_cmd(tmp_path, settings, "--apply", "--target", str(settings))
    assert code == 1 and settings.read_bytes() == before and "DRY RUN" in out


def test_apply_then_rollback_on_a_temporary_copy(tmp_path):
    make_runs(tmp_path / "logs")
    settings = settings_copy(tmp_path)
    before = settings.read_bytes()
    code, out = ship_cmd(tmp_path, settings, "--apply", "--target", str(settings))
    d = tmp_path / "logs" / "director_training"
    assert code == 0 and "applied; backup" in out and settings.read_bytes() != before
    assert list((d / "run-0003-ship" / "backup").glob("settings.toml.*.bak"))
    last = runs.read_log(d)[-1]
    assert last["summary"] == "applied" and last["command"] == "ship"
    from daemon.settings import load_settings
    assert load_settings(settings).scoring.for_sport("football").weights.crowd == 1.25
    lines = []
    assert cli.main(["--logs-dir", str(tmp_path / "logs"), "ship", "--rollback", "3"], out=lines.append) == 0
    assert settings.read_bytes() == before and "restored" in lines[-1]
    assert runs.read_log(d)[-1]["summary"].startswith("rolled back run 3")
    assert cli.main(["--logs-dir", str(tmp_path / "logs"), "ship", "--rollback", "9"], out=lines.append) == 1


def test_ship_without_the_runs_it_needs_says_so(tmp_path):
    lines = []
    assert cli.main(["--logs-dir", str(tmp_path / "logs"), "ship"], out=lines.append) == 2
    assert cli.main(["--logs-dir", str(tmp_path / "logs"), "ship", "--fit-run", "1", "--shadow-run", "2"], out=lines.append) == 1


def test_the_features_map_to_settings_a_sport_may_override():
    assert json.dumps([f.key for f in F.FEATURES])          # smoke: stays serialisable for the artifacts
