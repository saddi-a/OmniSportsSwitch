"""How much a game's situation adds to (or takes from) its interest: closeness and time left.

Pure logic. A late, close game is the most interesting; a blowout late in the game the least; early in the game
the situation matters little. The result is a number from -1 to +1 that the scorer multiplies by
`leverage_weight` and adds to the stream's score.

  closeness c   1 when the teams are at most one "score" apart, falling to 0 at a blowout (per sport, in Config)
  lateness  l   0 at the start, 1 in the last quarter / last half hour (overtime counts as the latest moment)
  leverage      c * (0.3 + 0.7 * l)  -  (1 - c) * l

Nothing known (score or period not read, unknown sport) gives 0: a missing reading never changes a swap.
"""

PERIOD_LATENESS = {1: 0.0, 2: 1 / 3, 3: 2 / 3, 4: 1.0, 5: 1.0}   # quarter 1..4, 5 = overtime
SOCCER_LATE_START_MIN, SOCCER_LATE_SPAN_MIN = 30.0, 55.0          # lateness 0 at 30', 1 from 85'


def _scale(sport: str, cfg) -> tuple[float, float] | None:
    if sport == "american football":
        return cfg.lev_us_football_close, cfg.lev_us_football_blowout
    if sport == "football":
        return cfg.lev_soccer_close, cfg.lev_soccer_blowout
    return None


def leverage(sport: str | None, score: tuple[int, int] | None, period: int | None, minute: float | None, cfg) -> float:
    cfg = cfg.for_sport(sport)   # the closeness limits as this sport sees them (per-sport overrides in settings.toml)
    scale = _scale((sport or "").strip().lower(), cfg)
    if scale is None or score is None:
        return 0.0
    if (sport or "").strip().lower() == "football":     # soccer: lateness from the game clock
        if minute is None:
            return 0.0
        lateness = min(max((minute - SOCCER_LATE_START_MIN) / SOCCER_LATE_SPAN_MIN, 0.0), 1.0)
    else:
        if period is None or period not in PERIOD_LATENESS:
            return 0.0
        lateness = PERIOD_LATENESS[period]
    close, blowout = scale
    gap = abs(score[0] - score[1])
    if gap <= close:
        closeness = 1.0
    elif gap >= blowout:
        closeness = 0.0
    else:
        closeness = (blowout - gap) / (blowout - close)
    return closeness * (0.3 + 0.7 * lateness) - (1.0 - closeness) * lateness


def tab_leverage(tiles, cfg) -> float:
    """Leverage of a whole tab: the best of its video areas. `tiles` is a list of (sport, score, period, minute)."""
    values = [leverage(sport, score, period, minute, cfg) for sport, score, period, minute in tiles]
    return max(values, default=0.0)
