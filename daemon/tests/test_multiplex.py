"""Multiplex tabs (NFL RedZone, a Premier League multiplex channel): one video jumps between several games of the same sport. The user flags
the tab; a different pair of team codes read several times in a row is a new game and resets everything that belonged to the old one."""
from daemon.game_identity import GameIdentity, key_text, team_pair
from daemon.game_state import GameTracker
from daemon.recorder import snapshot_rows
from daemon.score_watch import ScoreWatcher
from daemon.scoring import Config, Scorer
from daemon.server import Server

NFL = "american football"


def board(a, sa, b, sb, clock="9:30", extra=()):
    return {"texts": [a, str(sa), b, str(sb), "3rd & 4", "2nd", *extra], "clocks": [clock]}


def make(flag=True, **cfg):
    cfg.setdefault("warmup_s", 0.0)
    server = Server(Scorer(Config(**cfg)))
    server.handle_message({"type": "register", "tab_id": 1, "is_main": True, "role": "MAIN"})
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT", "sport": "American football", "multiplex": flag})
    return server


def feed(server, readings, start=100.0, step=3.0):
    for i, r in enumerate(readings):
        server._update_game_states({(2, 0): r}, start + step * i)
    return start + step * len(readings)


def game(a, b, n, score=(0, 0), clock="9:30"):
    return [board(a, score[0], b, score[1], clock) for _ in range(n)]


# -- identity ------------------------------------------------------------------------------------------------------------
def test_team_pair_needs_exactly_two_codes():
    assert team_pair(["KC", "BUF", "7", "3"]) is None            # one code is no pair
    assert team_pair(["KCC", "7", "BUF", "3"]) == frozenset({"KCC", "BUF"})
    assert team_pair(["KCC", "BUF", "DAL", "NYG"]) is None       # a results ticker
    assert team_pair(["REDZONE", "LIVE", "MORE"]) is None        # no scorebug
    assert key_text(frozenset({"KCC", "BUF"})) == "BUF-KCC"


def test_identity_needs_a_stable_new_pair():
    ident = GameIdentity(3)
    a, b = frozenset({"KCC", "BUF"}), frozenset({"DAL", "NYG"})
    assert ident.update(a) is None and ident.update(a) is None and ident.update(a) is None and ident.key == a   # the first game is no change
    assert ident.update(b) is None and ident.update(None) is None and ident.update(a) is None   # a flicker, a studio shot, back to A
    assert ident.update(b) is None and ident.update(b) is None   # not three in a row yet
    assert ident.update(None) is None                            # a missing reading keeps the old game and the count
    assert ident.update(b) == (a, b) and ident.key == b


# -- the resets ----------------------------------------------------------------------------------------------------------
def test_watcher_new_game_forgets_the_old_scores_and_sees_the_new_goal():
    w = ScoreWatcher(Config())
    for i in range(4):
        w.update(["KCC", "21", "BUF", "14"], ["9:30"], 100.0 + 3 * i)
    assert w.score == (21, 14)
    w.new_game()
    quiet = [w.update(["DAL", "0", "NYG", "0"], ["5:00"], 120.0 + 3 * i) for i in range(4)]
    assert w.score == (0, 0) and not any(quiet)                  # not a misread below the old peak
    goal = [w.update(["DAL", "7", "NYG", "0"], ["4:40"], 140.0 + 3 * i, sport=NFL) for i in range(4)]
    assert any(goal)


def test_tracker_new_game_drops_final_memory():
    t = GameTracker("football", Config())
    for i in range(4):
        t.update(100.0 + i, ["ARS", "1", "CHE", "0", "FULL TIME"], ["90:00"])
    assert t.state == "over"
    t.new_game()
    assert t.update(110.0, ["MCI", "0", "LIV", "0"], ["12:00"]) == "live"


def test_tracker_new_game_accepts_the_new_clock_at_once():
    t = GameTracker("football", Config())
    for i in range(3):
        t.update(100.0 + 3 * i, ["ARS", "1", "CHE", "0"], [f"88:{i:02d}"])
    t.update(120.0, ["MCI", "0", "LIV", "0"], ["12:00"])
    assert t._real_clock[0] > 80 * 60                            # without the reset the other game's clock is dropped as foreign
    t.new_game()
    t.update(130.0, ["MCI", "0", "LIV", "0"], ["12:05"])
    assert t._real_clock == (12 * 60 + 5, 130.0)


def test_scorer_reset_tile_clears_event_and_goal_hold():
    s = Scorer(Config(warmup_s=0.0))
    s.register(2, 1.0, False)
    s.set_tiles(2, [0, 1])
    s.set_event(2, 100.0, 1.0, goal=True, tile=0)
    s.set_event(2, 100.0, 1.0, goal=True, tile=1)
    s.reset_tile(2, 0)
    assert s.streams[2].tiles[0].event_at == float("-inf") and s.streams[2].tiles[0].goal_at == float("-inf")
    assert s.streams[2].tiles[1].goal_at == 100.0                # the other video is untouched


# -- the server ----------------------------------------------------------------------------------------------------------
def test_flagged_tab_new_game_is_logged_and_gives_no_false_goal(caplog):
    server = make(True)
    caplog.set_level("INFO", logger="daemon")
    now = feed(server, game("KCC", "BUF", 5, (3, 0), "9:30"))
    server.scorer.set_event(2, now, 1.0, goal=True)
    feed(server, game("DAL", "NYG", 5, (7, 3), "9:30"), start=now)
    assert (2, 0) not in server.event_at
    assert "new game on tab 2: BUF-KCC -> DAL-NYG" in caplog.text
    assert server.scorer.streams[2].goal_at == float("-inf")
    assert server.watchers[(2, 0)].score == (7, 3)


def test_flagged_tab_blind_spot_below_the_old_peak_is_gone():
    server = make(True)
    now = feed(server, game("KCC", "BUF", 5, (21, 14), "9:30"))
    now = feed(server, game("DAL", "NYG", 5, (0, 0), "9:30"), start=now)
    feed(server, game("DAL", "NYG", 5, (7, 0), "9:30"), start=now)
    assert (2, 0) in server.event_at                             # the new game's touchdown is seen


def test_unflagged_tab_behaves_as_before():
    server = make(False)
    now = feed(server, game("KCC", "BUF", 5, (21, 14), "9:30"))
    feed(server, game("DAL", "NYG", 5, (0, 0), "9:30"), start=now)
    assert server.watchers[(2, 0)]._peak == (21, 14)             # the old behaviour: 0-0 is a misread below the peak
    assert server.identities[(2, 0)].key == frozenset({"DAL", "NYG"})   # the identity is only tracked (dataset)


def test_a_studio_shot_or_ticker_does_not_reset():
    server = make(True)
    now = feed(server, game("KCC", "BUF", 5, (21, 14), "9:30"))
    studio = [{"texts": ["REDZONE", "LIVE"], "clocks": []}] * 4
    ticker = [board("DAL", 7, "NYG", 3, extra=("MIA", "NEP", "LAC", "DEN"))] * 4
    feed(server, studio + ticker, start=now)
    assert server.watchers[(2, 0)]._peak == (21, 14) and server.identities[(2, 0)].key == frozenset({"KCC", "BUF"})


def test_a_flagged_tab_still_honours_the_teams_the_user_typed():
    # user 2026-10-18: anything typed by hand in the extension overrides the automatic readout, on a multiplex tab too
    reg = {"type": "register", "tab_id": 2, "role": "LEFT", "sport": "American football", "teams": "Chiefs vs Bills", "favourites": [{"name": "Chiefs", "mult": 2}]}
    server = make(True)
    server.handle_message({**reg, "multiplex": True})
    server._update_favourite(2, 100.0)
    assert server.scorer.streams[2].favourite == 2.0
    assert server._feed_text(2) == "Chiefs vs Bills"


def test_a_flagged_tab_ignores_the_page_text_but_not_the_typed_teams():
    server = make(True)
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT", "sport": "American football", "multiplex": True})
    server.page_text[2] = "New Orleans Saints / Minnesota Vikings"
    assert server._feed_text(2) == ""                       # the page title belongs to the channel
    server.handle_message({"type": "register", "tab_id": 2, "role": "LEFT", "sport": "American football", "multiplex": False})
    assert server._feed_text(2) == "New Orleans Saints / Minnesota Vikings"   # an unflagged tab uses it as before


def test_new_game_drops_fav_seen_rule_seen_and_buildup():
    server = make(True)
    now = feed(server, game("KCC", "BUF", 5, (3, 0)))
    server.fav_seen[(2, 0)] = (2.0, now)
    server.rule_seen[(2, 0)] = ({"MAIN"}, now)
    server.buildup.note(2, now, 0.5)
    feed(server, game("DAL", "NYG", 4, (0, 0)), start=now)
    assert (2, 0) not in server.fav_seen and (2, 0) not in server.rule_seen
    assert server.buildup.cues(2, now + 12) == []


def test_dataset_rows_carry_the_game_key():
    server = make(True)
    feed(server, game("KCC", "BUF", 5, (3, 0)))
    rows = snapshot_rows(server, 200.0, 1.0)
    assert next(r for r in rows if r["tab"] == 2)["tiles"][0]["game_key"] == "BUF-KCC"


def test_a_channel_label_is_not_a_team_code():
    # live 2026-10-18, CANAL+ Premier League multiplex: the '#FOOT' logo read as a team code gave pairs like FOOT-MUN / FOOT-LEE (three codes with
    # both teams = no pair at all)
    assert team_pair(["#FOOT", "BHA", "3", "0", "CRY", "DIRECT"]) == frozenset({"BHA", "CRY"})
    assert team_pair(["#FOOT", "BHA", "direct"]) is None   # one team and the label: still no pair
    assert key_text(team_pair(["FOOT", "SUN", "BOU"])) == "BOU-SUN"
