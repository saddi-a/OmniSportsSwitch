"""How much a cue before a likely score is worth, by what it is about.

A cue is a banner or graphic (`GameTracker.anticipation`). Reviews (VAR, "under review", challenge) are worth more or less by their
SUBJECT: a possible goal or touchdown, a penalty kick or a red card (top) > an interception, fumble or turnover (high) > offside, pass
interference or a foul (mid) > no subject (low). The subject comes from the banner text itself ("VAR: POSSIBLE GOAL", "PENALTY CHECK"),
else from the commentary heard in the last seconds (`SpeechMemory`). Other cues have one weight per group: scoring chances (red zone,
goal to go, field goal, free throw, penalty kick in soccer) and minor cues (corner, flag, 4th down...). Pure logic, callers pass `now`.
The weights are `Weights.cue_*`; `Scorer` scales them by the stream's leverage and the master weight `Weights.anticipation`.
"""
import re

REVIEW_CUES = frozenset({"review", "under review", "replay review", "challenge", "var", "var check", "révision", "contestation", "assistance vidéo"})
CHANCE_CUES = frozenset({"red zone", "goal to go", "yard line", "field goal", "field goal attempt", "tir de placement", "free throw", "free throws",
                         "lancer franc", "lancers francs", "tiro libre", "tiros libres", "tiro livre", "buzzer beater", "penalty shootout",
                         "tir au but", "tirs au but"})
PENALTY_KICK_CUES = frozenset({"penalty", "pénalty", "penalti", "pênalti"})   # a chance in soccer; in American football a flag (minor)
AMERICAN_FOOTBALL = "american football"

TIER_ORDER = ("top", "high", "mid")
# Subject words on a banner (English, French, Spanish, Portuguese). Whole words, any case.
_TOP = r"goal|but|buts|touchdown|td|gol|golo|red card|carton rouge|tarjeta roja|cart[aã]o vermelho|penalty|p[ée]nalty|penalti|p[êe]nalti"
_HIGH = r"interceptions?|intercept[ée]e?|fumbles?|turnovers?|turn over|perte de balle|balle perdue|p[ée]rdida"
_MID = r"offsides?|hors[- ]jeu|fuera de juego|impedimento|pass interference|interf[ée]rence|holding|fouls?|fautes?|faltas?|contact|catch|r[ée]ception"
_SUBJECT = {tier: re.compile(r"(?<![a-zà-ÿ])(?:" + words + r")(?![a-zà-ÿ])", re.IGNORECASE) for tier, words in (("top", _TOP), ("high", _HIGH), ("mid", _MID))}
_PENALTY_RE = re.compile(r"(?<![a-zà-ÿ])(?:penalty|p[ée]nalty|penalti|p[êe]nalti)(?![a-zà-ÿ])", re.IGNORECASE)
_RED_CARD_RE = re.compile(r"red card|carton rouge|sent off|sending off|dismissed|expuls|exclu|exclusion|[àa] dix|tarjeta roja|cart[aã]o vermelho", re.IGNORECASE)


def _is_american_football(sport: str | None) -> bool:
    return (sport or "").strip().lower() == AMERICAN_FOOTBALL


def group_of(cue: str, sport: str | None) -> str:
    """"review", "chance" or "minor"."""
    if cue in REVIEW_CUES:
        return "review"
    if cue in CHANCE_CUES or (cue in PENALTY_KICK_CUES and not _is_american_football(sport) and (sport or "").strip().lower() != "basketball"):
        return "chance"
    return "minor"


def banner_tier(text: str, sport: str | None) -> str | None:
    """The tier of what a banner says is under review, or None when it names no subject ("UNDER REVIEW", "VAR CHECK")."""
    for tier in TIER_ORDER:
        if _SUBJECT[tier].search(text):
            if tier == "top" and _is_american_football(sport) and _PENALTY_RE.search(text) and not _SUBJECT["top"].search(_PENALTY_RE.sub(" ", text)):
                return "mid"   # "PENALTY CHECK" in American football is a flag, not a penalty kick
            return tier
    return None


def speech_tiers(cues: list[str], text: str, sport: str | None) -> list[str]:
    """The review tiers a commentary reading points to, by sport; an empty list when it names no subject."""
    american = _is_american_football(sport)
    soccer_like = not american and (sport or "").strip().lower() != "basketball"
    found = []
    for cue in cues:
        if cue == "touchdown" and (american or sport is None):
            found.append("top")
        elif cue == "interception" and (american or sport is None):
            found.append("high")
        elif cue == "goal" and soccer_like:
            found.append("top")
        elif cue == "penalty" and soccer_like:
            found.append("top")
        elif cue == "card" and soccer_like:
            found.append("top" if _RED_CARD_RE.search(text) else "mid")   # a yellow card is a foul
        elif cue in ("offside", "foul") or (cue in ("penalty", "flag") and american):
            found.append("mid")
    return [tier for tier in TIER_ORDER if tier in found]


class SpeechMemory:
    """The review subjects the commentary named in the last `window_s` seconds, per tab."""

    def __init__(self, window_s: float = 20.0):
        self.window_s = window_s
        self._heard: dict[int, list[tuple[float, list[str]]]] = {}

    def record(self, tab_id: int, cues: list[str], text: str, sport: str | None, now: float) -> None:
        tiers = speech_tiers(cues, text, sport)
        kept = [(t, x) for t, x in self._heard.get(tab_id, []) if now - t <= self.window_s]
        if tiers:
            kept.append((now, tiers))
        self._heard[tab_id] = kept

    def tier(self, tab_id: int, now: float) -> str | None:
        recent = [tier for t, tiers in self._heard.get(tab_id, []) if 0 <= now - t <= self.window_s for tier in tiers]
        return next((tier for tier in TIER_ORDER if tier in recent), None)   # the highest: contradictory commentary counts for its biggest subject


def cue_weight(cue: str, banner: str, speech_tier: str | None, sport: str | None, weights) -> float:
    """The weight of one cue (before leverage and the master scale). `banner` is the text on screen, `speech_tier` what `SpeechMemory.tier` says."""
    group = group_of(cue, sport)
    if group == "chance":
        return weights.cue_chance
    if group == "minor":
        return weights.cue_minor
    tier = banner_tier(banner, sport) or speech_tier   # the subject written on the banner wins over what was heard
    return {"top": weights.cue_review_top, "high": weights.cue_review_high, "mid": weights.cue_review_mid}.get(tier, weights.cue_review_low)
