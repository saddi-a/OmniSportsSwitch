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

    def read(self, image: np.ndarray) -> list[tuple[str, float]]:
        """Text found in the image, top-to-bottom then left-to-right, as (text, confidence)."""
        found = self.reader.readtext(image, detail=1, paragraph=False)
        found.sort(key=lambda item: (round(item[0][0][1] / 40), item[0][0][0]))
        return [(text.strip(), float(conf)) for _, text, conf in found if conf >= MIN_CONFIDENCE and text.strip()]


def find_clocks(texts: list[str]) -> list[str]:
    """Game-clock-looking strings such as '32:19' or '3:08' in the OCR output."""
    clocks = []
    for text in texts:
        clocks += [re.sub(r"[.,]", ":", m.replace(" ", "")) for m in CLOCK_RE.findall(text)]
    return clocks
