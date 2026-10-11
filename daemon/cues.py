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
FREE_KICK_CUES = frozenset({"free kick", "coup franc", "coups francs"})   # soccer: its own weight (`Weights.cue_free_kick`)
AMERICAN_FOOTBALL = "american football"

ZONE_CUES = frozenset({"goal to go", "yard line", "red zone"})   # the cues graded by the distance to the end zone (American football)
GOAL_LINE_YD = 3.0   # a 4th down this close to the end zone is the strongest cue there is

TIER_ORDER = ("top", "high", "mid")
# Subject words on a banner (English, French, Spanish, Portuguese). Whole words, any case.
_TOP = r"goal|but|buts|touchdown|td|gol|golo|red card|carton rouge|tarjeta roja|cart[aã]o vermelho|penalty|p[ée]nalty|penalti|p[êe]nalti"
_HIGH = r"interceptions?|intercept[ée]e?|fumbles?|turnovers?|turn over|perte de balle|balle perdue|p[ée]rdida"
_MID = r"offsides?|hors[- ]jeu|fuera de juego|impedimento|pass interference|interf[ée]rence|holding|fouls?|fautes?|faltas?|contact|catch|r[ée]ception"
_SUBJECT = {tier: re.compile(r"(?<![a-zà-ÿ])(?:" + words + r")(?![a-zà-ÿ])", re.IGNORECASE) for tier, words in (("top", _TOP), ("high", _HIGH), ("mid", _MID))}
_PENALTY_RE = re.compile(r"(?<![a-zà-ÿ])(?:penalty|p[ée]nalty|penalti|p[êe]nalti)(?![a-zà-ÿ])", re.IGNORECASE)
_RED_CARD_RE = re.compile(r"red card|carton rouge|sent off|sending off|dismissed|expuls|exclu|exclusion|[àa] dix|tarjeta roja|cart[aã]o vermelho", re.IGNORECASE)


SPEECH_SCORING = {"free kick": "free_kick", "chance": "chance", "post": "chance", "penalty": "chance", "goal": "chance"}   # spoken cues that feed the score (soccer)


def speech_cue_weight(cue: str, sport: str | None, weights, factor: float) -> float | None:
    """The weight a SPOKEN cue adds, or None when it does not feed the score: only free kicks, chances, shots on the post, penalties and goal
    calls, only in soccer (or an unknown sport), at `factor` of the banner weight. Corners, fouls and offsides stay log-only."""
    kind = SPEECH_SCORING.get(cue)
    if kind is None or factor <= 0 or (sport or "").strip().lower() not in ("football", ""):
        return None
    return (weights.cue_free_kick if kind == "free_kick" else weights.cue_chance) * factor


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


def end_zone_factor(yards: float | None, down: int | None, goal_to_go: bool, cfg) -> float | None:
    """0..1: how close to the end zone the ball is, from a distance in yards from ANY source (the scorebug's yard line graphic, the feed).
    1.0 at `cfg.cue_zone_near_yd` yards or less (and for a 4th down within 3 yards of the goal line whatever the setting), 0.0 at
    `cfg.cue_zone_far_yd` or more, linear between. None when the distance is not known: the caller keeps today's plain cue.
    `goal_to_go` is informational (the feed knows it too): the factor depends on the distance and the 4th down only."""
    if yards is None or yards < 0:
        return None
    if down == 4 and yards <= GOAL_LINE_YD:
        return 1.0
    near, far = cfg.cue_zone_near_yd, cfg.cue_zone_far_yd
    if far <= near:
        return 1.0 if yards <= near else 0.0
    return min(max((far - yards) / (far - near), 0.0), 1.0)


def zone_weight(cue: str, factor: float | None, weights) -> float:
    """The weight of a chance cue; the end-zone cues run from `cue_zone_far` to `cue_zone_near` with the factor, or keep the plain
    `cue_chance` when the distance is unknown (`factor` None)."""
    if cue in ZONE_CUES and factor is not None:
        return weights.cue_zone_far + (weights.cue_zone_near - weights.cue_zone_far) * factor
    return weights.cue_chance if cue in ZONE_CUES or group_of(cue, None) == "chance" else weights.cue_minor


def cue_weight(cue: str, banner: str, speech_tier: str | None, sport: str | None, weights, zone: float | None = None) -> float:
    """The weight of one cue (before leverage and the master scale). `banner` is the text on screen, `speech_tier` what `SpeechMemory.tier` says."""
    group = group_of(cue, sport)
    if cue in FREE_KICK_CUES and not _is_american_football(sport) and (sport or "").strip().lower() != "basketball":
        return weights.cue_free_kick
    if cue in ZONE_CUES and _is_american_football(sport):
        return zone_weight(cue, zone, weights)   # `zone` = end_zone_factor(...) or None
    if group == "chance":
        return weights.cue_chance
    if group == "minor":
        return weights.cue_minor
    tier = banner_tier(banner, sport) or speech_tier   # the subject written on the banner wins over what was heard
    return {"top": weights.cue_review_top, "high": weights.cue_review_high, "mid": weights.cue_review_mid}.get(tier, weights.cue_review_low)


class GoalPhraseWatch:
    """MEASURING ONLY: a goal phrase in the commentary and a loudness surge on the same tab within `window_s` seconds of each other.
    `phrase(tab, now)` / `surge(tab, value, now)` return True once when the pair is complete (then not again for `cooldown_s`), so the
    server can log "goal phrase + surge would fire" and the user can compare it with the real goals before it is used for anything."""

    def __init__(self, surge_min: float = 0.6, window_s: float = 10.0, cooldown_s: float = 30.0):
        self.surge_min, self.window_s, self.cooldown_s = surge_min, window_s, cooldown_s
        self._phrase_at: dict[int, float] = {}
        self._surge_at: dict[int, float] = {}
        self._fired_at: dict[int, float] = {}

    def phrase(self, tab_id: int, now: float) -> bool:
        self._phrase_at[tab_id] = now
        return self._check(tab_id, now, self._surge_at.get(tab_id))

    def surge(self, tab_id: int, value: float, now: float) -> bool:
        if value < self.surge_min:
            return False
        self._surge_at[tab_id] = now
        return self._check(tab_id, now, self._phrase_at.get(tab_id))

    def _check(self, tab_id: int, now: float, other: float | None) -> bool:
        if other is None or abs(now - other) > self.window_s or now - self._fired_at.get(tab_id, float("-inf")) < self.cooldown_s:
            return False
        self._fired_at[tab_id] = now
        return True
