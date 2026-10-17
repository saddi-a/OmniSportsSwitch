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
    saved_promo = list(game_state.PROMO_PHRASES)
    saved_big = list(game_state.BIG_PLAY_WORDS)
    saved_replay = list(game_state.REPLAY_WORDS)
    yield
    game_state.PROFILES.clear()
    game_state.PROFILES.update(saved)
    game_state.AD_PHRASES[:] = saved_phrases
    game_state.PROMO_PHRASES[:] = saved_promo
    game_state.BIG_PLAY_WORDS[:] = saved_big
    game_state.REPLAY_WORDS[:] = saved_replay


def test_the_shipped_settings_file_equals_the_built_in_defaults(tmp_path):
    shipped = tmp_path / SETTINGS_PATH.name   # a copy, so the user's own settings.local.json (popup edits) does not count
    shipped.write_bytes(SETTINGS_PATH.read_bytes())
    settings = load_settings(shipped)
    assert dataclasses.asdict(settings.scoring) == dataclasses.asdict(Config())
    assert dataclasses.asdict(settings.focus) == dataclasses.asdict(FocusConfig())
    assert game_state.profile_for("football") == game_state.SportProfile(20, 45)
    assert game_state.profile_for("American football") == game_state.SportProfile(240, 90)
    assert game_state.profile_for(None) == game_state.SportProfile(120, 60)
    assert game_state.AD_PHRASES == ["commercial break", "publicitaria", "intervalo comercial", "publicité", "publicite", "publicitaire",
                                     "publicidade", "pausa comercial", "ad break", "advertisement", "right back", "volvemos",
                                     "de retour dans", "watch now", "ondazn", "event break", "evento em intervalo",
                                     "interrupcion del evento", "interrupción del evento"]
    assert game_state.PROMO_PHRASES == ["advertising", "on dazn", "nfl game pass", "watchevery", "daznboxing", "dazn boxing"]
    assert game_state.BIG_PLAY_WORDS == ["takeaway", "interception", "intercepted", "fumble", "sack", "red card",
                                         "carton rouge", "penalty kick"]
    assert game_state.REPLAY_WORDS == ["replay", "instant replay", "action replay", "slo-mo", "slow motion", "ralenti", "reprise", "reprise vidéo",
                                      "reprise video", "repetición", "repeticion", "ralentí", "câmera lenta", "camera lenta", "cámara lenta",
                                      "camara lenta", "repetição", "repeticao"]


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


# ---- editing from the popup ----------------------------------------------------------------------------------

from daemon import settings as settings_module  # noqa: E402


def test_every_setting_in_the_popup_layout_exists_with_a_default(tmp_path):
    settings = load_settings(tmp_path / "none.toml")
    ids = [item["id"] for group in settings_module.describe(settings) for item in group["items"]]
    assert len(ids) == len(set(ids)) > 30
    for group in settings_module.describe(settings):
        for item in group["items"]:
            assert item["value"] == item["default"] and not item["changed"]


def test_a_change_applies_at_once_and_is_saved_next_to_the_settings_file(tmp_path):
    path = tmp_path / "settings.toml"
    settings = load_settings(path)
    errors = settings_module.apply_changes(settings, {"scoring.threshold": 0.6, "weights.speech": -0.5,
                                                      "sports.football.freeze_s": 30,
                                                      "banners.big_play_words": ["Touchdown", " Pick six "]})
    assert errors == {}
    assert settings.scoring.threshold == 0.6 and settings.scoring.weights.speech == -0.5
    assert game_state.profile_for("football").freeze_s == 30
    assert game_state.BIG_PLAY_WORDS == ["touchdown", "pick six"]
    again = load_settings(path)                     # a new start finds the same values
    assert again.scoring.threshold == 0.6 and again.overrides["sports.football.freeze_s"] == 30.0


def test_a_value_equal_to_the_default_is_not_kept(tmp_path):
    settings = load_settings(tmp_path / "settings.toml")
    settings_module.apply_changes(settings, {"scoring.threshold": 0.6})
    settings_module.apply_changes(settings, {"scoring.threshold": Config().threshold})
    assert settings.overrides == {} and not (tmp_path / "settings.local.json").exists()


def test_bad_values_are_refused_and_change_nothing(tmp_path):
    settings = load_settings(tmp_path / "settings.toml")
    errors = settings_module.apply_changes(settings, {"scoring.threshold": "high", "scoring.min_gap": -1,
                                                      "weights.crowd": float("nan"), "scoring.nope": 1,
                                                      "banners.break_phrases": ["ok", ""], "scoring.window_s": True})
    assert set(errors) == {"scoring.threshold", "scoring.min_gap", "weights.crowd", "scoring.nope",
                           "banners.break_phrases", "scoring.window_s"}
    assert settings.overrides == {} and settings.scoring.threshold == Config().threshold


def test_weights_may_be_negative_but_timings_may_not(tmp_path):
    settings = load_settings(tmp_path / "settings.toml")
    assert settings_module.apply_changes(settings, {"weights.music": -2}) == {}
    assert "scoring.stale_s" in settings_module.apply_changes(settings, {"scoring.stale_s": -2})


def test_reset_restores_every_default_and_removes_the_saved_file(tmp_path):
    settings = load_settings(tmp_path / "settings.toml")
    settings_module.apply_changes(settings, {"scoring.threshold": 0.9, "sports.basketball.freeze_s": 5,
                                             "banners.break_phrases": ["x"]})
    settings_module.reset_all(settings)
    assert settings.scoring.threshold == Config().threshold
    assert game_state.profile_for("basketball").freeze_s == 180
    assert "commercial break" in game_state.AD_PHRASES
    assert not (tmp_path / "settings.local.json").exists()


def test_a_damaged_saved_file_never_stops_the_start(tmp_path):
    (tmp_path / "settings.local.json").write_text("{not json", encoding="utf-8")
    assert load_settings(tmp_path / "settings.toml").overrides == {}
    (tmp_path / "settings.local.json").write_text('{"scoring.threshold": "x", "scoring.min_gap": 0.3}', encoding="utf-8")
    assert load_settings(tmp_path / "settings.toml").overrides == {"scoring.min_gap": 0.3}
