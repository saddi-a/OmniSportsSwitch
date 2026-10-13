"""Per-sport overrides of the scoring settings ([scoring.football], [weights."american football"] in settings.toml)."""
import copy
import dataclasses
import logging

import pytest

from daemon import game_state, settings
from daemon.cues import speech_cue_weight
from daemon.leverage import leverage
from daemon.scoring import OVERRIDABLE_SCORING, Config, Scorer, Weights, sport_key
from daemon.server import Server
from daemon.settings import SETTINGS_PATH, apply_changes, load_settings, reset_all
from daemon.tests.test_scoring import make, roar, steady, warm_up


@pytest.fixture(autouse=True)
def restore_profiles():
    saved = copy.deepcopy(game_state.PROFILES)
    yield
    game_state.PROFILES.clear()
    game_state.PROFILES.update(saved)


def load(tmp_path, toml: str):
    path = tmp_path / "s.toml"
    path.write_text(toml, encoding="utf-8")
    return load_settings(path)


# --- no override: nothing changes --------------------------------------------------------------------------------

def test_without_overrides_every_sport_gets_the_config_itself():
    cfg = Config()
    for sport in (None, "", "football", "American football", "basketball", "generic", "cricket"):
        assert cfg.for_sport(sport) is cfg


def test_the_shipped_settings_file_has_no_override_values_only_a_commented_example(tmp_path):
    text = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "# [scoring.football]" in text and "# [weights.football]" in text
    shipped = tmp_path / SETTINGS_PATH.name
    shipped.write_bytes(SETTINGS_PATH.read_bytes())
    s = load_settings(shipped)
    assert s.scoring.sport_scoring == {} and s.scoring.sport_weights == {}
    assert dataclasses.asdict(s.scoring) == dataclasses.asdict(Config())   # overrides are no dataclass fields


def test_scores_are_identical_with_or_without_a_sport_lookup():
    scores = []
    for sport_of in (None, lambda tab: "football", lambda tab: "american football", lambda tab: None):
        s = make()
        s.sport_of = sport_of
        now = warm_up(s)
        now = roar(s, 2, now)
        s.set_anticipation(2, now, 0.3)
        scores.append(s.components(2, now))
    assert all(c == scores[0] for c in scores)


# --- an override applies only to its sport ------------------------------------------------------------------------

def test_an_override_applies_only_to_its_sport_and_the_rest_follow_the_shared_value():
    cfg = Config()
    cfg.sport_scoring["football"] = {"buildup_min": 0.5}
    cfg.sport_weights["football"] = {"crowd": 3.0}
    assert cfg.for_sport("football").buildup_min == 0.5 and cfg.for_sport("Football ").weights.crowd == 3.0
    assert cfg.for_sport("football").buildup_gap == cfg.buildup_gap          # not overridden: shared
    assert cfg.for_sport("football").weights.surge == cfg.weights.surge
    for other in ("american football", "basketball", "generic", None):
        assert cfg.for_sport(other).buildup_min == cfg.buildup_min and cfg.for_sport(other).weights.crowd == 1.0


def test_a_shared_edit_reaches_other_sports_at_once_and_does_not_clobber_an_override():
    cfg = Config()
    cfg.sport_scoring["football"] = {"buildup_min": 0.5}
    cfg.sport_weights["football"] = {"crowd": 3.0}
    football, basketball = cfg.for_sport("football"), cfg.for_sport("basketball")   # views taken BEFORE the edits stay live
    cfg.buildup_min, cfg.buildup_gap = 0.9, 0.2
    cfg.weights.crowd, cfg.weights.surge = 2.0, 0.6
    assert basketball.buildup_min == 0.9 and basketball.weights.crowd == 2.0
    assert football.buildup_min == 0.5 and football.weights.crowd == 3.0          # override kept
    assert football.buildup_gap == 0.2 and football.weights.surge == 0.6           # a shared value it does not override: new value


def test_the_popup_edit_path_keeps_overrides_and_reset_all_leaves_them_alone(tmp_path):
    s = load(tmp_path, "[scoring.football]\nmin_score = 0.9\n[weights.football]\ncrowd = 3\n")
    assert apply_changes(s, {"scoring.min_score": 0.5, "weights.crowd": 2.0}) == {}
    assert s.scoring.for_sport("basketball").min_score == 0.5 and s.scoring.for_sport("basketball").weights.crowd == 2.0
    assert s.scoring.for_sport("football").min_score == 0.9 and s.scoring.for_sport("football").weights.crowd == 3.0
    reset_all(s)
    assert s.scoring.min_score == Config().min_score and s.scoring.weights.crowd == 1.0
    assert s.scoring.for_sport("football").min_score == 0.9 and s.scoring.for_sport("football").weights.crowd == 3.0
    shown = {i["id"]: i["value"] for _, items in [(g["title"], g["items"]) for g in settings.describe(s)] for i in items}
    assert shown["scoring.min_score"] == Config().min_score   # the popup shows the shared values


# --- the file -----------------------------------------------------------------------------------------------------

def test_a_sport_table_is_read_with_quoted_names_and_collapsed_spaces(tmp_path):
    s = load(tmp_path, '[scoring.football]\nbuildup_min = 0.5\n[scoring."American  Football"]\ncue_alone_cap = 0.2\n'
                       '[weights.basketball]\nmusic = -1\n[scoring]\nmin_gap = 0.2\n')
    assert s.scoring.sport_scoring == {"football": {"buildup_min": 0.5}, "american football": {"cue_alone_cap": 0.2}}
    assert s.scoring.sport_weights == {"basketball": {"music": -1.0}}
    assert s.scoring.min_gap == 0.2 and s.scoring.buildup_min == Config().buildup_min   # the shared values are untouched


def test_mistakes_in_a_sport_table_are_ignored_with_a_warning(tmp_path, caplog):
    toml = ('[scoring.football]\nbuildup_min = "high"\nbuildup_mn = 1\nwindow_s = 9\nfeed_mode = 1\nweights = 2\nbuildup_gap = true\nmin_score = 0.6\n'
            '[scoring.cricket]\nmin_score = 0.1\n[scoring.generic]\nmin_score = 0.1\n[weights.football]\nnope = 1\ncrowd = "x"\ncue_chance = 0.3\n')
    with caplog.at_level(logging.WARNING):
        s = load(tmp_path, toml)
    assert s.scoring.sport_scoring == {"football": {"min_score": 0.6}}
    assert s.scoring.sport_weights == {"football": {"cue_chance": 0.3}}
    text = caplog.text
    for word in ("buildup_min", "buildup_mn", "window_s", "feed_mode", "weights", "buildup_gap", "cricket", "generic", "nope", "crowd"):
        assert word in text, word
    assert "cannot differ per sport" in text
    assert s.scoring.window_s == Config().window_s and s.scoring.feed_mode == 0    # nothing leaked into the shared values


def test_overridable_keys_are_real_config_fields_and_the_server_wide_ones_are_not_in_the_list():
    names = {f.name for f in dataclasses.fields(Config)}
    assert OVERRIDABLE_SCORING <= names
    for shared in ("window_s", "baseline_s", "warmup_s", "stale_s", "min_dwell_s", "manual_hold_s", "feed_mode", "speech_interval_s",
                   "classifier_target_rms", "event_s", "idle_music", "ad_penalty", "field_goal_strength"):
        assert shared not in OVERRIDABLE_SCORING
    assert sport_key("  American   FOOTBALL ") == "american football" and sport_key("cricket") == "generic" and sport_key(None) == "generic"


# --- read sites ---------------------------------------------------------------------------------------------------

def _crowd_scorer(sport_of):
    s = make()
    s.sport_of = sport_of
    return s


def test_the_stream_score_uses_the_weights_of_its_own_sport():
    s = _crowd_scorer(lambda tab: {2: "football", 3: "basketball"}.get(tab))
    s.config.sport_weights["football"] = {"crowd": 2.0}
    now = warm_up(s)
    roar(s, 2, now)
    now = roar(s, 3, now)
    football, basketball = s.components(2, now), s.components(3, now)
    assert football["score"] > basketball["score"] * 1.5 and football["crowd"] == basketball["crowd"]


def test_a_challenger_is_judged_by_its_own_sports_min_score():
    for sport, swaps in (("football", False), ("basketball", True), (None, True)):
        s = _crowd_scorer(lambda tab, sport=sport: sport if tab == 2 else None)
        s.config.sport_scoring["football"] = {"min_score": 5.0}
        now = warm_up(s)
        now = roar(s, 2, now)
        steady(s, 1, 0.05, 60, 5)
        assert (s.decide(now) is not None) is swaps, sport


def test_the_goal_hold_belongs_to_the_stream_that_scored():
    s = _crowd_scorer(lambda tab: "football" if tab == 1 else None)
    s.config.sport_scoring["football"] = {"goal_hold_s": 100.0}
    now = warm_up(s)
    s.set_event(1, now, goal=True)
    s.set_event(2, now, goal=True)
    assert s.goal_hold_left(1, now + 60) == pytest.approx(40.0) and s.goal_hold_left(2, now + 60) == 0.0


def test_the_hold_break_scores_are_the_challengers():
    s = _crowd_scorer(lambda tab: "football" if tab == 2 else None)
    s.config.sport_scoring["football"] = {"goal_hold_break": 0.0}
    now = warm_up(s)
    s.set_event(1, now, goal=True)   # a goal on MAIN (tab 1): its hold runs
    assert s.take_main_block(2, now + 5) is None                              # tab 2 is football: its (zero) break score is met
    assert s.take_main_block(3, now + 5).startswith("goal on MAIN")           # tab 3 uses the shared 0.8


def test_the_cue_boost_time_of_a_stream_uses_its_sports_value():
    s = _crowd_scorer(lambda tab: "football" if tab == 2 else None)
    s.config.sport_scoring["football"] = {"anticipation_s": 100.0}
    now = warm_up(s)
    s.set_anticipation(2, now, 0.3, strong=True)
    s.set_anticipation(3, now, 0.3, strong=True)
    for tab in (2, 3):
        later = steady(s, tab, 0.05, now, 50)
    assert s.components(2, later)["cue"] > 0.1 and s.components(3, later)["anticipation"] == 0.0


def test_spoken_cue_weights_follow_the_sports_factor_and_weights():
    cfg = Config()
    cfg.sport_scoring["football"] = {"speech_cue_factor": 0.5}
    cfg.sport_weights["football"] = {"cue_chance": 0.4}
    view = cfg.for_sport("football")
    assert speech_cue_weight("chance", "football", view.weights, view.speech_cue_factor) == pytest.approx(0.2)
    shared = cfg.for_sport("basketball")
    assert speech_cue_weight("chance", "football", shared.weights, shared.speech_cue_factor) == pytest.approx(0.25)


def test_the_leverage_limits_come_from_the_sport_of_the_game():
    cfg = Config()
    cfg.sport_scoring["football"] = {"lev_soccer_close": 3.0, "lev_soccer_blowout": 6.0}
    assert leverage("football", (2, 0), 1, 85, cfg) == pytest.approx(1.0)     # two goals apart is close for this override
    assert leverage("football", (2, 0), 1, 85, Config()) < 1.0
    assert leverage("basketball", (80, 79), 4, None, cfg) == leverage("basketball", (80, 79), 4, None, Config())


def _buildup_server(sport, level=0.46):
    server = Server()
    for t in (1, 2):
        server.handle_message({"type": "register", "tab_id": t, "is_main": t == 1})
        for i in range(120):
            server.scorer.add_audio(t, 0.05, i * 0.5)
    server.sports[2] = sport
    server.buildup.note(2, 59.0, level)
    return server


def test_the_buildup_tracker_uses_the_build_up_level_of_the_streams_sport():
    taken = {}
    for override in (None, 0.9):
        server = _buildup_server("football")
        if override:
            server.scorer.config.sport_scoring["football"] = {"buildup_min": override}
        c = server.scorer.components(2, 60.0)
        taken[override] = server._check_buildup(2, c, 60.0) is not None
    assert taken == {None: True, 0.9: False}
    server = _buildup_server("football")
    server.scorer.config.sport_scoring["basketball"] = {"buildup_min": 0.9}       # another sport's override changes nothing
    assert server._check_buildup(2, server.scorer.components(2, 60.0), 60.0) is not None


def test_a_shared_buildup_edit_reaches_football_at_once_unless_it_overrides_it():
    server = _buildup_server("football")
    server.scorer.config.buildup_min = 0.9      # a popup edit
    assert server._check_buildup(2, server.scorer.components(2, 60.0), 60.0) is None
    server = _buildup_server("football")
    server.scorer.config.sport_scoring["football"] = {"buildup_min": 0.3}
    server.scorer.config.buildup_min = 0.9
    assert server._check_buildup(2, server.scorer.components(2, 60.0), 60.0) is not None
