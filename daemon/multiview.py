"""Chooses which tile of a multiview tab should have the sound (audio focus).

Pure logic: callers pass `now` in seconds. A tile is a dict:
  index   position of the <video> in the page
  audible True for the tile that currently has sound
  state   "live" | "break" | "over" (from game_state)
  event   0..1, how fresh a score change on this tile is (1 = just now)
  crowd   0..1 crowd-noise probability, only known for the audible tile
  ad      True while the tile shows the player's ad placeholder
  score   optional: the tile's own score from the Scorer (`Scorer.tile_scores`: its picture signals read with its own sport, plus the
          audio for the audible tile). When every tile has one it replaces event + crowd, so a cue, a review or a late close game on a
          silent tile can also win the sound. It already carries the tile's game state and ads.
"""
from dataclasses import dataclass, field

STATUS_MULT = {"live": 1.0, "break": 0.25, "over": 0.0}


@dataclass
class FocusConfig:
    switch_ratio: float = 1.5     # another tile must beat the focused one by this factor
    min_dwell_s: float = 30.0     # minimum time between focus changes in one tab
    break_dwell_s: float = 10.0   # the same while the focused tile shows an ad or a break/over (leave it soon, but a failing click is no loop)
    event_weight: float = 1.0     # how much a fresh score change counts
    crowd_weight: float = 1.0     # how much crowd noise on the focused tile counts
    floor: float = 0.05           # every live tile has at least this much, so scores never reach zero
    ad_penalty: float = 0.5       # a tile showing an ad has negative interest, so focus moves off it


@dataclass
class TileFocuser:
    config: FocusConfig = field(default_factory=FocusConfig)
    _last_change: dict[int, float] = field(default_factory=dict)

    def tile_score(self, tile: dict) -> float:
        c = self.config
        if tile.get("ad"):
            return -c.ad_penalty
        if tile.get("score") is not None:   # the scorer's score of this tile (it already carries the state multiplier)
            return c.floor * STATUS_MULT.get(tile.get("state", "live"), 1.0) + max(tile["score"], 0.0)
        interest = c.floor + c.event_weight * tile.get("event", 0.0) + c.crowd_weight * tile.get("crowd", 0.0)
        return interest * STATUS_MULT.get(tile.get("state", "live"), 1.0)

    def decide(self, tab_id: int, now: float, tiles: list[dict]) -> int | None:
        """Index of the tile to focus, or None to leave things as they are."""
        if len(tiles) < 2:
            return None
        if any(t.get("score") is None for t in tiles):   # a tile without a scorer score: all are judged the old way (event + crowd)
            tiles = [{k: v for k, v in t.items() if k != "score"} for t in tiles]
        focused = next((t for t in tiles if t.get("audible")), None)
        best = max(tiles, key=self.tile_score)
        dwell = self.config.min_dwell_s
        if focused is not None and (focused.get("ad") or focused.get("state") in ("break", "over")):
            dwell = self.config.break_dwell_s
        if now - self._last_change.get(tab_id, float("-inf")) < dwell:
            return None
        if focused is None:
            return None if self.tile_score(best) <= 0 else self._switch(tab_id, now, best["index"])
        if best["index"] == focused["index"]:
            return None
        if self.tile_score(best) <= self.tile_score(focused) * self.config.switch_ratio:
            return None
        return self._switch(tab_id, now, best["index"])

    def _switch(self, tab_id: int, now: float, index: int) -> int:
        self._last_change[tab_id] = now
        return index
