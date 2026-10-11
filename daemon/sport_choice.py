"""Which sport a tab is: the one source with the highest priority that knows. Pure logic.

The user's order (2026-10-11), a higher source always replaces a lower one:
  popup  the sport picked on the tab in the popup
  hard   a scorebug cue (down and distance, quarter, a clock above 20 min): `SportDetector.sport`
  feed   the league of the game the tab was matched to in the outside feed (`FeedMapper.mapping(tab, tile).league` -> its sport)
  title  league words in the tab title sent by the extension
  weak   league words on the screen (`SportDetector.weak_sport`, needs its own vote)
  memory the sport last seen on this tab (`SportMemory`): only a start guess
"""
from daemon.sport_detect import BASKETBALL, FOOTBALL, SOCCER

SOURCES = ("popup", "hard", "feed", "title", "weak", "memory")
_KNOWN = {name.casefold(): name for name in (FOOTBALL, SOCCER, BASKETBALL)}
_FEED_SPORTS = {"soccer": SOCCER, "american football": FOOTBALL, "basketball": BASKETBALL}   # the sport names of `feed.LEAGUES`


def _known(name: str | None) -> str | None:
    return _KNOWN.get((name or "").strip().casefold())


def sport_from_feed(feed_sport: str | None) -> str | None:
    """Our sport name for the feed's ("soccer", "american football", "basketball"); None for anything else."""
    return _FEED_SPORTS.get((feed_sport or "").strip().casefold())


def choose_sport(popup=None, hard=None, feed=None, title=None, weak=None, memory=None) -> tuple[str | None, str | None]:
    """(sport, source) of the highest source that names a known sport, else (None, None)."""
    given = {"popup": popup, "hard": hard, "feed": feed, "title": title, "weak": weak, "memory": memory}
    for source in SOURCES:
        sport = _known(given[source])
        if sport:
            return sport, source
    return None, None
