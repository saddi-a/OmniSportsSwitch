"""The clock gate between the feed and the streams (Stage 2 shadow lines, Stage 3a feed goals, referee and rewind).

For every tile that `feed_map` has tied to a game, an event of the feed (goal, red card, touchdown...) is compared with the
stream's own game clock. When the stream gets within `lead_s` seconds BEFORE the event's moment (the feed is ahead of the
picture), the gate logs "would fire" and queues one action (`drain_fired`); the server applies it only when
`scoring.feed_event_weight` is above 0. An event the stream has already passed means the feed is behind: it never acts, it
only confirms (the referee part). The feed's score change and its goal event are one "feed goal" (they arrive 5-11 s apart,
in either order). When the real OCR score change follows, the log says how long after the would-fire it came, and
`real_change` says whether the feed confirmed it. Spoilers are impossible by construction: an event is looked at only against
the stream's clock, never released by the time it was first seen. A clock that jumps back is a rewind: nothing fires and
score changes are not "ocr-only" until the stream is back at its high point.
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
CLOCK_FRESH_S = 20.0       # a verdict needs a stream clock read this recently
MATCH_BEFORE_S = 150.0    # a feed event up to this far behind the stream clock still belongs to a score change on screen
MATCH_EXTRA_S = 10.0      # ...and up to lead_s plus this far ahead of it
ENDZONE_YDS = 20          # a play that starts this close to the end zone is logged as an end-zone cue
MERGE_S = 90.0            # a feed score change and a feed goal event this close (wall time) are the same goal
REWIND_S = 20.0           # the stream clock this far under its high point (twice in a row) is a rewind
REWIND_READINGS = 2
REWIND_MAX_S = 600.0      # a rewind that lasts this long is given up (a wrong period read must not block a tile for good)
CAP_ACTIONS = 4           # at most this many feed actions per tile ...
CAP_WINDOW_S = 600.0      # ... in this many seconds
HOLD_S = 20.0              # a feed goal with no readable stream clock this long is reported once
HOLD_MAX_S = 120.0        # ...but only while it is this recent (a tile mapped late must not report the day's old goals)
OVER_WINDOW_S = 600.0     # a feed goal this recent, with no OCR change, is reported feed-only when the tile turns over
REFEREE_N = 10            # readings in a row that disagree with the feed before the referee speaks


@dataclasses.dataclass
class _Pending:
    event: feed.FeedEvent
    sport: str
    league: str
    label: str
    elapsed: float          # the event on the game's own time line
    key: tuple              # stable identity (a merged score change keeps its first key)
    added: float = 0.0      # wall time (monotonic) the feed goal was first noticed
    synthetic: bool = False  # made from a score change, waiting for the real event
    scored: bool = False    # a score change has been paired with this event


@dataclasses.dataclass
class _Fire:
    pending: _Pending
    at: float
    lead_s: float
    confirmed: bool = False
    acted: bool = False     # an action was queued for the scorer


@dataclasses.dataclass(frozen=True)
class Fired:
    """A feed goal released on a tile: the server applies `strength * scoring.feed_event_weight` to the scorer."""
    tab: int
    tile: int
    kind: str
    strength: float
    key: tuple


@dataclasses.dataclass
class _Wait:
    at: float                # when the OCR change was read
    game_id: str
    clock_s: float           # the stream clock then
    period: int | None


@dataclasses.dataclass
class _Rewind:
    high: float = float("-inf")   # the highest game time the stream clock has shown
    low_n: int = 0
    active: bool = False
    since: float = 0.0


@dataclasses.dataclass(frozen=True)
class Change:
    """What the feed says about a score change the OCR just read. 'confirmed': a matching feed event came first (or within
    the grace); 'ocr-only': the tile is mapped, the feed is alive and has no matching event (yet); 'rewind': the stream is
    behind its own high point (a replayed or rewound goal); 'unknown': not mapped, feed off or stale, or no stream clock.
    Only 'ocr-only' is reduced. `counted`: the feed already acted for this goal, so the OCR change must not count again."""
    verdict: str
    lines: list
    counted: bool = False


def _elapsed(sport: str, clock_s: float, period: int | None) -> float | None:
    return clock_s if sport == "soccer" else feed.game_elapsed_s(sport, period, clock_s)


def _lag_text(lag_s: float) -> str:
    """The feed's lag against the picture: feed time minus the time of the screen's score change (+ = the feed was behind)."""
    return f"feed lag {lag_s:+.0f} s (the feed was {'behind the picture' if lag_s > 0 else 'ahead of the picture'})"


def _clock_text(clock_s: float) -> str:
    return f"{int(clock_s // 60)}:{int(clock_s % 60):02d}"


class FeedGate:
    def __init__(self, lead_s: float = 10.0) -> None:
        self.lead_s = lead_s
        self.confirm_s = 30.0                        # the feed's grace to confirm an OCR score change (scoring.feed_confirm_s)
        self.field_goal_strength = 0.6               # set by the server from the scoring config
        self.big_play_strength = 0.7
        self.fire_note = None                        # optional hook(tab, tile, kind, strength) -> text appended to a would-fire line
        self.fresh: set[str] = set()                 # games whose league answered recently (set by the server each loop)
        self.sport_of: dict[str, str] = {}           # game id -> sport (set by the server)
        self.stream: dict[tuple, tuple] = {}         # (tab, tile) -> (clock_s, period, when) of the last readable stream clock
        self.rewind: dict[tuple, _Rewind] = {}
        self.claimed: set[tuple] = set()             # (tab, tile, event key) already used to confirm an OCR change
        self.waiting: dict[tuple, list] = {}         # (tab, tile) -> _Wait: OCR changes the feed has not confirmed yet
        self.messages: list[str] = []                # log lines from `confirmations`
        self.fired: list[Fired] = []
        self.acted: dict[tuple, list] = {}           # (tab, tile) -> times of the actions queued (for the cap)
        self.held: set[tuple] = set()                # (tab, tile, event key) already reported as held
        self.over_done: set[tuple] = set()           # (tab, tile, event key) already reported feed-only at game over
        self.last_change: dict[tuple, float] = {}    # (tab, tile) -> when the OCR watcher last saw a score change
        self.disagree: dict[tuple, int] = {}         # (tab, tile) -> readings in a row that disagree with the feed score
        self.pending: dict[str, list[_Pending]] = {}
        self.done: set[tuple] = set()                # (tab, tile, event key) decided
        self.armed: dict[tuple, int] = {}            # (tab, tile, event key) -> readings in a row that qualified
        self.fires: dict[tuple, list[_Fire]] = {}    # (tab, tile) -> would-fires waiting for the real score change
        self.mapped: dict[tuple, str] = {}           # (tab, tile) -> game id, as of the last observe
        self.situations: dict[str, tuple] = {}       # game id -> feed.Situation of every scrimmage play (American football)

    # -- the feed's goals ---------------------------------------------------------------------------------------------
    def strength(self, kind: str) -> float:
        if kind in ("field goal good", "safety"):
            return self.field_goal_strength
        if kind in ("red card", "penalty"):
            return self.big_play_strength
        return 1.0

    def _append(self, game_id: str, p: _Pending) -> None:
        rows = self.pending.setdefault(game_id, [])
        rows.append(p)
        del rows[:-MAX_PENDING]

    def add(self, event: feed.FeedEvent, snap: feed.Snapshot, now: float = 0.0) -> None:
        """A feed event. A goal that a score change already announced (within MERGE_S) takes its exact clock."""
        self.sport_of[snap.game_id] = snap.sport
        if event.kind not in KINDS.get(snap.sport, ()):
            return
        elapsed = feed.game_elapsed_s(snap.sport, event.period, event.clock_s)
        if elapsed is None:
            return
        if event.kind in SCORING:
            for p in self.pending.get(event.game_id, ()):
                if p.synthetic and now - p.added <= MERGE_S:
                    p.event, p.elapsed, p.synthetic, p.scored = event, elapsed, False, True
                    return
        self._append(event.game_id, _Pending(event, snap.sport, snap.league, snap.label, elapsed, event.key, now))

    def add_score(self, snap: feed.Snapshot, before: tuple | None, now: float = 0.0) -> None:
        """The feed's score changed. It is a feed goal of its own unless the goal event is already known."""
        self.sport_of[snap.game_id] = snap.sport
        if before is None or None in before or snap.home_score is None or snap.away_score is None:
            return
        dh, da = snap.home_score - before[0], snap.away_score - before[1]
        if dh < 0 or da < 0 or (dh > 0) == (da > 0):
            return                                   # a correction, or both sides at once
        delta = dh or da
        if snap.sport == "soccer":
            kind = "goal"
        elif snap.sport == "american football":
            kind = "touchdown" if delta >= 6 else "field goal good" if delta == 3 else ""
        else:
            kind = ""
        if not kind:
            return
        for p in self.pending.get(snap.game_id, ()):
            if not p.synthetic and not p.scored and p.event.kind in SCORING and now - p.added <= MERGE_S:
                p.scored = True                      # the event came first; this score change is its twin
                return
        elapsed = feed.game_elapsed_s(snap.sport, snap.period, snap.clock_s)
        if elapsed is None:
            return
        key = ("score", snap.game_id, snap.home_score, snap.away_score)
        event = feed.FeedEvent(snap.game_id, key, kind, snap.period, snap.clock_s, snap.display, "", "score change")
        self._append(snap.game_id, _Pending(event, snap.sport, snap.league, snap.label, elapsed, key, now, True))

    def set_situations(self, game_id: str, situations: tuple) -> None:
        self.situations[game_id] = situations

    def drain_fired(self) -> list[Fired]:
        out, self.fired = self.fired, []
        return out

    def forget(self, tab_id: int) -> None:
        self.held = {k for k in self.held if k[0] != tab_id}
        self.over_done = {k for k in self.over_done if k[0] != tab_id}
        for store in (self.fires, self.mapped, self.stream, self.waiting, self.rewind, self.acted, self.disagree, self.last_change):
            for key in [k for k in store if k[0] == tab_id]:
                del store[key]
        self.claimed = {k for k in self.claimed if k[0] != tab_id}
        self.done = {k for k in self.done if k[0] != tab_id}
        self.armed = {k: v for k, v in self.armed.items() if k[0] != tab_id}
        self.fired = [f for f in self.fired if f.tab != tab_id]

    # -- one OCR reading of a tile ----------------------------------------------------------------------------------
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
        if clock_s is None and not replay:
            msgs += self._held(tab_id, tile, game_id, now)
        if replay or clock_s is None:
            return msgs                                  # unknown clock = hold
        self.stream[tile_key] = (clock_s, period, now)
        msgs += self._watch_rewind(tab_id, tile, game_id, clock_s, period, now)
        if self.rewind[tile_key].active:
            return msgs                                  # nothing fires while the stream replays its past
        for p in self.pending.get(game_id, ()):
            key = (tab_id, tile, p.key)
            if key in self.done:
                continue
            stream = _elapsed(p.sport, clock_s, period)
            if stream is None:
                continue                                 # football without a read period: hold
            if self._waiting_for(tile_key, p):
                continue                                 # an OCR change is waiting for exactly this event: `confirmations` takes it
            gap = p.elapsed - stream                     # > 0: the event is still ahead of the picture
            guessed = " (period guessed)" if p.sport != "soccer" else ""
            what = f"{p.event.kind} {p.event.display} {p.event.team}".rstrip()
            where = f"tab {tab_id} tile {tile} {p.league} {p.label}"
            if gap < 0:
                self.done.add(key)
                self.armed.pop(key, None)
                if p.event.kind in SCORING or gap < -SKIP_BEHIND_S:
                    msgs.append(f"feed skipped {where}: {what}, {-gap:.0f} s behind the stream, the picture already shows it{guessed}")
            elif gap <= self.lead_s:
                self.armed[key] = self.armed.get(key, 0) + 1
                if self.armed[key] >= ARM_READINGS:
                    self.done.add(key)
                    self.armed.pop(key, None)
                    msgs.append(self._fire(tab_id, tile, tile_key, p, gap, clock_s, now, where, what, guessed))
            else:
                self.armed.pop(key, None)
        return msgs + self._end_zone(tab_id, tile, game_id, clock_s, period)

    def _held(self, tab_id: int, tile: int, game_id: str, now: float) -> list[str]:
        """Log only: a feed goal the tile cannot be compared with because its stream clock cannot be read (banners, replays,
        the final board). Once per goal, after HOLD_S, while it is recent."""
        tile_key = (tab_id, tile)
        last_clock = self.stream.get(tile_key, (None, None, float("-inf")))[2]
        msgs = []
        for p in self.pending.get(game_id, ()):
            key = (tab_id, tile, p.key)
            age = now - p.added
            if p.event.kind not in SCORING or key in self.held or key in self.done or last_clock >= p.added:
                continue
            if HOLD_S <= age <= HOLD_MAX_S:
                self.held.add(key)
                msgs.append(f"feed goal held tab {tab_id} tile {tile} {p.league} {p.label}: {p.event.kind} {p.event.display} "
                            f"{p.event.team}".rstrip() + f", no readable stream clock for {age:.0f} s")
        return msgs

    def game_over(self, tab_id: int, tile: int, now: float) -> list[str]:
        """Log only: the tile shows a final/over board. A recent feed goal that the screen never changed for stands alone."""
        tile_key = (tab_id, tile)
        game_id = self.mapped.get(tile_key)
        if game_id is None:
            return []
        changed_at = self.last_change.get(tile_key, float("-inf"))
        msgs = []
        for p in self.pending.get(game_id, ()):
            key = (tab_id, tile, p.key)
            if (p.event.kind not in SCORING or key in self.claimed or key in self.over_done or now - p.added > OVER_WINDOW_S
                    or changed_at >= p.added - 60.0):
                continue
            self.over_done.add(key)
            msgs.append(f"feed-only tab {tab_id} tile {tile} {p.league} {p.label}: {p.event.kind} {p.event.display} "
                        f"{p.event.team}".rstrip() + f", the tile shows the game over and the screen never changed its score for it "
                        f"(the feed event stands alone, seen {now - p.added:.0f} s ago)")
        return msgs

    def _fire(self, tab_id, tile, tile_key, p: _Pending, gap, clock_s, now, where, what, guessed) -> str:
        fire = _Fire(p, now, gap)
        self.fires.setdefault(tile_key, []).append(fire)
        strength = self.strength(p.event.kind)
        times = [t for t in self.acted.get(tile_key, []) if now - t <= CAP_WINDOW_S]
        capped = len(times) >= CAP_ACTIONS
        if not capped:
            times.append(now)
            fire.acted = True
            self.fired.append(Fired(tab_id, tile, p.event.kind, strength, p.key))
        self.acted[tile_key] = times
        note = self.fire_note(tab_id, tile, p.event.kind, strength) if self.fire_note else ""
        cap = f" (cap of {CAP_ACTIONS} per {CAP_WINDOW_S / 60:.0f} min reached: not acted)" if capped else ""
        return (f"feed would-fire {where}: {what} (stream clock {clock_s:.0f} s, event {gap:.0f} s ahead){guessed}{cap}{note}")

    def _waiting_for(self, tile_key: tuple, p: _Pending) -> bool:
        for w in self.waiting.get(tile_key, ()):
            stream = _elapsed(p.sport, w.clock_s, w.period)
            if stream is not None and -MATCH_BEFORE_S <= p.elapsed - stream <= self.lead_s + MATCH_EXTRA_S:
                return True
        return False

    def _watch_rewind(self, tab_id: int, tile: int, game_id: str, clock_s: float, period: int | None, now: float) -> list[str]:
        tile_key = (tab_id, tile)
        st = self.rewind.setdefault(tile_key, _Rewind())
        sport = self.sport_of.get(game_id) or ("soccer" if period is None else None)
        cur = _elapsed(sport, clock_s, period) if sport else None
        if cur is None:
            return []
        if st.active:
            if cur >= st.high - 5.0 or now - st.since > REWIND_MAX_S:
                st.high = max(st.high, cur) if cur >= st.high - 5.0 else cur
                st.active, st.low_n = False, 0
                return [f"feed rewind over tab {tab_id} tile {tile}: the stream is back at {_clock_text(clock_s)}"]
            return []
        if cur < st.high - REWIND_S:
            st.low_n += 1
            if st.low_n >= REWIND_READINGS:
                st.active, st.since = True, now
                return [f"feed rewind tab {tab_id} tile {tile}: the stream clock is {st.high - cur:.0f} s behind its high point, "
                        f"goals shown now are replays"]
            return []
        st.low_n = 0
        st.high = max(st.high, cur)
        return []

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

    # -- the referee ------------------------------------------------------------------------------------------------
    def referee(self, tab_id: int, tile: int, ocr_score: tuple | None, allowed: set | None, replay: bool,
                clock_s: float) -> list[str]:
        """Log only: the OCR score disagrees with every score the feed had around this stream moment, REFEREE_N readings in
        a row. `allowed` comes from `FeedMapper.allowed_scores` (None = the feed cannot tell)."""
        tile_key = (tab_id, tile)
        if replay:
            return []
        if ocr_score is None or allowed is None or ocr_score in allowed or ocr_score[::-1] in allowed:
            self.disagree.pop(tile_key, None)
            return []
        n = self.disagree.get(tile_key, 0) + 1
        self.disagree[tile_key] = n
        if n != REFEREE_N:
            return []
        feed_text = " or ".join(f"{a}-{b}" for a, b in sorted(allowed))
        return [f"feed referee tab {tab_id} tile {tile}: OCR {ocr_score[0]}-{ocr_score[1]}, feed {feed_text} "
                f"at stream clock {_clock_text(clock_s)}, {n} readings in a row"]

    # -- the OCR watcher saw a score change -------------------------------------------------------------------------
    def _match(self, tab_id: int, tile: int, game_id: str, clock_s: float, period: int | None):
        """An unclaimed scoring event of the game that belongs to the stream moment (clock_s, period)."""
        for p in self.pending.get(game_id, ()):
            if p.event.kind not in SCORING or (tab_id, tile, p.key) in self.claimed:
                continue
            stream = _elapsed(p.sport, clock_s, period)
            if stream is None:
                continue
            if -MATCH_BEFORE_S <= p.elapsed - stream <= self.lead_s + MATCH_EXTRA_S:
                return p
        return None

    def is_rewinding(self, tab_id: int, tile: int) -> bool:
        """The tile's stream is replaying its past (the clock fell far behind its high point and has not come back)."""
        return self.rewind.get((tab_id, tile), _Rewind()).active

    def distrust_rewind(self, tab_id: int, tile: int, clock_s: float | None, period: int | None) -> list[str]:
        """The rewind flag was stale (the feed shows the OCR score at the stream clock): end it and restart the high point
        from the current clock, so an inflated high point (the viewer scrubbed far ahead) cannot re-trigger it."""
        st = self.rewind.get((tab_id, tile))
        if st is None or not st.active:
            return []
        game_id = self.mapped.get((tab_id, tile))
        sport = self.sport_of.get(game_id) or ("soccer" if period is None else None)
        cur = _elapsed(sport, clock_s, period) if sport and clock_s is not None else None
        st.active, st.low_n = False, 0
        if cur is not None:
            st.high = cur
        return [f"feed rewind tab {tab_id} tile {tile}: dropped, the feed shows the stream's score at its clock (the high point was stale)"]

    def real_change(self, tab_id: int, tile: int, now: float) -> Change:
        """The OCR watcher saw a real score change on this tile: does the feed agree?"""
        tile_key = (tab_id, tile)
        self.last_change[tile_key] = now
        game_id, seen = self.mapped.get(tile_key), self.stream.get(tile_key)
        if game_id is None or game_id not in self.fresh or seen is None or now - seen[2] > CLOCK_FRESH_S:
            return Change("unknown", [])
        where = f"tab {tab_id} tile {tile}"
        if self.rewind.get(tile_key, _Rewind()).active:
            return Change("rewind", [f"feed rewind score change {where}: the stream is replaying its past, not a new goal"])
        for f in self.fires.get(tile_key, ()):
            if not f.confirmed and f.pending.event.kind in SCORING and now - f.at <= CONFIRM_S:
                f.confirmed = True
                self.claimed.add((tab_id, tile, f.pending.key))
                p = f.pending
                return Change("confirmed", [f"feed confirmed {where} {p.league} {p.label}: {p.event.kind} {p.event.display}, "
                                            f"the real score change came {now - f.at:.0f} s after would-fire, "
                                            f"{_lag_text(p.added - now)}"], f.acted)
        p = self._match(tab_id, tile, game_id, seen[0], seen[1])
        if p is not None:
            self.claimed.add((tab_id, tile, p.key))
            return Change("confirmed", [f"feed confirmed {where} {p.league} {p.label}: {p.event.kind} {p.event.display}, "
                                        f"the feed event was there before the score change, {_lag_text(p.added - now)}"])
        self.waiting.setdefault(tile_key, []).append(_Wait(now, game_id, seen[0], seen[1]))
        return Change("ocr-only", [f"feed ocr-only score change {where}: no feed event came first, "
                                   f"the feed has {self.confirm_s:.0f} s to confirm it"])

    def confirmations(self, now: float) -> list[tuple]:
        """Call every loop. Returns the (tab, tile) pairs whose waiting OCR score change the feed has now confirmed (the
        server raises that event to full strength); changes still unconfirmed after `confirm_s` are dropped and stay reduced."""
        out = []
        for tile_key, waits in list(self.waiting.items()):
            keep = []
            for w in waits:
                p = self._match(tile_key[0], tile_key[1], w.game_id, w.clock_s, w.period)
                if p is not None:
                    self.claimed.add((tile_key[0], tile_key[1], p.key))
                    if tile_key not in out:
                        out.append(tile_key)
                    self.messages.append(f"feed confirmed later tab {tile_key[0]} tile {tile_key[1]} {p.league} {p.label}: "
                                         f"{p.event.kind} {p.event.display}, {now - w.at:.0f} s after the OCR change, "
                                         f"{_lag_text(p.added - w.at)}")
                elif now - w.at > self.confirm_s:
                    self.messages.append(f"feed not confirmed tab {tile_key[0]} tile {tile_key[1]}: no feed event within "
                                         f"{self.confirm_s:.0f} s of the OCR score change")
                else:
                    keep.append(w)
            if keep:
                self.waiting[tile_key] = keep
            else:
                del self.waiting[tile_key]
        return out

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
                    msgs.append(f"feed-only tab {tile_key[0]} tile {tile_key[1]} {p.league} {p.label}: {p.event.kind} "
                                f"{p.event.display}, no real score change seen within {CONFIRM_S:.0f} s of would-fire "
                                f"(the feed event stands alone)")
            else:
                keep.append(f)
        if keep:
            self.fires[tile_key] = keep
        else:
            self.fires.pop(tile_key, None)
        return msgs
