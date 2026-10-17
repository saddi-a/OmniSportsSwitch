"""A second OCR reader for the scorebug: PaddleOCR's PP-OCR models through ONNX Runtime (the `rapidocr-onnxruntime` package, CPU only).

EasyOCR reads the whole screen; it misses pale digits, reads the 3 of a condensed italic font as an 8 and loses most clocks (measured on
real frames, PLAN.md section 0). PaddleOCR reads those well, but is worse on other scorebugs, so it is a SECOND reader: it runs only on a
tile where a scorebug is on screen and the score or the clock is missing, its items are merged into the first reader's, and the digit
slots between the team names are read once more with recognition only (`daemon/score_slots.py`).

Fail-safe: a missing package or an engine error never reaches the OCR pass; the first reader's items come back unchanged, and the reader
switches itself off after `MAX_ERRORS` errors in a row.
"""
import logging
import re

import numpy as np

from daemon.game_state import AD_PHRASES, PROMO_PHRASES
from daemon.score_slots import read_slots
from daemon.score_watch import find_score_geo
from daemon.screen_ocr import GAP_DIGIT_HEIGHT, find_clocks, find_name_pair, looks_like_team_name

log = logging.getLogger("daemon")

MAX_ERRORS = 3            # errors in a row after which the reader switches itself off
BOTTOM_FROM = 0.62        # the bottom band starts here (fraction of the frame height) ...
TOP_TO = 0.30             # ... and the top band ends here (soccer scorebugs sit in the top left corner)
BAND_SCALE = 2.0          # the bands are read at this magnification
READ_FULL = False         # also read the whole frame once (False = bands only, faster)
MERGE_IOU = 0.3           # two boxes overlapping this much are the same text
MIN_ANCHOR_CONFIDENCE = 0.5
NUMBER_RE = re.compile(r"^\d{1,3}$")
DOWN_RE = re.compile(r"\b(1st|2nd|3rd|4th|ist|isr|2no|2wd|znd|3r0|ath)\b|&\s?(goal|\d+)|\bdown\b", re.IGNORECASE)


def _load_engine():
    """The PaddleOCR engine, or None when the package is not installed (the daemon then runs on EasyOCR alone)."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception as error:   # missing package, missing model file, onnxruntime failing to start
        log.warning("second OCR reader (rapidocr-onnxruntime) not available: %s", error)
        return None


def regions(height: int, full: bool | None = None) -> list[tuple[str, int, int, float]]:
    """(name, y0, y1, magnification) of the strips read: the bottom band and the top band at double size, and (when `full`) the whole frame."""
    full = READ_FULL if full is None else full
    found = [("bottom", int(height * BOTTOM_FROM), height, BAND_SCALE), ("top", 0, int(height * TOP_TO), BAND_SCALE)]
    return ([("full", 0, height, 1.0)] if full else []) + found


def to_items(raw, ox: int = 0, oy: int = 0, scale: float = 1.0) -> list[dict]:
    """The engine's (box, text, confidence) results as item dicts in frame coordinates (`ox`, `oy` = the strip's corner, `scale` = its magnification)."""
    items = []
    for box, text, conf in raw or []:
        text = str(text).strip()
        if not text:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        items.append({"text": text, "conf": float(conf), "x0": int(ox + min(xs) / scale), "y0": int(oy + min(ys) / scale),
                      "x1": int(ox + max(xs) / scale), "y1": int(oy + max(ys) / scale)})
    return items


def _iou(a: dict, b: dict) -> float:
    x0, y0, x1, y1 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"]), min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter
    return inter / union if union else 0.0


def merge_items(base: list[dict], extra: list[dict]) -> list[dict]:
    """The first reader's items plus the second reader's. Text both read keeps the first reader's box; a NUMBER both read takes the
    second reader's value (PaddleOCR reads digits better: EasyOCR's 48 for 43); an item that overlaps nothing is added."""
    merged = list(base)
    for new in extra:
        clash = [old for old in merged if _iou(old, new) > MERGE_IOU]
        if not clash:
            merged.append(new)
        elif NUMBER_RE.match(new["text"]):
            merged = [old for old in merged if old not in clash]
            merged.append(new)
    return sorted(merged, key=lambda it: (round((it["y0"] + it["y1"]) / 2 / 10), it["x0"]))


def has_scorebug_anchor(items: list[dict]) -> bool:
    """A clock, a down-and-distance text, a quarter or a team name is on screen: the frame shows (part of) a scorebug."""
    for it in items:
        text = it["text"].strip()
        if find_clocks([text]) or DOWN_RE.search(text):
            return True
        lowered = text.casefold()
        if any(p in lowered for p in PROMO_PHRASES) or any(p in lowered for p in AD_PHRASES):
            continue   # a promo or an ad placeholder is no scorebug ("NFL GAME PASS" looks like a team name)
        if it.get("conf", 1.0) >= MIN_ANCHOR_CONFIDENCE and looks_like_team_name(text):
            return True
    return False


def needs_rescue(items: list[dict]) -> bool:
    """True when a scorebug is on screen but the first reader gave no score or no clock."""
    if not has_scorebug_anchor(items):
        return False
    clocks = find_clocks([it["text"] for it in items])
    return not clocks or find_score_geo(items, clocks) is None


def slot_items(left: dict, right: dict, left_score: int, right_score: int, conf: float = 0.9) -> list[dict]:
    """The two score digits read from the slots as items beside their team names (as the gap pass builds them)."""
    h = max(left["y1"] - left["y0"], right["y1"] - right["y0"])
    cy, dh, w = (left["y0"] + left["y1"]) // 2, int(GAP_DIGIT_HEIGHT * h), max(2, int(0.8 * h))
    y0, y1 = cy - dh // 2, cy + dh // 2
    return [{"text": str(left_score), "conf": conf, "x0": left["x1"] + 2, "x1": left["x1"] + 2 + w, "y0": y0, "y1": y1},
            {"text": str(right_score), "conf": conf, "x0": right["x0"] - 2 - w, "x1": right["x0"] - 2, "y0": y0, "y1": y1}]


class RapidReader:
    def __init__(self, engine=None):
        self.engine = engine if engine is not None else _load_engine()
        self.errors = 0

    @property
    def available(self) -> bool:
        return self.engine is not None and self.errors < MAX_ERRORS

    def _fail(self, error: Exception) -> None:
        self.errors += 1
        log.warning("second OCR reader error %d/%d: %s", self.errors, MAX_ERRORS, error)
        if self.errors >= MAX_ERRORS:
            log.warning("second OCR reader switched off")

    def read_items(self, image: np.ndarray) -> list[dict]:
        """Items of the whole frame and of its top and bottom bands (magnified), merged. [] when the reader is unavailable or fails."""
        if not self.available:
            return []
        try:
            from PIL import Image
            height, width = image.shape[:2]
            found: list[dict] = []
            for _, y0, y1, scale in regions(height):
                strip = image[y0:y1]
                if scale != 1.0:
                    strip = np.asarray(Image.fromarray(strip).resize((int(width * scale), int((y1 - y0) * scale)), Image.LANCZOS))
                raw, _ = self.engine(strip)
                found = merge_items(found, to_items(raw, 0, y0, scale))
            self.errors = 0
            return found
        except Exception as error:
            self._fail(error)
            return []

    def rec(self, crop: np.ndarray) -> tuple[str | None, float]:
        """Recognition only (no text detection) of one small crop: (text, confidence). Used by the digit slots."""
        try:
            import cv2
            padded = cv2.copyMakeBorder(np.ascontiguousarray(crop), 6, 6, 6, 6, cv2.BORDER_REPLICATE)
            raw, _ = self.engine(padded, use_det=False, use_cls=False, use_rec=True)
            return (str(raw[0][0]).strip(), float(raw[0][1])) if raw else (None, 0.0)
        except Exception as error:
            self._fail(error)
            return None, 0.0

    def rescue(self, image: np.ndarray, items: list[dict]) -> list[dict]:
        """The first reader's items plus whatever this reader adds: its own items for the strips, then (when the geometry reader still finds
        no score and two team names stand on one row) the two score digits read slot by slot. Never raises; unchanged when unavailable."""
        if not self.available:
            return items
        merged = merge_items(items, self.read_items(image))
        clocks = find_clocks([it["text"] for it in merged])
        if find_score_geo(merged, clocks) is None:
            pair = find_name_pair([dict(it, conf=max(it["conf"], 0.55)) for it in merged])
            if pair is not None:
                scores = read_slots(image, pair[0], pair[1], self.rec)
                if scores is not None:
                    merged = merge_items(merged, slot_items(pair[0], pair[1], scores[0], scores[1]))
        return merged
