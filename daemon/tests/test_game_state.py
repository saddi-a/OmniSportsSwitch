from daemon.game_state import GameTracker, combine


def run(tracker, start, seconds, clock_fn, texts=(), step=3.0):
    """Feed one reading every `step` seconds; clock_fn(t) returns the clock string or None."""
    state = None
    t = start
    while t < start + seconds:
        c = clock_fn(t)
        state = tracker.update(t, list(texts), [c] if c else [])
        t += step
    return state


def ticking(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def test_running_clock_is_live():
    g = GameTracker("football")
    assert run(g, 0, 120, ticking) == "live"


def test_soccer_clock_frozen_becomes_break_quickly():
    g = GameTracker("football")
    run(g, 0, 60, ticking)
    assert run(g, 60, 15, lambda t: "00:59") == "live"
    assert run(g, 75, 30, lambda t: "00:59") == "break"


def test_american_football_stopped_clock_between_plays_stays_live():
    g = GameTracker("American football")
    run(g, 0, 60, ticking)
    assert run(g, 60, 120, lambda t: "01:00") == "live"  # 2 minutes of stopped clock is normal
    assert run(g, 180, 150, lambda t: "01:00") == "break"  # but 4+ minutes is a real break


def test_clock_restarting_ends_the_break():
    g = GameTracker("football")
    run(g, 0, 30, ticking)
    run(g, 30, 60, lambda t: "00:29")
    assert g.state == "break"
    assert run(g, 90, 9, lambda t: ticking(t - 30)) == "live"


def test_half_time_caption_is_an_immediate_break():
    g = GameTracker("football")
    assert run(g, 0, 6, ticking, texts=["PSG", "MI-TEMPS"]) == "break"


def test_final_caption_means_over_even_with_clock_running():
    g = GameTracker("american football")
    assert run(g, 0, 6, ticking, texts=["FINAL"]) == "over"


def test_caption_memory_expires():
    g = GameTracker("football")
    run(g, 0, 6, ticking, texts=["HALF TIME"])
    assert run(g, 200, 30, ticking) == "live"


def test_no_clock_for_a_long_time_is_a_break():
    g = GameTracker("football")
    run(g, 0, 30, ticking)
    assert run(g, 30, 20, lambda t: None) == "live"   # a short gap (overlay) is tolerated
    assert run(g, 50, 60, lambda t: None) == "break"  # a long one is an ad or studio segment


def test_never_seeing_a_clock_becomes_break_after_the_grace_period():
    g = GameTracker("basketball")
    assert run(g, 0, 30, lambda t: None) == "live"
    assert run(g, 30, 60, lambda t: None) == "break"


def test_unknown_sport_uses_generic_rules():
    g = GameTracker(None)
    run(g, 0, 60, ticking)
    assert run(g, 60, 60, lambda t: "01:00") == "live"
    assert run(g, 120, 90, lambda t: "01:00") == "break"


def test_combine_tab_state_from_tiles():
    assert combine(["break", "live", "over"]) == "live"
    assert combine(["break", "over"]) == "break"
    assert combine(["over", "over"]) == "over"
    assert combine([]) == "live"


def test_caption_inside_a_sentence_is_ignored():
    g = GameTracker("football")
    assert run(g, 0, 30, ticking, texts=["Pret pour la mi-temps ?", "Final countdown", "PSG"]) == "live"


def test_caption_must_appear_on_consecutive_readings():
    g = GameTracker("football")
    g.update(0, ["FINAL"], ["00:01"])
    assert g.update(3, ["PSG"], ["00:04"]) == "live"  # appeared once only (e.g. a popup flashing past)
    g.update(6, ["FINAL"], ["00:07"])
    assert g.update(9, ["FINAL"], ["00:10"]) == "over"


# ---- the commercial placeholder seen live on the Texas-Oklahoma stream ---------------------------------------

AD_READINGS = [
    (["PUBLICITARIA EN CURSO.", "0: 41"], ["0:41"]),
    (["COMMERCIAL BREAK IN PROGRESS.", "0:37"], ["0:37"]),
    (["COMMERCIAL BREAK IN PROGRESS", "0:34"], ["0:34"]),
    (["EAK IN PROGRESS", "0:30"], ["0:30"]),
    (["INTERVALO COMERCIAL.", "0:27"], ["0:27"]),
    (["PAUSA PUBLICITARIA EN CURSO.", "0:18"], ["0:18"]),
]
SCOREBUG = (["TEXAS", "14", "1st & 10", "7", "OKLAHOMA"], ["18:58"])


def test_commercial_placeholder_with_its_countdown_is_a_break():
    g = GameTracker("American football")
    t = 0.0
    for i in range(10):                       # live play first
        g.update(t, *SCOREBUG)
        t += 3
    states = []
    for texts, clocks in AD_READINGS:
        states.append(g.update(t, texts, clocks))
        t += 3
    assert states[0] == "live"                # one reading is not trusted yet
    assert states[1:] == ["break"] * 5        # confirmed on the second reading, the countdown is ignored


def test_a_single_ad_phrase_in_other_text_does_not_make_a_break():
    g = GameTracker("football")
    g.update(0, ["Right back after this?"], ["00:01"])           # seen once, e.g. a slogan or a flash of text
    assert g.update(3, ["PSG", "LMFC"], ["00:04"]) == "live"
    assert g.update(6, ["PSG", "LMFC"], ["00:07"]) == "live"


def test_game_returns_to_live_a_few_seconds_after_the_placeholder_goes():
    g = GameTracker("American football")
    t = 0.0
    for i in range(6):
        g.update(t, *SCOREBUG)
        t += 3
    for texts, clocks in AD_READINGS:
        g.update(t, texts, clocks)
        t += 3
    assert g.state == "break"
    last = None
    for i in range(6):                        # the scorebug is back, with a clock that moves again
        last = g.update(t, ["TEXAS", "14", "1st & 10", "7", "OKLAHOMA"], [f"18:{50 - i * 3}"])
        t += 3
    assert last == "live"


def test_on_ad_follows_the_placeholder():
    g = GameTracker("American football")
    t = 0.0
    for i in range(4):
        g.update(t, *SCOREBUG)
        t += 3
    assert g.on_ad is False
    for texts, clocks in AD_READINGS[:3]:
        g.update(t, texts, clocks)
        t += 3
    assert g.on_ad is True
    for i in range(4):
        g.update(t, ["TEXAS", "14", "7", "OKLAHOMA"], [f"18:{50 - i * 3}"])
        t += 3
    assert g.on_ad is False


# ---- big-play banners: "TAKEAWAY!" was read live after an interception -----------------------------------------

def test_takeaway_banner_from_the_live_log_is_a_big_play_once():
    g = GameTracker("American football")
    g.update(0, ["UCLA", "10", "6", "OREGON", "0:21"], ["0:21"])
    assert g.big_play == ""
    g.update(3, ["TAKEAWAY!", "5", "UCLA", "10", "CBS | BIG TIME", "6", "[Ucla _", "0:21", "25", "4-0"], ["0:21"])
    assert g.big_play == "takeaway"
    g.update(6, ["TAKEAWAY!", "UCLA", "10", "6", "OREGON"], ["0:21"])
    assert g.big_play == ""                       # the same banner lingering counts once


def test_stat_labels_and_sentences_are_not_big_plays():
    g = GameTracker("American football")
    for t, texts in enumerate([["CBS SPORTS", "ZN", "TURNOVERS", "3", "21 UCLA", "10"],
                               ["SACKS", "FUMBLES", "INTERCEPTIONS"],
                               ["How to avoid a sack on third down in the red zone"],
                               ["Hoosiers outlast Huskers"]]):
        g.update(t * 30.0, texts, [])
        assert g.big_play == ""


def test_the_banner_counts_again_after_the_cooldown():
    g = GameTracker("football")
    g.update(0, ["RED CARD"], [])
    assert g.big_play == "red card"
    g.update(10, ["RED CARD"], [])
    assert g.big_play == ""
    g.update(30, ["RED CARD"], [])
    assert g.big_play == "red card"


def test_no_big_play_is_reported_during_an_ad_break():
    g = GameTracker("football")
    for t in (0, 3):
        g.update(t, ["COMMERCIAL BREAK IN PROGRESS", "FUMBLE"], ["0:30"])
    assert g.big_play == ""


def test_dazn_promo_screens_count_as_a_break():
    g = GameTracker("American football")
    promo = [(["FIFA+", "WATCH NOW", "ONDAZNCOM", "FIFA+"], []),
             (["After", "Diego", "FIFA+", "WNATCHNOW", "ONDAZNCOM"], []),
             (["Iatchevery gameonly onDAZN", "NFL GAME PASS"], [])]
    states = [g.update(3.0 * i, texts, clocks) for i, (texts, clocks) in enumerate(promo)]
    assert states[0] == "live" and states[1:] == ["break", "break"]
    assert g.on_ad is True


# ---- overtime and "FINAL/OT" (live log 01:19-01:21, Illinois at Michigan State) -------------------------------------

OT_READINGS = [
    ["GSJEFB", "ZN", "8", "OVERTIME", "1st & GOAL", "FS2", "CFB: Nevada at UTEP airing on FS2 and FOX One", "FS1"],
    ["ZN", "4", "73", "OVERTIME", "FS2", "FS1"],
    ["IlLINOIS", "2PT", "CONVERSIONS", "I/1", "OVERTIME", "23", "FS2", "FS1"],
    ["ZN", "ILLINOIS", "OVERTIME", "15", "FS2", "FS1"],
]


def test_overtime_has_no_game_clock_and_is_not_a_break():
    g = GameTracker("american football")
    run(g, 0, 60, ticking)                                        # a normal game first, then overtime without a clock
    state = None
    for i in range(60):                                           # 3 minutes of overtime readings
        state = g.update(60 + i * 3.0, list(OT_READINGS[i % 4]), [])
    assert state == "live"


def test_the_same_clockless_screen_without_the_overtime_banner_is_still_a_break():
    g = GameTracker("american football")
    run(g, 0, 60, ticking)
    state = None
    for i in range(60):
        state = g.update(60 + i * 3.0, ["ZN", "FS2", "FS1"], [])
    assert state == "break"


def test_french_overtime_words_count_too():
    for word in ("PROLONGATIONS", "PROLONGATION", "TEMPS SUPPLÉMENTAIRE", "2OT", "OT"):
        g = GameTracker("american football")
        run(g, 0, 60, ticking)
        state = None
        for i in range(60):
            state = g.update(60 + i * 3.0, ["ZN", word, "FS1"], [])
        assert state == "live", word


def test_final_in_overtime_means_over_whatever_the_ocr_made_of_it():
    for caption in ("FINAL/OT", "FINALOT", "FINAUOT", "FINALIOT", "FINAL/2OT", "FINAL OT", "FINAL/OT2"):
        g = GameTracker("american football")
        run(g, 0, 30, ticking)
        assert g.update(100.0, [caption, "ILLINOIS"], ["00:30"]) != "over"          # one reading is not enough
        assert g.update(103.0, [caption, "ILLINOIS"], ["00:33"]) == "over", caption


def test_french_final_captions_mean_over():
    for caption in ("FIN DU MATCH", "MATCH TERMINÉ", "TERMINÉ", "FINAL", "SCORE FINAL"):
        g = GameTracker("football")
        g.update(0.0, [caption], ["90:00"])
        assert g.update(3.0, [caption], ["90:03"]) == "over", caption
