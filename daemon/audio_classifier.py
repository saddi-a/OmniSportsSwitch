"""Audio event classifier (PANNs CNN14, trained on AudioSet) running locally on the GPU.

Takes mono audio at 32 kHz and returns a probability for each of 527 sound classes
("Cheering", "Crowd", "Speech", "Music", ...). Model files are read from models/panns/.
"""
import csv
import importlib.metadata
import pathlib
import sys
import types

import numpy as np
import torch

SAMPLE_RATE = 32000
MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "models" / "panns"
CHECKPOINT = MODEL_DIR / "Cnn14_mAP=0.431.pth"
LABELS_CSV = MODEL_DIR / "class_labels_indices.csv"


def _load_cnn14_class():
    # The package's __init__ downloads files with wget (fails on Windows), so import only its model
    # definition by registering an empty parent package.
    root = pathlib.Path(importlib.metadata.distribution("panns-inference").locate_file("panns_inference"))
    package = types.ModuleType("panns_inference")
    package.__path__ = [str(root)]
    sys.modules.setdefault("panns_inference", package)
    from panns_inference.models import Cnn14

    return Cnn14


class AudioClassifier:
    def __init__(self, device: str | None = None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        Cnn14 = _load_cnn14_class()
        self.model = Cnn14(sample_rate=SAMPLE_RATE, window_size=1024, hop_size=320,
                           mel_bins=64, fmin=50, fmax=14000, classes_num=527)
        state = torch.load(CHECKPOINT, map_location="cpu", weights_only=False)
        self.model.load_state_dict(state["model"])
        self.model.to(self.device).eval()

        with open(LABELS_CSV, newline="", encoding="utf-8") as f:
            self.labels = [row["display_name"] for row in csv.DictReader(f)]
        self.index = {name: i for i, name in enumerate(self.labels)}

    @torch.no_grad()
    def predict(self, audio: np.ndarray) -> np.ndarray:
        """audio: (samples,) or (batch, samples) float32 at 32 kHz. Returns (batch, 527) probabilities."""
        batch = np.atleast_2d(audio).astype(np.float32)
        x = torch.from_numpy(batch).to(self.device)
        return self.model(x, None)["clipwise_output"].cpu().numpy()

    def top(self, probs: np.ndarray, k: int = 4) -> list[tuple[str, float]]:
        best = np.argsort(probs)[::-1][:k]
        return [(self.labels[i], float(probs[i])) for i in best]
