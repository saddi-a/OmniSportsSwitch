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

    def read(self, image: np.ndarray) -> list[tuple[str, float]]:
        """Text found in the image, top-to-bottom then left-to-right, as (text, confidence)."""
        return [(item["text"], item["conf"]) for item in self.read_items(image)]


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
