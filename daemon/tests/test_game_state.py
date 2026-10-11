from daemon.game_state import GameTracker, combine
from daemon.scoring import Config


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
    assert run(g, 0, 30, ticking, texts=["Pret pour la mi-temps ?", "Final countdown", "PSG", "1", "LMFC", "0"]) == "live"


def test_caption_must_appear_on_consecutive_readings():
    g = GameTracker("football")
    g.update(0, ["FINAL"], ["00:01"])
    assert g.update(3, ["PSG", "1", "LMFC", "0"], ["00:04"]) == "live"  # appeared once only (e.g. a popup flashing past)
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
    assert g.update(3, ["PSG", "1", "LMFC", "0"], ["00:04"]) == "live"
    assert g.update(6, ["PSG", "1", "LMFC", "0"], ["00:07"]) == "live"


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


# ---- down and distance proves the game is being played even when the clock is unreadable (live log, Texas-Oklahoma) ----

DOWN_TEXTS = ["3rd & 4", "4th & 2", "Ath & 2", "Ist & GOAL", "2nd & 5", "3R0 & 10", "Znd & 7", "Isr & 10", "Jrd & 8", "2nD & GOAL", "1st & 10"]


def clockless(texts_cycle, start=60, readings=60):
    """A normal minute with a clock, then `readings` clock-less readings (the colon is lost: clocks=[])."""
    g = GameTracker("american football")
    run(g, 0, start, ticking)
    state = None
    for i in range(readings):
        state = g.update(start + i * 3.0, list(texts_cycle(i)), [])
    return state


def test_down_and_distance_keeps_a_clockless_game_live():
    for text in DOWN_TEXTS:
        assert clockless(lambda i: ["TEXAS", "24", "OKLAHOMA", "24", text, "14848"]) == "live", text


def test_down_and_distance_seen_now_and_then_is_enough():
    assert clockless(lambda i: ["TEXAS", "24", "OKLAHOMA", "24", "4th & 2"] if i % 3 == 0 else ["TEXAS", "24", "OKLAHOMA", "24"]) == "live"


def test_without_down_and_distance_a_clockless_scorebug_is_still_a_break():
    assert clockless(lambda i: ["TEXAS", "24", "OKLAHOMA", "24", "14848"]) == "break"


def test_stat_labels_with_ordinals_are_not_the_scorebug():
    assert clockless(lambda i: ["3rd DOWNS", "2/10", "3RD DOWN CONVERSIONS", "0/7", "1st half", "and", "Best"]) == "break"


def test_the_quarter_alone_on_the_scorebug_keeps_a_clockless_game_live():
    # live log 01:17-01:18: ['TEXAS', '40', '24', 'Ath', '1605', '23', '24', '2-2', 'OKLAHOMA'] with clocks=[]
    for quarter in ("Ath", "4th", "Ist", "2nd", "Znd", "3rd", "3R0", "Jrd"):
        assert clockless(lambda i: ["TEXAS", "40", "24", quarter, "1605", "24", "2-2", "OKLAHOMA"]) == "live", quarter


def test_a_break_caption_still_beats_down_and_distance():
    g = GameTracker("american football")
    run(g, 0, 60, ticking)
    state = None
    for i in range(6):
        state = g.update(60 + i * 3.0, ["HALFTIME", "3rd & 4"], [])
    assert state == "break"
    over = None
    for i in range(6):
        over = g.update(100 + i * 3.0, ["FINAL", "ILLINOIS"], [])
    assert over == "over"


# ---- the period (quarter) read from the scorebug, by vote ---------------------------------------------------------

def test_period_from_the_quarter_text_needs_several_readings():
    g = GameTracker("american football")
    g.update(0.0, ["TEXAS", "24", "Ath", "OKLAHOMA", "24"], [])
    assert g.period is None
    g.update(3.0, ["TEXAS", "24", "Ath", "OKLAHOMA", "24"], [])
    g.update(6.0, ["TEXAS", "24", "Ath", "OKLAHOMA", "24"], [])
    assert g.period == 4


def test_period_mangles_and_overtime():
    for text, period in (("Q1", 1), ("Q4", 4), ("Ist", 1), ("1st", 1), ("Znd", 2), ("2nd", 2), ("3R0", 3), ("Jrd", 3), ("4th", 4), ("OVERTIME", 5), ("2OT", 5)):
        g = GameTracker("american football")
        for i in range(4):
            g.update(i * 3.0, ["TEXAS", "24", text, "OKLAHOMA", "24"], [])
        assert g.period == period, text


def test_a_stray_reading_does_not_change_the_period():
    g = GameTracker("american football")
    for i in range(5):
        g.update(i * 3.0, ["TEXAS", "Ath", "OKLAHOMA"], [])
    g.update(20.0, ["TEXAS", "2nd", "OKLAHOMA"], [])
    assert g.period == 4


def test_stat_labels_do_not_give_a_period():
    g = GameTracker("american football")
    for i in range(5):
        g.update(i * 3.0, ["3rd DOWNS", "2/10", "1st half", "4th qtr stats"], [])
    assert g.period is None


def test_the_clock_minute_is_available_for_soccer():
    g = GameTracker("football")
    assert g.clock_minute is None
    g.update(0.0, ["PSG", "1", "LYON", "1"], ["67:23"])
    assert g.clock_minute == 67
    g.update(3.0, ["PSG", "1", "LYON", "1"], ["bad"])
    assert g.clock_minute == 67


# ---- a results ticker with "Final" over a live game (live log 02:10: USC at Penn State, clock 0:13 running) ---------

TICKER = ["Texas", "24", "Final", "25", "Oklahoma 27", "26", "10", "19 USC", "5-1", "7", "PENN STATE", "3-2", "23", "IST", "2nd & 5"]


def test_final_on_a_ticker_does_not_end_a_game_with_a_live_scorebug():
    g = GameTracker("american football")
    state = None
    for i in range(10):
        state = g.update(i * 3.0, list(TICKER), [f"0:{40 - i * 3:02d}"])
    assert state == "live"


def test_final_without_a_down_and_distance_still_means_over():
    g = GameTracker("american football")
    for i in range(6):
        g.update(i * 3.0, ["FINAL", "ILLINOIS"], ["0:00"])
    assert g.state == "over"
    g2 = GameTracker("american football")
    for i in range(6):
        g2.update(i * 3.0, ["FINAL", "ILLINOIS"], [])
    assert g2.state == "over"


def test_halftime_with_a_running_countdown_is_still_a_break():
    g = GameTracker("american football")
    for i in range(10):
        g.update(i * 3.0, ["HALFTIME", "OHIO STATE"], [f"10:{59 - i * 3:02d}"])
    assert g.state == "break"


def test_the_ticker_final_stays_ignored_in_readings_where_the_down_text_is_missing():
    # live log 02:05:31-02:05:47: the down and distance flickers out for a few readings while "Final" shows
    g = GameTracker("american football")
    g.update(0.0, ["19 USC", "PENN STATE", "Ath & 13"], ["13:06"])
    for i in range(1, 5):
        g.update(i * 3.0, ["25", "Final", "19 USC", "PENN STATE"], [])
    assert g.state != "over"


def test_down_word_forms_count_as_a_game_in_play():
    for text in ("2nd Down", "Znd Down", "3rd DOWN", "Ath DOWN"):
        g = GameTracker("american football")
        for i in range(8):
            g.update(i * 3.0, ["Final", "19 USC", text], [])
        assert g.state == "live", text


def test_a_real_final_still_ends_the_game_once_the_scorebug_has_gone():
    g = GameTracker("american football")
    g.update(0.0, ["19 USC", "PENN STATE", "2nd & 5"], ["0:10"])
    for i in range(1, 12):
        g.update(i * 3.0, ["FINAL", "USC", "PENN STATE"], [])
    assert g.state == "over"


# ---- an ad countdown shown alone is not the game clock (live log 02:18, LSU-Kentucky tab: texts == ['2:27']) -------

def test_a_lone_countdown_clock_is_an_ad_not_a_live_game():
    g = GameTracker("american football")
    run(g, 0, 60, ticking, texts=["LSU", "44", "KENTUCKY", "14", "2nd & 5"])       # a normal game first
    state = None
    for i, clock in enumerate(("2:27", "2:24", "2:21", "2:18")):
        state = g.update(100 + i * 3.0, [clock], [clock])
    assert state == "break" and g.on_ad


def test_one_lone_clock_reading_is_not_enough():
    g = GameTracker("american football")
    run(g, 0, 60, ticking, texts=["LSU", "44", "KENTUCKY", "14", "2nd & 5"])
    assert g.update(100.0, ["2:27"], ["2:27"]) == "live"


def test_a_clock_with_scoreboard_text_is_still_a_game_clock():
    g = GameTracker("football")
    state = None
    for i in range(10):
        state = g.update(i * 3.0, ["PSG", "1", "LYON", "0", f"{50 + i // 2}:{(i * 3) % 60:02d}"], [f"{50 + i // 2}:{(i * 3) % 60:02d}"])
    assert state == "live" and not g.on_ad


def test_the_game_comes_back_when_the_scorebug_returns():
    g = GameTracker("american football")
    run(g, 0, 60, ticking, texts=["LSU", "44", "KENTUCKY", "14", "2nd & 5"])
    for i, clock in enumerate(("2:27", "2:24", "2:21")):
        g.update(100 + i * 3.0, [clock], [clock])
    state = None
    for i in range(5):
        state = g.update(120 + i * 3.0, ["LSU", "44", "KENTUCKY", "14", "3rd & 2"], [])
    assert state == "live"


# ---- an ad countdown with the ad's own text beside it (live log 02:29, LSU-Kentucky tab) ----------------------------

AD_COPY = [["hulu", "THE TESTAMENTS", "TOUS LES ÉPISODES DISPONIBLES EN EXCLUSIVITÉ"], ["Vivez les plus grandes histoires", "02026 Disney et ses sociétés affiliées ."],
           ["Eminence", "TLE", "Shadow", "Nouvelle série"], ["L'ÉPOPÉE D'UN COLLEC", "IF\"", "LIBÉRATION"], []]


def test_an_ad_countdown_with_the_ads_own_text_stays_a_break():
    g = GameTracker("american football")
    run(g, 0, 60, ticking, texts=["LSU", "44", "KENTUCKY", "14", "2nd & 5"])
    states = []
    for i in range(24):                      # the countdown 1:48, 1:45, ... with ad copy on some readings, alone on others
        clock = f"1:{48 - i * 2:02d}" if i < 24 else "0:00"
        copy = AD_COPY[i % len(AD_COPY)]
        states.append(g.update(100 + i * 3.0, [*copy, clock], [clock]))
    assert states[3:] == ["break"] * 21


def test_a_game_clock_with_scores_is_not_taken_for_an_ad_countdown():
    g = GameTracker("american football")
    state = None
    for i in range(10):
        clock = f"{12 - i // 3}:{(59 - i * 3) % 60:02d}"
        state = g.update(i * 3.0, ["LSU", "44", "KENTUCKY", "14", clock], [clock])
    assert state == "live" and not g.on_ad


def test_a_game_clock_with_a_quarter_but_no_scores_read_is_not_an_ad():
    g = GameTracker("american football")
    state = None
    for i in range(10):
        clock = f"{9 - i // 3}:{(59 - i * 3) % 60:02d}"
        state = g.update(i * 3.0, ["LSU", "Ath", "KENTUCKY", clock], [clock])
    assert state == "live" and not g.on_ad


# ---- bilingual / multilingual banners (EN, FR, ES, PT) ---------------------------------------------------------------

def _state_after(caption, sport="football", readings=3, clock=True):
    g = GameTracker(sport)
    state = None
    for i in range(readings):
        state = g.update(i * 3.0, [caption, "TEAM A", "1", "TEAM B", "0"], ["45:00"] if clock else [])
    return state


def test_halftime_and_second_language_break_captions():
    for caption in ("HALFTIME", "HALF TIME", "MI-TEMPS", "MI TEMPS", "INTERVALLE", "DESCANSO", "ENTRETIEMPO", "MEDIO TIEMPO", "MEDIOTIEMPO",
                    "INTERVALO", "INTERVALO DE JOGO"):
        assert _state_after(caption) == "break", caption


def test_end_of_a_quarter_captions_in_english_french_spanish_portuguese():
    for caption in ("END OF 1st QTR", "END OF Ist OTR", "END OF 2ND QUARTER", "END OF 3RD QTR", "END OF FIRST QUARTER", "END OF 4TH QUARTER",
                    "FIN DU QUART", "FIN DU 1ER QUART", "FIN DU 2E QUART", "FIN DE LA 1RE PÉRIODE", "FIN DE PÉRIODE", "FIN DU PREMIER QUART",
                    "FIN DEL PRIMER CUARTO", "FIN DEL 2º CUARTO", "FIM DO 1º QUARTO", "FIM DO PRIMEIRO QUARTO"):
        assert _state_after(caption, "american football", clock=False) == "break", caption


def test_a_quarter_end_caption_ends_when_the_scorebug_shows_play_again():
    g = GameTracker("american football")
    for i in range(4):
        g.update(i * 3.0, ["END OF 1st QTR"], [])
    assert g.state == "break"
    state = None
    for i in range(3):
        state = g.update(20 + i * 3.0, ["TEXAS", "7", "OKLAHOMA", "7", "2nd & 10", "Znd"], [])
    assert state == "live"


def test_over_captions_in_french_spanish_portuguese():
    for caption in ("FIN DU MATCH", "COUP DE SIFFLET FINAL", "FINAL DEL PARTIDO", "FIN DEL PARTIDO", "PARTIDO FINALIZADO", "FINALIZADO", "TERMINADO",
                    "FIM DE JOGO", "FIM DO JOGO", "FINAL DO JOGO", "JOGO ENCERRADO", "FULL TIME", "FULL-TIME"):
        assert _state_after(caption) == "over", caption


def test_ordinary_words_are_not_captions():
    for text in ("PAUSE", "PAUSE BUTTON", "ENTRETIEMPO DE LA SERIE", "END OF SEASON", "FIN DU MONDE", "INTERVAL TRAINING"):
        assert _state_after(text) == "live", text


def test_ad_phrases_in_more_languages():
    for text in ("PAUSE PUBLICITAIRE", "PAUSA COMERCIAL", "PUBLICIDADE EM CURSO", "Volvemos enseguida", "De retour dans un instant", "We'll be right back"):
        g = GameTracker("football")
        for i in range(3):
            g.update(i * 3.0, [text], [])
        assert g.on_ad, text


# ---- replays: a live one (scorebug still there) and one in a studio show / break (no scorebug) ----------------------

def feed(g, start, texts, clock=None, readings=3, step=2.0):
    """Feed identical readings; returns the state after the last one."""
    state = None
    for i in range(readings):
        state = g.update(start + i * step, list(texts), [clock] if clock else [])
    return state


def test_replay_banner_over_a_live_scorebug_is_flagged_and_the_game_stays_live():
    g = GameTracker("American football")
    feed(g, 0, ["TEXAS", "14", "7", "OKLAHOMA", "2nd & 4"], "8:41", readings=2)
    assert g.replay is False
    assert feed(g, 4, ["REPLAY", "TEXAS", "14", "7", "OKLAHOMA", "2nd & 4"], "8:41", readings=2) == "live"
    assert g.replay is True


def test_replay_ends_a_few_seconds_after_the_banner_goes():
    g = GameTracker("American football")
    feed(g, 0, ["REPLAY", "TEXAS", "14", "7", "OKLAHOMA", "2nd & 4"], "8:41")
    assert g.replay is True
    g.update(30, ["TEXAS", "14", "7", "OKLAHOMA", "2nd & 4"], ["8:30"])
    assert g.replay is False


def test_replay_without_a_scorebug_is_a_break_like_an_ad():
    g = GameTracker("football")
    feed(g, 0, ["TEXAS", "1", "0", "OKLAHOMA"], "12:00", readings=2)
    assert feed(g, 4, ["ACTION REPLAY"], None, readings=6) == "break"   # after the scorebug memory (6 s) ran out
    assert g.replay is False   # `replay` means a replay inside a live game; this one is plainly not live
    assert g.update(40, ["TEXAS", "1", "0", "OKLAHOMA"], ["12:20"]) == "live"


def test_replay_banners_in_english_french_spanish_portuguese():
    for word in ["REPLAY", "INSTANT REPLAY", "SLO-MO", "SLOW MOTION", "RALENTI", "RALENTI!", "REPRISE", "REPRISE VIDÉO",
                 "ACTION REPLAY", "REPETICIÓN", "CÂMERA LENTA", "REPETIÇÃO"]:
        g = GameTracker("football")
        feed(g, 0, [word, "PSG", "1", "0", "LYON"], "30:00")
        assert g.replay is True, word


def test_sentences_and_other_words_are_not_replays():
    for texts in (["Watch the replay on our app tonight"], ["Reprise du jeu dans quelques minutes"], ["REPLAYS"], ["Highlights"]):
        g = GameTracker("football")
        feed(g, 0, texts + ["PSG", "1", "0", "LYON"], "30:00")
        assert g.replay is False, texts


def test_a_single_reading_of_the_banner_is_not_enough():
    g = GameTracker("football")
    g.update(0, ["REPLAY", "PSG", "1", "0", "LYON"], ["30:00"])
    assert g.replay is False


def test_a_replay_banner_inside_an_ad_placeholder_is_ignored():
    g = GameTracker("football")
    feed(g, 0, ["COMMERCIAL BREAK IN PROGRESS", "REPLAY"], "0:30")
    assert g.replay is False


# ---- scorebug hidden (DAZN hides it during replays): only reported, for measuring; it changes nothing ------------------

BUG = ["DA", "COM", "0-0", "ROM", "ZN"]


def notes(g, start, seconds, texts, clock_fn=None, step=2.0):
    """Feed readings; returns [(time, note)] of the readings that carried a scorebug note."""
    out, t = [], start
    while t < start + seconds:
        clock = clock_fn(t) if clock_fn else None
        g.update(t, list(texts), [clock] if clock else [])
        if g.scorebug_note:
            out.append((t, g.scorebug_note))
        t += step
    return out


def test_a_hidden_scorebug_is_reported_once_and_again_when_it_returns():
    g = GameTracker("football")
    assert notes(g, 0, 30, BUG, lambda t: ticking(t + 600)) == []
    hidden = notes(g, 30, 20, ["DA", "ZN"])
    assert [n[0] for _, n in hidden] == ["hidden"] and 6 <= hidden[0][1][1] < 9    # once, as soon as 6 s have passed
    back = notes(g, 50, 6, BUG, lambda t: ticking(t + 600))
    assert [n[0] for _, n in back] == ["back"] and 18 <= back[0][1][1] <= 24      # the length of the gap


def test_a_short_gap_is_not_reported():
    g = GameTracker("football")
    notes(g, 0, 30, BUG, lambda t: ticking(t + 600))
    assert notes(g, 30, 4, ["DA", "ZN"]) == []
    assert notes(g, 34, 6, BUG, lambda t: ticking(t + 600)) == []


def test_no_report_before_a_scorebug_was_ever_seen():
    g = GameTracker("football")
    assert notes(g, 0, 40, ["DA", "ZN"]) == []


def test_a_gap_with_an_ad_placeholder_is_not_reported():
    g = GameTracker("football")
    notes(g, 0, 30, BUG, lambda t: ticking(t + 600))
    assert notes(g, 30, 20, ["COMMERCIAL BREAK IN PROGRESS"]) == []
    assert notes(g, 50, 6, BUG, lambda t: ticking(t + 600)) == []


def test_a_report_in_progress_is_cancelled_by_a_break_caption():
    g = GameTracker("football")
    notes(g, 0, 30, BUG, lambda t: ticking(t + 600))
    got = notes(g, 30, 8, ["DA", "ZN"])
    assert [n[0] for _, n in got] == ["hidden"]
    got = notes(g, 38, 12, ["HALFTIME"])
    assert [n[0] for _, n in got] == ["cancelled"]
    assert notes(g, 50, 6, BUG, lambda t: ticking(t + 600)) == []   # no "back" for a cancelled gap


def test_a_stale_gap_after_a_long_pause_is_not_reported():
    g = GameTracker("football")
    notes(g, 0, 20, BUG, lambda t: ticking(t + 600))
    assert notes(g, 300, 10, ["DA", "ZN"]) == []   # the first reading is 5 minutes after the last scorebug


def test_the_thresholds_are_settings():
    g = GameTracker("football", Config(scorebug_hidden_s=2.0))
    notes(g, 0, 20, BUG, lambda t: ticking(t + 600))
    assert [n[0] for _, n in notes(g, 20, 6, ["DA", "ZN"])] == ["hidden"]


# ---- anticipation cues (measure only): banners and graphics that come just before a likely score ----------------------

SCORE = ["TEXAS", "7", "14", "OKLAHOMA"]   # scoreboard text beside the clock (a clock alone is an ad countdown)


def cues_over(g, start, texts, clock=None, readings=4, step=2.0):
    """Feed identical readings; returns [(reading index, cue)] for every cue reported."""
    out = []
    for i in range(readings):
        g.update(start + i * step, list(texts), [clock] if clock else [])
        out += [(i, c) for c in g.anticipation]
    return out


def test_a_goal_to_go_banner_is_a_cue_once_it_is_confirmed_on_two_readings():
    g = GameTracker("American football")
    assert cues_over(g, 0, ["GOAL TO GO", "TEXAS", "7", "OKLAHOMA"], "8:41") == [(1, "goal to go")]   # not on the first reading, once


def test_a_goal_down_text_is_the_same_cue():
    g = GameTracker("American football")
    assert [c for _, c in cues_over(g, 0, ["1st & GOAL", "TEXAS"], "8:41")] == ["goal to go"]


def test_fourth_down_even_as_ocr_mangles_it():
    for text in ("4th & 3", "Ath & 3", "4TH & GOAL"):
        g = GameTracker("American football")
        assert "4th down" in [c for _, c in cues_over(g, 0, [text, "TEXAS"], "8:41")], text


def test_stat_labels_are_not_cues():
    for text in ("4th DOWN CONVERSIONS", "3rd & 4", "FIELD GOALS 2/3", "Penalty box analysis tonight"):
        g = GameTracker("American football")
        assert cues_over(g, 0, [text, "TEXAS"], "8:41") == [], text


def test_english_and_french_banners():
    for text, cue in (("FIELD GOAL", "field goal"), ("TWO-MINUTE WARNING", "two-minute warning"), ("UNDER REVIEW", "under review"),
                      ("FLAG", "flag"), ("PENALTY", "penalty"), ("VAR CHECK", "var check"), ("VAR", "var"), ("FREE KICK", "free kick"),
                      ("CORNER", "corner"), ("COUP FRANC", "coup franc"), ("TIR AU BUT", "tir au but"), ("PÉNALTY", "pénalty"),
                      ("PENALTY SHOOTOUT", "penalty shootout"), ("RED ZONE", "red zone"), ("CHALLENGE", "challenge")):
        g = GameTracker("football")
        assert [c for _, c in cues_over(g, 0, [text, "PSG", "0", "LYON"], "30:00")] == [cue], text


def test_basketball_banners():
    for text, cue in (("TECHNICAL FOUL", "technical foul"), ("FLAGRANT FOUL", "flagrant foul"), ("BUZZER BEATER", "buzzer beater"),
                      ("FREE THROWS", "free throws"), ("TIMEOUT", "timeout"), ("LANCERS FRANCS", "lancers francs"), ("TEMPS MORT", "temps mort")):
        g = GameTracker("basketball")
        assert [c for _, c in cues_over(g, 0, [text, "LAL", "55", "50", "BOS"], "5:30")] == [cue], text
    g = GameTracker("basketball")
    assert cues_over(g, 0, ["TIMEOUTS", "LAL", "55", "50", "BOS"], "5:30") == []    # a stat label


def test_a_red_card_or_penalty_kick_is_a_big_play_not_also_a_cue():
    for text in ("RED CARD", "PENALTY KICK", "CARTON ROUGE"):
        g = GameTracker("football")
        assert cues_over(g, 0, [text, "PSG"], "30:00") == [], text


def test_the_same_cue_counts_again_only_after_its_cooldown():
    g = GameTracker("football")
    assert len(cues_over(g, 0, ["CORNER"] + SCORE, "30:00", readings=8)) == 1       # lingering for 16 s
    assert len(cues_over(g, 40, ["CORNER"] + SCORE, "30:40", readings=4)) == 1      # more than 30 s after the first


def test_two_different_cues_do_not_share_a_cooldown():
    g = GameTracker("football")
    assert [c for _, c in cues_over(g, 0, ["CORNER", "VAR"] + SCORE, "30:00")] == ["corner", "var"]


def test_no_cues_during_an_ad_a_break_or_a_replay():
    g = GameTracker("football")
    assert cues_over(g, 0, ["COMMERCIAL BREAK IN PROGRESS", "CORNER"], "0:30") == []
    g = GameTracker("football")
    assert cues_over(g, 0, ["HALFTIME", "CORNER"], "45:00") == []
    g = GameTracker("football")
    feed(g, 0, ["PSG", "1", "0", "LYON"], "30:00")
    assert cues_over(g, 6, ["REPLAY", "CORNER", "PSG", "1", "0", "LYON"], "30:06") == []


def test_added_time_next_to_the_clock_after_45_or_90():
    for clock in ("45:50", "46:02", "90:30", "93:10"):
        g = GameTracker("football")
        assert [c for _, c in cues_over(g, 0, ["+3", "PSG", "0", "LYON"], clock)] == ["added time"], clock
    g = GameTracker("football")
    assert cues_over(g, 0, ["+3", "PSG", "0", "LYON"], "20:00") == []


def test_big_gain_graphics_and_the_yard_line_need_the_threshold():
    g = GameTracker("American football")
    assert [c for _, c in cues_over(g, 0, ["+35 YDS"] + SCORE, "8:41")] == ["big gain"]
    g = GameTracker("American football")
    assert cues_over(g, 0, ["+5 YDS"] + SCORE, "8:41") == []
    g = GameTracker("American football")
    assert [c for _, c in cues_over(g, 0, ["4 YD", "2nd & 4"] + SCORE, "8:41")] == ["yard line"]
    g = GameTracker("American football")
    assert cues_over(g, 0, ["4 YD"] + SCORE, "8:41") == []        # no down text: not a scorebug
    g = GameTracker("American football")
    assert cues_over(g, 0, ["25 YD", "2nd & 4"] + SCORE, "8:41") == []


def test_the_cue_thresholds_are_settings():
    g = GameTracker("American football", Config(anticipation_big_gain_yd=50.0, anticipation_cooldown_s=5.0))
    assert cues_over(g, 0, ["+35 YDS"] + SCORE, "8:41") == []
    g = GameTracker("football", Config(anticipation_cooldown_s=5.0))
    assert len(cues_over(g, 0, ["CORNER"] + SCORE, "30:00", readings=8)) == 3   # cooldown 5 s: at 2, 8 and 14 s


# ---- a graphic over the scoreboard (substitution banner) must not turn the running game clock into an "ad countdown" ----
# Live log, Como-Roma 14:23:35 to 14:23:43 (a false break for 8 s): the team codes and the score were hidden for two readings.

def overlay_run(g, readings):
    return [g.update(t, list(texts), [clock]) for t, clock, texts in readings]


def test_a_running_clock_under_a_substitution_graphic_stays_live():
    g = GameTracker("football")
    readings = [(0.0, "89:02", ["89.02", "COM", "0-0", "ROM", "ZN"]),
                (2.5, "89:05", ["89.05", "COM", "0-0", "ROM", "STORE", "TARS", "POKERSTARS NEWS"]),
                (5.3, "89:07", ["89.07", "NO MORE SUBS AVAILABLE", "POKERSTARSs", "PCKERSTARS"]),
                (7.7, "89:10", ["DA", "89.10", "NO MORE SUBS AVAILABLE", "POKERS", "Ars", "POKERS"]),
                (10.3, "89:12", ["Tars", "89.12", "COM", "0-0", "ROM", "POKERSTARS NEWS"])]
    assert overlay_run(g, readings) == ["live"] * 5
    assert g.on_ad is False


def test_a_stopped_clock_under_a_graphic_stays_live_in_american_football():
    g = GameTracker("American football")
    readings = [(0.0, "8:41", ["8:41", "TEXAS", "7", "14", "OKLAHOMA", "2nd & 4"])] + \
               [(3.0 * i, "8:41", ["8:41", "PLAYER OF THE GAME"]) for i in range(1, 5)]
    assert overlay_run(g, readings) == ["live"] * 5


def test_an_unrelated_countdown_right_after_the_scoreboard_is_still_an_ad():
    g = GameTracker("football")
    g.update(0.0, ["89.02", "COM", "0-0", "ROM"], ["89:02"])
    states = [g.update(2.5 + 2.5 * i, [c], [c]) for i, c in enumerate(("2:27", "2:25", "2:22"))]
    assert states[-1] == "break" and g.on_ad


def test_a_countdown_that_only_happens_to_match_does_not_hold_for_long():
    g = GameTracker("American football")
    g.update(0.0, ["TEXAS", "7", "14", "OKLAHOMA", "2nd & 4", "3:10"], ["3:10"])
    for i in range(1, 9):                       # the same clock keeps running down for 40 s with no scoreboard: an ad after all
        c = f"3:{10 - 5 * i:02d}" if 10 - 5 * i >= 0 else f"2:{60 + 10 - 5 * i:02d}"
        g.update(5.0 * i, [c], [c])
    assert g.on_ad


def test_ft_in_a_stats_graphic_beside_a_running_clock_is_not_the_end():
    # Como-Roma 14:28:57: a MATCH MOMENTUM graphic showed "45'" and "FT" while the 94:26 clock was still running.
    g = GameTracker("football")
    texts = ["94.26", "+7", "COM", "0-0", "ROM", "MATCH MOMENTUM", "45'", "FT"]
    state = None
    for i in range(6):
        s = 94 * 60 + 26 + i * 8
        state = g.update(i * 8.0, texts, [f"{s // 60}:{s % 60:02d}"])
    assert state == "live"


def test_ft_with_no_clock_still_means_over():
    g = GameTracker("football")
    assert run(g, 0, 6, lambda t: None, texts=["COMO", "0", "0", "ROMA", "FT"]) == "over"


BOARD_TEXTS = ["2-2", "PHI", "14", "3-1", "JAX", "7", "3RD"]
STATS_TEXTS = ["GAME LEADERS", "18/26", "COMP/ATT", "QB", "TREVOR", "LAWRENCE"]


def board_clock(t):
    return f"{int(10 - t // 60)}:{int(60 - t % 60) % 60:02d}"


def feed_for(g, start, seconds, texts, clock_fn=lambda t: None, step=3.0):
    state, t = None, start
    while t < start + seconds:
        c = clock_fn(t)
        state = g.update(t, list(texts), [c] if c else [])
        t += step
    return state


def test_a_clock_beyond_the_sport_in_a_stats_graphic_does_not_keep_the_game_live():
    # Eagles-Jaguars 17:51:30: GAME LEADERS showed "80:40" read as a clock; each such reading restarted the 90 s no-clock timer.
    g = GameTracker("american football")
    assert feed_for(g, 0, 30, BOARD_TEXTS + ["3RD & 4"], board_clock) == "live"
    feed_for(g, 30, 30, STATS_TEXTS)
    feed_for(g, 60, 3, STATS_TEXTS, lambda t: "80:40")
    assert feed_for(g, 63, 60, STATS_TEXTS) == "break"   # more than 90 s after the last real clock (t=27)


def test_a_plausible_clock_in_a_stats_graphic_beside_no_scoreboard_does_not_count_either():
    g = GameTracker("american football")
    feed_for(g, 0, 30, BOARD_TEXTS + ["3RD & 4"], board_clock)
    feed_for(g, 30, 60, STATS_TEXTS, lambda t: "7:11" if t in (60, 63, 66) else None)
    assert feed_for(g, 90, 36, STATS_TEXTS) == "break"   # last real clock t=27, so 90 s are over at t=117


def test_a_real_scoreboard_under_a_stats_overlay_still_counts():
    g = GameTracker("american football")
    assert feed_for(g, 0, 200, BOARD_TEXTS + STATS_TEXTS + ["3RD & 4"], board_clock) == "live"
