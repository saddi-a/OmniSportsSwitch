"""Recap / negation filter on the soccer scoring cues (speech.is_recap_or_negation) and the football-only chance phrases."""
import pytest

from daemon.speech import find_speech_cues

SOCCER, AMERICAN = "football", "american football"


@pytest.mark.parametrize("text", [
    "Il n'y a pas de faute", "pas de faute sur Maraud", "pas du tout hors jeu", "Un penalty obtenu et le but du break",
    "ce qui amène ce penalty dans cette entame", "Sur le penalty justement frappé par Béraldo", "Il choisit le même côté sur ce penalty",
    "On a pu jouer le penalty que va Rastrelia", "de ne pas conseiller un pénalty", "There was no foul", "He did not get a yellow card",
    "without a foul there", "it had been a penalty", "Siddiqi qui a été obtenir ce penalty", "qui avait déjà un carton jaune",
])
def test_recaps_and_negations_are_dropped(text):
    assert find_speech_cues(text, SOCCER) == []


@pytest.mark.parametrize("text,cue", [
    ("Et c'est un pénalty sifflé par John Brou", "penalty"), ("C'est penalty, sans aucun doute", "penalty"), ("Il siffle un penalty", "penalty"),
    ("The referee awarded a penalty", "penalty"), ("Oh quelle occasion", "chance"), ("juste à côté", "chance"), ("juste au-dessus", "chance"),
    ("pour le deuxième poteau", "post"), ("il tente sa chance", "chance"), ("coup franc pour Grimes", "free kick"),
    ("hors-jeu, le drapeau est levé", "offside"), ("une faute dans la surface", "foul"), ("avec Greenlaw a été expulsé", "card"),
    ("va obtenir un coup franc", "free kick"),
])
def test_genuine_cues_still_fire(text, cue):
    assert cue in find_speech_cues(text, SOCCER)


def test_filter_is_per_match():
    assert find_speech_cues("pas de faute. Mais là, faute sur Mbappé", SOCCER) == ["foul"]
    assert find_speech_cues("un penalty obtenu, et là un autre penalty", SOCCER) == ["penalty"]


def test_filter_only_looks_before_the_match_in_the_same_clause():
    assert "penalty" in find_speech_cues("Pas de panique, penalty", SOCCER)
    assert "foul" in find_speech_cues("sans hésiter, faute", SOCCER)


def test_filter_is_soccer_only_and_skips_review_cues():
    assert find_speech_cues("pas de faute", AMERICAN) == ["foul"]
    assert find_speech_cues("pas de review", SOCCER) == ["review"]
    assert find_speech_cues("sans replay", SOCCER) == ["replay"]
    assert find_speech_cues("pas de faute") == ["foul"]       # unknown sport: as before


@pytest.mark.parametrize("text", ["Rodoli et le contre-pied !", "Akliouche dans la surface de réparation", "he is in the box", "dans la surface"])
def test_new_football_chance_phrases(text):
    assert find_speech_cues(text, SOCCER) == ["chance"]
    assert "chance" not in find_speech_cues(text, "basketball")
    assert "chance" not in find_speech_cues(text, AMERICAN)
