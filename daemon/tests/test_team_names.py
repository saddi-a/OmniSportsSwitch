import dataclasses
import json
import pathlib
import re

import pytest

from daemon import feed, team_names as table
from daemon.feed_map import FeedMapper, shows_game, team_names

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SOCCER = json.loads((FIXTURES / "feed_soccer_scoreboard.json").read_text(encoding="utf-8"))
TOP5 = ("eng.1", "esp.1", "ita.1", "ger.1", "fra.1")


def game(home, away, league="eng.1"):
    """A feed snapshot between two teams, given as (abbr, name)."""
    base = feed.parse_scoreboard(SOCCER, "ita.1")[0]
    return dataclasses.replace(base, league=league, home=feed.Team("1", *home), away=feed.Team("2", *away))


def tokens(s):
    return re.findall(r"[A-Z0-9&]+", s)


def contained(a, b):
    ta, tb = tokens(a), tokens(b)
    return any(tb[i:i + len(ta)] == ta for i in range(len(tb) - len(ta) + 1))


# -- the table itself -------------------------------------------------------------------------------------------------
def test_table_loads_one_file_per_sport():
    assert set(table.AMBIGUOUS) == {"football", "nfl", "cfb"}
    assert {c.group for c in table.CLUBS} == {"football", "nfl", "cfb"}
    assert {c.section for c in table.CLUBS} == {*TOP5, "uefa", "nfl", "college-football"}
    count = lambda s: sum(c.section == s for c in table.CLUBS)
    assert (count("nfl"), count("college-football")) == (32, 138)
    assert all(18 <= count(s) <= 20 for s in TOP5)
    assert sorted(p.name for p in table.TABLE_DIR.glob("*.txt")) == ["cfb.txt", "football.txt", "nfl.txt"]


def test_a_new_sport_is_a_new_file(tmp_path):
    (tmp_path / "hockey.txt").write_text("[nhl]\nBOS | Boston Bruins | BRUINS\n[ambiguous hockey]\nNY | New York Rangers\n", encoding="utf-8")
    clubs, amb = table.load(tmp_path)
    assert [(c.group, c.name, c.aliases) for c in clubs] == [("hockey", "Boston Bruins", ("BRUINS",))]
    assert amb == {"hockey": {"NY": "New York Rangers"}}


def test_no_alias_belongs_to_two_clubs_of_the_same_sport():
    owner = {}
    for c in table.CLUBS:
        for a in c.aliases:
            key = (c.group, a)
            assert owner.setdefault(key, c.name) == c.name, f"{a!r} is an alias of {owner[key]} and {c.name}"


def test_no_alias_equals_another_clubs_name_or_abbreviation_in_the_same_league():
    strings = {}
    for c in table.CLUBS:
        for s in (table.fold(c.abbr), table.fold(c.name)):
            strings.setdefault((c.section, s), set()).add(c.name)
    for c in table.CLUBS:
        for a in c.aliases:
            others = strings.get((c.section, a), set()) - {c.name}
            assert not others, f"{a!r} of {c.name} is also the name/code of {others}"


def test_no_alias_is_a_whole_word_part_of_another_clubs_name():
    """GEORGIA inside GEORGIA TECH, MILAN inside INTER MILAN: such a string is a candidate, not an alias."""
    names = {(c.group, table.fold(c.name)): c.name for c in table.CLUBS}
    for c in table.CLUBS:
        for a in c.aliases:
            for (group, n), owner in names.items():
                if group == c.group and owner != c.name:
                    assert not contained(a, n), f"{a!r} of {c.name} is inside the name {owner!r}"


def test_ambiguous_codes_are_never_aliases_but_candidates_of_every_club_they_may_mean():
    for group, codes in table.AMBIGUOUS.items():
        for c in table.CLUBS:
            if c.group == group:
                assert not set(codes) & set(c.aliases), c.name
                assert not set(codes) & set(table.aliases_for(c.name)), c.name
    assert {"REAL", "FCB", "UNITED", "CITY", "LEV", "MUN"} <= set(table.AMBIGUOUS["football"])
    assert {"MSU", "OSU", "UT", "USC"} <= set(table.AMBIGUOUS["cfb"])
    assert table.CANDIDATES["football"]["REAL"] == {"Real Madrid", "Real Betis", "Real Sociedad"}
    assert {"Barcelona", "Bayern Munich"} <= table.CANDIDATES["football"]["FCB"]
    assert table.CANDIDATES["football"]["MUN"] == {"Manchester United", "Bayern Munich"}
    assert table.CANDIDATES["cfb"]["MSU"] >= {"Michigan State Spartans", "Mississippi State Bulldogs"}
    assert {"Georgia Bulldogs", "Georgia Tech Yellow Jackets", "Georgia State Panthers"} <= table.CANDIDATES["cfb"]["GEORGIA"]
    assert "MSU" in table.candidates_for("Mississippi State Bulldogs") and "REAL" in table.candidates_for("Real Betis")


def test_two_letter_codes_are_candidates_only_for_the_listed_clubs():
    assert "OM" in table.candidates_for("Marseille") and "OL" in table.candidates_for("Lyon")
    assert "KC" in table.candidates_for("Kansas City Chiefs") and "ND" in table.candidates_for("Notre Dame Fighting Irish")
    assert table.CANDIDATES["nfl"]["LA"] == {"Los Angeles Chargers", "Los Angeles Rams"}
    every = {code for c in table.CLUBS for code in table.candidates_for(c.name)}
    assert not every & {"IN", "ON", "AT", "OR", "IT", "IS", "AM", "PM", "TO", "OF"}
    short = {n for c in table.CLUBS for n in team_names(feed.Team("1", c.abbr, c.name)) if len(n) < 3}
    assert short and short <= every


def test_doubtful_entries_are_not_active():
    assert any(c.doubtful for c in table.CLUBS)
    for c in table.CLUBS:
        assert not set(c.doubtful) & set(c.aliases)
        for d in c.doubtful:
            assert d not in table.aliases_for(c.name)


def test_every_espn_team_code_is_covered():
    """tests/fixtures/espn_team_abbrs.json holds only the team codes ESPN listed on 2026-10-13 (eng.1 ... college-football FBS)."""
    codes = json.loads((FIXTURES / "espn_team_abbrs.json").read_text(encoding="utf-8"))
    assert set(codes) == {*TOP5, "uefa.champions", "uefa.europa", "nfl", "college-football"}
    football_all = {c.abbr for c in table.CLUBS if c.group == "football"}
    for league, abbrs in codes.items():
        pool = {c.abbr for c in table.CLUBS if c.section == league} if league in (*TOP5, "nfl", "college-football") else football_all
        assert not set(abbrs) - pool, f"{league}: not in the table: {sorted(set(abbrs) - pool)}"
    assert len(codes["college-football"]) >= 130


def test_the_table_only_adds_to_the_hand_written_aliases(monkeypatch):
    teams = [feed.Team("1", c.abbr, c.name) for c in table.CLUBS]
    with_table = {t: set(team_names(t)) for t in teams}
    team_names.cache_clear()
    monkeypatch.setattr(table, "aliases_for", lambda name: ())
    monkeypatch.setattr(table, "candidates_for", lambda name: ())
    try:
        without = {t: set(team_names(t)) for t in teams}
    finally:
        team_names.cache_clear()
    assert all(without[t] <= with_table[t] or {n for n in without[t] if len(n) >= 3} <= with_table[t] for t in teams)
    assert sum(len(with_table[t]) - len(without[t]) for t in teams) > 400


# -- the mapper -------------------------------------------------------------------------------------------------------
def test_known_broadcast_codes_still_map():
    atm_man = game(("ATM", "Atlético Madrid"), ("MAN", "Manchester United"), "uefa.champions")
    assert shows_game(["ATM 1", "MUN 0"], atm_man)
    assert shows_game(["ATLETICO", "MAN U"], atm_man)
    lille = game(("LILL", "Lille"), ("RCL", "Lens"), "fra.1")
    assert shows_game(["LIL 1", "LENS 0"], lille)
    psg = game(("PSG", "Paris Saint-Germain"), ("OLM", "Marseille"), "fra.1")
    assert shows_game(["PARIS SG", "MAR"], psg) and shows_game(["PSG", "OLYMPIQUE DE MARSEILLE"], psg)
    assert shows_game(["PARIS", "MARSEILLE"], psg)


def test_new_soccer_codes_from_the_table():
    mnc_liv = game(("MNC", "Manchester City"), ("LIV", "Liverpool"))
    assert shows_game(["MAN CITY", "LFC"], mnc_liv)
    leverkusen = game(("B04", "Bayer Leverkusen"), ("DOR", "Borussia Dortmund"), "ger.1")
    assert shows_game(["LEVERKUSEN", "BVB"], leverkusen)
    lyon = game(("LYON", "Lyon"), ("REN", "Stade Rennais"), "fra.1")
    assert shows_game(["LYONNAIS", "RENNES"], lyon)
    assert shows_game(["OL", "RENNES"], lyon)               # OL: a 2-letter candidate code, the other team is clear
    assert not shows_game(["OL"], lyon) and not shows_game(["OL", "OL"], lyon)
    assert not shows_game(["ol", "RENNES"], lyon)           # 2-letter codes must be upper case
    spurs = game(("TOT", "Tottenham Hotspur"), ("NEW", "Newcastle United"))
    assert shows_game(["SPURS", "NUFC"], spurs)


def test_candidate_codes_map_to_every_club_they_may_mean_but_never_decide_alone():
    rma_bet = game(("RMA", "Real Madrid"), ("BET", "Real Betis"), "esp.1")
    assert not shows_game(["REAL"], rma_bet) and not shows_game(["REAL 1", "REAL 0"], rma_bet)   # one token cannot be both teams
    assert shows_game(["RMA", "BETIS"], rma_bet) and shows_game(["REAL", "BETIS"], rma_bet) and shows_game(["REAL", "BET"], rma_bet)
    bar_bay = game(("BAR", "Barcelona"), ("MUN", "Bayern Munich"), "uefa.champions")
    assert not shows_game(["FCB", "FCB"], bar_bay) and not shows_game(["FCB"], bar_bay)
    assert shows_game(["FCB 2", "BAR 1"], bar_bay) and shows_game(["BARCA", "FCB"], bar_bay)   # FCB is Bayern here, BAR decides
    assert "REAL" in team_names(feed.Team("1", "RMA", "Real Madrid")) and "FCB" in team_names(feed.Team("1", "MUN", "Bayern Munich"))
    ath_atm = game(("ATH", "Athletic Club"), ("ATM", "Atlético Madrid"), "esp.1")
    assert not shows_game(["ATH"], ath_atm) and shows_game(["ATH", "ATM"], ath_atm)


def test_bayern_and_man_united_share_mun_the_opposing_team_decides():
    mun_mun = game(("MAN", "Manchester United"), ("MUN", "Bayern Munich"), "uefa.champions")
    assert not shows_game(["MUN"], mun_mun) and not shows_game(["MUN", "MUN"], mun_mun)
    assert shows_game(["MUN", "BAYERN"], mun_mun) and shows_game(["MUN", "MANU"], mun_mun)
    man_atm = game(("ATM", "Atlético Madrid"), ("MAN", "Manchester United"), "uefa.champions")
    bay_atm = game(("ATM", "Atlético Madrid"), ("MUN", "Bayern Munich"), "uefa.champions")
    assert shows_game(["ATM", "MUN"], man_atm) and shows_game(["ATM", "MUN"], bay_atm)   # both fit: score/clock must decide


def test_nfl_examples():
    kc_nyj = game(("KC", "Kansas City Chiefs"), ("NYJ", "New York Jets"), "nfl")
    assert shows_game(["CHIEFS 7", "NY JETS 0"], kc_nyj)
    assert shows_game(["KANSAS CITY", "JETS"], kc_nyj)
    assert shows_game(["KC", "NYJ"], kc_nyj) and shows_game(["KC", "JETS"], kc_nyj)
    assert not shows_game(["KC"], kc_nyj)
    assert shows_game(["NEW YORK", "KANSAS CITY"], kc_nyj)    # NEW YORK may be either New York team: a candidate
    assert not shows_game(["NEW YORK", "NEW YORK"], kc_nyj)
    wsh = game(("WSH", "Washington Commanders"), ("JAX", "Jacksonville Jaguars"), "nfl")
    assert shows_game(["WAS", "JAC"], wsh)
    gb_no = game(("GB", "Green Bay Packers"), ("NO", "New Orleans Saints"), "nfl")
    assert shows_game(["GB", "SAINTS"], gb_no) and shows_game(["GB", "NO"], gb_no) and not shows_game(["GB"], gb_no)


def test_cfb_examples_and_candidate_pairs():
    ohio_psu = game(("OSU", "Ohio State Buckeyes"), ("PSU", "Penn State Nittany Lions"), "college-football")
    assert shows_game(["OHIO ST", "PENN ST"], ohio_psu) and shows_game(["OSU", "PSU"], ohio_psu)
    bama = game(("ALA", "Alabama Crimson Tide"), ("UGA", "Georgia Bulldogs"), "college-football")
    assert shows_game(["BAMA", "UGA"], bama) and shows_game(["BAMA", "GEORGIA"], bama)
    tamu = game(("TA&M", "Texas A&M Aggies"), ("TTU", "Texas Tech Red Raiders"), "college-football")
    assert shows_game(["TXAM", "TTU"], tamu) and shows_game(["TAMU", "TEXAS TECH"], tamu)
    # MSU may be Michigan State or Mississippi State; the other team resolves it
    msst_mich = game(("MSST", "Mississippi State Bulldogs"), ("MICH", "Michigan Wolverines"), "college-football")
    msu_mich = game(("MSU", "Michigan State Spartans"), ("MICH", "Michigan Wolverines"), "college-football")
    assert shows_game(["MICH", "MSU"], msst_mich) and shows_game(["MICH", "MSU"], msu_mich)
    assert not shows_game(["MSU"], msst_mich) and not shows_game(["MSU", "MSU"], msst_mich)
    assert shows_game(["MICH", "MSST"], msst_mich) and not shows_game(["MICH", "MSST"], msu_mich)
    for abbr, name in (("OKST", "Oklahoma State Cowboys"), ("ORST", "Oregon State Beavers"), ("OSU", "Ohio State Buckeyes")):
        assert "OSU" in team_names(feed.Team("1", abbr, name))
    nd = game(("ND", "Notre Dame Fighting Irish"), ("USC", "USC Trojans"), "college-football")
    assert shows_game(["ND", "USC"], nd) and not shows_game(["ND"], nd) and not shows_game(["ND", "ND"], nd)


def live(home, away, league):
    return dataclasses.replace(game(home, away, league), game_id=league + home[0] + away[0], state="in", clock_s=4980.0, period=2,
                               home_score=0, away_score=0)


def mapper_with(*snaps):
    m = FeedMapper()
    m.set_games(list(snaps))
    return m


def test_a_two_letter_code_plus_a_clear_opponent_locks_marseille():
    om_lil = live(("OLM", "Marseille"), ("LILL", "Lille"), "fra.1")
    m = mapper_with(om_lil)
    for _ in range(4):
        m.observe(1, 0, ["OM 0", "LIL 0"], (0, 0), ["83:00"])
    assert m.mapping(1, 0) is not None and m.mapping(1, 0).game_id == om_lil.game_id


def test_kc_with_a_clear_opponent_locks_kansas_city_and_nd_alone_locks_nothing():
    kc = live(("KC", "Kansas City Chiefs"), ("DEN", "Denver Broncos"), "nfl")
    nd = live(("ND", "Notre Dame Fighting Irish"), ("USC", "USC Trojans"), "college-football")
    m = mapper_with(kc, nd)
    for _ in range(5):
        m.observe(1, 0, ["KC 0", "DEN 0"], (0, 0), ["83:00"])
        m.observe(2, 0, ["ND 0"], (0, 0), ["83:00"])
        m.observe(3, 0, ["ND", "ND"], (0, 0), ["83:00"])
    assert m.mapping(1, 0).game_id == kc.game_id
    assert m.mapping(2, 0) is None and m.mapping(3, 0) is None


def test_msu_and_mich_resolve_through_the_other_team():
    a = live(("MSU", "Michigan State Spartans"), ("OSU", "Ohio State Buckeyes"), "college-football")
    b = live(("MSST", "Mississippi State Bulldogs"), ("MICH", "Michigan Wolverines"), "college-football")
    m = mapper_with(a, b)
    for _ in range(4):
        m.observe(1, 0, ["MSU 0", "MICH 0"], (0, 0), ["83:00"])        # only b has MICH
        m.observe(2, 0, ["MSU 0", "OSU 0"], (0, 0), ["83:00"])         # only a has OSU
    assert m.mapping(1, 0).game_id == b.game_id and m.mapping(2, 0).game_id == a.game_id


def test_two_games_that_both_fit_stay_ambiguous_no_lock():
    a = live(("RMA", "Real Madrid"), ("BAR", "Barcelona"), "esp.1")
    b = live(("BET", "Real Betis"), ("BAR", "Barcelona"), "esp.1")
    m = mapper_with(a, b)
    lines = []
    for _ in range(5):
        lines += m.observe(1, 0, ["REAL 0", "BAR 0"], (0, 0), ["83:00"])
    assert m.mapping(1, 0) is None
    assert any("ambiguous" in line for line in lines)
    for _ in range(4):
        m.observe(2, 0, ["RMA 0", "BAR 0"], (0, 0), ["83:00"])          # a clear code breaks the tie
    assert m.mapping(2, 0).game_id == a.game_id


def test_noise_two_letter_words_never_lock_anything():
    m = mapper_with(live(("KC", "Kansas City Chiefs"), ("DEN", "Denver Broncos"), "nfl"),
                    live(("OLM", "Marseille"), ("LILL", "Lille"), "fra.1"),
                    live(("ND", "Notre Dame Fighting Irish"), ("USC", "USC Trojans"), "college-football"))
    for _ in range(6):
        m.observe(1, 0, ["IN", "ON", "AT", "OR", "TO", "OF", "NO 0", "ok"], (0, 0), ["83:00"])
        m.observe(2, 0, ["om", "nd", "kc", "in on at or"], (0, 0), ["83:00"])
    assert m.mapping(1, 0) is None and m.mapping(2, 0) is None


def test_a_lock_through_the_mapper_with_a_table_code():
    m = FeedMapper()
    snap = dataclasses.replace(game(("MAN", "Manchester United"), ("MNC", "Manchester City")), state="in", clock_s=4980.0,
                               period=2, home_score=1, away_score=0)
    m.set_games([snap])
    for _ in range(4):
        m.observe(1, 0, ["MUFC 1", "MCI 0"], (1, 0), ["83:00"])
    assert m.mapping(1, 0) is not None and m.mapping(1, 0).game_id == snap.game_id
