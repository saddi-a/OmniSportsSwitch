import pytest

from daemon.cues import SpeechMemory, banner_tier, cue_weight, group_of, speech_tiers
from daemon.scoring import Weights

W = Weights()
AF, SOCCER = "american football", "football"


# ---- the groups of the cues -------------------------------------------------------------------------------------------------------------

def test_cue_groups():
    for cue in ("review", "under review", "var", "var check", "challenge", "révision", "assistance vidéo"):
        assert group_of(cue, AF) == "review"
    for cue in ("red zone", "goal to go", "yard line", "field goal", "field goal attempt", "free throw", "buzzer beater", "penalty shootout", "tirs au but"):
        assert group_of(cue, AF) == "chance"
    for cue in ("corner", "flag", "4th down", "big gain", "added time", "timeout", "free kick", "drapeau", "coup franc"):
        assert group_of(cue, SOCCER) == "minor"


def test_a_penalty_banner_is_a_chance_in_soccer_but_a_foul_in_american_football():
    assert group_of("penalty", SOCCER) == "chance" and group_of("pénalty", None) == "chance"
    assert group_of("penalty", AF) == "minor" and group_of("pénalité", AF) == "minor"


# ---- the subject of a banner ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,sport,tier", [
    ("VAR: POSSIBLE GOAL", SOCCER, "top"), ("REVIEW: TOUCHDOWN", AF, "top"), ("VAR - PENALTY", SOCCER, "top"), ("PENALTY CHECK", SOCCER, "top"),
    ("RED CARD CHECK", SOCCER, "top"), ("VAR : BUT POSSIBLE", SOCCER, "top"), ("CARTON ROUGE", SOCCER, "top"),
    ("REVIEW: INTERCEPTION", AF, "high"), ("FUMBLE REVIEW", AF, "high"), ("REVIEW: TURNOVER", AF, "high"),
    ("OFFSIDE CHECK", SOCCER, "mid"), ("VAR: HORS-JEU", SOCCER, "mid"), ("REVIEW: PASS INTERFERENCE", AF, "mid"), ("REVIEW: FOUL", AF, "mid"),
    ("PENALTY CHECK", AF, "mid"),       # a flag in American football, not a penalty kick
    ("UNDER REVIEW", AF, None), ("VAR CHECK", SOCCER, None), ("REVIEW", AF, None), ("", AF, None),
])
def test_the_subject_of_a_banner(text, sport, tier):
    assert banner_tier(text, sport) == tier


# ---- the subject from the commentary ---------------------------------------------------------------------------------------------------------------

def test_speech_subjects_by_sport():
    assert speech_tiers(["touchdown"], "touchdown pour les Eagles", AF) == ["top"]
    assert speech_tiers(["interception"], "sa première interception de la saison", AF) == ["high"]
    assert speech_tiers(["goal"], "but !", SOCCER) == ["top"]
    assert speech_tiers(["card"], "il prend un carton rouge", SOCCER) == ["top"]
    assert speech_tiers(["card"], "carton jaune pour lui", SOCCER) == ["mid"]
    assert speech_tiers(["offside"], "hors-jeu", SOCCER) == ["mid"]
    assert speech_tiers(["penalty"], "c'est un penalty", SOCCER) == ["top"]
    assert speech_tiers(["foul", "corner"], "faute", SOCCER) == ["mid"]          # a corner says nothing about a review


def test_commentary_misheard_as_a_card_in_american_football_is_ignored():
    # live 18:05:18: "ce troisième carton" is the third quarter ("quart-temps") misheard; there are no cards in American football
    assert speech_tiers(["interception", "card"], "...de ce troisième carton. ... Après avoir intercepté cette balle...", AF) == ["high"]
    assert speech_tiers(["card"], "fin de troisième carton", AF) == []


# ---- the weight of a cue -----------------------------------------------------------------------------------------------------------------------------

def test_the_subject_on_the_banner_wins_over_the_commentary():
    assert cue_weight("review", "REVIEW: OFFSIDE", "top", SOCCER, W) == W.cue_review_mid
    assert cue_weight("var", "VAR: POSSIBLE GOAL", None, SOCCER, W) == W.cue_review_top


def test_a_bare_review_takes_its_subject_from_the_commentary_heard_lately():
    assert cue_weight("under review", "UNDER REVIEW", "high", AF, W) == W.cue_review_high      # the 18:06:32 interception review
    assert cue_weight("under review", "UNDER REVIEW", "top", AF, W) == W.cue_review_top
    assert cue_weight("under review", "UNDER REVIEW", "mid", AF, W) == W.cue_review_mid


def test_a_review_with_no_subject_is_low():
    assert cue_weight("under review", "UNDER REVIEW", None, AF, W) == W.cue_review_low
    assert cue_weight("var", "VAR", None, SOCCER, W) == W.cue_review_low


def test_the_other_groups_do_not_look_at_the_subject():
    assert cue_weight("red zone", "RED ZONE", "mid", AF, W) == W.cue_chance
    assert cue_weight("flag", "FLAG", "top", AF, W) == W.cue_minor
    assert cue_weight("corner", "CORNER", None, SOCCER, W) == W.cue_minor
    assert cue_weight("penalty", "PENALTY", None, SOCCER, W) == W.cue_chance


def test_the_tiers_go_down():
    assert W.cue_review_top > W.cue_review_high > W.cue_review_mid > W.cue_review_low >= W.cue_minor


# ---- what was heard in the last seconds -------------------------------------------------------------------------------------------------------------

def test_the_commentary_of_the_last_20_seconds_counts_and_older_does_not():
    m = SpeechMemory(window_s=20.0)
    m.record(7, ["interception"], "sa première interception de la saison", AF, now=100.0)
    assert m.tier(7, 110.0) == "high"
    assert m.tier(7, 119.9) == "high"
    assert m.tier(7, 121.0) is None            # a subject from long ago is not what is being reviewed
    assert m.tier(8, 110.0) is None            # another tab


def test_the_live_lines_of_18_06():
    m = SpeechMemory(window_s=20.0)
    m.record(485660207, ["interception"], "La puissance est la plus éteignée, mais l'interception sera validée.", AF, now=18 * 3600 + 6 * 60 + 5)
    m.record(485660207, ["interception", "corner"], "Ça va pour le corner back des Eagles qui vient intercepter.", AF, now=18 * 3600 + 6 * 60 + 11.8)
    m.record(485660207, ["card"], "Il est intercepté le Cotterback des Jaguars. Fin de troisième carton, les Eagles", AF, now=18 * 3600 + 6 * 60 + 18.3)
    m.record(485660207, ["replay", "interception"], "On voit la situation précédente au ralenti ... cette interception.", AF, now=18 * 3600 + 6 * 60 + 31.3)
    assert m.tier(485660207, 18 * 3600 + 6 * 60 + 32.7) == "high"       # "anticipation under review" at 18:06:32


def test_contradictory_commentary_takes_the_highest_subject():
    m = SpeechMemory(window_s=20.0)
    m.record(1, ["interception"], "interception", AF, now=0.0)
    m.record(1, ["touchdown"], "touchdown !", AF, now=5.0)         # a pick-six
    assert m.tier(1, 8.0) == "top"


def test_a_memory_with_no_subject_stays_empty():
    m = SpeechMemory(window_s=20.0)
    m.record(1, ["replay", "corner", "post"], "au ralenti", SOCCER, now=0.0)
    assert m.tier(1, 1.0) is None


# ---- the distance to the end zone (American football): a graded chance cue --------------------------------------------------------------------------

from daemon.cues import end_zone_factor, zone_weight        # noqa: E402
from daemon.scoring import Config                           # noqa: E402


def test_the_factor_is_highest_near_the_goal_line_and_lowest_around_the_ten():
    cfg = Config()
    assert end_zone_factor(1, 1, True, cfg) == 1.0
    assert end_zone_factor(3, 2, True, cfg) == 1.0                    # 1 to 3 yards: the top
    assert end_zone_factor(10, 1, True, cfg) == 0.0                   # around the 10: the bottom of the scale
    assert end_zone_factor(25, 1, False, cfg) == 0.0
    factors = [end_zone_factor(y, 1, True, cfg) for y in range(1, 12)]
    assert factors == sorted(factors, reverse=True)                   # never rises with the distance
    assert 0.0 < end_zone_factor(6, 1, True, cfg) < 1.0


def test_the_fourth_down_at_the_goal_line_is_the_top_whatever_the_settings():
    cfg = Config(cue_zone_near_yd=0.5)
    assert end_zone_factor(1, 4, True, cfg) == 1.0
    assert end_zone_factor(1, 3, True, cfg) < 1.0


def test_an_unknown_distance_gives_none_so_the_caller_falls_back_to_the_plain_cue():
    cfg = Config()
    assert end_zone_factor(None, 1, True, cfg) is None
    assert end_zone_factor(None, None, False, cfg) is None
    assert end_zone_factor(-2, 1, True, cfg) is None                  # nonsense


def test_the_zone_weight_runs_from_far_to_near_and_the_plain_cue_is_kept_without_a_distance():
    w, cfg = Weights(), Config()
    assert zone_weight("goal to go", None, w) == w.cue_chance                       # today's yes/no cue
    assert zone_weight("goal to go", 1.0, w) == w.cue_zone_near
    assert zone_weight("yard line", 0.0, w) == w.cue_zone_far
    assert w.cue_zone_far < w.cue_chance < w.cue_zone_near
    assert zone_weight("red zone", 0.5, w) == pytest.approx((w.cue_zone_far + w.cue_zone_near) / 2)
    assert zone_weight("field goal", 1.0, w) == w.cue_chance                        # only the end-zone cues are graded
    assert zone_weight("corner", 1.0, w) == w.cue_minor


def test_the_strongest_end_zone_cue_alone_stays_under_the_minimum_score():
    from daemon.scoring import Scorer
    from daemon.tests.test_scoring import warm_up
    s = Scorer(Config(confirm_s=0.0))
    s.register(1, is_main=True)
    s.register(2)
    t = warm_up(s)
    s.set_leverage(2, 1.0)
    s.set_anticipation(2, t, zone_weight("goal to go", 1.0, s.config.weights))
    c = s.components(2, t)
    assert c["cue"] > 0 and c["score"] < s.config.min_score


# ---- goal phrase + surge: MEASURING ONLY (it only says "would fire"; nothing reads it but a log line) -----------------------------------------------

from daemon.cues import GoalPhraseWatch      # noqa: E402


def test_a_goal_phrase_and_a_surge_within_the_window_would_fire_once():
    w = GoalPhraseWatch(surge_min=0.6, window_s=10.0, cooldown_s=30.0)
    w.surge(7, 0.90, 100.0)                    # the real goal: surge 0.90 at 18:56:36, the call "prend les devants" at 18:56:43
    assert w.phrase(7, 107.0) is True
    assert w.phrase(7, 108.0) is False         # the same goal again (the commentator repeats it): cooldown


def test_the_surge_may_come_after_the_phrase():
    w = GoalPhraseWatch(surge_min=0.6, window_s=10.0)
    assert w.phrase(7, 100.0) is False
    assert w.surge(7, 0.7, 104.0) is True


def test_a_weak_old_or_foreign_surge_does_not_count():
    w = GoalPhraseWatch(surge_min=0.6, window_s=10.0)
    w.surge(7, 0.5, 100.0)
    assert w.phrase(7, 101.0) is False         # too weak
    w.surge(7, 0.9, 120.0)
    assert w.phrase(7, 140.0) is False         # 20 s apart
    w.surge(8, 0.9, 200.0)
    assert w.phrase(7, 201.0) is False         # another tab


def test_a_new_goal_after_the_cooldown_fires_again():
    w = GoalPhraseWatch(surge_min=0.6, window_s=10.0, cooldown_s=30.0)
    w.surge(7, 0.9, 100.0)
    assert w.phrase(7, 101.0) is True
    w.surge(7, 0.9, 200.0)
    assert w.phrase(7, 201.0) is True


def test_a_free_kick_has_its_own_weight_in_soccer():
    from daemon.scoring import Weights
    w = Weights()
    assert w.cue_free_kick == 0.25 and w.cue_free_kick > w.cue_minor
    assert cue_weight("free kick", "free kick", None, "football", w) == w.cue_free_kick
    assert cue_weight("free kick", "free kick", None, None, w) == w.cue_free_kick             # sport unknown
    assert cue_weight("free kick", "free kick", None, "american football", w) == w.cue_minor   # not a soccer cue there
    assert cue_weight("corner", "corner", None, "football", w) == w.cue_minor                  # corners stay minor
