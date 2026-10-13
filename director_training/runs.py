"""Run folders and the experiment log.

Every CLI command makes `<runs_dir>/run-0001-<command>/` holding:
  config.json   everything that defines the run: command, sport, parameters, git commit, the input files (size + sha256)
  metrics.json  the numbers
  report.md     the same in plain language
  <artifact>    weights.json / shadow.json / patch.toml ... (written by the command with `Run.write_json` / `Run.write_text`)
and appends ONE line to `<runs_dir>/runs.jsonl` (the experiment log). Nothing outside `runs_dir` is ever written.
"""
import datetime
import hashlib
import json
import pathlib
import re
import subprocess

RUN_RE = re.compile(r"^run-(\d{4,})-([a-z]+)$")
LOG_NAME = "runs.jsonl"


def file_info(path) -> dict:
    """{path, size, sha256} of an input file; a missing file is listed with size None (never raises)."""
    p = pathlib.Path(path)
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return {"path": str(p), "size": p.stat().st_size, "sha256": h.hexdigest()}
    except OSError:
        return {"path": str(p), "size": None, "sha256": None}


def git_state(repo) -> dict:
    """{commit, dirty} of the repository at `repo`; 'unknown' if git is not available."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    try:
        return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": None}


def read_log(runs_dir) -> list[dict]:
    path = pathlib.Path(runs_dir) / LOG_NAME
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def list_run_dirs(runs_dir) -> list[tuple[int, str, pathlib.Path]]:
    base = pathlib.Path(runs_dir)
    found = []
    if base.is_dir():
        for p in base.iterdir():
            m = RUN_RE.match(p.name)
            if m and p.is_dir():
                found.append((int(m.group(1)), m.group(2), p))
    return sorted(found)


def find_run(runs_dir, ref, command: str | None = None) -> pathlib.Path | None:
    """The run folder for `ref`: a number (7 or '7' or 'run-0007'), a folder name or a path. `command` restricts a number to that command."""
    if ref is None:
        return None
    p = pathlib.Path(str(ref))
    if p.is_dir() and (p / "config.json").exists():
        return p
    text = str(ref)
    m = re.match(r"^(?:run-)?(\d+)(?:-[a-z]+)?$", text)
    if not m:
        return None
    n = int(m.group(1))
    for num, cmd, path in list_run_dirs(runs_dir):
        if num == n and (command is None or cmd == command):
            return path
    return None


def load_run(path) -> dict:
    """{config, metrics} of a finished run folder."""
    p = pathlib.Path(path)
    out = {"dir": p}
    for name in ("config", "metrics"):
        f = p / f"{name}.json"
        out[name] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return out


class Run:
    """One run folder. Make it with `Run.create`, write artifacts, then call `finish` once."""

    def __init__(self, path: pathlib.Path, number: int, command: str, config: dict, runs_dir: pathlib.Path, now_fn):
        self.dir, self.number, self.command, self.config, self.runs_dir, self._now = path, number, command, config, runs_dir, now_fn
        self.artifacts: list[str] = []
        self.finished = False

    @classmethod
    def create(cls, runs_dir, command: str, params: dict, sport: str | None = None, inputs=(), repo=None, now_fn=None) -> "Run":
        runs_dir = pathlib.Path(runs_dir)
        runs_dir.mkdir(parents=True, exist_ok=True)
        now_fn = now_fn or datetime.datetime.now
        number = max([n for n, _, _ in list_run_dirs(runs_dir)], default=0) + 1
        path = runs_dir / f"run-{number:04d}-{command}"
        path.mkdir()
        config = {"run": number, "command": command, "sport": sport, "started": now_fn().isoformat(timespec="seconds"),
                  "git": git_state(repo) if repo else {"commit": "unknown", "dirty": None},
                  "params": params, "inputs": [file_info(f) for f in inputs]}
        run = cls(path, number, command, config, runs_dir, now_fn)
        run.write_json("config.json", config)
        return run

    @property
    def name(self) -> str:
        return self.dir.name

    def write_json(self, name: str, obj) -> pathlib.Path:
        return self.write_text(name, json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=False, default=str) + "\n")

    def write_text(self, name: str, text: str) -> pathlib.Path:
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"an artifact is a plain file name in the run folder: {name!r}")
        path = self.dir / name
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if name not in ("config.json", "metrics.json", "report.md") and name not in self.artifacts:
            self.artifacts.append(name)
        return path

    def finish(self, metrics: dict, report: str, status: str = "ok", summary: str = "", dry_run: bool = False) -> dict:
        """Writes metrics.json and report.md and appends the line to runs.jsonl. `status`: ok | insufficient-data | refused | error."""
        if self.finished:
            raise RuntimeError("run already finished")
        self.finished = True
        metrics = dict(metrics, status=status, dry_run=dry_run)
        self.write_json("metrics.json", metrics)
        self.write_text("report.md", report if report.endswith("\n") else report + "\n")
        line = {"run": self.number, "name": self.name, "command": self.command, "sport": self.config.get("sport"), "status": status,
                "dry_run": dry_run, "started": self.config["started"], "finished": self._now().isoformat(timespec="seconds"),
                "git": self.config["git"].get("commit"), "summary": summary, "headline": metrics.get("headline", {}),
                "artifacts": list(self.artifacts)}
        with open(self.runs_dir / LOG_NAME, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        return line


def append_log(runs_dir, line: dict) -> None:
    """An extra experiment-log line for an action that is not a run of its own (a rollback)."""
    runs_dir = pathlib.Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    with open(runs_dir / LOG_NAME, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def status_table(runs_dir) -> tuple[list[dict], dict]:
    """(log lines, latest headline per (command, sport)). Only the latest non-error line of each pair counts."""
    lines = read_log(runs_dir)
    latest: dict = {}
    for ln in lines:
        if ln.get("status") in ("ok", "insufficient-data") and "run" in ln:
            latest[(ln.get("command"), ln.get("sport"))] = ln
    return lines, latest
