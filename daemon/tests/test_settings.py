import copy
import dataclasses
import logging

import pytest

from daemon import game_state
from daemon.multiview import FocusConfig
from daemon.scoring import Config
from daemon.settings import SETTINGS_PATH, load_settings


@pytest.fixture(autouse=True)
def restore_profiles():
    saved = copy.deepcopy(game_state.PROFILES)
    saved_phrases = list(game_state.AD_PHRASES)
    saved_big = list(game_state.BIG_PLAY_WORDS)
    yield
    game_state.PROFILES.clear()
    game_state.PROFILES.update(saved)
    game_state.AD_PHRASES[:] = saved_phrases
    game_state.BIG_PLAY_WORDS[:] = saved_big


def test_the_shipped_settings_file_equals_the_built_in_defaults():
    settings = load_settings(SETTINGS_PATH)
    assert dataclasses.asdict(settings.scoring) == dataclasses.asdict(Config())
    assert dataclasses.asdict(settings.focus) == dataclasses.asdict(FocusConfig())
    assert game_state.profile_for("football") == game_state.SportProfile(20, 45)
    assert game_state.profile_for("American football") == game_state.SportProfile(240, 90)
    assert game_state.profile_for(None) == game_state.SportProfile(120, 60)
    assert game_state.AD_PHRASES == ["commercial break", "publicitaria", "intervalo comercial", "publicité",
                                     "publicite", "ad break", "advertisement", "right back"]
    assert game_state.BIG_PLAY_WORDS == ["takeaway", "interception", "intercepted", "fumble", "sack", "red card",
                                         "carton rouge", "penalty kick"]


def test_missing_file_gives_defaults(tmp_path):
    settings = load_settings(tmp_path / "nope.toml")
    assert settings.scoring.min_score == Config().min_score


def test_values_are_applied(tmp_path):
    path = tmp_path / "s.toml"
    path.write_text("[scoring]\nmin_score = 0.5\nmin_dwell_s = 45\n[weights]\ncrowd = 2\n"
                    "[multiview]\nmin_dwell_s = 10\n[sports.football]\nfreeze_s = 30\n", encoding="utf-8")
    s = load_settings(path)
    assert s.scoring.min_score == 0.5 and s.scoring.min_dwell_s == 45
    assert s.scoring.weights.crowd == 2
    assert s.focus.min_dwell_s == 10
    assert game_state.profile_for("football") == game_state.SportProfile(30, 45)   # no_clock_s kept


def test_mistakes_are_ignored_with_a_warning(tmp_path, caplog):
    path = tmp_path / "s.toml"
    path.write_text('[scoring]\nmin_scor = 1\nthreshold = "high"\n[nonsense]\nx = 1\n[sports.cricket]\nfreeze_s = 1\n',
                    encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        s = load_settings(path)
    assert s.scoring.threshold == Config().threshold
    text = caplog.text
    assert "min_scor" in text and "threshold" in text and "nonsense" in text and "cricket" in text


def test_a_broken_file_falls_back_to_defaults(tmp_path, caplog):
    path = tmp_path / "s.toml"
    path.write_text("[scoring\nmin_score = ", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        s = load_settings(path)
    assert s.scoring.min_score == Config().min_score
    assert "could not be read" in caplog.text


def test_break_phrases_can_be_replaced_and_mistakes_are_reported(tmp_path, caplog):
    path = tmp_path / "s.toml"
    path.write_text('[banners]\nbreak_phrases = ["Werbung", "  Pub  "]\ntypo = 1\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        load_settings(path)
    assert game_state.AD_PHRASES == ["werbung", "pub"]
    assert "typo" in caplog.text
    path.write_text('[banners]\nbreak_phrases = "nope"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        load_settings(path)
    assert game_state.AD_PHRASES == ["werbung", "pub"]            # unchanged
    assert "must be a list" in caplog.text
