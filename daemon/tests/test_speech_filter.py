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
    ("il tente sa chance", "chance"), ("coup franc pour Grimes", "free kick"),
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
    assert "chance" not in find_speech_cues(text, "hockey")
    assert "chance" not in find_speech_cues(text, AMERICAN)


# 2026-10-13: 2924 football transcripts. "premier/second poteau" is a cross or a marking position (about 35 of 41 `post` hits): it is its own
# medium cue `post area`; `post` is left for a ball that hits the woodwork. "sous la barre dans la lucarne" is a goal already scored.
@pytest.mark.parametrize("text", [
    "vers le second poteau", "Petit premier poteau", "Le 2e poteau, il était seul", "pour deuxième poteau", "Clinique au second poteau",
    "the cross to the far post", "a ball to the near post", "Faisez poteau sortant", "centre rentrant sur le 1er poteau",
])
def test_positional_post_is_a_post_area_cue_not_a_post(text):
    cues = find_speech_cues(text, SOCCER)
    assert "post area" in cues and "post" not in cues
    assert "post area" not in find_speech_cues(text, AMERICAN) and "post area" not in find_speech_cues(text, "hockey")


@pytest.mark.parametrize("text", [
    "Il touche le poteau", "c'est sur la barre transversale, quelle action", "Les poteaux ont sauvé le gardien", "he hit the post", "the bar denied him",
    "le ballon rebondit sur son poteau",
])
def test_ball_on_the_woodwork_is_still_a_post(text):
    assert "post" in find_speech_cues(text, SOCCER)


def test_a_goal_under_the_bar_is_no_post():
    assert "post" not in find_speech_cues("ça va sous la barre dans la lucarne, superbe geste", SOCCER)


@pytest.mark.parametrize("text", [
    "Centre dévié, c'est capté par le gardien", "La position de Boteman, et c'est capté cette fois-ci", "Ce ballon repoussé par la défense centrale",
    "Bobby Thomas repousse presque du torse", "il sera repoussé par ce mur très hermétique", "Les Whistles qui repoussent seulement pour un temps",
    "c'est repoussé par l'autre capitaine de cette pelouse",
])
def test_a_catch_or_a_clearance_without_a_shot_is_no_chance(text):
    assert "chance" not in find_speech_cues(text, SOCCER)


@pytest.mark.parametrize("text", [
    "prépare sa frappe, c'est capté en deux temps par le gardien", "en position de frappe Basouman, c'est capté par le gardien",
    "belle reprise de volée, repoussé par Thomas", "le tir de Barnes est repoussé", "Beau travail, le gardien repousse", "sa frappe est capté par Karl",
])
def test_a_shot_then_a_save_is_still_a_chance(text):
    assert "chance" in find_speech_cues(text, SOCCER)


def test_the_shot_must_be_in_the_same_clause():
    assert "chance" not in find_speech_cues("Belle frappe tout à l'heure. Centre, c'est capté par le gardien", SOCCER)
