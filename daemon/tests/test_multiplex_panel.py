"""The CANAL+ multiplex results panel (soccer): rows found, the scorebug side split off, goals voted. Frames are real OCR boxes from
logs/boxes.log: (text, height, x0, y0) as test_score_watch.boxes() takes them. The RedZone frames (2026-10-18, beIN, American football) are controls:
nothing there may look like a panel."""

from daemon.game_identity import team_pair
from daemon.multiplex_panel import PanelWatch, find_panel
from daemon.tests.test_score_watch import boxes


SPORTING_GOAL_BEFORE = boxes([('MULTIPLEX', 24, 14, 30), ('Ligue des champions', 18, 15, 59), ('3e journée', 16, 165, 59), ('#SPORt 360', 44, 1969, 70), ('Direct', 32, 2105, 116), ('SPO', 36, 245, 127), ('00', 28, 353, 131), ('LASK', 36, 463, 127), ('4;16', 32, 116, 130), ('CANAL+', 29, 1873, 217), ('NRWAYS', 89, 1230, 240), ('BRU 0-0 LEN', 38, 1919, 263), ('OATAP', 66, 1614, 255), ('#FOOT', 32, 1872, 370), ('BAY 0-0', 38, 1919, 419), ('ARS', 38, 2071, 417), ('5', 46, 654, 450), ('AIRWA', 98, 520, 445), ('RMA', 38, 1917, 573), ('0-0 RBL', 36, 2005, 573), ('[AIRWAYS', 128, -15, 568), ('Live 4', 39, 1868, 676), ('INT', 38, 1917, 727), ('0-0 SHK', 38, 1987, 727), ('Live 5', 36, 1869, 833), ('AVL 0-0', 36, 1917, 883), ('VFK', 36, 2069, 883), ('Live 6', 36, 1871, 987), ('BET', 36, 1917, 1037), ('0-0 FEY', 36, 1993, 1037), ('LASk', 42, 127, 1133), ('LASKJUNGWIRTH-DIBANGO.ANDRADE.TORNICH,MBUYAMBA', 32, 131, 1138), ('JUNGWIRTH', 42, 251, 1133), ('DIBANGO', 42, 529, 1133), ('ANDRADE', 42, 739, 1133), ('TORNICH, MBUYAMBA ,', 54, 956, 1126), ('LIVE7', 31, 1872, 1142), ('VORGENSEN', 42, 121, 1181), ('ORGENSEN-HORVATH(C).LJUBICIC,DANEK-LANG,KALAJDZIC', 35, 139, 1185), ('HoRVATH (CI', 50, 390, 1180)])

SPORTING_GOAL_FRAME = boxes([('MULTIPLEX', 24, 14, 30), ('Ligue des champions', 18, 15, 59), ('3e journée', 18, 163, 57), ('+SPORT360', 34, 1974, 76), ('MULTIPLEX', 23, 2047, 119), ('4;20', 36, 115, 127), ('GOAL', 60, 335, 115), ('UEFACHAMPIONSLEAGUE', 27, 1954, 151), ('Live 7', 36, 1871, 215), ('SPO1-0LASK', 36, 1919, 263), ('CANAL+', 19, 1878, 371), ('BRU 0-0 LEN', 38, 1919, 419), ('#FOOT', 32, 1872, 524), ('BAY 0-0', 38, 1919, 573), ('ARS', 36, 2071, 573), ('RMA', 39, 1917, 726), ('0-0 RBL', 38, 2005, 727), ('Live 4', 40, 1868, 830), ('TP', 23, 1879, 889), ('INT', 36, 1917, 883), ('0-0 SHK', 38, 1987, 881), ('Live 5', 36, 1871, 987), ('AVL 0-0', 36, 1919, 1037), ('VFK', 36, 2069, 1037), ('LASk', 42, 127, 1133), ('LASKJUNGWIRTH-DIBANGO.ANDRADE.TORNICH,MBUYAMBA', 32, 131, 1138), ('JunGWIRTH', 42, 249, 1133), ('DIBANGO', 42, 529, 1133), ('ANDRADE', 42, 739, 1133), ('TORNICH, MBUYAMBA ,', 54, 956, 1126), ('LIVE6', 30, 1871, 1143), ('VORGENSEN', 42, 121, 1180), ('ORGENSEN-HORVATH(C).LJUBICIC,DANEK-LANG,KALAJDZIC', 35, 139, 1185), ('HoRVATH (CI', 50, 390, 1180), ('DANEK', 40, 881, 1183), ('LANG KALAJDZIC', 52, 1054, 1178), ('LIVE', 14, 89, 1209), ('TOUTES LES CHAINES', 20, 1901, 1205), ('REVOIR/ASUIVRE', 18, 2109, 1206)])

LATE_FRAME = boxes([('#SPORt 360', 42, 1969, 71), ('68.35', 36, 105, 127), ('INT', 38, 247, 126), ('0   0', 36, 353, 127), ('SHK', 36, 471, 127), ('Live7', 36, 1869, 215), ('SPO', 40, 1915, 261), ('1-3 LASK', 40, 1997, 261), ('AVL 3-0', 36, 1917, 419), ('VFK', 36, 2069, 419), ('#FOOT', 32, 1872, 524), ('BAY', 38, 1919, 573), ('2-1', 36, 1997, 573), ('ARS', 36, 2071, 573), ('Live 6', 36, 1869, 679), ('BET', 36, 1919, 729), ('1-0 FEY', 38, 1993, 727), ('beteson', 162, 589, 868), ('RMA', 38, 1919, 881), ('2-0 RBL', 38, 2005, 881), ('BRU', 36, 1917, 1037), ('2-0 LEN', 36, 1999, 1037), ('DEMAIN', 44, 115, 1129), ('JJ JUV', 50, 290, 1128), ('DEMAIN', 42, 643, 1131), ('SGR', 42, 867, 1131), ('18H45', 48, 118, 1178), ('REN', 42, 341, 1181), ('18H45', 50, 644, 1178), ('MAR', 42, 869, 1181), ('INT', 36, 1917, 1191), ('0-0 SHK', 38, 1987, 1191)])

REDZONE_FRAMES = [boxes(r) for r in (
    [('bein spoets', 24, 88, 68), ('COACHING COMPARISON', 56, 524, 128), ('CAREER STATISTICS', 30, 724, 186), ('4TH', 48, 620, 322), ('SEASON', 30, 790, 343), ('2ND', 54, 956, 320), ('33-23', 50, 606, 436), ('RECORD', 30, 790, 460), ('17-5', 50, 958, 436), ('3-3', 50, 630, 552), ('0-1', 48, 974, 554), ('0-0', 50, 630, 668), ('SUPER BOWL', 30, 764, 666), ('RECORD', 30, 794, 690), ('0-0', 50, 972, 668), ('DEMECO', 32, 346, 726), ('LIAM', 32, 1232, 726), ('RYANS', 32, 360, 770)],
    [('bein spoets', 24, 88, 68), ('QB CAREER COMPARISON', 66, 497, 143), ('4TH', 50, 620, 264), ('SEASON', 30, 790, 286), ('6TH', 50, 960, 264), ('12,278', 56, 606, 378), ('PASS YDS', 30, 784, 402), ('18,996', 60, 947, 375), ('68', 52, 638, 494), ('PASS TD', 30, 792, 516), ('108', 52, 966, 494), ('94.0', 52, 616, 610), ('PASS RTG', 30, 784, 632), ('87.4', 52, 958, 610), ('QB', 30, 244, 728), ('CJ', 32, 312, 726), ('0-0', 50, 630, 726), ('SUPER BOWL', 30, 764, 724), ('RECORD', 30, 792, 748), ('0-0', 50, 970, 726), ('0B', 30, 1102, 728), ('TREVOR', 32, 1172, 726), ('7', 30, 250, 768), ('STRoUD', 36, 309, 767), ('16', 38, 1101, 765), ('LAWRENCE', 36, 1170, 767)],
    [('bein spoets', 24, 88, 68), ('UFD', 50, 294, 162), ('QB CAREER COMPARISON', 66, 497, 143), ('4TH', 48, 619, 263), ('SEASON', 30, 790, 285), ('6TH', 50, 960, 264), ('12,278', 56, 606, 378), ('PASS YDS', 30, 784, 402), ('18,996', 60, 947, 375), ('68', 52, 638, 494), ('PASS TD', 30, 792, 516), ('108', 52, 966, 494), ('94.0', 52, 616, 610), ('PASS RTG', 30, 784, 632), ('87.4', 52, 958, 610), ('QB', 30, 244, 728), ('CJ', 32, 312, 726), ('0-0', 50, 630, 726), ('SUPER BOWL', 30, 764, 724), ('RECORD', 30, 792, 748), ('0-0', 50, 970, 726), ('0B', 30, 1102, 728), ('TREVOR', 32, 1172, 726), ('7', 30, 250, 768), ('STRoUD', 36, 309, 767), ('16', 38, 1101, 765), ('LAWRENCE', 36, 1170, 767)],
    [('OmniSportsSwitch', 18, 1075, 9), ('JACKSONVILLE JAGUARS', 24, 12, 30), ('HOUSTON TEXANS', 24, 260, 30), ('This window is', 14, 1075, 37), ('Window', 16, 1169, 35), ('NFL', 16, 15, 59), ('6e journ', 16, 53, 59), ('bein spoeTS', 24, 88, 68), ('Freeze Main (Ctrl+Shift+P)', 18, 1226, 60), ('Freeze Window', 16, 1213, 91), ('(Ctrl+Shift+L)', 20, 1318, 87), ('Re-track this tab (Ctrl+Shift+Y)', 16, 1215, 119), ('NFD', 54, 1546, 106), ('Sport', 16, 1083, 157), ('CANAL+', 14, 1123, 157), ('Main', 14, 1187, 157), ('Auto', 14, 1229, 157), ('Hold Main', 16, 1295, 155), ('To Window', 16, 1377, 155), ('Untrack', 16, 1475, 155), ('teamse.9', 16, 1215, 185), ('Paris SG vs Lyon', 16, 1287, 183), ('4llcwed in:', 12, 1083, 211), ('Main', 12, 1163, 213), ('Wincow', 14, 1215, 211), ('Window 3', 12, 1293, 213), ('Wincow 4', 14, 1369, 211), ('Wingow', 14, 1447, 211), ('Sport', 16, 1083, 257), ('CA. Window', 16, 1125, 255), ('Auto', 14, 1229, 255), ('To Main', 16, 1295, 253), ('Hold Window', 14, 1363, 255), ('Untrack', 14, 1475, 255), ('(sport)', 14, 1087, 283), ('teams', 14, 1217, 283), ('e.g. Paris SG vs Lyon', 18, 1261, 281), ('Allowed in:', 14, 1081, 309), ('Main', 14, 1163, 311), ('Wincow', 14, 1215, 311)],
    [('WFL', 50, 1544, 108), ('2', 70, 247, 329), ('UF', 26, 238, 858), ('0', 42, 574, 844), ('JAXI', 42, 783, 841), ('0', 44, 886, 844), ('Ist 10:13', 42, 949, 843), ('31', 40, 1159, 845), ('LONDON', 40, 1264, 833)],
    [('0', 42, 572, 843), ('0', 41, 885, 844), ('1st', 39, 948, 845), ('9.56', 38, 1030, 845), ('15', 38, 1162, 846), ('3RD & 2', 42, 1281, 843), ('1-4', 34, 441, 852), ('Hou', 56, 470, 839), ('4-1', 25, 753, 854), ('JAX', 52, 782, 841)],
    [('OmniSportsSwitch', 18, 1075, 9), ('This window is', 14, 1075, 37), ('Window', 16, 1169, 35), ('Freeze Main (Ctrl+Shift+P)', 18, 1226, 60), ('Freeze Window', 16, 1213, 91), ('(Ctrl+Shift+L)', 20, 1318, 87), ('Re-track this tab (Ctrl+Shift+Y)', 16, 1215, 119), ('Sport', 16, 1083, 157), ('CANAL+', 14, 1123, 157), ('Main', 14, 1187, 157), ('Auto', 14, 1229, 157), ('Hold Main', 16, 1295, 155), ('To Window', 16, 1377, 155), ('Untrack', 16, 1475, 155), ('sport)', 14, 1091, 185), ('teamse.9', 16, 1215, 185), ('Paris SG vs Lyon', 16, 1287, 183), ('4llcwed in:', 12, 1083, 211), ('Main', 12, 1163, 213), ('Wincow', 14, 1215, 211), ('Window 3', 12, 1293, 213), ('Wincow 4', 14, 1369, 211), ('Wingow', 14, 1447, 211), ('Sport', 16, 1083, 257), ('CA.. Window', 16, 1123, 255), ('Auto', 14, 1229, 255), ('To Main', 16, 1295, 253), ('Hold Window', 14, 1363, 255), ('Untrack', 14, 1475, 255), ('sport)', 14, 1091, 283), ('teams', 14, 1217, 283), ('e.g. Paris SG vs Lyon', 18, 1261, 281), ('llowed in:', 14, 1083, 309), ('Main', 14, 1163, 311), ('Wincow', 14, 1215, 311), ('Window', 14, 1293, 311), ('Window 4', 14, 1369, 311), ('Window', 14, 1447, 311), ('Priorities (sports and favourite teams)', 16, 1089, 347), ('Settings (swapping; ads, weights; timings; banners)', 18, 1089, 371)],
    [('JACKSONVILLE JAGUARS', 24, 12, 30), ('HOUSTON TEXANS', 24, 260, 30), ('NFL', 16, 15, 59), ('6e journée', 16, 53, 59), ('bein spoets', 36, 121, 93), ('0', 24, 1806, 90), ('40', 20, 1967, 91), ('89', 96, 782, 430), ('69', 96, 1210, 472), ('p4', 106, 1641, 502), ('84', 72, 658, 618), ('77', 96, 66, 946), ('6874', 121, 291, 896), ('28TH NFL REGULAR SEASON GAME', 99, 548, 1077), ('LIVE', 14, 89, 1209), ('TOUTES LES CHAINES', 20, 1953, 1205)],
)]


def labels(panel):
    return {(r.left, r.right): (r.a, r.b) for r in panel.rows}


def test_the_panel_rows_are_found_even_when_the_ocr_splits_or_joins_them():
    p = find_panel(LATE_FRAME)
    assert labels(p) == {("SPO", "LASK"): (1, 3), ("AVL", "VFK"): (3, 0), ("BAY", "ARS"): (2, 1), ("BET", "FEY"): (1, 0),
                         ("RMA", "RBL"): (2, 0), ("BRU", "LEN"): (2, 0), ("INT", "SHK"): (0, 0)}
    assert labels(find_panel(SPORTING_GOAL_FRAME))[("SPO", "LASK")] == (1, 0)      # 'SPO1-0LASK' in one item
    before = labels(find_panel(SPORTING_GOAL_BEFORE))
    assert before[("BRU", "LEN")] == (0, 0) and ("BET", "FEY") in before          # six rows are enough, the SPO row hidden


def test_the_scorebug_side_has_no_panel_text_and_gives_the_pair():
    rest = [it["text"] for it in find_panel(LATE_FRAME).rest]
    assert "INT" in rest and "SHK" in rest and "68.35" in rest
    assert not any(t in rest for t in ("SPO", "LASK", "BAY", "ARS", "BET", "FEY", "RMA", "RBL", "VFK", "AVL", "Live7", "#FOOT"))
    scorebug_only = [it["text"] for it in find_panel(SPORTING_GOAL_BEFORE).rest if it["y0"] < 300]
    assert team_pair(scorebug_only) == frozenset({"SPO", "LASK"})
    assert team_pair([it["text"] for it in SPORTING_GOAL_BEFORE]) is None         # all the codes of the screen: no pair, the old problem


def test_redzone_and_other_frames_are_never_a_panel():
    for frame in REDZONE_FRAMES:
        assert find_panel(frame) is None
    assert find_panel(None) is None and find_panel([]) is None
    two_rows = [it for it in LATE_FRAME if it["text"] not in ("BAY", "2-1", "ARS", "RMA", "2-0 RBL", "BRU", "2-0 LEN", "INT", "0-0 SHK", "BET", "1-0 FEY")]
    assert find_panel(two_rows) is None                                           # three rows are needed


def _reading(panel_scores):
    """A panel reading from {(left, right): (a, b)}: one item per row, stacked on the right."""
    items = [{"text": f"{l} {a}-{b} {r}", "x0": 1900, "x1": 2150, "y0": 200 + 150 * i, "y1": 238 + 150 * i} for i, ((l, r), (a, b)) in enumerate(panel_scores.items())]
    return find_panel(items + [{"text": "10:43", "x0": 100, "x1": 200, "y0": 100, "y1": 140}])


def test_a_goal_is_a_row_going_up_by_one_on_two_identical_readings():
    w = PanelWatch(readings=2)
    base = {("SPO", "LASK"): (0, 0), ("BRU", "LEN"): (0, 0), ("BAY", "ARS"): (0, 0), ("BET", "FEY"): (0, 0)}
    assert w.update(_reading(base)) == [] and w.update(_reading(base)) == []     # the baseline
    goal = {**base, **{("SPO", "LASK"): (1, 0)}}
    assert w.update(_reading(goal)) == []                                         # one reading is never enough
    assert w.update(_reading(goal)) == [("SPO-LASK", (0, 0), (1, 0))]
    assert w.update(_reading(goal)) == []                                         # reported once
    away = {**goal, **{("BAY", "ARS"): (0, 1)}}
    w.update(_reading(away))
    assert w.update(_reading(away)) == [("BAY-ARS", (0, 0), (0, 1))]


def test_a_misread_a_flicker_a_disallowed_goal_and_a_first_sight_are_no_goals():
    w = PanelWatch(readings=2)
    base = {("SPO", "LASK"): (1, 0), ("BRU", "LEN"): (0, 0), ("BAY", "ARS"): (2, 1)}
    for _ in range(2):
        w.update(_reading(base))
    flicker = {**base, **{("BRU", "LEN"): (3, 0)}}
    assert w.update(_reading(flicker)) == [] and w.update(_reading(base)) == [] and w.update(_reading(flicker)) == []   # not identical twice in a row
    assert w.update(_reading({**base, **{("BRU", "LEN"): (2, 0)}})) == []        # a jump of two goals: no event
    down = {**base, **{("SPO", "LASK"): (0, 0)}}
    assert w.update(_reading(down)) == [] and w.update(_reading(down)) == []       # a goal taken back: a new baseline, no event
    new = {**base, **{("SPO", "LASK"): (0, 0), ("INT", "SHK"): (1, 0)}}
    assert w.update(_reading(new)) == [] and w.update(_reading(new)) == []          # a game seen for the first time: a baseline
    assert w.update(None) == []


def test_the_late_frame_scores_are_a_baseline_not_goals():
    w = PanelWatch(readings=2)
    assert w.update(find_panel(LATE_FRAME)) == [] and w.update(find_panel(LATE_FRAME)) == []


# ---- through the server -------------------------------------------------------------------------------------------------------------------

def _frame(rows, scorebug=("10:43", "BET", "0", "0", "FEY")):
    items = [{"text": f"{l} {a}-{b} {r}", "x0": 1900, "x1": 2150, "y0": 200 + 150 * i, "y1": 238 + 150 * i} for i, ((l, r), (a, b)) in enumerate(rows.items())]
    items += [{"text": t, "x0": 100 + 120 * i, "x1": 200 + 120 * i, "y0": 100, "y1": 140} for i, t in enumerate(scorebug)]
    return {"texts": [it["text"] for it in items], "clocks": [scorebug[0]], "items": items}


def _server(sport="football"):
    from daemon.server import Server
    server = Server()
    server.handle_message({"type": "register", "tab_id": 5, "sport": sport, "multiplex": True})
    return server


ROWS = {("SPO", "LASK"): (0, 0), ("BRU", "LEN"): (0, 0), ("BAY", "ARS"): (0, 0), ("BET", "FEY"): (0, 0)}


def test_the_server_turns_a_panel_goal_into_a_strong_tab_cue_and_reads_the_scorebug_pair(caplog):
    import logging
    server = _server()
    t = 100.0
    with caplog.at_level(logging.INFO, logger="daemon"):
        for i in range(4):
            server._update_game_states({(5, 0): _frame(ROWS)}, t + 3 * i)
        assert "panel goal" not in caplog.text
        assert server.identities[(5, 0)].key == frozenset({"BET", "FEY"})          # the 14 codes of the panel no longer hide the pair
        goal = {**ROWS, ("SPO", "LASK"): (1, 0)}
        for i in range(2):
            server._update_game_states({(5, 0): _frame(goal)}, t + 20 + 3 * i)
    assert "panel goal tab 5: SPO-LASK 0-0 -> 1-0" in caplog.text and "log only" not in caplog.text
    assert server.scorer.streams[5].anticipation_value >= server.scorer.config.weights.panel_goal_cue_weight


def test_with_the_cue_off_a_panel_goal_is_only_logged(caplog):
    import logging
    server = _server()
    server.scorer.config.panel_goal_cue = 0
    with caplog.at_level(logging.INFO, logger="daemon"):
        for i in range(2):
            server._update_game_states({(5, 0): _frame(ROWS)}, 100.0 + 3 * i)
        for i in range(2):
            server._update_game_states({(5, 0): _frame({**ROWS, ("BAY", "ARS"): (0, 1)})}, 120.0 + 3 * i)
    assert "panel goal tab 5: BAY-ARS 0-0 -> 0-1 (log only)" in caplog.text
    assert server.scorer.streams[5].anticipation_value == 0.0


def test_an_american_football_tab_never_reads_a_panel(caplog):
    import logging
    server = _server("american football")
    with caplog.at_level(logging.INFO, logger="daemon"):
        for i in range(2):
            server._update_game_states({(5, 0): _frame(ROWS)}, 100.0 + 3 * i)
        for i in range(2):
            server._update_game_states({(5, 0): _frame({**ROWS, ("BAY", "ARS"): (0, 1)})}, 120.0 + 3 * i)
    assert "panel goal" not in caplog.text and not server.panel_watch


# ---- stale rows and repeated goals (2026-10-21 22:41-22:47: a standings table hid the panel for minutes, VAR took a goal back) -------------

def test_a_row_that_comes_back_after_a_long_absence_is_a_new_baseline_not_a_goal():
    w = PanelWatch(readings=2, stale_s=45.0)
    t = 100.0
    for i in range(2):
        assert w.update(_reading(ROWS), t + 3 * i) == []
    for i in range(2):                                    # the standings table replaces the panel for 3.5 minutes (no reading at all)
        goal = {**ROWS, ("BAY", "ARS"): (0, 1)}
        assert w.update(_reading(goal), t + 215 + 3 * i) == []
    assert w.base[("BAY", "ARS")] == (0, 1)
    nxt = {**ROWS, ("BAY", "ARS"): (0, 2)}                # a goal seen live afterwards still counts
    assert w.update(_reading(nxt), t + 230) == []
    assert w.update(_reading(nxt), t + 233) == [("BAY-ARS", (0, 1), (0, 2))]


def test_a_goal_taken_back_and_given_again_is_reported_once():
    w = PanelWatch(readings=2, repeat_s=120.0)
    base = {**ROWS, ("AVL", "VFK"): (3, 0)}
    up = {**base, ("AVL", "VFK"): (3, 1)}
    t = 100.0
    for i in range(2):
        w.update(_reading(base), t + 3 * i)
    got = []
    for frame, at in ((up, 10), (up, 13), (base, 40), (base, 43), (up, 80), (up, 83)):    # goal, VAR takes it back, given again 80 s later
        got += w.update(_reading(frame), t + at)
    assert got == [("AVL-VFK", (3, 0), (3, 1))]
    got = []
    for frame, at in ((base, 300), (base, 303), (up, 340), (up, 343)):                    # minutes later (a new baseline first) it is a new goal
        got += w.update(_reading(frame), t + at)
    assert got == [("AVL-VFK", (3, 0), (3, 1))]


def test_no_time_given_means_no_time_rules():
    w = PanelWatch(readings=2)
    up = {**ROWS, ("BAY", "ARS"): (1, 0)}
    for _ in range(2):
        w.update(_reading(ROWS))
    w.update(_reading(up))
    assert w.update(_reading(up)) == [("BAY-ARS", (0, 0), (1, 0))]
