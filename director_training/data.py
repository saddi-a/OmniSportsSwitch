"""Reading the logs (read-only) and the built rows. Same loading as scripts/build_dataset.py, with the list of files read for config.json."""
import json
import pathlib

from daemon import dataset_builder as db


def input_files(logs_dir, days: list[str]) -> list[pathlib.Path]:
    logs = pathlib.Path(logs_dir)
    files = []
    for day in days:
        for name in (f"dataset/{day}.jsonl", f"dataset/{day}.events.jsonl", f"review/{day}.jsonl"):
            if (logs / name).exists():
                files.append(logs / name)
    files += sorted((logs / "feed").glob("*.jsonl")) if (logs / "feed").is_dir() else []
    if (logs / "events.log").exists():
        files.append(logs / "events.log")
    return files


def load_inputs(logs_dir, days: list[str] | None = None) -> dict:
    """Everything `db.build` needs: {days, rows, events, feed_rows, maps, labels, marks, event_marks, files}. rows is empty when nothing was recorded."""
    logs = pathlib.Path(logs_dir)
    days = list(days) if days else db.available_days(logs / "dataset")
    rows, events = db.read_days(logs / "dataset", days)
    out = {"days": days, "rows": rows, "events": events, "feed_rows": [], "maps": [], "labels": {}, "marks": {}, "event_marks": [],
           "files": input_files(logs, days)}
    if not rows:
        return out
    t0, t1 = db.row_span(rows)
    out["feed_rows"] = db.read_feed(logs / "feed", t0, t1)
    out["maps"], out["labels"] = db.load_maps(logs / "events.log", out["feed_rows"])
    out["marks"] = db.load_marks(logs, days)
    out["event_marks"] = db.load_event_marks(logs, days)
    return out


def build_from_inputs(inputs: dict, holdout=()) -> dict:
    return db.build(inputs["rows"], inputs["events"], inputs["feed_rows"], holdout=list(holdout), maps=inputs["maps"], label_to_game=inputs["labels"],
                    marks=inputs["marks"], event_marks=inputs["event_marks"])


def read_rows(path) -> list[dict]:
    """The rows of a `rows.jsonl` written by the dataset builder (a broken line is skipped)."""
    return db.load_jsonl(path)


def latest_rows_path(logs_dir, runs_dir) -> pathlib.Path:
    """The rows file to fit on by default: the newest finished `build` run's rows.jsonl, else logs/dataset_built/rows.jsonl."""
    from . import runs
    for _, cmd, path in reversed(runs.list_run_dirs(runs_dir)):
        if cmd == "build" and (path / "rows.jsonl").exists() and (path / "metrics.json").exists():
            return path / "rows.jsonl"
    return pathlib.Path(logs_dir) / "dataset_built" / "rows.jsonl"


def write_json(path, obj) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
