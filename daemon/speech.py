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
    "touchdown": [r"touch ?downs?", r"touché ?!"],   # plain "touché" is French for "touched" ("on a touché", "touché le ballon")
    "red zone": [r"red ?zone", r"zone rouge"],
    "goal line": [r"goal ?line", r"ligne de but"],
    "field goal": [r"field goal", r"coup de pied de placement"],
    "fourth down": [r"fourth down", r"4th down", r"quatrième essai"],
    "flag": [r"\bflag\b", r"\bpenalty flag\b", r"drapeau jaune"],
    "review": [r"\breview\b", r"challenge", r"vidéo ?review", r"assistance vidéo"],
    "interception": [r"intercept", r"fumble", r"\bsack\b"],
    "penalty": [r"\bpenalt(y|ies|i)\b", r"pénalty", r"point de penalty"],
    # a penalty GIVEN (not a mention of an old one): the strongest build-up sign in football, so it has its own cue (see cues.SPEECH_SCORING)
    "penalty awarded": [r"p[ée]nalty (sifflé|accordé|accordée|pour)\b", r"(siffle|accorde|donne|accordent) (un |le )?p[ée]nalty",
                        r"(il y a|c'est|ce sera|ce serait) (un |le )?p[ée]nalty", r"(awards?|awarded|gives?|given) (a |the )?penalty",
                        r"penalty (is )?(awarded|given)", r"points? to the spot"],
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
    # the ball hits the woodwork. "premier/second poteau" is a position (a cross, marking): its own cue below. "sous la barre" is a goal already scored.
    "post": [r"\bthe post\b", r"\bthe bar\b", r"\bcrossbar\b",
             r"(?<!premier )(?<!second )(?<!seconde )(?<!deuxième )(?<!deuxieme )(?<!1er )(?<!1ère )(?<!2e )(?<!2ème )\bpoteaux?\b(?! (sortant|rentrant))",
             r"(?<!sous )\bla barre\b", r"\btransversale\b"],
    # near/far post: a cross or a delivery is coming to that spot (about 35 of 41 `post` hits on 2924 football transcripts, 2026-10-13); a medium cue
    "post area": [r"\b(premier|second|seconde|deuxième|deuxieme|1er|1ère|2e|2ème)\s+poteaux?\b", r"\bpoteaux?\s+(sortant|rentrant)\b", r"\b(near|far|back) post\b"],
    "chance": [r"what a (big )?chance", r"big chance", r"so close", r"just wide", r"just over", r"just past", r"(great|what a|brilliant) save",
               r"quelle occasion", r"(belle|grosse) occasion", r"juste à côté", r"juste au-dessus", r"(quel|bel) arrêt", r"(belle|superbe) parade",
               # a shot and a save (heard 2026-10-12: "prépare sa frappe, c'est capté en deux temps" matched nothing)
               # a catch or a clearance alone is routine (a keeper takes a cross, a defender clears): only after a shot word in the same clause
               # (4 of 5 "c'est capté" and most of 13 "repoussé" in 2924 transcripts were not shots, 2026-10-13)
               r"capté en deux temps", r"\b(frappe|tir|reprise|volée)\b[^.!?;:\n]{0,40}\b(capté|repouss(e|é|ée|ées|és|ent))\b", r"\bdétourne\b", r"arrêt du gardien", r"le gardien (repousse|capte|détourne|s'interpose|boxe)",
               r"frappe (cadrée|puissante|lourde)", r"tir cadré", r"saved by", r"denied by", r"shot on target",
               # "tries his luck / has a go" (heard 2026-10-12 one second before a goal: "va tenter sa chance !"); bare "shoots" is left out
               r"\bten(te|ter|tent|tera)\s+(sa|leur)\s+chance\b", r"\btries (his|her|their) luck\b", r"\bhas a go\b", r"\btakes a shot\b",
               r"\blets fly\b", r"\bfires at goal\b",
               # shot wrong-footed the keeper; the ball is in the box (measured on 6253 transcript lines 2026-10-12: 2, 27, 0 and 1 lines)
               r"\bcontre-?pied\b", r"\bdans la surface\b", r"\bin the (box|area)\b"],
    "free throw": [r"free throws?", r"lancers? francs?"],
    "timeout": [r"time ?out", r"temps mort"],
}
# cue -> the sports it is valid for (sport names as in Server._tab_sport: "football" = soccer, "american football", "basketball").
# None = every sport. One place to read the classification; a cue outside its sports is dropped when the sport is known
# ("sortent de la zone rouge" = relegation zone, said on a soccer stream, is not the American football red zone).
_SOCCER, _AMERICAN, _BASKETBALL = "football", "american football", "basketball"
CUE_SPORTS = {
    "touchdown": frozenset({_AMERICAN}),
    "red zone": frozenset({_AMERICAN}),
    "goal line": frozenset({_AMERICAN}),        # soccer: goal-line technology / clearances, not a cue
    "field goal": frozenset({_AMERICAN}),       # basketball "field goal %" is a statistic
    "fourth down": frozenset({_AMERICAN}),
    "flag": frozenset({_AMERICAN}),             # soccer: the linesman's flag, already the "offside" cue
    "interception": frozenset({_AMERICAN}),     # also fumble, sack; soccer "intercepts the pass" is routine
    "review": None,                             # VAR, challenge, video review: every sport
    "penalty": frozenset({_SOCCER, _AMERICAN}), # soccer: penalty kick; American football: a flag (cues.py grades it per sport)
    "penalty awarded": frozenset({_SOCCER}),
    "free kick": frozenset({_SOCCER}),          # cues.FREE_KICK_CUES: soccer weight only
    "corner": frozenset({_SOCCER}),             # basketball "corner three" is not a cue
    "offside": frozenset({_SOCCER, _AMERICAN}), # American football: offside is a defensive penalty
    "var": frozenset({_SOCCER}),
    "card": frozenset({_SOCCER}),               # cues.speech_tiers: soccer only
    "replay": None,
    "foul": None,                               # soccer, basketball, American football all have fouls (cues.py: mid tier)
    "goal": frozenset({_SOCCER}),               # goal calls ("he scores", "takes the lead") are soccer-scored only
    "post": frozenset({_SOCCER}),               # "the post" / "la barre" in basketball is a position or a bar
    "post area": frozenset({_SOCCER}),          # near/far post: a cross is coming
    "chance": frozenset({_SOCCER}),
    "free throw": frozenset({_BASKETBALL}),
    "timeout": None,                            # every sport with timeouts
}
assert set(CUE_SPORTS) == set(CUES), "every cue needs a sports entry"
_COMPILED = {cue: [re.compile(p, re.IGNORECASE) for p in patterns] for cue, patterns in CUES.items()}

# Recap / negation filter for the soccer cues that feed scoring ("il n'y a pas de faute", "un penalty obtenu", "ce qui amène ce penalty" are not
# a build-up). Applied per MATCH, on soccer streams only; review, replay, var and the American football cues are never filtered.
FILTERED_CUES = frozenset({"penalty", "penalty awarded", "foul", "offside", "chance", "post", "post area", "goal", "free kick", "card", "corner"})
_BEFORE_WORDS, _BEFORE_CHARS = 4, 30      # only the last few words of the same clause BEFORE the match count (text after the match never does)
_AFTER_CHARS = 28
# words just before a cue that make it a negation, a hypothetical or a recap (French and English)
NEGATION_OR_RECAP = [r"\bpas (de|du|d'|un|une|le|la|l')", r"\bne pas\b", r"\bn'y a (pas|aucun|aucune)\b", r"\baucune?\b", r"\bsans\b", r"\bjamais\b",
                     r"\bavait\b", r"\bavaient\b", r"\bobtenu\b", r"\ba été obtenir\b", r"\ba pu\b", r"\bont pu\b", r"\bce qui amène\b", r"\bsur ce\b",
                     r"\bno\b", r"\bnot\b", r"n't\b", r"\bwithout\b", r"\bwas\b", r"\bwere\b", r"\bhad\b", r"\bnever\b"]
# words just after a cue that make it a recap of something already played
RECAP_AFTER = [r"^\W*(justement|déjà|obtenu|obtenue|avait été|qu'il avait|qu'ils avaient)", r"^\W*(was|had been|already) "]
_NEG_RE = [re.compile(p, re.IGNORECASE) for p in NEGATION_OR_RECAP]
_RECAP_AFTER_RE = [re.compile(p, re.IGNORECASE) for p in RECAP_AFTER]
_CLAUSE_END = re.compile(r"[.!?;:,\n]|\.\.\.")


def is_recap_or_negation(text: str, start: int, end: int) -> bool:
    """True when the cue match text[start:end] sits in a negation or a recap (see NEGATION_OR_RECAP / RECAP_AFTER). Pure; `text` is lower-cased or not."""
    clause = _CLAUSE_END.split(text[:start])[-1]
    window = " ".join(clause.split()[-_BEFORE_WORDS:])[-_BEFORE_CHARS:]
    if any(r.search(window) for r in _NEG_RE):
        return True
    after = text[end:end + _AFTER_CHARS]
    return any(r.search(after) for r in _RECAP_AFTER_RE)


def cue_hits(cue: str, text: str, filtered: bool = True) -> bool:
    """True when `cue` has at least one match in `text` that survives the recap filter (filtered=False: raw regex hits)."""
    use = filtered and cue in FILTERED_CUES
    for p in _COMPILED[cue]:
        for m in p.finditer(text):
            if not use or not is_recap_or_negation(text, m.start(), m.end()):
                return True
    return False


def find_speech_cues(text: str, sport: str | None = None) -> list[str]:
    """The cues a transcript contains, in the order of CUES. With a known `sport` only the cues valid for it (CUE_SPORTS); with None or ''
    (sport not detected yet) all of them, as before. On a soccer stream the scoring cues also skip negations and recaps (see is_recap_or_negation)."""
    sport = " ".join((sport or "").lower().split())
    soccer = sport == _SOCCER
    return [cue for cue in _COMPILED
            if (not sport or cue_valid_for(cue, sport)) and cue_hits(cue, text, filtered=soccer)]


def cue_valid_for(cue: str, sport: str | None) -> bool:
    """True when the spoken cue means something in this sport (an unknown sport accepts every cue)."""
    sport = " ".join((sport or "").lower().split())
    sports = CUE_SPORTS.get(cue)
    return not sport or sports is None or sport in sports


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
