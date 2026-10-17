"""A spoken interception / fumble / sack call feeds the score in American football (live: Tennessee-Alabama 22:47:13, the French commentary
said "l'interception ... Bama", the sound jumped (surge 1.00) but the stream scored 0.29 and stayed off MAIN)."""
import pytest

from daemon.cues import speech_cue_weight
from daemon.scoring import Config, Scorer, Weights

from .test_scoring import roar


def test_a_spoken_interception_weighs_the_high_tier_in_american_football():
    w = Weights()
    assert speech_cue_weight("interception", "american football", w, 1.0) == pytest.approx(w.cue_review_high)
    assert speech_cue_weight("interception", "american football", w, 0.5) == pytest.approx(w.cue_review_high * 0.5)


def test_it_does_not_count_in_other_sports_or_when_the_sport_is_unknown_or_switched_off():
    w = Weights()
    for sport in ("football", "basketball", None, ""):
        assert speech_cue_weight("interception", sport, w, 1.0) is None
    assert speech_cue_weight("interception", "american football", w, 0.0) is None


def test_the_other_american_football_cues_are_unchanged():
    w = Weights()
    for cue in ("touchdown", "red zone", "fourth down", "flag"):
        assert speech_cue_weight(cue, "american football", w, 1.0) is None


def make():
    s = Scorer(Config(confirm_s=0.0, min_dwell_s=0))
    s.register(1, is_main=True)
    s.register(2)
    return s


def test_with_a_surge_the_spoken_interception_takes_main_from_a_quiet_game():
    s = make()
    for tab in (1, 2):
        for t in range(0, 60):
            s.add_audio(tab, 0.05, float(t))
    now = 60.0
    s.set_anticipation(2, now, speech_cue_weight("interception", "american football", Weights(), 1.0))
    now = roar(s, 2, now, seconds=3, level=0.15, crowd=0.1)    # the sound jumps with the call
    c = s.components(2, now)
    assert c["score"] >= s.config.min_score


def test_the_cue_alone_never_takes_main():
    s = make()
    for tab in (1, 2):
        for t in range(0, 60):
            s.add_audio(tab, 0.05, float(t))
    now = 60.0
    s.set_anticipation(2, now, speech_cue_weight("interception", "american football", Weights(), 1.0))
    for t in range(60, 63):
        s.add_audio(2, 0.05, float(t))
        s.set_tags(2, 0.02, 0.8, 0.05, float(t))               # commentary only (a stat mentioned in passing): no surge, no crowd
    c = s.components(2, 63.0)
    assert c["score"] < s.config.min_score


# ---- scoring-bugs-62: field goal calls count, "sack" only as a real sack (live: Auburn 23:02:24, "and not take that sack") ----

def test_a_spoken_field_goal_call_weighs_a_chance_in_american_football():
    w = Weights()
    assert speech_cue_weight("field goal", "american football", w, 1.0) == pytest.approx(w.cue_chance)
    assert speech_cue_weight("field goal", "football", w, 1.0) is None          # not a soccer cue
    assert speech_cue_weight("field goal", "basketball", w, 1.0) is None        # "field goal %" is a statistic
    assert speech_cue_weight("field goal", "american football", w, 0.0) is None


def test_the_kick_call_with_its_distance_is_a_field_goal_cue():
    from daemon.speech import find_speech_cues
    for text in ("Alex McPherson is four of six on the year. This from 49 yards to give Auburn the lead.",
                 "They are in field goal range", "the field goal attempt is good"):
        assert "field goal" in find_speech_cues(text, "american football"), text


def test_sack_counts_only_as_a_real_sack_call():
    from daemon.speech import find_speech_cues
    for text in ("and not take that sack", "he can avoid the sack", "a sack is a sack"):
        assert "interception" not in find_speech_cues(text, "american football"), text
    for text in ("he is sacked!", "a sack by Walker", "sack on third down", "what a fumble", "intercepted at the 30"):
        assert "interception" in find_speech_cues(text, "american football"), text
