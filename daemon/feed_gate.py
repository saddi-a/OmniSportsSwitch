"""Stage 2 of the external feed: the clock gate in SHADOW mode (measuring only: nothing here changes scoring or swaps).

For every tile that `feed_map` has tied to a game, an event of the feed (goal, red card, touchdown...) is compared with the
stream's own game clock. When the stream gets within `lead_s` seconds of the event's moment, the gate logs "would fire": the
moment the director would have switched early. When the real OCR score change follows, the log says how many seconds later
it came: that is the lead the feed would have given. Events that are already behind the stream (rewind, late mapping),
replays and unreadable clocks never fire. Spoilers are impossible by construction: an event is looked at only against the
stream's clock, never released by the time it was first seen.
"""
import dataclasses

from daemon import feed
from daemon.feed_map import parse_clock_s

KINDS = {
    "soccer": {"goal", "penalty goal", "own goal", "red card", "penalty"},
    "american football": {"touchdown", "field goal good", "safety"},
}
SCORING = {"goal", "penalty goal", "own goal", "touchdown", "field goal good", "safety"}   # these change the score
SKIP_BEHIND_S = 30.0      # an event this far behind the stream clock is not "early" any more
CONFIRM_S = 120.0         # a real score change this soon after a would-fire belongs to it
ARM_READINGS = 2          # the condition must hold on this many readings in a row (a clock misread once must not fire)
MAX_PENDING = 400
ENDZONE_YDS = 20          # a play that starts this close to the end zone is logged as an end-zone cue


@dataclasses.dataclass
class _Pending:
    event: feed.FeedEvent
    sport: str
    league: str
    label: str
    elapsed: float          # the event on the game's own time line


@dataclasses.dataclass
class _Fire:
    pending: _Pending
    at: float
    lead_s: float
    confirmed: bool = False


class FeedGate:
    def __init__(self, lead_s: float = 10.0) -> None:
        self.lead_s = lead_s
        self.pending: dict[str, list[_Pending]] = {}
        self.done: set[tuple] = set()                # (tab, tile, event key) decided
        self.armed: dict[tuple, int] = {}            # (tab, tile, event key) -> readings in a row that qualified
        self.fires: dict[tuple, list[_Fire]] = {}    # (tab, tile) -> would-fires waiting for the real score change
        self.mapped: dict[tuple, str] = {}           # (tab, tile) -> game id, as of the last observe
        self.situations: dict[str, tuple] = {}       # game id -> feed.Situation of every scrimmage play (American football)

    def add(self, event: feed.FeedEvent, snap: feed.Snapshot) -> None:
        if event.kind not in KINDS.get(snap.sport, ()):
            return
        elapsed = feed.game_elapsed_s(snap.sport, event.period, event.clock_s)
        if elapsed is None:
            return
        rows = self.pending.setdefault(event.game_id, [])
        rows.append(_Pending(event, snap.sport, snap.league, snap.label, elapsed))
        del rows[:-MAX_PENDING]

    def set_situations(self, game_id: str, situations: tuple) -> None:
        self.situations[game_id] = situations

    def forget(self, tab_id: int) -> None:
        for store in (self.fires, self.mapped):
            for key in [k for k in store if k[0] == tab_id]:
                del store[key]
        self.done = {k for k in self.done if k[0] != tab_id}
        self.armed = {k: v for k, v in self.armed.items() if k[0] != tab_id}

    def observe(self, tab_id: int, tile: int, game_id: str | None, clocks: list[str], period: int | None, replay: bool,
                now: float) -> list[str]:
        """One OCR reading of a tile. Returns log lines."""
        tile_key = (tab_id, tile)
        msgs = self._expire(tile_key, now)
        if game_id is None:
            self.mapped.pop(tile_key, None)
            return msgs
        self.mapped[tile_key] = game_id
        clock_s = parse_clock_s(clocks)
        if replay or clock_s is None:
            return msgs                                  # unknown clock = hold
        for p in self.pending.get(game_id, ()):
            key = (tab_id, tile, p.event.key)
            if key in self.done:
                continue
            stream = feed.game_elapsed_s(p.sport, period, clock_s) if p.sport != "soccer" else clock_s
            if stream is None:
                continue                                 # football/basketball without a read period: hold
            gap = p.elapsed - stream                     # > 0: the event is still ahead of the picture
            guessed = " (period guessed)" if p.sport != "soccer" else ""
            what = f"{p.event.kind} {p.event.display} {p.event.team}".rstrip()
            where = f"tab {tab_id} tile {tile} {p.league} {p.label}"
            if gap < -SKIP_BEHIND_S:
                self.done.add(key)
                msgs.append(f"feed skipped {where}: {what}, already {-gap:.0f} s behind the stream{guessed}")
            elif gap <= self.lead_s:
                self.armed[key] = self.armed.get(key, 0) + 1
                if self.armed[key] >= ARM_READINGS:
                    self.done.add(key)
                    self.armed.pop(key, None)
                    self.fires.setdefault(tile_key, []).append(_Fire(p, now, gap))
                    msgs.append(f"feed would-fire {where}: {what} (stream clock {clock_s:.0f} s, event {gap:.0f} s ahead){guessed}")
            else:
                self.armed.pop(key, None)
        return msgs + self._end_zone(tab_id, tile, game_id, clock_s, period)

    def _end_zone(self, tab_id: int, tile: int, game_id: str, clock_s: float, period: int | None) -> list[str]:
        """Measuring only: the start state of a play close to the end zone, released by the same rule as events (the stream
        clock is within `lead_s` before the snap, two readings in a row, never when already behind)."""
        msgs = []
        for s in self.situations.get(game_id, ()):
            if s.yards_to_endzone > ENDZONE_YDS:
                continue
            key = (tab_id, tile, "zone", s.play_id)
            stream = feed.game_elapsed_s("american football", period, clock_s)
            if key in self.done or stream is None:
                continue
            gap = feed.game_elapsed_s("american football", s.period, s.clock_s) - stream
            if gap < -SKIP_BEHIND_S:
                self.done.add(key)
            elif gap <= self.lead_s:
                self.armed[key] = self.armed.get(key, 0) + 1
                if self.armed[key] >= ARM_READINGS:
                    self.done.add(key)
                    self.armed.pop(key, None)
                    goal = "goal" if s.goal_to_go else str(s.distance)
                    msgs.append(f"feed end zone would raise cue: {s.yards_to_endzone} yds, {s.down} & {goal} "
                                f"(tab {tab_id} tile {tile}, {s.offense or '?'} has the ball, play {s.display} in period {s.period}, "
                                f"{gap:.0f} s ahead of the stream clock) (period guessed)")
            else:
                self.armed.pop(key, None)
        return msgs

    def real_change(self, tab_id: int, tile: int, now: float) -> list[str]:
        """The OCR watcher saw a real score change on this tile."""
        tile_key = (tab_id, tile)
        if tile_key not in self.mapped:
            return []
        for f in self.fires.get(tile_key, ()):
            if not f.confirmed and f.pending.event.kind in SCORING and now - f.at <= CONFIRM_S:
                f.confirmed = True
                p = f.pending
                return [f"feed confirmed tab {tab_id} tile {tile} {p.league} {p.label}: {p.event.kind} {p.event.display}, "
                        f"the real score change came {now - f.at:.0f} s after would-fire"]
        return [f"feed ocr-only score change tab {tab_id} tile {tile}: no feed event came first"]

    def _expire(self, tile_key: tuple, now: float) -> list[str]:
        msgs = []
        keep = []
        for f in self.fires.get(tile_key, ()):
            if f.confirmed:
                if now - f.at <= CONFIRM_S:
                    keep.append(f)
            elif now - f.at > CONFIRM_S:
                p = f.pending
                if p.event.kind in SCORING:
                    msgs.append(f"feed unconfirmed tab {tile_key[0]} tile {tile_key[1]} {p.league} {p.label}: {p.event.kind} "
                                f"{p.event.display}, no real score change seen within {CONFIRM_S:.0f} s of would-fire")
            else:
                keep.append(f)
        if keep:
            self.fires[tile_key] = keep
        else:
            self.fires.pop(tile_key, None)
        return msgs
