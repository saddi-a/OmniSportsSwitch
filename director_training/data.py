"""Reading the logs (read-only) and the built rows. Same loading as scripts/build_dataset.py, with the list of files read for config.json."""
import datetime
import json
import pathlib
import re

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


def parse_since(text: str) -> float:
    """'YYYY-MM-DD HH:MM' (local time) -> epoch seconds. ValueError on anything else."""
    return datetime.datetime.strptime(text.strip(), "%Y-%m-%d %H:%M").timestamp()


_EXCLUDE = re.compile(r"^(\d+):(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def parse_exclude(text: str) -> tuple:
    """'TAB:HH:MM-HH:MM' -> (tab, start minute of the day, end minute of the day + 1: the last minute is included). ValueError otherwise."""
    m = _EXCLUDE.match(text.strip())
    if not m:
        raise ValueError(f"bad --exclude {text!r}: use TAB:HH:MM-HH:MM")
    tab, h0, m0, h1, m1 = (int(g) for g in m.groups())
    if h0 > 23 or h1 > 23 or m0 > 59 or m1 > 59:
        raise ValueError(f"bad --exclude {text!r}: not a time of day")
    return tab, h0 * 60 + m0, h1 * 60 + m1 + 1


def _kept(item: dict, since: float | None, excludes: list) -> bool:
    """False for an item (row or event) recorded before `since` or inside an exclusion of its tab (local time of day, the file's date)."""
    t = item.get("t")
    if t is None:
        return True
    if since is not None and t < since:
        return False
    if excludes and item.get("tab") is not None:
        local = datetime.datetime.fromtimestamp(t)
        if item.get("date") in (None, local.strftime("%Y-%m-%d")):    # the range belongs to the date of the dataset file
            minute = local.hour * 60 + local.minute
            return not any(item["tab"] == tab and a <= minute < b for tab, a, b in excludes)
    return True


def load_inputs(logs_dir, days: list[str] | None = None, since: float | None = None, excludes=()) -> dict:
    """Everything `db.build` needs: {days, rows, events, feed_rows, maps, labels, marks, event_marks, files}. rows is empty when nothing was recorded.
    `since` (epoch seconds) drops the rows and events recorded before it; `excludes` (parse_exclude tuples) drop those of a tab in a time range."""
    logs = pathlib.Path(logs_dir)
    days = list(days) if days else db.available_days(logs / "dataset")
    rows, events = db.read_days(logs / "dataset", days)
    if since is not None or excludes:
        excludes = list(excludes)
        rows = [r for r in rows if _kept(r, since, excludes)]
        events = [e for e in events if _kept(e, since, excludes)]
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
