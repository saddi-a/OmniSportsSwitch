import dataclasses
import json
import pathlib

from daemon import feed
from daemon.feed_map import DROP_AFTER, FeedMapper, shows_game, team_names

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def games():
    return feed.parse_scoreboard(json.loads((FIXTURES / "feed_soccer_scoreboard.json").read_text(encoding="utf-8")), "ita.1")


def mapper(snaps=None):
    m = FeedMapper()
    m.set_games(snaps or games())
    return m


def read(m, texts=("COMO", "ROMA", "0", "0"), score=(0, 0), clock="83:10", tab=1, tile=0):
    return m.observe(tab, tile, list(texts), score, [clock] if clock else [])


def test_names_cover_abbreviation_full_name_prefix_and_alias():
    como, roma = games()[0].home, games()[0].away
    assert {"COMO", "COME"} <= set(team_names(como))
    assert {"ROMA", "AS ROMA", "ROME"} <= set(team_names(roma))


def test_scorebug_codes_that_differ_from_the_feeds_abbreviation():
    """Seen live 2026-10-11: ESPN says MNC for Manchester City, the Canal+ scorebug says MCI."""
    liv = feed.Team("364", "LIV", "Liverpool")
    mnc = feed.Team("382", "MNC", "Manchester City")
    snap = dataclasses.replace(games()[0], home=liv, away=mnc)
    assert shows_game(["LIV", "0", "MCI", "CANAL+"], snap)


def test_both_teams_needed_and_whole_words_only():
    snap = games()[0]
    assert shows_game(["COMO 0", "ROMA 0"], snap)
    assert shows_game(["C\u00f4me", "Rome"], snap)
    assert not shows_game(["COMO 0"], snap)
    assert not shows_game(["COMO", "ROMANIA"], snap)
    assert shows_game(["Como - Roma"], snap)


def test_lock_after_enough_consistent_readings_and_logs_once():
    m = mapper()
    lines = []
    for _ in range(4):
        lines += read(m)
    assert len(lines) == 1 and "ita.1 COMO-ROMA" in lines[0] and "via ocr" in lines[0]
    assert m.mapping(1, 0).verified


def test_one_or_two_readings_are_not_enough():
    m = mapper()
    assert read(m) == [] and read(m) == []
    assert m.mapping(1, 0) is None


def test_one_team_alone_stays_unmapped():
    m = mapper()
    for _ in range(6):
        read(m, texts=("COMO", "0", "0"))
    assert m.mapping(1, 0) is None


def test_wrong_score_never_locks():
    """A replay or old game with the same teams: the OCR score is higher than the feed ever had."""
    m = mapper()
    for _ in range(8):
        read(m, score=(3, 1))
    assert m.mapping(1, 0) is None


def test_timeline_lookup_uses_the_stream_clock_not_the_latest_score():
    snaps = games()
    m = mapper(snaps)
    # the feed saw 0-0 until 70', then 1-0 for Como
    early = dataclasses.replace(snaps[0], clock_s=4200.0, home_score=0, away_score=0)
    late = dataclasses.replace(snaps[0], clock_s=5040.0, home_score=1, away_score=0)
    m = FeedMapper()
    m.set_games([early, snaps[1]])
    m.set_games([late, snaps[1]])
    # the stream is at 80:00 and shows 1-0: the feed only had 0-0 then for that score -> does not fit
    for _ in range(5):
        read(m, score=(1, 0), clock="80:00")
    assert m.mapping(1, 0) is None
    # a stream at 83:20 showing 1-0 fits
    for _ in range(5):
        read(m, score=(1, 0), clock="83:20", tile=1)
    assert m.mapping(1, 1) is not None


def test_stream_ahead_of_the_feed_clock_does_not_fit():
    m = mapper()
    for _ in range(6):
        read(m, clock="120:00")
    assert m.mapping(1, 0) is None


def test_game_not_started_does_not_fit():
    snaps = games()
    m = mapper(snaps)
    for _ in range(6):
        m.observe(1, 0, ["LAZ", "MON"], (0, 0), ["10:00"])
    assert m.mapping(1, 0) is None


def test_popup_text_lowers_the_bar_and_names_the_source():
    m = mapper()
    m.set_text(1, "Como vs Roma")
    lines = []
    for _ in range(2):
        lines += read(m)
    assert len(lines) == 1 and "ocr+text" in lines[0]


def test_popup_only_is_logged_unverified_and_later_upgraded():
    m = mapper()
    m.set_text(1, "Roma - Como")
    lines = m.observe(1, 0, [], None, [])
    assert len(lines) == 1 and "unverified" in lines[0]
    assert not m.mapping(1, 0).verified
    lines = []
    for _ in range(3):
        lines += read(m)
    assert m.mapping(1, 0).verified and len(lines) == 1


def test_lock_survives_ads_and_is_dropped_by_repeated_disagreement():
    m = mapper()
    for _ in range(4):
        read(m)
    for _ in range(20):
        m.observe(1, 0, ["ADVERT"], None, [])       # an ad: no scoreboard, no vote
    assert m.mapping(1, 0) is not None
    lines = []
    for _ in range(DROP_AFTER):
        lines += read(m, score=(5, 4))
    assert m.mapping(1, 0) is None and "dropped" in lines[-1]


def test_two_games_with_the_same_teams_are_ambiguous_not_guessed():
    snaps = games()
    twin = dataclasses.replace(snaps[0], game_id="999")
    m = mapper([snaps[0], twin])
    lines = []
    for _ in range(5):
        lines += read(m)
    assert m.mapping(1, 0) is None and len(lines) == 1 and "ambiguous" in lines[0]


def test_each_tile_is_mapped_on_its_own_and_forget_clears_the_tab():
    m = mapper()
    for _ in range(4):
        read(m, tile=0)
        read(m, texts=("LAZ", "MON"), score=(0, 0), clock="10:00", tile=1)
    assert m.mapping(1, 0) is not None and m.mapping(1, 1) is None
    m.forget(1)
    assert m.mapping(1, 0) is None


def test_non_soccer_uses_only_the_scores_never_go_down_rule():
    snaps = feed.parse_scoreboard(json.loads((FIXTURES / "feed_nfl_scoreboard.json").read_text(encoding="utf-8")), "nfl")
    live = dataclasses.replace(snaps[0], state="in", home_score=14, away_score=10)
    m = FeedMapper()
    m.set_games([live])
    assert m.fits(live, (live.home_score, live.away_score), 300.0) is True
    assert m.fits(live, (live.home_score + 7, live.away_score), 300.0) is False


def test_the_server_gives_the_mapper_the_popup_teams_and_forgets_them():
    from daemon.scoring import Config, Scorer
    from daemon.server import Server
    server = Server(Scorer(Config()))
    server.handle_message({"type": "register", "tab_id": 7, "teams": "Como vs Roma"})
    assert server.feed_map.text[7] == "Como vs Roma"
    server.handle_message({"type": "unregister", "tab_id": 7})
    assert 7 not in server.feed_map.text


def test_canal_plus_header_with_a_slash_and_the_popup_text_forms():
    """Seen live 2026-10-11 (tab 485660202): header 'LIVERPOOL / MANCHESTER CITY', scorebug 'LIV 0 0 MCI 61:16'."""
    snap = dataclasses.replace(games()[0], home=feed.Team("364", "LIV", "Liverpool"), away=feed.Team("382", "MNC", "Manchester City"))
    assert shows_game(["LIVERPOOL / MANCHESTER CITY", "Premier League - 6e journée"], snap)
    for text in ("Liverpool vs Man City", "Liverpool - Man City", "Man City vs Liverpool", "liverpool v manchester city", "LIV vs MCI"):
        assert shows_game([text], snap), text
    assert not shows_game(["Liverpool vs Everton"], snap)


def test_popup_text_with_a_slash_header_locks_through_the_text_path_too():
    snap = dataclasses.replace(games()[0], home=feed.Team("364", "LIV", "Liverpool"), away=feed.Team("382", "MNC", "Manchester City"))
    m = mapper([snap])
    m.set_text(1, "Liverpool vs Man City")
    lines = []
    for _ in range(2):
        lines += m.observe(1, 0, ["LIVERPOOL / MANCHESTER CITY"], (0, 0), ["61:16"])
    assert len(lines) == 1 and "ocr+text" in lines[0]


def test_man_united_broadcast_code_mun_matches_atm_man_but_not_man_city():
    """Seen live 2026-10-13 (Champions League): feed ATM-MAN, the scorebug reads ATM and MUN."""
    atm = feed.Team("1068", "ATM", "Atletico Madrid")
    man = feed.Team("360", "MAN", "Manchester United")
    snap = dataclasses.replace(games()[0], home=atm, away=man)
    assert shows_game(["12.32", "ATM", "1 0", "MUN"], snap)
    for text in ("MANU", "Man United", "Manchester United", "Man Utd"):
        assert shows_game(["ATM", text], snap)
    mnc = feed.Team("382", "MNC", "Manchester City")
    city = dataclasses.replace(games()[0], home=atm, away=mnc)
    assert not shows_game(["ATM", "MUN"], city)
    assert not shows_game(["ATM", "MCI"], snap)


def test_lille_scorebug_code_lil():
    ars = feed.Team("359", "ARS", "Arsenal")
    lill = feed.Team("166", "LILL", "Lille")
    snap = dataclasses.replace(games()[0], home=ars, away=lill)
    assert shows_game(["ARS", "LIL"], snap)

