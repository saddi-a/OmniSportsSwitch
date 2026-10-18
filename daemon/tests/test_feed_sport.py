"""One league -> sport table (`sport_choice.sport_of_league`). Before 2026-10-18 `backtest.feed_sport`, `dataset_builder.feed_sport` and the server's
`sport_from_feed(LEAGUES[league][0])` each held their own copy, with different spellings: these tests pin what every caller must keep getting."""
import pytest

from daemon import backtest, dataset_builder, feed
from daemon.sport_choice import sport_from_feed, sport_of_league

SOCCER_LEAGUES = [k for k, v in feed.LEAGUES.items() if v[0] == "soccer"]
FOOTBALL_LEAGUES = [k for k, v in feed.LEAGUES.items() if v[0] == "american football"]


def test_the_league_table_has_both_sports():
    assert SOCCER_LEAGUES and FOOTBALL_LEAGUES and len(SOCCER_LEAGUES) + len(FOOTBALL_LEAGUES) == len(feed.LEAGUES)


@pytest.mark.parametrize("league", SOCCER_LEAGUES)
def test_soccer_leagues(league):
    assert sport_of_league(league) == "Football"
    assert backtest.feed_sport(league) == "Football"               # the daemon's own spelling
    assert dataset_builder.feed_sport(league) == "football"        # lower case: the dataset rows keep this spelling


@pytest.mark.parametrize("league", FOOTBALL_LEAGUES)
def test_american_football_leagues(league):
    assert sport_of_league(league) == "American football"
    assert backtest.feed_sport(league) == "American football"
    assert dataset_builder.feed_sport(league) == "american football"


@pytest.mark.parametrize("league", ["", "nba", "ita.2", "NFL", None])
def test_an_unknown_league(league):
    assert sport_of_league(league) is None
    assert backtest.feed_sport(league) == "unknown"                # the two analysis tools keep their 'unknown' marker
    assert dataset_builder.feed_sport(league) == "unknown"


@pytest.mark.parametrize("league", list(feed.LEAGUES))
def test_the_server_path_gives_the_same_answer(league):
    assert sport_from_feed(feed.LEAGUES[league][0]) == sport_of_league(league)
