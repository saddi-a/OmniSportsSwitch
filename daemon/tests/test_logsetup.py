import gzip
import logging
import os
import time

from daemon import logsetup
from daemon.logsetup import ARCHIVE_NAME, ArchivingRotatingFileHandler, archive_file, archive_rotated, prune_archive


def test_events_go_to_both_files_and_noise_only_to_the_full_log(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logsetup.setup_logging(tmp_path, console=False)
        log = logging.getLogger("daemon")
        log.info("  ocr tab 5[0]: clocks=['31:30'] texts=['NAP', '1-0']")       # noise
        log.info("score change tab 5 tile 0: ['NAP', '2-0']")                      # event
        log.info("swap: promote tab 5, demote tab 7 | challenger [score=0.7]")     # event
        log.warning("ocr pass failed: boom")                                         # warning
        for handler in root.handlers:
            handler.flush()
        full = (tmp_path / "daemon.log").read_text(encoding="utf-8")
        events = (tmp_path / "events.log").read_text(encoding="utf-8")
        assert "ocr tab 5[0]" in full and "score change" in full and "ocr pass failed" in full
        assert "score change" in events and "swap:" in events and "ocr pass failed" in events
        assert "ocr tab 5[0]" not in events
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                handler.close()
                root.removeHandler(handler)


def _archived(log_dir):
    return sorted((log_dir / ARCHIVE_NAME).rglob("*.gz"))


def _wait(handler):
    for t in handler.threads:
        t.join(10)


def test_a_rotation_archives_exactly_what_was_rotated_out(tmp_path):
    handler = ArchivingRotatingFileHandler(tmp_path / "t.log", tmp_path, maxBytes=300, backupCount=2, encoding="utf-8")
    logger = logging.getLogger("test_rotation_archive")
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(12):
            logger.info("line number %02d %s", i, "x" * 40)
        handler.flush()
        _wait(handler)
    finally:
        logger.removeHandler(handler)
        handler.close()
    files = _archived(tmp_path)
    assert files, "a rotation must produce an archive file"
    assert all(f.name.startswith("t.log.") and f.name.endswith(".gz") for f in files)
    archived = "".join(gzip.open(f, "rt", encoding="utf-8").read() for f in files)
    live = "".join((tmp_path / n).read_text(encoding="utf-8") for n in ("t.log", "t.log.1", "t.log.2") if (tmp_path / n).exists())
    # nothing is lost: every line written is either still in a live file or in the archive
    for i in range(12):
        assert f"line number {i:02d}" in archived + live
    assert not list(tmp_path.glob("*.archiving"))   # the temporary link is gone


def test_archive_rotated_is_idempotent_and_skips_the_live_file(tmp_path):
    (tmp_path / "daemon.log").write_text("live\n")
    (tmp_path / "daemon.log.1").write_text("one\n")
    (tmp_path / "events.log.3").write_text("three\n")
    assert archive_rotated(tmp_path) == 2
    assert archive_rotated(tmp_path) == 0
    contents = sorted(gzip.open(f, "rt").read() for f in _archived(tmp_path))
    assert contents == ["one\n", "three\n"]
    assert sorted(f.name.split(".log.")[0] for f in _archived(tmp_path)) == ["daemon", "events"]


def test_archive_folder_is_the_day_of_the_last_write(tmp_path):
    source = tmp_path / "boxes.log.1"
    source.write_text("old\n")
    stamp = time.mktime((2026, 10, 9, 13, 14, 15, 0, 0, -1))
    os.utime(source, (stamp, stamp))
    target = archive_file(source, tmp_path, name="boxes.log")
    assert target.parent.name == "2026-10-09" and target.name == "boxes.log.20261009-131415.gz"


def _old(path, days, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1000)
    t = now - days * 86400
    os.utime(path, (t, t))


def test_pruning_respects_the_age_limit(tmp_path):
    now = time.time()
    _old(tmp_path / ARCHIVE_NAME / "a" / "old.gz", 100, now)
    _old(tmp_path / ARCHIVE_NAME / "b" / "edge.gz", 89, now)
    _old(tmp_path / ARCHIVE_NAME / "b" / "new.gz", 1, now)
    assert prune_archive(tmp_path, max_days=90, max_mb=50000, now=now) == 1
    assert not (tmp_path / ARCHIVE_NAME / "a").exists()   # the empty day folder goes too
    assert (tmp_path / ARCHIVE_NAME / "b" / "edge.gz").exists() and (tmp_path / ARCHIVE_NAME / "b" / "new.gz").exists()


def test_zero_days_and_zero_cap_keep_everything(tmp_path):
    now = time.time()
    _old(tmp_path / ARCHIVE_NAME / "a" / "ancient.gz", 4000, now)
    assert prune_archive(tmp_path, max_days=0, max_mb=0, now=now) == 0
    assert (tmp_path / ARCHIVE_NAME / "a" / "ancient.gz").exists()


def test_the_size_cap_removes_the_oldest_first(tmp_path):
    now = time.time()
    for n, days in enumerate((5, 4, 3, 2, 1)):
        _old(tmp_path / ARCHIVE_NAME / "d" / f"f{n}.gz", days, now)   # 1000 bytes each, f0 the oldest
    assert prune_archive(tmp_path, max_days=90, max_mb=0.0025, now=now) == 3   # room for two files
    assert sorted(p.name for p in (tmp_path / ARCHIVE_NAME / "d").iterdir()) == ["f3.gz", "f4.gz"]


def test_pruning_never_touches_the_dataset_or_the_live_logs(tmp_path):
    now = time.time()
    _old(tmp_path / "dataset" / "2020-01-01.jsonl", 2000, now)
    _old(tmp_path / "daemon.log.1", 2000, now)
    prune_archive(tmp_path, max_days=1, max_mb=0.001, now=now)
    assert (tmp_path / "dataset" / "2020-01-01.jsonl").exists() and (tmp_path / "daemon.log.1").exists()


def test_a_failing_archive_does_not_raise_and_is_reported_once(tmp_path, monkeypatch, caplog):
    (tmp_path / "daemon.log.1").write_text("one\n")
    (tmp_path / "daemon.log.2").write_text("two\n")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(logsetup, "archive_file", boom)
    logsetup._failed.clear()
    with caplog.at_level(logging.WARNING, logger="daemon.logsetup"):
        assert archive_rotated(tmp_path) == 0
        assert archive_rotated(tmp_path) == 0
    assert len([r for r in caplog.records if "log archive" in r.getMessage() and "daemon.log.1" in r.getMessage()]) == 1
    # the handler's worker thread swallows the same error
    handler = ArchivingRotatingFileHandler(tmp_path / "x.log", tmp_path, maxBytes=50, backupCount=1, encoding="utf-8")
    logger = logging.getLogger("test_failing_archive")
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(6):
            logger.info("%s", "y" * 40)
        _wait(handler)
    finally:
        logger.removeHandler(handler)
        handler.close()
    logsetup._failed.clear()


def test_pruning_a_missing_archive_is_fine(tmp_path):
    assert prune_archive(tmp_path / "nothing") == 0


def test_exit_trace_creates_crash_log_and_enables_faulthandler(tmp_path):
    import faulthandler
    from daemon import logsetup

    try:
        logsetup.install_exit_trace(tmp_path)
        assert faulthandler.is_enabled()
        assert "daemon started" in (tmp_path / "crash.log").read_text(encoding="utf-8")
    finally:
        faulthandler.disable()
        logsetup._crash_file.close()
