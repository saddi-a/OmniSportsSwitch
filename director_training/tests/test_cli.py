import datetime
import json

from daemon.tests import test_dataset_builder as tdb
from director_training import cli, runs

from .synth import built_rows, signal_rows, write_jsonl


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()


def settings_file(tmp_path):
    p = tmp_path / "s" / "settings.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[scoring]\nmin_score = 0.35\n", encoding="utf-8")
    return p


def call(tmp_path, *argv, settings=None):
    lines = []
    code = cli.main(["--logs-dir", str(tmp_path / "logs"), "--settings", str(settings or settings_file(tmp_path)), *argv], out=lines.append)
    return code, "\n".join(lines)


def test_status_on_an_empty_folder(tmp_path):
    code, out = call(tmp_path, "status")
    assert code == 0 and "no runs yet" in out and not (tmp_path / "logs").exists()      # status writes nothing


def test_fit_below_the_guard_writes_an_insufficient_data_run_and_exits_zero(tmp_path):
    rows = tmp_path / "rows.jsonl"
    write_jsonl(rows, built_rows(3, events_per_game=2))
    code, out = call(tmp_path, "fit", "--rows", str(rows))
    d = tmp_path / "logs" / "director_training"
    assert code == 0 and "insufficient-data" in out
    assert (d / "run-0001-fit").is_dir() and (d / "run-0002-fit").is_dir()                     # football, american football
    m = json.loads((d / "run-0001-fit" / "metrics.json").read_text(encoding="utf-8"))
    assert m["status"] == "insufficient-data" and m["sports"]["football"]["primary"]["games"] == 3
    w = json.loads((d / "run-0001-fit" / "weights.json").read_text(encoding="utf-8"))
    assert w["status"] == "insufficient-data" and "need 8" in w["reason"]
    assert "Nothing was fitted" in (d / "run-0001-fit" / "report.md").read_text(encoding="utf-8")


def test_fit_dry_run_marks_the_artifact_and_status_shows_it(tmp_path):
    rows = tmp_path / "rows.jsonl"
    write_jsonl(rows, built_rows(4, events_per_game=2))
    code, out = call(tmp_path, "fit", "--sport", "football", "--rows", str(rows), "--dry-run")
    d = tmp_path / "logs" / "director_training"
    w = json.loads((d / "run-0001-fit" / "weights.json").read_text(encoding="utf-8"))
    assert code == 0 and w["stamp"] == "DRY RUN - do not ship" and w["status"] == "ok" and w["settings"]["weights"]["crowd"] > 0
    assert json.loads((d / "run-0001-fit" / "config.json").read_text(encoding="utf-8"))["inputs"][0]["size"] > 0
    line = runs.read_log(d)[0]
    assert line["dry_run"] is True and line["headline"]["games"] == 4
    code, out = call(tmp_path, "status")
    assert "[DRY RUN]" in out and "football" in out and "candidate_auc" in out


def test_fit_is_deterministic(tmp_path):
    rows = tmp_path / "rows.jsonl"
    write_jsonl(rows, built_rows(4, events_per_game=2))
    for n in range(2):
        call(tmp_path / str(n), "fit", "--sport", "football", "--rows", str(rows), "--dry-run")
    a, b = (json.loads((tmp_path / str(n) / "logs" / "director_training" / "run-0001-fit" / "weights.json").read_text(encoding="utf-8")) for n in range(2))
    assert a == b


def test_a_missing_rows_file_is_a_result_not_a_crash(tmp_path):
    code, out = call(tmp_path, "fit", "--rows", str(tmp_path / "nothing.jsonl"))
    assert code == 0 and "insufficient-data" in out


def test_a_crash_inside_a_command_still_closes_the_run(tmp_path, monkeypatch):
    rows = tmp_path / "rows.jsonl"
    write_jsonl(rows, built_rows(3))
    monkeypatch.setattr(cli.fit, "fit_sport", lambda *a, **k: 1 / 0)
    code, out = call(tmp_path, "fit", "--sport", "football", "--rows", str(rows))
    log = runs.read_log(tmp_path / "logs" / "director_training")
    assert code == 1 and log[-1]["status"] == "error" and "ZeroDivisionError" in log[-1]["summary"]


def test_build_fit_evaluate_end_to_end_on_a_synthetic_day(tmp_path):
    """build -> fit (dry) -> evaluate on a tiny day written the way the recorder writes it; nothing touches the real logs."""
    logs = tmp_path / "logs"
    rows, events = signal_rows(lambda tab, off: 0.9 if (tab == 2 and off >= 100) else 0.1)
    for r in rows:
        r.pop("date", None)
    write_jsonl(logs / "dataset" / "2026-10-13.jsonl", rows)
    write_jsonl(logs / "dataset" / "2026-10-13.events.jsonl", events)
    feed = [dict(f, t=iso(f["t"])) for f in tdb.feed(goals=[("gA", 110, "goal")])]
    write_jsonl(logs / "feed" / "2026-10-13.jsonl", feed)
    d = logs / "director_training"
    code, out = call(tmp_path, "build")
    assert code == 0 and (d / "run-0001-build" / "rows.jsonl").exists() and (d / "run-0001-build" / "swaps.jsonl").exists() and "football" in out
    code, out = call(tmp_path, "fit", "--sport", "football", "--dry-run")           # picks the newest build run's rows.jsonl
    assert code == 0 and (d / "run-0002-fit" / "weights.json").exists()
    cfg = json.loads((d / "run-0002-fit" / "config.json").read_text(encoding="utf-8"))
    assert cfg["inputs"][0]["path"].endswith("run-0001-build\\rows.jsonl") or cfg["inputs"][0]["path"].endswith("run-0001-build/rows.jsonl")
    fit_status = json.loads((d / "run-0002-fit" / "metrics.json").read_text(encoding="utf-8"))["sports"]["football"]["status"]
    code, out = call(tmp_path, "evaluate", "--fit-run", "2")
    shadow = json.loads((d / "run-0003-evaluate" / "shadow.json").read_text(encoding="utf-8"))
    assert code == 0 and shadow["sport"] == "football" and shadow["fit_run"] == 2
    if fit_status == "ok":
        assert shadow["hold_out"] is False and "IN-SAMPLE" in (d / "run-0003-evaluate" / "report.md").read_text(encoding="utf-8")
    assert shadow["dry_run"] is True and shadow["stamp"] == "DRY RUN - do not ship"
    assert [x["command"] for x in runs.read_log(d)] == ["build", "fit", "evaluate"]


def test_evaluate_without_a_fit_run_says_so(tmp_path):
    code, out = call(tmp_path, "evaluate", "--fit-run", "5")
    assert code == 1 and "no fit run" in out
