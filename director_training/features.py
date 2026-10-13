"""Features = the score components the daemon already has, each tied to ONE named setting.

The daemon's score (daemon/scoring.py `Scorer.components`) is, before the stream weight and the game-state multiplier:

    raw = max(0, w.surge*surge + w.crowd*crowd + w.shout*shout + w.event*event + w.speech*speech + w.music*music + cue_part)
          + w.favourite*(fav - 1) + leverage_weight*lev
    cue_part = w.anticipation * cue_value * cue_fade * clamp(1 + cue_leverage_gain*lev, 0.5, 1.5)   (capped when nothing else is excited)

so raw is LINEAR in the settings below and a logistic regression on these columns gives numbers that map straight back:

    feature column (from a dataset_built row)   setting that it multiplies                where it can live
    surge                                       [weights]  surge                          [weights.<sport>]
    crowd                                       [weights]  crowd                          [weights.<sport>]
    speech                                      [weights]  speech                         [weights.<sport>]
    music                                       [weights]  music                          [weights.<sport>]
    shout                                       [weights]  shout                          [weights.<sport>]
    event                                       [weights]  event                          [weights.<sport>]
    cue   = cue_part / today's w.anticipation   [weights]  anticipation (the cue master)  [weights.<sport>]
    fav   = fav - 1                             [weights]  favourite                      [weights.<sport>]
    lev                                         [scoring]  leverage_weight                [scoring.<sport>]

Not features (they stay as they are): the build-up tracker's own sum (it is built from the same cues, so it would count them twice and its
threshold buildup_min is a different kind of number), the stream weight, the game-state multiplier, ads (rows on an ad are not used).
"""
import dataclasses
import math
import pathlib

from daemon.scoring import STATUS_MULT, sport_key


@dataclasses.dataclass(frozen=True)
class Feature:
    name: str
    section: str      # "weights" or "scoring"
    key: str          # the setting name in that section
    doc: str


FEATURES = (
    Feature("surge", "weights", "surge", "loudness above the stream's own normal"),
    Feature("crowd", "weights", "crowd", "crowd / cheering probability"),
    Feature("speech", "weights", "speech", "commentary probability (negative weight expected)"),
    Feature("music", "weights", "music", "music / ad jingle probability (negative weight expected)"),
    Feature("shout", "weights", "shout", "shouting probability (0 under music)"),
    Feature("event", "weights", "event", "fade of a fresh score change / goal banner / big play"),
    Feature("cue", "weights", "anticipation", "the cue part of the score, per unit of the cue master weight"),
    Feature("fav", "weights", "favourite", "favourite multiplier minus 1"),
    Feature("lev", "scoring", "leverage_weight", "leverage -1 (late blowout) .. +1 (late close game)"),
)
FEATURE_NAMES = tuple(f.name for f in FEATURES)
BY_NAME = {f.name: f for f in FEATURES}
ROW_COLUMNS_NEEDED = ("surge", "crowd", "speech", "music", "shout", "event", "cue_part", "fav", "lev")


def today_values(settings_path, sport: str) -> dict:
    """{feature name: today's value of its setting} as the sport sees it (the shared value or the sport's override). Loads the settings the way
    the daemon does (`daemon.settings.load_settings`: settings.toml + settings.local.json next to it), read-only."""
    cfg = sport_config(settings_path, sport)
    return {f.name: float(getattr(cfg.weights if f.section == "weights" else cfg, f.key)) for f in FEATURES}


def sport_config(settings_path, sport: str):
    """The config view of a sport (live values) from a settings file; read-only."""
    from daemon.settings import load_settings
    return load_settings(pathlib.Path(settings_path)).scoring.for_sport(sport)


def feature_row(row: dict, anticipation_today: float = 1.0) -> list[float] | None:
    """The feature vector of a dataset row, or None when the row cannot be used (no audio reading yet)."""
    for k in ("surge", "crowd", "speech", "music"):
        if row.get(k) is None:
            return None
    cue_part = row.get("cue_part") or 0.0
    return [float(row["surge"]), float(row["crowd"]), float(row["speech"]), float(row["music"]), float(row.get("shout") or 0.0),
            float(row.get("event") or 0.0), float(cue_part) / anticipation_today if anticipation_today else 0.0,
            float(row.get("fav") or 1.0) - 1.0, float(row.get("lev") or 0.0)]


def linear_score(vector: list[float], values: dict) -> float:
    """sum of value*feature for a named dict of settings values (the daemon's raw score without the cap and the clip)."""
    return sum(values[name] * x for name, x in zip(FEATURE_NAMES, vector))


# ---------------------------------------------------------------------------------------------------------------------------
# The daemon's score for a RECORDED row under any settings (used by the shadow evaluation). Mirrors Scorer.components.
# ---------------------------------------------------------------------------------------------------------------------------
def score_from_row(row: dict, cfg) -> float | None:
    """The score the stream of this recorded row would have under `cfg` (a Config or `Config.for_sport` view): the formula of
    `Scorer.components` applied to the recorded signals. None when the row had no audio reading (warm false)."""
    if row.get("surge") is None or row.get("crowd") is None:
        return None
    w = cfg.weights
    surge, crowd, speech, music, shout = (float(row.get(k) or 0.0) for k in ("surge", "crowd", "speech", "music", "shout"))
    event, lev = float(row.get("event") or 0.0), float(row.get("lev") or 0.0)
    cue = row.get("cue") or {}
    fade = float(row.get("cue_fade") or 0.0)
    strong = bool(cue.get("strong"))
    support = w.surge * surge + w.crowd * crowd + w.shout * shout + w.event * event
    cue_part = w.anticipation * float(cue.get("value") or 0.0) * fade * min(max(1.0 + cfg.cue_leverage_gain * lev, 0.5), 1.5)
    if support < cfg.cue_support and not strong:
        room = cfg.min_score - 0.01 - max(0.0, cfg.leverage_weight * lev)
        cue_part = min(cue_part, cfg.cue_alone_cap, max(room, 0.0))
    talk = 0.0 if strong and cue_part > 0 else w.speech * speech
    excitement = max(0.0, support + talk + w.music * music + cue_part)
    raw = excitement + w.favourite * (float(row.get("fav") or 1.0) - 1.0) + cfg.leverage_weight * lev
    state = row.get("state") or "live"
    score = max(raw, 0.0) * float(row.get("weight") if row.get("weight") is not None else 1.0) * STATUS_MULT.get(state, 1.0)
    if row.get("on_ad"):
        return -cfg.ad_penalty
    if row.get("ad"):
        return -cfg.ad_penalty / 2
    return score


class CandidateView:
    """A Config view with candidate numbers on top: `weights` = {setting: value} and `scoring` = {setting: value}. Reads fall through to the
    base view, so a candidate only changes what it names."""

    def __init__(self, base, weights: dict | None = None, scoring: dict | None = None):
        self._base, self._scoring = base, dict(scoring or {})
        self.weights = _Layer(base.weights, weights or {})

    def __getattr__(self, name):
        s = self.__dict__["_scoring"]
        return s[name] if name in s else getattr(self.__dict__["_base"], name)


class _Layer:
    def __init__(self, base, over: dict):
        self._base, self._over = base, dict(over)

    def __getattr__(self, name):
        o = self.__dict__["_over"]
        return o[name] if name in o else getattr(self.__dict__["_base"], name)


def round_setting(x: float) -> float:
    return round(float(x), 3) if math.isfinite(x) else 0.0


__all__ = ["FEATURES", "FEATURE_NAMES", "BY_NAME", "feature_row", "linear_score", "score_from_row", "CandidateView", "today_values", "sport_config",
           "sport_key", "round_setting", "ROW_COLUMNS_NEEDED"]
