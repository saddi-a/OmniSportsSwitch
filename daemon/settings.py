"""Loads settings.toml (project root) into the scoring, multiview and per-sport rules.

Everything is optional: a missing file, section or key keeps the built-in default. Unknown keys and values of the
wrong type are ignored with a warning in the log, so a typo never stops the daemon.

The extension popup edits the same settings live: `describe` lists them (with help text and defaults) for the
popup, `apply_changes` validates and applies edits at once, and the edits are kept in settings.local.json
(next to settings.toml, never edited by hand) and applied on top of settings.toml at every start.
"""
import dataclasses
import json
import logging
import math
import pathlib
import tomllib

from daemon import game_state
from daemon.multiview import FocusConfig
from daemon.scoring import Config

SETTINGS_PATH = pathlib.Path(__file__).resolve().parent.parent / "settings.toml"
log = logging.getLogger("daemon")


@dataclasses.dataclass
class Settings:
    scoring: Config
    focus: FocusConfig
    overrides: dict = dataclasses.field(default_factory=dict)  # setting id -> value changed in the popup
    local_path: pathlib.Path | None = None                      # where the overrides are kept


def _apply(target, values: dict, section: str) -> None:
    names = {f.name for f in dataclasses.fields(target)}
    for key, value in values.items():
        if key not in names:
            log.warning("settings: unknown key [%s] %s ignored", section, key)
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            log.warning("settings: [%s] %s must be a number, ignored", section, key)
        elif dataclasses.is_dataclass(getattr(target, key)):
            log.warning("settings: [%s] %s is a section, ignored", section, key)
        else:
            setattr(target, key, float(value))


def load_settings(path: pathlib.Path = SETTINGS_PATH) -> Settings:
    scoring, focus = Config(), FocusConfig()
    settings = Settings(scoring, focus, local_path=path.with_name(LOCAL_NAME))
    _read_toml(settings, path)
    _read_local(settings)
    return settings


def _read_toml(settings: Settings, path: pathlib.Path) -> None:
    scoring, focus = settings.scoring, settings.focus
    if not path.exists():
        return
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        log.warning("settings: %s could not be read (%s); using defaults", path.name, e)
        return

    _apply(scoring, data.get("scoring", {}), "scoring")
    _apply(scoring.weights, data.get("weights", {}), "weights")
    _apply(focus, data.get("multiview", {}), "multiview")

    for name, values in data.get("sports", {}).items():
        key = name.strip().lower()
        known = game_state.PROFILES.get(key)
        if known is None:
            log.warning("settings: unknown sport [sports.%s] ignored (use football, american football, basketball or generic)", name)
            continue
        edited = dataclasses.replace(known, **{k: float(v) for k, v in values.items()
                                               if k in ("freeze_s", "no_clock_s") and isinstance(v, (int, float))})
        for k in values:
            if k not in ("freeze_s", "no_clock_s"):
                log.warning("settings: unknown key [sports.%s] %s ignored", name, k)
        game_state.PROFILES[key] = edited
    banners = data.get("banners", {})
    for key, target in (("break_phrases", game_state.AD_PHRASES), ("big_play_words", game_state.BIG_PLAY_WORDS)):
        phrases = banners.get(key)
        if isinstance(phrases, list) and all(isinstance(p, str) and p.strip() for p in phrases):
            target[:] = [p.strip().casefold() for p in phrases]
        elif phrases is not None:
            log.warning("settings: [banners] %s must be a list of non-empty strings, ignored", key)
    for key in set(banners) - {"break_phrases", "big_play_words"}:
        log.warning("settings: unknown key [banners] %s ignored", key)
    known_unknown = set(data) - {"scoring", "weights", "multiview", "sports", "banners"}
    for section in known_unknown:
        log.warning("settings: unknown section [%s] ignored", section)


# ---------------------------------------------------------------------------------------------------------------
# Editing settings from the popup
# ---------------------------------------------------------------------------------------------------------------
LOCAL_NAME = "settings.local.json"
_DEFAULT_PROFILES = dict(game_state.PROFILES)
_DEFAULT_PHRASES = {"break_phrases": list(game_state.AD_PHRASES), "big_play_words": list(game_state.BIG_PLAY_WORDS)}

_SPORT_LABELS = {"football": "Football (soccer)", "american football": "American football",
                 "basketball": "Basketball", "generic": "Other sports"}

# (group title, [(setting id, label, help text)]) - this is what the popup shows, in this order.
LAYOUT = [
    ("When to swap", [
        ("scoring.threshold", "Win margin", "A challenger must beat the stream on MAIN by this fraction (0.4 = 40% better)."),
        ("scoring.min_gap", "Minimum gap", "...and by at least this much in score."),
        ("scoring.min_score", "Minimum score", "A challenger scoring below this never wins, so loudness alone cannot cause a swap."),
        ("scoring.min_dwell_s", "Time between swaps (s)", "No swap happens sooner than this after the last one."),
        ("scoring.warmup_s", "Warm-up (s)", "A new stream cannot win until it has been watched this long."),
    ]),
    ("How a stream is judged", [
        ("scoring.window_s", "Loudness window (s)", "Loudness is averaged over this many seconds."),
        ("scoring.baseline_s", "Normal loudness window (s)", "Each stream's normal loudness is averaged over about this long."),
        ("scoring.surge_cap", "Loudness surge cap", "Upper limit of the loudness signal (1.0 = loudness doubled)."),
        ("scoring.event_s", "Goal boost (s)", "How long a goal or touchdown boosts a stream (it fades)."),
        ("scoring.big_play_strength", "Big play strength", "A big play banner counts this much of a goal (1.0 = the same as a goal)."),
        ("scoring.stale_s", "Silence limit (s)", "A stream with no audio for this long is ignored."),
    ]),
    ("Ads", [
        ("scoring.idle_music", "Music level for an ad", "MAIN counts as showing an ad when the music probability is at least this."),
        ("scoring.leverage_weight", "Close / late game bonus", "A late close game adds up to this to a stream's score; a late blowout takes it away. 0 turns it off."),
        ("scoring.lev_us_football_close", "American football: close (points)", "At most this many points apart counts as a close game."),
        ("scoring.lev_us_football_blowout", "American football: blowout (points)", "This many points apart or more counts as a blowout."),
        ("scoring.lev_basketball_close", "Basketball: close (points)", "At most this many points apart counts as a close game."),
        ("scoring.lev_basketball_blowout", "Basketball: blowout (points)", "This many points apart or more counts as a blowout."),
        ("scoring.lev_soccer_close", "Soccer: close (goals)", "At most this many goals apart counts as a close game."),
        ("scoring.lev_soccer_blowout", "Soccer: blowout (goals)", "This many goals apart or more counts as a blowout."),
        ("scoring.recent_ad_s", "No hopping between ads (s)", "MAIN is not handed to a stream that was showing an ad this recently."),
        ("scoring.audio_ad_margin", "Music over speech for an ad", "A music-only ad needs music to exceed speech by this much, so stadium music under commentary is not taken for an ad."),
        ("scoring.audio_ad_memory_s", "Ad memory (s)", "A stream that sounded like an ad stays flagged this long."),
        ("scoring.ad_penalty", "Ad penalty", "An ad in the picture scores minus this (music-only ads: half of it)."),
    ]),
    ("Signal weights", [
        ("weights.surge", "Loudness surge", "Weight of loudness above the stream's own normal."),
        ("weights.crowd", "Crowd", "Weight of cheering, crowd and applause sound."),
        ("weights.speech", "Commentary", "Weight of speech (negative: talking is not excitement)."),
        ("weights.music", "Music", "Weight of music, ads and jingles (negative)."),
        ("weights.event", "Score change", "Weight of a fresh goal or touchdown read from the scoreboard."),
        ("weights.favourite", "Favourite bonus", "Bonus per +1 of a favourite team's multiplier (a x2 team gets +0.4)."),
    ]),
    ("Multiview tiles", [
        ("multiview.switch_ratio", "Switch ratio", "Another tile must beat the current one by this factor."),
        ("multiview.min_dwell_s", "Time between tile changes (s)", "Minimum time between tile changes in one tab."),
        ("multiview.event_weight", "Score change weight", "How much a fresh score change counts for a tile."),
        ("multiview.crowd_weight", "Crowd weight", "How much crowd noise counts for the tile that has sound."),
        ("multiview.floor", "Interest floor", "Every live tile has at least this much interest."),
        ("multiview.ad_penalty", "Ad penalty", "A tile showing an ad gets negative interest, so focus moves off it."),
    ]),
    ("Break detection by sport", [
        (f"sports.{name}.{key}", f"{_SPORT_LABELS[name]}: {text}", help_)
        for name in _DEFAULT_PROFILES
        for key, text, help_ in (("freeze_s", "clock stopped (s)", "A game clock unchanged for this long means a break."),
                                 ("no_clock_s", "no clock (s)", "A scoreboard with no readable clock for this long means a break."))
    ]),
    ("Banner phrases", [
        ("banners.break_phrases", "Ad break wording", "Text a player shows during an ad break (any case, inside a text). Add your service's wording."),
        ("banners.big_play_words", "Big play banners", "Banners flashed for a big play (the text must be just that phrase)."),
    ]),
]
_IDS = {item[0] for _, items in LAYOUT for item in items}


def _split(setting_id: str) -> tuple[str, str | None, str]:
    if setting_id.startswith("sports."):
        name, key = setting_id[len("sports."):].rsplit(".", 1)
        return "sports", name, key
    section, key = setting_id.split(".", 1)
    return section, None, key


def _fresh(setting_id: str):
    """The built-in default of a setting."""
    section, name, key = _split(setting_id)
    if section == "scoring":
        return getattr(Config(), key)
    if section == "weights":
        return getattr(Config().weights, key)
    if section == "multiview":
        return getattr(FocusConfig(), key)
    if section == "sports":
        return getattr(_DEFAULT_PROFILES[name], key)
    return list(_DEFAULT_PHRASES[key])


def get_value(settings: Settings, setting_id: str):
    section, name, key = _split(setting_id)
    if section == "scoring":
        return getattr(settings.scoring, key)
    if section == "weights":
        return getattr(settings.scoring.weights, key)
    if section == "multiview":
        return getattr(settings.focus, key)
    if section == "sports":
        return getattr(game_state.PROFILES[name], key)
    return list(game_state.AD_PHRASES if key == "break_phrases" else game_state.BIG_PLAY_WORDS)


def _set(settings: Settings, setting_id: str, value) -> None:
    """Checks the value and applies it; raises ValueError (with a message for the user) if it is not acceptable."""
    if setting_id not in _IDS:
        raise ValueError("unknown setting")
    section, name, key = _split(setting_id)
    if section == "banners":
        if not isinstance(value, list) or not all(isinstance(p, str) and p.strip() for p in value):
            raise ValueError("must be a list of non-empty phrases")
        target = game_state.AD_PHRASES if key == "break_phrases" else game_state.BIG_PLAY_WORDS
        target[:] = [p.strip().casefold() for p in value]
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("must be a number")
    if section != "weights" and value < 0:
        raise ValueError("cannot be negative")
    value = float(value)
    if section == "scoring":
        setattr(settings.scoring, key, value)
    elif section == "weights":
        setattr(settings.scoring.weights, key, value)
    elif section == "multiview":
        setattr(settings.focus, key, value)
    else:
        game_state.PROFILES[name] = dataclasses.replace(game_state.PROFILES[name], **{key: value})


def describe(settings: Settings) -> list[dict]:
    """Every setting with its label, help text, current value and default, grouped for the popup."""
    return [{"title": title, "items": [
        {"id": sid, "label": label, "help": help_, "kind": "list" if sid.startswith("banners.") else "number",
         "value": get_value(settings, sid), "default": _fresh(sid), "changed": sid in settings.overrides}
        for sid, label, help_ in items]} for title, items in LAYOUT]


def apply_changes(settings: Settings, changes: dict) -> dict[str, str]:
    """Apply edits live and remember them. Returns {setting id: reason} for the edits that were refused."""
    errors = {}
    for sid, value in changes.items():
        try:
            _set(settings, sid, value)
        except (ValueError, KeyError, TypeError) as e:
            errors[str(sid)] = str(e)
            continue
        current = get_value(settings, sid)
        if current == _fresh(sid):
            settings.overrides.pop(sid, None)
        else:
            settings.overrides[sid] = current
    _write_local(settings)
    return errors


def reset_all(settings: Settings) -> None:
    for sid in _IDS:
        _set(settings, sid, _fresh(sid))
    settings.overrides.clear()
    _write_local(settings)


def _write_local(settings: Settings) -> None:
    path = settings.local_path
    if path is None:
        return
    try:
        if settings.overrides:
            path.write_text(json.dumps(settings.overrides, indent=1, ensure_ascii=False), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError as e:
        log.warning("settings: could not save %s (%s)", path.name, e)


def _read_local(settings: Settings) -> None:
    path = settings.local_path
    if path is None or not path.exists():
        return
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("not an object")
    except (ValueError, OSError) as e:
        log.warning("settings: %s could not be read (%s); ignored", path.name, e)
        return
    for sid, value in saved.items():
        try:
            _set(settings, sid, value)
            settings.overrides[sid] = get_value(settings, sid)
        except (ValueError, KeyError, TypeError) as e:
            log.warning("settings: saved value %s ignored (%s)", sid, e)
