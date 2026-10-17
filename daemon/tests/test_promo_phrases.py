"""DAZN event-break placeholders and promo screens count as ads (live 2026-10-17: tab 983 tile 1 showed 'ADVERTISING', 'EVENT BREAK IN PROGRESS',
'EVENTO EM INTERVALO', 'INTERRUPCION DEL EVENTO EN CURSO', Game Pass and DAZN promos and none was taken for an ad). The promo phrases count only
with NO scoreboard on screen: a live game frame also carries 'This programme contains US advertising...' under its scorebug."""
import pytest

from daemon.game_state import AD_PHRASES, PROMO_PHRASES, GameTracker

SCOREBUG = ["TEV", "16", "Ist & GOAL", "7 TexaS", "4-2", "27"]


def readings(texts, clocks=(), n=3, start=0.0):
    g = GameTracker("American football")
    for i in range(n):
        g.update(start + i * 3.0, list(texts), list(clocks))
    return g


@pytest.mark.parametrize("text", ["EVENT BREAK IN PROGRESS.", "EVENTO EM INTERVALO.", "INTERRUPCION DEL EVENTO EN CURSO.",
                                  "Interrupción del evento en curso"])
def test_the_dazn_event_break_placeholders_are_ads(text):
    g = readings([text])
    assert g.on_ad and g.state == "break"


@pytest.mark.parametrize("texts", [["DA", "ZN", "ADVERTISING", "EXP"], ["NFL GAME PASS"], ["WATCHEVERY GAMEONLY ONDAZN"],
                                   ["IATCHEVERY GAMEONLY ON DAZN"], ["DAZN Boxing", "DA", "ZN", "40.8K", "posts"]])
def test_promo_screens_with_no_scoreboard_are_ads(texts):
    g = readings(texts)
    assert g.on_ad


def test_the_advertising_disclaimer_under_a_live_scorebug_is_not_an_ad():
    texts = ["This programme contains US advertising that is not directed to viewers in your region"] + SCOREBUG
    g = readings(texts, clocks=["12:21"], n=6)
    assert not g.on_ad and g.state == "live"


def test_a_game_pass_logo_beside_a_scoreboard_is_not_an_ad():
    g = readings(["NFL GAME PASS", "PHI", "14", "JAX", "7", "3RD", "9:39"], clocks=["9:39"], n=6)
    assert not g.on_ad


def test_the_lists_are_in_the_defaults():
    assert "event break" in AD_PHRASES and "evento em intervalo" in AD_PHRASES
    assert "advertising" in PROMO_PHRASES and "nfl game pass" in PROMO_PHRASES


def test_one_reading_is_not_enough():
    g = readings(["NFL GAME PASS"], n=1)
    assert not g.on_ad
