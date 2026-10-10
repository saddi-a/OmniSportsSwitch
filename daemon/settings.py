"""Loads settings.toml (project root) into the scoring, multiview and per-sport rules.

Everything is optional: a missing file, section or key keeps the built-in default. Unknown keys and values of the
wrong type are ignored with a warning in the log, so a typo never stops the daemon.
"""
import dataclasses
import logging
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
    settings = Settings(scoring, focus)
    if not path.exists():
        return settings
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        log.warning("settings: %s could not be read (%s); using defaults", path.name, e)
        return settings

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
    phrases = banners.get("break_phrases")
    if isinstance(phrases, list) and all(isinstance(p, str) and p.strip() for p in phrases):
        game_state.AD_PHRASES[:] = [p.strip().casefold() for p in phrases]
    elif phrases is not None:
        log.warning("settings: [banners] break_phrases must be a list of non-empty strings, ignored")
    for key in set(banners) - {"break_phrases"}:
        log.warning("settings: unknown key [banners] %s ignored", key)
    known_unknown = set(data) - {"scoring", "weights", "multiview", "sports", "banners"}
    for section in known_unknown:
        log.warning("settings: unknown section [%s] ignored", section)
    return settings
