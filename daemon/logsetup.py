"""Log files that survive restarts.

logs/daemon.log  everything the daemon prints (rotated at 5 MB, 5 files kept)
logs/boxes.log   size and position of every OCR text item (for tuning), rotated at 20 MB
logs/events.log  only what matters afterwards: goals, swaps and why, tile focus, game states, freeze,
                 warnings and errors (rotated at 2 MB, 10 files kept)
"""
import logging
import logging.handlers
import pathlib

LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "logs"
FORMAT = "%(asctime)s %(message)s"
EVENT_WORDS = ("score change", "big play", "sport detected", "swap:", "arrange:", "swap refused", "focus tile", "game state", "freeze",
               "audio classifier ready", "screen reader ready", "listening on", "extension connected",
               "extension disconnected")


class EventFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        message = record.getMessage()
        return any(word in message for word in EVENT_WORDS)


def setup_logging(log_dir: pathlib.Path = LOG_DIR, console: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(FORMAT)

    full = logging.handlers.RotatingFileHandler(log_dir / "daemon.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    events = logging.handlers.RotatingFileHandler(log_dir / "events.log", maxBytes=2_000_000, backupCount=10, encoding="utf-8")
    events.addFilter(EventFilter())
    handlers = [full, events]
    # Text sizes and positions of every OCR reading, kept apart (it is bulky) for tuning the scoreboard reader.
    boxes = logging.handlers.RotatingFileHandler(log_dir / "boxes.log", maxBytes=20_000_000, backupCount=3, encoding="utf-8")
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
