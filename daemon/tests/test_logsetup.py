import logging

from daemon import logsetup


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
