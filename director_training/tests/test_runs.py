import json
import pathlib
import re
import subprocess

from director_training import runs

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_run_folders_are_numbered_and_log_one_line_each(tmp_path):
    d = tmp_path / "director_training"
    data = tmp_path / "in.txt"
    data.write_text("hello", encoding="utf-8")
    r1 = runs.Run.create(d, "fit", {"seed": 0}, sport="football", inputs=[data], repo=REPO)
    r1.write_json("weights.json", {"a": 1})
    line1 = r1.finish({"headline": {"auc": 0.5}}, "# report", summary="ok")
    r2 = runs.Run.create(d, "evaluate", {}, sport="football")
    r2.finish({"headline": {}}, "# r2", status="insufficient-data")
    assert [p.name for _, _, p in runs.list_run_dirs(d)] == ["run-0001-fit", "run-0002-evaluate"]
    for name in ("config.json", "metrics.json", "report.md", "weights.json"):
        assert (d / "run-0001-fit" / name).exists()
    cfg = json.loads((d / "run-0001-fit" / "config.json").read_text(encoding="utf-8"))
    assert cfg["params"] == {"seed": 0} and cfg["sport"] == "football" and cfg["command"] == "fit"
    assert cfg["inputs"][0]["size"] == 5 and len(cfg["inputs"][0]["sha256"]) == 64
    assert re.fullmatch(r"[0-9a-f]{40}", cfg["git"]["commit"])
    lines = runs.read_log(d)
    assert [x["run"] for x in lines] == [1, 2] and lines[0]["headline"] == {"auc": 0.5} and lines[0]["artifacts"] == ["weights.json"]
    assert line1["status"] == "ok" and lines[1]["status"] == "insufficient-data"
    assert len((d / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_numbering_continues_after_the_highest_folder(tmp_path):
    d = tmp_path / "x"
    (d / "run-0007-fit").mkdir(parents=True)
    assert runs.Run.create(d, "build", {}).number == 8


def test_find_run_by_number_name_and_command(tmp_path):
    d = tmp_path
    runs.Run.create(d, "fit", {}).finish({}, "r")
    runs.Run.create(d, "evaluate", {}).finish({}, "r")
    assert runs.find_run(d, 1).name == "run-0001-fit" and runs.find_run(d, "2").name == "run-0002-evaluate"
    assert runs.find_run(d, 2, "fit") is None and runs.find_run(d, "run-0001-fit").name == "run-0001-fit" and runs.find_run(d, 9) is None


def test_artifact_names_cannot_leave_the_run_folder(tmp_path):
    run = runs.Run.create(tmp_path, "fit", {})
    for bad in ("../x.json", "a/b.json", ".hidden"):
        try:
            run.write_text(bad, "x")
        except ValueError:
            continue
        raise AssertionError(bad)


def test_a_run_finishes_only_once(tmp_path):
    run = runs.Run.create(tmp_path, "fit", {})
    run.finish({}, "r")
    try:
        run.finish({}, "r")
    except RuntimeError:
        return
    raise AssertionError("second finish accepted")


def test_status_table_keeps_the_latest_per_command_and_sport(tmp_path):
    for n in (1, 2):
        r = runs.Run.create(tmp_path, "fit", {}, sport="football")
        r.finish({"headline": {"n": n}}, "r")
    runs.Run.create(tmp_path, "fit", {}, sport="football").finish({"headline": {"n": 3}}, "r", status="error")
    lines, latest = runs.status_table(tmp_path)
    assert len(lines) == 3 and latest[("fit", "football")]["headline"] == {"n": 2}


def test_the_daemon_never_imports_director_training():
    """The live daemon must not depend on the offline training package (user decision 2026-10-13)."""
    pat = re.compile(r"^\s*(?:from|import)\s+director_training\b|__import__\(\s*['\"]director_training", re.M)
    offenders = [p.name for p in sorted((REPO / "daemon").glob("*.py")) if pat.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert offenders == []
    names = [p.name for p in (REPO / "daemon").glob("*.py")]
    assert "server.py" in names                      # the glob really looked at the daemon


def test_the_script_name_does_not_shadow_the_package():
    """scripts/director_training.py puts the repo root first on sys.path, so `import director_training` is the package."""
    out = subprocess.run([__import__("sys").executable, str(REPO / "scripts" / "director_training.py"), "--logs-dir", "nowhere", "status"],
                         capture_output=True, text=True, cwd=str(REPO), timeout=120)
    assert out.returncode == 0, out.stderr
    assert "no runs yet" in out.stdout
