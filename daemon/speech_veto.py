"""SHADOW spoken-cue veto: would a learned model have dropped this regex cue? (OFFLINE-trained, runs on the CPU, LOGS ONLY.)

Two small models read the transcript window plus the names of the cues the regexes found and each gives P(a real moment is being
called NOW, not a recap / negation / talk about it):
  C = TF-IDF (character 2-5 grams + word 1-3 grams) + logistic regression;
  G = fine-tuned `distilbert-base-multilingual-cased`.
The veto is `mean(C, G) < threshold` (`scoring.speech_veto_threshold`, 0.39, tuned on the old labelled set).
Weights live in `models/speech_veto/` (git-ignored, built by `scripts/train_speech_veto.py`; never commit them, the training data is
personal). Fail-safe: missing weights or any error = ONE warning, the veto switches itself off, nothing is raised to the caller.
Pure scoring, takes no `now`; thread-safe (one lock: the daemon calls it from `asyncio.to_thread`).
"""
import logging
import pathlib
import re
import threading

import numpy as np

log = logging.getLogger("omni.speech_veto")

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "models" / "speech_veto"
C_FILE = "c_model.joblib"
G_DIR = "g_model"
THRESHOLD = 0.39       # default cut-off on the mean probability (settings: scoring.speech_veto_threshold)
NUM_THREADS = 2        # torch CPU threads: the veto must stay out of the way of OCR / audio
MAX_LENGTH = 128       # DistilBERT tokens
# The soccer cues the model was trained on (the experiment's set); other cue names are not shown to the models.
VETO_CUES = ("card", "chance", "corner", "foul", "free kick", "goal", "offside", "penalty", "penalty awarded", "post", "post area",
             "replay", "review", "var")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def keep_cues(cues) -> list[str]:
    return [c for c in cues if c in VETO_CUES]


def c_text(text: str, cues) -> str:
    """Input of model C: normalised words, then each cue as a token CUE_<name>."""
    return norm(text) + " " + " ".join("CUE_" + c.replace(" ", "_") for c in keep_cues(cues))


def g_text(text: str, cues) -> str:
    """Input of model G: the cue names, a bar, the words."""
    return " ".join(keep_cues(cues)) + " | " + text


class CModel:
    """TF-IDF vectorizers + logistic regression, saved as one joblib file."""

    def __init__(self, vectorizers, classifier):
        self.vectorizers, self.classifier = vectorizers, classifier

    def predict(self, text: str, cues) -> float:
        from scipy.sparse import hstack

        x = hstack([v.transform([c_text(text, cues)]) for v in self.vectorizers]).tocsr()
        return float(self.classifier.predict_proba(x)[0, 1])

    def save(self, path: pathlib.Path) -> None:
        import joblib

        joblib.dump({"vectorizers": self.vectorizers, "classifier": self.classifier}, path)

    @classmethod
    def load(cls, path: pathlib.Path) -> "CModel":
        import joblib

        data = joblib.load(path)
        return cls(data["vectorizers"], data["classifier"])


class GModel:
    """A fine-tuned sequence classifier (two labels, label 1 = now) on the CPU."""

    def __init__(self, tokenizer, model):
        import torch

        self.torch, self.tokenizer, self.model = torch, tokenizer, model.to("cpu").eval()

    def predict(self, text: str, cues) -> float:
        torch = self.torch
        batch = self.tokenizer([g_text(text, cues)], truncation=True, max_length=MAX_LENGTH, return_tensors="pt")
        with torch.inference_mode():
            logits = self.model(**batch).logits.float()
        return float(logits.softmax(-1)[0, 1])

    @classmethod
    def load(cls, path: pathlib.Path) -> "GModel":
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.set_num_threads(NUM_THREADS)
        return cls(AutoTokenizer.from_pretrained(str(path)), AutoModelForSequenceClassification.from_pretrained(str(path)))


class SpeechVeto:
    """`score(text, cues)` -> {p_c, p_g, p_mean, veto} or None when the veto is off. Loads lazily on the first call.
    `c_model` / `g_model` (anything with `predict(text, cues) -> float`) replace the files, for tests."""

    def __init__(self, directory: pathlib.Path | str | None = None, threshold: float = THRESHOLD, c_model=None, g_model=None):
        self.directory = pathlib.Path(directory) if directory else MODEL_DIR
        self.threshold = threshold
        self.c, self.g = c_model, g_model
        self._lock = threading.Lock()
        self._loaded = c_model is not None and g_model is not None
        self.disabled = False

    def _disable(self, why: str) -> None:
        if not self.disabled:
            log.warning("speech veto (shadow) switched off: %s", why)
        self.disabled = True

    def _load(self) -> None:
        self._loaded = True
        c_path, g_path = self.directory / C_FILE, self.directory / G_DIR
        if not c_path.is_file() or not g_path.is_dir():
            self._disable(f"no weights in {self.directory} (run scripts/train_speech_veto.py)")
            return
        self.c, self.g = CModel.load(c_path), GModel.load(g_path)
        log.info("speech veto (shadow) ready (cpu, threshold %.2f)", self.threshold)

    def score(self, text: str, cues, threshold: float | None = None) -> dict | None:
        if self.disabled:
            return None
        try:
            with self._lock:
                if not self._loaded:
                    self._load()
                if self.disabled:
                    return None
                p_c, p_g = self.c.predict(text, cues), self.g.predict(text, cues)
            p_mean = float(np.mean([p_c, p_g]))
            limit = self.threshold if threshold is None else threshold
            return {"p_c": p_c, "p_g": p_g, "p_mean": p_mean, "veto": p_mean < limit}
        except Exception as error:   # never raise into the speech loop
            self._disable(f"{type(error).__name__}: {error}")
            return None
