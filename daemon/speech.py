"""What the commentators say, heard locally (faster-whisper on the GPU). MEASURING ONLY: the words are logged
(logs/daemon.log `speech heard`, logs/events.log `speech cue`) and compared with what really happened; nothing here
changes a score or a swap. Needs the optional package `faster-whisper` (see README); without it the daemon runs as before.
"""
import glob
import os
import pathlib
import re
import sys

import numpy as np

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "models" / "whisper"
MODEL_SIZE = "small"
SAMPLE_RATE = 32000          # what the daemon buffers
WHISPER_RATE = 16000         # what the model wants
MIN_RMS = 0.002              # quieter than this is silence: not worth a transcription

# cue -> regular expressions on the lower-cased transcript (English and French; the transcript is often misspelt,
# so the list is short and plain). A cue is a thing a commentator says before or around a big moment.
CUES = {
    "touchdown": [r"touch ?downs?", r"touché"],
    "red zone": [r"red ?zone", r"zone rouge"],
    "goal line": [r"goal ?line", r"ligne de but"],
    "field goal": [r"field goal", r"coup de pied de placement"],
    "fourth down": [r"fourth down", r"4th down", r"quatrième essai"],
    "flag": [r"\bflag\b", r"\bpenalty flag\b", r"drapeau jaune"],
    "review": [r"\breview\b", r"challenge", r"vidéo ?review", r"assistance vidéo"],
    "interception": [r"intercept", r"fumble", r"\bsack\b"],
    "penalty": [r"\bpenalt(y|ies|i)\b", r"pénalty", r"point de penalty"],
    "free kick": [r"free kick", r"coups? francs?"],
    "corner": [r"\bcorner\b"],
    "offside": [r"off ?side", r"hors[- ]jeu"],
    "var": [r"\bvar\b", r"video assistant"],
    "card": [r"red card", r"carton rouge", r"yellow card", r"carton jaune", r"sent off", r"sending off", r"dismissed", r"expuls", r"\bexclu(e|s|es)?\b", r"exclusion", r"\bcarton\b", r"expulsado", r"\bà dix\b", r"infériorité numérique", r"a man down"],
    "replay": [r"revoyons", r"on revoit", r"au ralenti", r"\bralenti\b", r"\breplay\b", r"another look", r"\bcan we see that again\b"],
    "foul": [r"\btacle\b", r"\btackle\b", r"\bfaute\b", r"\bfoul\b"],
    "goal": [r"\bgo+a{2,}l+\b", r"\bgo{2,}a+l+\b", r"\bgoal ?!", r"\bbut ?!", r"\bbu{2,}t\b", r"ouvre le score", r"égalis(e|ent)\b", r"opens the scoring", r"equali[sz]es?",
             # goal calls heard 2026-10-11 that nothing matched (checked on 1924 lines of speech: no other hit)
             r"prend les devants", r"ouvre la marque", r"double la mise", r"inscrit son \d+\s?(e|è|ème|eme|er)? but", r"revient au score", r"met le deuxi[eè]me",
             r"porte le score [àa]", r"what a goal", r"\bhe scores\b", r"\btakes the lead\b(?! pass)", r"\bdoubles (the|their) lead\b", r"\bpull(s|ed)? one back\b",
             r"\bgo{3,}l\b", r"\bgolazo\b", r"\bgo{2,}l[oa]\b"],   # plain "goal" is too common (first and goal)
    "post": [r"\bthe post\b", r"\bthe bar\b", r"\bcrossbar\b", r"\bpoteaux?\b", r"\bla barre\b", r"\btransversale\b"],
    "chance": [r"what a (big )?chance", r"big chance", r"so close", r"just wide", r"just over", r"just past", r"(great|what a|brilliant) save",
               r"quelle occasion", r"(belle|grosse) occasion", r"juste à côté", r"juste au-dessus", r"(quel|bel) arrêt", r"(belle|superbe) parade"],
    "free throw": [r"free throws?", r"lancers? francs?"],
    "timeout": [r"time ?out", r"temps mort"],
}
_COMPILED = {cue: [re.compile(p, re.IGNORECASE) for p in patterns] for cue, patterns in CUES.items()}


def find_speech_cues(text: str) -> list[str]:
    """The cues a transcript contains, in the order of CUES."""
    return [cue for cue, patterns in _COMPILED.items() if any(p.search(text) for p in patterns)]


def to_whisper_rate(samples: np.ndarray) -> np.ndarray:
    """32 kHz -> 16 kHz by averaging neighbouring samples (a crude low-pass, enough for speech)."""
    n = len(samples) // 2 * 2
    return samples[:n].reshape(-1, 2).mean(axis=1).astype(np.float32)


def _add_cuda_libraries() -> None:
    """The CUDA libraries that faster-whisper needs (cuBLAS, cuDNN) come as pip packages under site-packages/nvidia."""
    for base in sys.path:
        for folder in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(folder)
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


class SpeechListener:
    """Loads the speech model once and turns audio into text."""

    def __init__(self, size: str = MODEL_SIZE, device: str = "cuda"):
        _add_cuda_libraries()
        from faster_whisper import WhisperModel

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        self.model = WhisperModel(size, device=device, compute_type="float16" if device == "cuda" else "int8",
                                  download_root=str(MODEL_DIR))
        self.device = device

    def transcribe(self, samples32k: np.ndarray) -> tuple[str, str]:
        """(text, language) of the audio; ('', '') for silence. VAD filters out the stretches with no speech, which is
        what stops the model inventing 'thanks for watching' over crowd noise."""
        if samples32k.size == 0 or float(np.sqrt(np.mean(samples32k * samples32k))) < MIN_RMS:
            return "", ""
        segments, info = self.model.transcribe(to_whisper_rate(samples32k), beam_size=1, vad_filter=True,
                                               condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip(), info.language
