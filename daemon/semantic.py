"""Semantic reading of the commentary: how close is what was said to the things commentators say at big moments?
MEASURING ONLY (like speech.py): the result is logged and compared with what really happened; nothing here changes a
score or a swap. A small multilingual sentence model (paraphrase-multilingual-MiniLM, quantised ONNX, about 120 MB, run
on the CPU with onnxruntime, so it cannot disturb PyTorch or the GPU) turns each piece of the transcript into a vector;
the best cosine similarity to the reference phrases of each level (high, mid, low) says how exciting the talk is.
"""
import pathlib
import re

import numpy as np

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "models" / "semantic"
REPO = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_FILE = "onnx/model_quantized.onnx"
MAX_TOKENS = 64
MIN_WORDS = 2          # pieces shorter than this are not worth embedding

# Reference phrases (English and French). High = the moment itself or a near miss; mid = a notable event or a dangerous
# phase; low = ordinary match talk, there to tell "he talks about the match" from "something is happening".
HIGH = [
    "goal! he scores", "what a goal", "it's in the net", "he opens the scoring", "they equalise", "touchdown", "he is in the red zone",
    "penalty for the home team", "he hits the post", "it hits the crossbar", "what a chance", "so close, just wide", "great save by the goalkeeper",
    "but ! quel but", "il ouvre le score", "ils égalisent", "penalty pour la Roma", "quelle occasion", "juste à côté du poteau",
    "il touche la barre", "quel arrêt du gardien", "quelle parade",
]
MID = [
    "free kick near the box", "corner kick", "yellow card", "red card", "the referee checks the video", "a dangerous attack",
    "a great run down the sideline", "fourth down", "a flag on the play", "coup franc dangereux", "corner pour Côme", "carton jaune",
    "l'arbitre consulte la VAR", "une belle action", "une grosse frappe",
]
LOW = [
    "the team is playing a tactical game", "he passes the ball back to the keeper", "they are keeping possession",
    "ils jouent avec un bloc bas", "la possession est à Côme", "ils ont fait des changements tactiques", "le travail défensif est bon",
]
# Ordinary talk of a match, so that "he talks about the match" does not look like "something is happening" (the evaluation on
# 109 real transcripts: without these, 45% of all chunks raised an alarm; with them and the stricter thresholds, 4 of 109, all true).
NEUTRAL = [
    "il passe le ballon à son coéquipier", "ils gardent la possession", "le match est très tactique", "c'est un match engagé",
    "le joueur a fait une petite erreur", "il faut analyser le jeu", "bon placement défensif", "le coach a fait des changements",
    "la série A est un championnat difficile", "depuis le début de la saison", "il récupère le ballon au milieu",
    "he passes the ball to his teammate", "the match is very tactical", "he made a small mistake", "good defensive positioning",
    "this is a tough league", "since the start of the season", "he wins the ball in midfield",
]

_PIECES = re.compile(r"[.!?;…]+|,|\bmais\b|\bbut\b")


def pieces(text: str) -> list[str]:
    """The transcript cut into sentences and clauses (names and filler in a long sentence dilute its meaning)."""
    parts = [p.strip() for p in _PIECES.split(text) if len(p.split()) >= MIN_WORDS]
    whole = text.strip()
    if len(whole.split()) >= MIN_WORDS and whole not in parts:
        parts.append(whole)
    return parts


class SemanticMatcher:
    def __init__(self):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model = hf_hub_download(REPO, MODEL_FILE, local_dir=str(MODEL_DIR))
        self.tokenizer = Tokenizer.from_file(hf_hub_download(REPO, "tokenizer.json", local_dir=str(MODEL_DIR)))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding()
        self.session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        self.names = {i.name for i in self.session.get_inputs()}
        self.refs = {"high": HIGH, "mid": MID, "low": LOW, "neutral": NEUTRAL}
        self.vectors = {tier: self.embed(phrases) for tier, phrases in self.refs.items()}

    def embed(self, texts: list[str]) -> np.ndarray:
        enc = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        out = self.session.run(None, feeds)[0]
        pooled = (out * mask[..., None]).sum(1) / np.maximum(mask.sum(1, keepdims=True), 1)
        return pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9)

    def match(self, text: str) -> dict:
        """{'high': s, 'mid': s, 'low': s, 'phrase': best reference phrase, 'piece': the piece that matched} from the
        best similarity of any piece of the text to the phrases of each level; zeros for an empty text."""
        result = {"high": 0.0, "mid": 0.0, "low": 0.0, "neutral": 0.0, "phrase": "", "piece": ""}
        parts = pieces(text)
        if not parts:
            return result
        vectors = self.embed(parts)
        for tier in ("high", "mid", "low", "neutral"):
            sims = vectors @ self.vectors[tier].T
            result[tier] = float(sims.max())
            if tier == "high":
                row, col = np.unravel_index(int(sims.argmax()), sims.shape)
                result["phrase"], result["piece"] = self.refs["high"][col], parts[row]
        return result


def level(match: dict, min_sim: float, margin: float, mid_sim: float | None = None) -> str:
    """'high', 'mid' or '' : a level counts when its similarity reaches its minimum (`min_sim` for high, `mid_sim` for mid,
    default the same) and beats the best ordinary-talk similarity (low or neutral phrases) by `margin`."""
    base = max(match["low"], match.get("neutral", 0.0))
    if match["high"] >= min_sim and match["high"] - base >= margin:
        return "high"
    if match["mid"] >= (min_sim if mid_sim is None else mid_sim) and match["mid"] - base >= margin:
        return "mid"
    return ""
