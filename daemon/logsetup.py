"""Log files that survive restarts, and a compressed archive so nothing is ever lost.

logs/daemon.log  everything the daemon prints (rotated at DAEMON_LOG_BYTES, DAEMON_LOG_BACKUPS files kept)
logs/boxes.log   size and position of every OCR text item (for tuning), rotated at BOXES_LOG_BYTES
logs/events.log  only what matters afterwards: goals, swaps and why, tile focus, game states, freeze,
                 warnings and errors (rotated at EVENTS_LOG_BYTES, EVENTS_LOG_BACKUPS files kept)

Every file that rotates is first copied, gzip-compressed, to logs/archive/<YYYY-MM-DD>/<name>.<YYYYMMDD-HHMMSS>.gz (the date and time are the last
write of that file), in a worker thread, so the rotation never waits for the compression. `archive_rotated` does the same for the backups found at
start (a rotation missed by a crash), and is safe to repeat: a file already archived is skipped. `prune_archive` deletes archived files older than
`scoring.log_archive_days` (0 = keep for ever) and, if the archive is bigger than `scoring.log_archive_max_mb`, the oldest ones until it fits.
The learning data (logs/dataset/) is NOT in the archive and is never pruned. Nothing here may raise into the daemon: errors are logged once.
"""
import datetime
import gzip
import logging
import logging.handlers
import os
import pathlib
import shutil
import threading
import time

LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "logs"
ARCHIVE_NAME = "archive"
# Live rotation (the archive keeps what rotates out). Storage is not a constraint here; these only keep each live file easy to open.
DAEMON_LOG_BYTES, DAEMON_LOG_BACKUPS = 20_000_000, 10
EVENTS_LOG_BYTES, EVENTS_LOG_BACKUPS = 5_000_000, 20
BOXES_LOG_BYTES, BOXES_LOG_BACKUPS = 20_000_000, 5
FORMAT = "%(asctime)s %(message)s"
EVENT_WORDS = ("score change", "big play", "goal banner", "would fire", "sport detected", "swap:", "arrange:", "swap refused", "focus tile", "game state", "new game on tab", "scorebug ", "replay by clock", "anticipation ", "speech cue", "feed ", "semantic high", "semantic mid", "semantic matcher", "commentary listener", "freeze",
               "audio classifier ready", "screen reader ready", "listening on", "extension connected",
               "extension disconnected")

log = logging.getLogger("daemon.logsetup")
_failed: set[str] = set()   # kinds of failure already reported (each once)


def _report_once(kind: str, error: Exception) -> None:
    if kind not in _failed:
        _failed.add(kind)
        log.warning("log archive: %s failed (%s: %s); further failures of this kind are not repeated", kind, type(error).__name__, error)


class EventFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        message = record.getMessage()
        return any(word in message for word in EVENT_WORDS)


def archive_file(source: pathlib.Path, log_dir: pathlib.Path, name: str | None = None) -> pathlib.Path | None:
    """Copy `source` (compressed) to <log_dir>/archive/<day>/<name>.<stamp>.gz where day and stamp are its last-write time. Returns the archive
    file, or None if it is already there (or the source is gone). Raises OSError on a real failure: callers catch it."""
    try:
        modified = datetime.datetime.fromtimestamp(source.stat().st_mtime)
    except FileNotFoundError:
        return None
    target_dir = log_dir / ARCHIVE_NAME / modified.strftime("%Y-%m-%d")
    target = target_dir / f"{name or source.name.split('.log')[0] + '.log'}.{modified.strftime('%Y%m%d-%H%M%S')}.gz"
    if target.exists():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".gz.part")
    try:
        with open(source, "rb") as src, gzip.open(partial, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(partial, target)
    except FileNotFoundError:
        partial.unlink(missing_ok=True)
        return None
    except OSError:
        partial.unlink(missing_ok=True)
        raise
    return target


def archive_rotated(log_dir: pathlib.Path = LOG_DIR) -> int:
    """Archive every rotated backup (daemon.log.1, events.log.3 ...) not archived yet. Returns how many were added. Never raises."""
    added = 0
    try:
        for path in sorted(log_dir.glob("*.log.[0-9]*")):
            suffix = path.name.rsplit(".", 1)[-1]
            if not suffix.isdigit():
                continue
            try:
                if archive_file(path, log_dir, name=path.name.rsplit(".", 1)[0]) is not None:
                    added += 1
            except OSError as e:
                _report_once("archive of " + path.name, e)
    except OSError as e:
        _report_once("scan", e)
    return added


def _archive_files(archive_dir: pathlib.Path) -> list[tuple[float, int, pathlib.Path]]:
    files = []
    for path in archive_dir.rglob("*"):
        try:
            if path.is_file():
                stat = path.stat()
                files.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            continue
    return files


def prune_archive(log_dir: pathlib.Path = LOG_DIR, max_days: float = 90.0, max_mb: float = 50000.0, now: float | None = None) -> int:
    """Delete archived files older than `max_days` (0 = never by age); then, if the archive is over `max_mb` (0 = no cap), the oldest ones until it
    fits. Nothing younger than `max_days` goes unless the size cap forces it. Empty day folders are removed. Returns the files deleted. Never raises."""
    archive_dir = log_dir / ARCHIVE_NAME
    deleted = 0
    try:
        if not archive_dir.is_dir():
            return 0
        now = time.time() if now is None else now
        files = sorted(_archive_files(archive_dir))
        keep = []
        for entry in files:
            if max_days > 0 and now - entry[0] > max_days * 86400:
                deleted += _delete(entry[2])
            else:
                keep.append(entry)
        if max_mb > 0:
            total = sum(size for _, size, _ in keep)
            for entry in keep:   # oldest first
                if total <= max_mb * 1_000_000:
                    break
                if _delete(entry[2]):
                    total -= entry[1]
                    deleted += 1
        for day in sorted(p for p in archive_dir.iterdir() if p.is_dir()):
            try:
                day.rmdir()   # only succeeds when empty
            except OSError:
                pass
    except OSError as e:
        _report_once("prune", e)
    return deleted


def _delete(path: pathlib.Path) -> int:
    try:
        path.unlink()
        return 1
    except OSError as e:
        _report_once("delete of " + path.name, e)
        return 0


class ArchivingRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A rotating file handler that, after each rotation, compresses the file that was just rotated out (`<name>.1`) into the archive in a
    worker thread. The rollover itself stays a rename, so the logging call that triggers it does not wait for the compression."""

    def __init__(self, filename, log_dir: pathlib.Path, **kwargs):
        super().__init__(filename, **kwargs)
        self.log_dir = pathlib.Path(log_dir)
        self.threads: list[threading.Thread] = []

    def doRollover(self) -> None:
        super().doRollover()
        rotated = pathlib.Path(self.baseFilename + ".1")
        if self.backupCount > 0 and rotated.exists():
            try:
                # a private copy by rename-free hard work in the thread would race the next rotation: link it under another name first
                keep = rotated.with_name(rotated.name + ".archiving")
                os.link(rotated, keep)
                thread = threading.Thread(target=self._archive, args=(keep, rotated.name.rsplit(".", 1)[0]), name="log-archive", daemon=True)
                self.threads = [t for t in self.threads if t.is_alive()] + [thread]
                thread.start()
            except OSError as e:
                _report_once("start of archiving " + rotated.name, e)

    def _archive(self, copy: pathlib.Path, name: str) -> None:
        try:
            archive_file(copy, self.log_dir, name=name)
        except Exception as e:   # never let the archive hurt the daemon
            _report_once("archive of " + name, e)
        finally:
            try:
                copy.unlink()
            except OSError:
                pass


def setup_logging(log_dir: pathlib.Path = LOG_DIR, console: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(FORMAT)

    full = ArchivingRotatingFileHandler(log_dir / "daemon.log", log_dir, maxBytes=DAEMON_LOG_BYTES, backupCount=DAEMON_LOG_BACKUPS, encoding="utf-8")
    events = ArchivingRotatingFileHandler(log_dir / "events.log", log_dir, maxBytes=EVENTS_LOG_BYTES, backupCount=EVENTS_LOG_BACKUPS, encoding="utf-8")
    events.addFilter(EventFilter())
    handlers = [full, events]
    # Text sizes and positions of every OCR reading, kept apart (it is bulky) for tuning the scoreboard reader.
    boxes = ArchivingRotatingFileHandler(log_dir / "boxes.log", log_dir, maxBytes=BOXES_LOG_BYTES, backupCount=BOXES_LOG_BACKUPS, encoding="utf-8")
    boxes.setFormatter(formatter)
    boxes_logger = logging.getLogger("daemon.boxes")
    boxes_logger.setLevel(logging.INFO)
    boxes_logger.propagate = False
    boxes_logger.addHandler(boxes)
    if console:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
