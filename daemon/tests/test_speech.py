from daemon.speech import find_speech_cues

# real transcripts of 2026-10-11 (daemon.log): goal calls the 'goal' cue missed all day, and ordinary talk it must not take for one.
GOAL_CALLS = [
    "C'est toujours lui le phare dans le brouillard. Erling Allende, comme si de rien n'était Manchester City, prend les devants.",    # 18:56:43
    "Le crochet Cherqui jusqu'au bout, un petit tour de magie de Ryan Cherqui qui porte le score à 3 buts à 0.",                     # 19:06:24
    "Le classement de la première ligne. Oui, toujours solide à leader les citizens et à lente qui inscrit son 6e but en 6 journées",   # 18:57:58
    "Manchester City ouvre la marque juste avant la pause", "il double la mise pour Manchester City", "et le PSG revient au score",
    "Il met le deuxième pour les Citizens", "What a goal from Haaland!", "He scores! Erling Haaland!", "Arsenal takes the lead at the Emirates",
    "Chelsea doubles their lead", "and Villa pull one back", "Goooooal!", "Gooool de Vinicius", "un golazo de Neymar", "Gooolo do Benfica",
]
TALK = [
    "La chance de pouvoir égaliser lui a filé entre les doigts dans cette partie",   # 2026-10-11 20:23:41: infinitive, a miss, not a goal
    "Il touche quatre ballons et il marque le but. Il va falloir être vigilant sur toute la totalité du match avec une concentration énorme",   # 18:27:05
    "en seconde période et puis tout a été très vite avec ce carton rouge donné à Gravenberg et l'ouverture du score coup cd",                   # 19:00:24 recap
    "et contrée par McAllister et Arlene Allende. Clinique au second poteau. Prend les bons appuis pour être très stable au sol et envoyer ce ballon au fond des filets.",
    "Jérémy Doku, de nouveau, dangereux. Frappe de Jérémy Doku, exceptionnel !", "Et on l'a vu, Yraula qui va lancer le jeune Créignoni, une minute terrain pour...",
    "C'est un autre match qui commence. City 11 contre 10.", "Doku", "Qu'est-ce qu'il y a ici ?", "Le corner pour Liverpool.",
    "Ça va pour le corner back des Eagles qui vient intercepter. Trevor Lawrence a su c'est seulement la troisième fois cette saison",
    "Ce genre de manque de répétition, voilà ce que ça peut donner. Ça fait la pression, vous êtes en goal line, c'est un jeu d'agressions",
    "Il est pour le bord de l'attaquer, j'ai l'a mis deux coups. J'ai le quitté laissé pour Fardio Latalona", "Fin de troisième carton, les Eagles, qui ont une opportunité en or.",
    "The goalkeeper makes a save", "He takes the lead pass and goes wide", "He scored last week against them", "a goal kick for Liverpool", "touchdown pour les Eagles",
]


def test_goal_calls_are_the_goal_cue():
    for text in GOAL_CALLS:
        assert "goal" in find_speech_cues(text), text


def test_ordinary_commentary_is_not_the_goal_cue():
    for text in TALK:
        assert "goal" not in find_speech_cues(text), text
