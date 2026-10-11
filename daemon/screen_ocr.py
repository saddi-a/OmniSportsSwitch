"""Reads text (scorebug, game clock) from the screen area where a stream's video is playing.

Screen capture is used because page-level frame capture is blocked by DRM. EasyOCR runs on the GPU.
Models are stored in models/easyocr/ so everything stays local after the first download.
"""
import pathlib
import re

import mss
import numpy as np

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "models" / "easyocr"
CLOCK_RE = re.compile(r"\b\d{1,3}\s?[:.,]\s?\d{2}\b")
MIN_CONFIDENCE = 0.4
TEAM_NAME_RE = re.compile(r"[A-Z][A-Z .'&-]{3,}")   # an uppercase word such as MARYLAND or K-STATE
RANKED_NAME_RE = re.compile(r"\d{1,2}\s+[A-Za-z][A-Za-z .'&-]{2,}")   # a ranking and a name, any case: "2 Georgia", "6 ALABAMA"


def looks_like_team_name(text: str) -> bool:
    if not (TEAM_NAME_RE.fullmatch(text) or RANKED_NAME_RE.fullmatch(text)):
        return False
    return not any(word in NOT_TEAM_WORDS for word in re.findall(r"[a-z]+", text.casefold()))


DIGIT_MIN_CONFIDENCE = 0.7   # a number read beside a team name must be this sure
DIGIT_SEARCH = 2.4           # how far (in text heights) to look beside a name
DIGIT_MIN_RATIO = 1.5        # score digits are at least this much taller than the team name beside them
# Words of banners and graphics that are written in capitals like a team name but are not one (a replay-review banner
# "RULING ON FIELD / Targeting" once produced a false pair of digits beside it).
NOT_TEAM_WORDS = {"ruling", "field", "review", "reviewed", "reviewing", "targeting", "replay", "instant", "challenge",
                  "official", "officials", "upheld", "overturned", "confirmed", "stands", "penalty", "flag", "timeout",
                  "timeouts", "presented", "sponsored", "brought", "breaking", "update", "final", "live", "halftime",
                  "quarter", "down", "distance", "possession", "turnover", "turnovers", "commercial", "break"}


def grab(rect: dict) -> np.ndarray:
    """Screenshot of a screen rectangle (physical pixels) as an RGB array."""
    region = {k: int(rect[k]) for k in ("left", "top", "width", "height")}
    with mss.mss() as sct:
        shot = sct.grab(region)
    return np.asarray(shot)[:, :, [2, 1, 0]]  # BGRA -> RGB


class ScreenReader:
    def __init__(self, languages=("en", "fr"), gpu: bool = True):
        import easyocr

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        # verbose=False: the download progress bar crashes on Windows consoles that can't print block characters.
        self.reader = easyocr.Reader(list(languages), gpu=gpu, model_storage_directory=str(MODEL_DIR), verbose=False)

    def read_items(self, image: np.ndarray) -> list[dict]:
        """Text found in the image in reading order, each with its confidence and box (x0, y0, x1, y1)."""
        found = self.reader.readtext(image, detail=1, paragraph=False)
        found = [item for item in found if item[2] >= MIN_CONFIDENCE and item[1].strip()]
        items = []
        for box, text, conf in reading_order(found):
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            items.append({"text": text.strip(), "conf": float(conf),
                          "x0": int(min(xs)), "y0": int(min(ys)), "x1": int(max(xs)), "y1": int(max(ys))})
        return items

    def read_digits_beside_names(self, image: np.ndarray, items: list[dict]) -> list[dict]:
        """A fallback for scoreboards whose big score digits the normal reading misses (white italic digits with a
        shadow on a coloured plate). The team names are read fine, so look right beside a name: the digits are
        located by detection inside a small crop and read with a digits-only alphabet. Only a PAIR of numbers
        side by side counts (the two scores), so a stray number never comes back. Meant to be used only when the
        normal reading found no score at all."""
        names = [it for it in items if looks_like_team_name(it["text"]) and it["conf"] >= 0.6
                 and 14 <= it["y1"] - it["y0"] <= 90]
        for name in sorted(names, key=lambda it: it["y0"] - it["y1"])[:3]:
            h = name["y1"] - name["y0"]
            y0, y1 = name["y0"] - int(0.35 * h), name["y1"] + int(0.35 * h)
            first = self._number_right_of(image, name["x1"], y0, y1, h)
            if first is None:
                continue
            second = self._number_right_of(image, first["x1"], y0, y1, h)
            if second is not None:
                return [first, second]
        return []

    def _number_right_of(self, image: np.ndarray, x: int, y0: int, y1: int, text_height: int) -> dict | None:
        H, W = image.shape[:2]
        x0, x1, y0, y1 = max(0, x), min(W, x + int(DIGIT_SEARCH * text_height)), max(0, y0), min(H, y1)
        if x1 - x0 < 10 or y1 - y0 < 10:
            return None
        horizontal, _ = self.reader.detect(image[y0:y1, x0:x1], text_threshold=0.3, low_text=0.2, link_threshold=0.2)
        boxes = sorted(horizontal[0], key=lambda b: b[0]) if horizontal and horizontal[0] else []
        for bx0, bx1, by0, by1 in boxes:
            bx0, bx1, by0, by1 = int(bx0) + x0, int(bx1) + x0, int(by0) + y0, int(by1) + y0
            if not DIGIT_MIN_RATIO * text_height <= by1 - by0 <= 4.0 * text_height or bx0 - x > 1.2 * text_height:
                continue   # not number-sized, or not right beside the name
            result = self.reader.recognize(image, horizontal_list=[[bx0, bx1, by0, by1]], free_list=[],
                                           allowlist="0123456789", detail=1)
            for _, text, conf in result:
                if conf >= DIGIT_MIN_CONFIDENCE and 1 <= len(text) <= 3:
                    bx0, by0, bx1, by1 = _trim_padding(bx0, by0, bx1, by1)
                    return {"text": text, "conf": float(conf), "x0": bx0, "y0": by0, "x1": bx1, "y1": by1}
            return None   # the nearest box is not a number: stop here
        return None

    def read(self, image: np.ndarray) -> list[tuple[str, float]]:
        """Text found in the image, top-to-bottom then left-to-right, as (text, confidence)."""
        return [(item["text"], item["conf"]) for item in self.read_items(image)]


def _trim_padding(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    """The text detector pads its boxes by a fixed fraction (about 20% of the width on each side, measured on a real
    scoreboard), so two neighbouring numbers overlap. Take the padding off again."""
    dx = int(0.2 * (x1 - x0))
    return x0 + dx, y0, x1 - dx, y1   # the height stays as detected: the names' boxes are padded the same way


def reading_order(found: list) -> list:
    """Sort EasyOCR results (box, text, confidence) into lines, top to bottom, then left to right inside a line.

    Items belong to the same line when their vertical centres are closer than 60% of the item's height, so a
    scoreboard's team names and scores stay in order even if their boxes are a few pixels apart.
    """
    def centre(item):
        ys = [p[1] for p in item[0]]
        return (min(ys) + max(ys)) / 2, max(ys) - min(ys), min(p[0] for p in item[0])

    ordered = sorted(found, key=lambda item: centre(item)[0])
    lines: list[list] = []
    for item in ordered:
        cy, height, _ = centre(item)
        if lines:
            last_cy = sum(centre(i)[0] for i in lines[-1]) / len(lines[-1])
            last_h = max(centre(i)[1] for i in lines[-1])
            if abs(cy - last_cy) < 0.6 * max(height, last_h):
                lines[-1].append(item)
                continue
        lines.append([item])
    return [item for line in lines for item in sorted(line, key=lambda i: centre(i)[2])]


def find_clocks(texts: list[str]) -> list[str]:
    """Game-clock-looking strings such as '32:19' or '3:08' in the OCR output."""
    clocks = []
    for text in texts:
        clocks += [re.sub(r"[.,]", ":", m.replace(" ", "")) for m in CLOCK_RE.findall(text)]
    return clocks
