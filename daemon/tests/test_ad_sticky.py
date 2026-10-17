"""An ad break stays an ad until the game is seen again (live: STR-PSG session 2026-10-17 21:59, tabs 964/983: the picture state flickered
AD -> break -> live every few seconds inside ONE ad break because frames without the countdown cleared the 8 s ad memory, and MAIN was
handed to a stream that was still in its commercials)."""
from daemon.game_state import GameTracker
from daemon.scoring import Config

AD = ["Un hôtel ibis", "où un Heartist vous attend"]
BOARD = ["PSG", "1", "STR", "0", "35'"]


def countdown(g, t, start=120):
    return g.update(t, AD + [f"{start - int(t) % 120}:00"], [f"1:{(60 - int(t) % 60):02d}"])


def into_ad(g, t0=0.0):
    t = t0
    for _ in range(4):                       # the countdown seen alone with the ad's words: confirmed on the second reading
        countdown(g, t)
        t += 3
    assert g.on_ad
    return t


def test_the_ad_stays_an_ad_while_the_screen_shows_nothing_readable():
    g = GameTracker("football")
    t = into_ad(g)
    for _ in range(10):                      # 30 s of frames with no clock and no scoreboard: the old 8 s memory ran out here
        g.update(t, ["Profitez,", "on est là"], [])
        t += 3
    assert g.on_ad and g.state == "break"


def test_three_scoreboard_readings_end_the_ad():
    g = GameTracker("football")
    t = into_ad(g)
    for i in range(3):
        g.update(t, BOARD, ["35:10"])
        t += 3
    assert not g.on_ad


def test_one_scoreboard_reading_is_not_enough():
    g = GameTracker("football")
    t = into_ad(g)
    g.update(t, BOARD, ["35:10"])
    g.update(t + 3, ["Profitez,", "on est là"], [])
    assert g.on_ad


def test_a_long_ad_break_ends_by_itself_after_the_cap():
    g = GameTracker("football", Config(ad_sticky_s=60.0))
    t = into_ad(g)
    last = t
    while t < last + 70:
        g.update(t, ["Profitez,", "on est là"], [])
        t += 3
    assert not g.on_ad


def test_the_sticky_ad_can_be_switched_off():
    g = GameTracker("football", Config(ad_sticky_s=0.0))
    t = into_ad(g)
    for _ in range(10):
        g.update(t, ["Profitez,", "on est là"], [])
        t += 3
    assert not g.on_ad


def test_a_game_without_any_ad_is_unchanged():
    g = GameTracker("football")
    for i in range(10):
        g.update(i * 3.0, BOARD, [f"35:{10 + i * 3}"])
    assert not g.on_ad and g.state == "live"
