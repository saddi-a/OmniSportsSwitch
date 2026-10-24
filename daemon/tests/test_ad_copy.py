"""TV commercials (drug ads) are ads (live 2026-10-25 00:06 and 00:15, DAZN multiview tile on FS1: scored 0.30 with the ad's music as a surge and kept
pulling Main). Real OCR texts. One strong phrase, or two different weak words, on two readings in a row; a lone weak word on a game frame is no ad."""
import pytest

from daemon.game_state import AD_COPY_STRONG, AD_COPY_WEAK, GameTracker

YEZTUGO = [
    ["DA", "yeztugo", "ZN", "YEZTUGO is a prescription medicine that is used for HIV-1 PrEP to reduce the risk",
     "of getting HIV-1 in adults and adolescents who weigh at least 77 pounds (at least 35 kg).", "BREAKING NEWS", "UFC", "FS1"],
    ["DA", "yeztugo", "ZN", "2 injections per office visit every 6 months.", "BREAKING NEWS", "UFC", "FSI"],
]
EYLEA = [
    ["DA", "EYLEAHD", "ZN", "(aflibercept) Injection & m9", "For Wet AMD and DME", "The first and only treatment", "that gives you a chance for.",
     "After 3 initial monthly treatments, your specialist will choose a 2 t0 4 month treatment schedule.", "NBA", "WAS (1-1)", "FS1"],
    ["DA", "EYLEAHD", "ZN", "Eye injections like EYLEA HD may cause eye infection, separation", "of the retina, rare but severe swelling of blood vessels in the eye;",
     "NBA", "GS (0-2)", "Curry: Out (right knee management)", "FSI"],
    ["Ask about next-generation EYLEA HD", "ZN", "a treatment retina specialists trust", "K", "NBA", "2026-27 Champion", "2700", "FSI"],
]


def feed(frames, n=1):
    g = GameTracker("American football")
    t = 0.0
    for f in frames:
        for _ in range(n):
            g.update(t, list(f), [])
            t += 3.0
    return g


@pytest.mark.parametrize("frame", YEZTUGO + EYLEA)
def test_each_real_drug_ad_frame_is_an_ad_on_two_readings(frame):
    g = feed([frame], n=2)
    assert g.on_ad and g.state == "break"


@pytest.mark.parametrize("frame", YEZTUGO + EYLEA)
def test_one_reading_is_not_enough(frame):
    assert not feed([frame], n=1).on_ad


def test_the_ad_stays_an_ad_across_the_frames_without_copy():
    # the sticky ad memory carries the frames where only the sponsor logo and the ticker residue are read
    g = feed(EYLEA[:2] + [["DA", "EYLEAHD", "ZN", "NBA", "2026-27 CHAMPION", "Knicks +1lOO", "Celtics ~1300", "FSI"]])
    assert g.on_ad


GAME_FRAMES = [
    ["QUESTIONABLE: CB Ward (hand)", "OUT: WR Smith (neck)", "NFL", "INJURY REPORT"],
    ["Dybantsa: Out (left ankle sprain)", "Curry: Out (right knee management)", "Green: Doubtful (low back soreness)", "NBA"],
    ["SAVING LIVES", "OREGON", "10", "ILLINOIS", "21", "4th", "1:08", "1st & 10"],
    ["Commentary: he needed an injection before the half", "OREGON", "10", "ILLINOIS", "21", "4th", "1:08"],
    ["Back for treatment after the hit", "BYU", "UCF", "21", "17", "3RD", "4:28"],
    ["Gambling Problem? Call 1-800-GAMBLER", "FANDUEL", "NFL", "PHI", "14", "JAX", "7", "3RD", "9:39"],
]


@pytest.mark.parametrize("frame", GAME_FRAMES)
def test_real_or_plausible_game_frames_are_not_ads(frame):
    g = feed([frame], n=6)
    assert not g.on_ad


def test_the_lists_are_in_the_defaults():
    assert "prescription medic" in AD_COPY_STRONG and "side effects" in AD_COPY_STRONG
    assert "injection" in AD_COPY_WEAK and "treatment" in AD_COPY_WEAK
