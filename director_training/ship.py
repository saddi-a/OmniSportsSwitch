"""Ship: turn a fit run + a passing shadow run into a patch of the per-sport settings overrides.

WHERE OVERRIDES CAN LIVE (read from daemon/settings.py, no daemon change): ONLY in `settings.toml`, in the tables `[weights.<sport>]` and
`[scoring.<sport>]` (`_read_sport_overrides`, file only). `settings.local.json` (what the popup saves) is a flat {setting id: value} map read by
`_set`; it has no per-sport scoring or weights ids, so it cannot hold them. settings.toml is the tracked, public file; `ship` therefore never
writes it unless asked with `--apply --target <path>` (no default target for an apply), and by default only prints the diff.

Gate (all must hold): fit run status ok and not a dry run; shadow run status ok, not a dry run, a real hold-out (no game seen by the fit); the
shadow run belongs to this fit run and sport; at least `min_judged` judged swaps for today's rules; the candidate's regret rate is STRICTLY lower than
today's (replayed with the same approximation); the candidate's median lead is not lower than today's by more than `lead_tolerance_s`.
Misses are reported, not gated (see the design questions in the report of the run).
"""
import difflib
import hashlib
import pathlib
import re
import shutil
import tomllib

DEFAULT_LEAD_TOLERANCE_S = 5.0
DEFAULT_MIN_JUDGED = 10


def candidate_patch(fit_primary: dict, today_cfg) -> dict:
    """{'weights': {key: value}, 'scoring': {key: value}}: the fitted settings of a fit result that DIFFER from today's value in `today_cfg` (the
    sport's view of the current settings), so the patch lists only changes."""
    cand = fit_primary["candidate"]
    out = {"weights": {}, "scoring": {}}
    for section in ("weights", "scoring"):
        holder = today_cfg.weights if section == "weights" else today_cfg
        for key, value in cand["settings"][section].items():
            if abs(float(getattr(holder, key)) - float(value)) > 1e-9:
                out[section][key] = value
    return out


def toml_table_name(section: str, sport: str) -> str:
    return f"{section}.{sport}" if re.fullmatch(r"[A-Za-z0-9_-]+", sport) else f'{section}."{sport}"'


def patch_toml(patch: dict, sport: str, comment: str = "") -> str:
    lines = []
    if comment:
        lines += [f"# {c}" for c in comment.splitlines()]
    for section in ("weights", "scoring"):
        if patch[section]:
            lines += [f"[{toml_table_name(section, sport)}]"] + [f"{k} = {v}" for k, v in patch[section].items()] + [""]
    return "\n".join(lines) + ("\n" if lines else "")


def _header_re(section: str, sport: str):
    sp = re.escape(sport)
    return re.compile(rf'^\s*\[\s*{section}\s*\.\s*(?:"{sp}"|{sp})\s*\]\s*(?:#.*)?$')


def apply_text(text: str, sport: str, patch: dict) -> str:
    """The settings.toml text with the patch merged into `[weights.<sport>]` / `[scoring.<sport>]` (existing keys updated in place, new keys added at
    the end of their table, a missing table appended). Comments and the file's line endings are kept."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    trailing_nl = lines and lines[-1] == ""
    if trailing_nl:
        lines.pop()
    for section in ("weights", "scoring"):
        values = patch.get(section) or {}
        if not values:
            continue
        head = _header_re(section, sport)
        start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"[{toml_table_name(section, sport)}]"] + [f"{k} = {v}" for k, v in values.items()]
            continue
        end = start + 1
        while end < len(lines) and not re.match(r"^\s*\[", lines[end]):
            end += 1
        remaining = dict(values)
        for i in range(start + 1, end):
            m = re.match(r"^(\s*)([A-Za-z0-9_]+)(\s*=\s*)([^#\s]+)(.*)$", lines[i])
            if m and m.group(2) in remaining:
                lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{remaining.pop(m.group(2))}{m.group(5)}"
        last = end
        while last > start + 1 and not lines[last - 1].strip():
            last -= 1
        lines[last:last] = [f"{k} = {v}" for k, v in remaining.items()]
    out = eol.join(lines)
    return out + eol if trailing_nl else out


def validate_text(text: str, sport: str, patch: dict, tmp_dir) -> list[str]:
    """Problems of the patched text: not valid TOML, or the daemon's own loader does not read the numbers back. [] = fine. Writes only in tmp_dir."""
    problems = []
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return [f"patched file is not valid TOML: {e}"]
    from daemon.settings import load_settings
    tmp = pathlib.Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "settings.toml"
    path.write_text(text, encoding="utf-8", newline="")
    s = load_settings(path).scoring
    key = " ".join(sport.lower().split())
    for section, store in (("weights", s.sport_weights), ("scoring", s.sport_scoring)):
        for k, v in (patch.get(section) or {}).items():
            got = store.get(key, {}).get(k)
            if got is None or abs(got - float(v)) > 1e-9:
                problems.append(f"the daemon's loader does not read [{section}.{sport}] {k} = {v} back (got {got})")
    return problems


def unified_diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{name}", f"b/{name}"))


def gate(fit: dict, shadow: dict, fit_run: int, shadow_fit_run, sport: str, lead_tolerance_s: float = DEFAULT_LEAD_TOLERANCE_S,
         min_judged: int = DEFAULT_MIN_JUDGED) -> tuple[bool, list[str]]:
    """(ok, reasons it failed). `fit` = the fit run's metrics, `shadow` = the shadow.json of the evaluate run."""
    why = []
    fs = (fit.get("sports") or {}).get(sport)
    if fit.get("dry_run") or (fs or {}).get("dry_run"):
        why.append("the fit run is a DRY RUN")
    if not fs or fs.get("status") != "ok":
        why.append(f"the fit run has no fitted weights for {sport} ({(fs or {}).get('reason') or 'missing'})")
    if shadow.get("dry_run"):
        why.append("the shadow run is a DRY RUN")
    if shadow.get("status") != "ok":
        why.append(f"the shadow run has no result ({shadow.get('reason')})")
    if shadow_fit_run is not None and int(shadow_fit_run) != int(fit_run):
        why.append(f"the shadow run evaluated fit run {shadow_fit_run}, not {fit_run}")
    if shadow.get("sport") != sport:
        why.append(f"the shadow run is for {shadow.get('sport')}, not {sport}")
    if not shadow.get("hold_out"):
        why.append("the shadow run is not a hold-out (games the fit saw: " + ", ".join(shadow.get("games_seen_by_fit") or ["none evaluated"]) + ")")
    if shadow.get("status") == "ok":
        t, c = shadow["metrics"]["today_sim"], shadow["metrics"]["candidate_sim"]
        if (t.get("judged") or 0) < min_judged:
            why.append(f"only {t.get('judged') or 0} judged swaps under today's rules, need {min_judged}")
        if t.get("regret_per_10min") is None or c.get("regret_per_10min") is None:
            why.append("regret rate unknown (no live Main minutes)")
        elif not c["regret_per_10min"] < t["regret_per_10min"]:
            why.append(f"candidate regret rate {c['regret_per_10min']:.2f} is not below today's {t['regret_per_10min']:.2f} per 10 min")
        tl, cl = t.get("median_lead_last_s"), c.get("median_lead_last_s")
        if tl is not None and (cl is None or cl < tl - lead_tolerance_s):
            why.append(f"candidate median lead {cl} is more than {lead_tolerance_s} s below today's {tl:.1f}")
    return (not why), why


def sha256(path) -> str:
    h = hashlib.sha256()
    h.update(pathlib.Path(path).read_bytes())
    return h.hexdigest()


def apply_to_file(target, new_text: str, backup_dir, stamp: str) -> dict:
    """Backup (timestamped copy in backup_dir), then write. Returns {target, backup, sha256_before, sha256_after}."""
    target = pathlib.Path(target)
    backup_dir = pathlib.Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{target.name}.{stamp}.bak"
    shutil.copy2(target, backup)
    before = sha256(target)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)
    return {"target": str(target), "backup": str(backup), "sha256_before": before, "sha256_after": sha256(target)}


def rollback_file(info: dict, force: bool = False) -> tuple[bool, str]:
    """Restore a backup made by `apply_to_file`. Refuses if the target changed since the apply (unless force)."""
    target, backup = pathlib.Path(info["target"]), pathlib.Path(info["backup"])
    if not backup.exists():
        return False, f"backup {backup} is missing"
    if not target.exists():
        return False, f"target {target} is missing"
    if not force and sha256(target) != info["sha256_after"]:
        return False, "the target file changed after the apply (not the file this run wrote); use --force to restore the backup anyway"
    shutil.copy2(backup, target)
    return True, f"restored {target} from {backup}"
