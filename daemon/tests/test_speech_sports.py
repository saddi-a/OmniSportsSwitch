"""Spoken cues are classified by sport (speech.CUE_SPORTS): a cue outside its sports is dropped when the sport is known."""
import pytest

from daemon.speech import CUES, CUE_SPORTS, find_speech_cues

SOCCER, AMERICAN = "football", "american football"


def test_relegation_zone_in_soccer_is_not_a_red_zone():
    text = "ils sortent de la zone rouge au classement"
    assert find_speech_cues(text, SOCCER) == []
    assert find_speech_cues(text, AMERICAN) == ["red zone"]
    assert find_speech_cues("They are in the red zone now.", AMERICAN) == ["red zone"]
    assert find_speech_cues("They are in the red zone now.", SOCCER) == []


def test_touche_is_not_a_touchdown_in_soccer():
    assert find_speech_cues("Touché ! il a touché le ballon", SOCCER) == []
    assert find_speech_cues("Touché !", AMERICAN) == ["touchdown"]


SAMPLES = {"touchdown": "touchdown!", "red zone": "red zone", "goal line": "at the goal line", "field goal": "a field goal try",
           "fourth down": "fourth down", "flag": "there is a flag", "interception": "intercepted", "penalty awarded": "the referee awards a penalty",
           "free kick": "free kick", "corner": "a corner", "var": "the VAR", "card": "yellow card", "post": "off the post", "chance": "what a chance",
           "goal": "goooaal"}


@pytest.mark.parametrize("cue", sorted(SAMPLES))
def test_sport_only_cues_are_dropped_elsewhere(cue):
    for sport in (SOCCER, AMERICAN):
        assert (cue in find_speech_cues(SAMPLES[cue], sport)) == (sport in CUE_SPORTS[cue]), (cue, sport)
    assert cue in find_speech_cues(SAMPLES[cue], None)
    assert cue in find_speech_cues(SAMPLES[cue], "")


def test_shared_cues_pass_in_every_sport():
    for text, cue in (("let's go to the review", "review"), ("here is the replay", "replay"), ("a bad foul", "foul"), ("a timeout", "timeout")):
        for sport in (SOCCER, AMERICAN, "hockey", None):
            assert cue in find_speech_cues(text, sport), (cue, sport)


def test_unknown_sport_keeps_everything_and_names_are_normalised():
    text = "Red zone, corner kick"
    assert find_speech_cues(text) == find_speech_cues(text, None) == ["red zone", "corner"]
    assert find_speech_cues("corner kick", "  Football ") == ["corner"]
    assert find_speech_cues("red zone", "American  Football") == ["red zone"]
    assert find_speech_cues("a corner", "hockey") == []   # a known sport without that cue


def test_every_cue_is_classified():
    assert set(CUE_SPORTS) == set(CUES)


def test_tries_his_luck_phrases_are_football_chances_only():
    for text in ("Siddiq Chérif va tenter sa chance !", "il tente sa chance", "he tries his luck", "she has a go", "he takes a shot", "he lets fly", "fires at goal now"):
        assert "chance" in find_speech_cues(text, SOCCER), text
        assert "chance" not in find_speech_cues(text, AMERICAN), text
        assert "chance" not in find_speech_cues(text, "hockey"), text
    assert "chance" not in find_speech_cues("he shoots from the corner of the box", SOCCER)
