"""Matcher for a channel's goal jingle (the short repeated-note sting CANAL+ plays when a goal is scored in any game of its multiplex).

Pure numpy, CPU only, a few milliseconds per window. The reference is turned into a compact template once, offline
(`scripts/build_jingle_template.py` -> `models/jingle/jingle.npz`, git-ignored: the audio and the template are never committed). The daemon
loads it if present and silently has no jingle matcher if not.

Method: log-mel spectrogram (10 ms hop), each band reduced to its change around its own median over the segment, then normalised
cross-correlation of the template against every position of the recent audio. Speech and crowd are broadband and unstructured, so
they correlate weakly; other music has other notes and rhythm. `JingleWatch` turns the per-window peaks into hits: the score must pass the
threshold in `votes` windows in a row that agree on WHEN the jingle ended (a sliding window sees the same jingle several times), then a
refractory period.
"""
from __future__ import annotations

import pathlib

import numpy as np

SAMPLE_RATE = 32000
N_FFT = 1024
HOP = 320            # 10 ms
N_MELS = 40
FMIN, FMAX = 150.0, 8000.0
TEMPLATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "models" / "jingle" / "jingle.npz"


def _mel_matrix(sr: int = SAMPLE_RATE, n_fft: int = N_FFT, n_mels: int = N_MELS, fmin: float = FMIN, fmax: float = FMAX) -> np.ndarray:
    def hz(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

    def mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    points = hz(np.linspace(mel(fmin), mel(fmax), n_mels + 2))
    freqs = np.linspace(0, sr / 2, n_fft // 2 + 1)
    bank = np.zeros((n_mels, freqs.size), dtype=np.float32)
    for i in range(n_mels):
        lo, mid, hi = points[i], points[i + 1], points[i + 2]
        up = (freqs - lo) / max(mid - lo, 1e-9)
        down = (hi - freqs) / max(hi - mid, 1e-9)
        bank[i] = np.maximum(0.0, np.minimum(up, down))
    return bank


_MEL = _mel_matrix()
_WINDOW = np.hanning(N_FFT).astype(np.float32)


def log_mel(audio: np.ndarray) -> np.ndarray:
    """(frames, N_MELS) log-mel of mono float audio at 32 kHz, hop 10 ms. Empty when the audio is shorter than one FFT window."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size < N_FFT:
        return np.zeros((0, N_MELS), dtype=np.float32)
    n = 1 + (audio.size - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(audio[idx] * _WINDOW, axis=1)) ** 2
    return np.log(spec @ _MEL.T + 1e-8).astype(np.float32)


def _shape(feat: np.ndarray) -> np.ndarray:
    """Each band relative to its own median over the segment (steady noise and level drop out), floored so only the notes above the
    background remain, then scaled to zero mean / unit norm over the whole segment."""
    x = feat - np.median(feat, axis=0, keepdims=True)
    x = np.maximum(x, 0.0)
    x = x - x.mean()
    norm = float(np.linalg.norm(x))
    return x / norm if norm > 1e-6 else np.zeros_like(x)


def build_template(audio: np.ndarray, floor_db: float = 35.0) -> np.ndarray:
    """The log-mel template of the reference clip: leading and trailing silence cut (frames more than `floor_db` below the loudest)."""
    feat = log_mel(audio)
    level = feat.max(axis=1)
    keep = np.where(level > level.max() - floor_db * np.log(10) / 10.0)[0]
    if keep.size == 0:
        raise ValueError("the reference has no sound")
    return feat[keep[0]:keep[-1] + 1]


def save_template(path: pathlib.Path, template: np.ndarray) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, template=template.astype(np.float16), hop=HOP, sr=SAMPLE_RATE, mels=N_MELS)


def load_template(path: pathlib.Path | None = None) -> np.ndarray | None:
    """The saved template, or None when there is none (or it does not fit this module's feature shape): the feature is then off."""
    path = pathlib.Path(path) if path is not None else TEMPLATE_PATH
    try:
        with np.load(path) as f:
            if int(f["hop"]) != HOP or int(f["sr"]) != SAMPLE_RATE or int(f["mels"]) != N_MELS:
                return None
            return f["template"].astype(np.float32)
    except (OSError, KeyError, ValueError):
        return None


class JingleMatcher:
    """Normalised cross-correlation of a template with the recent audio."""

    def __init__(self, template: np.ndarray):
        self.frames = template.shape[0]
        self.ref = _shape(template)
        self.seconds = self.frames * HOP / SAMPLE_RATE

    def window_samples(self, extra_s: float = 1.0) -> int:
        """How much audio a window should hold: the template plus `extra_s` of room for it to slide."""
        return int((self.seconds + extra_s) * SAMPLE_RATE) + N_FFT

    def best(self, audio: np.ndarray, step: int = 1) -> tuple[float, int]:
        """(best score, end position in samples of the best-matching stretch) of the template in `audio`. Score -1 = nothing to compare."""
        feat = log_mel(audio)
        if feat.shape[0] < self.frames:
            return -1.0, 0
        best, at = -1.0, 0
        for start in range(0, feat.shape[0] - self.frames + 1, step):
            seg = _shape(feat[start:start + self.frames])
            score = float(np.sum(seg * self.ref))
            if score > best:
                best, at = score, start
        return best, (at + self.frames - 1) * HOP + N_FFT


class JingleWatch:
    """Hits from the per-window peaks of one tab. `feed(score, end_sample_abs)` returns the score of a hit, else None.

    A hit needs `votes` consecutive windows at or above `min_score` whose jingle ends agree within `agree_s` (the same sting, seen from
    two window positions); after a hit nothing counts for `cooldown_s` (counted in audio time, `end_sample_abs / SAMPLE_RATE`)."""

    def __init__(self, min_score: float = 0.5, votes: int = 2, agree_s: float = 0.3, cooldown_s: float = 8.0):
        self.min_score, self.votes, self.agree_s, self.cooldown_s = min_score, votes, agree_s, cooldown_s
        self.seen: list[tuple[float, float]] = []   # (end time s, score) of the current run
        self.last_hit = float("-inf")

    def feed(self, score: float, end_abs: int) -> float | None:
        t = end_abs / SAMPLE_RATE
        if score < self.min_score:
            self.seen.clear()
            return None
        if self.seen and abs(t - self.seen[-1][0]) > self.agree_s:
            self.seen.clear()
        self.seen.append((t, score))
        if len(self.seen) < self.votes:
            return None
        if t - self.last_hit < self.cooldown_s:
            return None
        self.last_hit = t
        best = max(s for _, s in self.seen)
        self.seen.clear()
        return best
