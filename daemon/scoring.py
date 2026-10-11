"""Decides when a secondary stream should be promoted to the main slot.

Pure logic: no network, no clock of its own (callers pass `now` in seconds), so it is easy to test.
Streams are keyed by tab id, so a stream's weight follows it when tabs move between screen slots.

Score of a stream = priority * max(0, sum of weighted signals):
  surge   how far recent loudness is above this stream's own long-term average (0 = normal)
  crowd   0..1 probability of cheering / crowd / applause
  speech  0..1 probability of speech (commentary)
  music   0..1 probability of music (ads, jingles)
  event   0..1 freshness of a score change read from the picture (1 = just now)
  fav     a favourite team's steady bonus: a live favourite holds MAIN against quiet streams
"""
import math
from collections import deque
from dataclasses import dataclass, field

SHOUT_CLASSES = ["Shout", "Yell", "Screaming"]   # AudioSet classes whose strongest probability is the `shout` tag
MIN_BASELINE = 0.002  # floor so near-silence cannot make the surge explode
STATUS_MULT = {"live": 1.0, "break": 0.25, "over": 0.0}  # how much a stream counts in each game state


@dataclass
class Weights:
    surge: float = 0.3
    crowd: float = 1.0
    speech: float = -0.2
    music: float = -0.5
    event: float = 0.8    # a fresh score change (goal, touchdown) read from the picture
    shout: float = 0.0    # shouting / yelling (not commentary); off until it has been checked on live games
    favourite: float = 0.4  # steady bonus per +1 of a favourite team's multiplier (x2 favourite = +0.4)


@dataclass
class Config:
    weights: Weights = field(default_factory=Weights)
    threshold: float = 0.40   # challenger must beat main by this fraction...
    min_gap: float = 0.15     # ...and by at least this much in absolute score
    min_score: float = 0.35   # a challenger scoring below this never wins (loudness alone is not enough)
    window_s: float = 3.0     # loudness is averaged over this many seconds
    baseline_s: float = 60.0  # time constant of each stream's long-term loudness average
    warmup_s: float = 20.0    # a stream cannot win until its baseline has had this long to settle
    surge_cap: float = 1.0    # limit on the surge signal (1.0 = loudness doubled): alone it stays below min_score
    event_s: float = 45.0     # a score change counts for this long, fading linearly
    confirm_s: float = 2.0    # a normal (not idle) challenger must keep qualifying this long: one applause reading is not a swap
    min_dwell_s: float = 20.0 # after a swap, wait this long before allowing another one
    stale_s: float = 5.0      # a stream with no audio for this long is ignored
    big_play_strength: float = 0.7  # a big play (interception, fumble, sack...) counts this much of a goal
    ad_penalty: float = 0.5   # a stream showing an ad scores -ad_penalty (picture) or half of it (music only)
    audio_ad_memory_s: float = 12.0  # a stream that sounded like an ad stays flagged this long (music flickers)
    manual_hold_s: float = 60.0    # "Make Main" from the popup holds the stream on MAIN this long
    hold_over_s: float = 60.0      # a held stream whose game has looked over for this long loses the hold
    hold_break_score: float = 1.2  # a challenger scoring at least this still takes MAIN from a held stream
    leverage_weight: float = 0.3      # closeness / time left: a late close game adds up to this to its score, a late blowout takes it away
    lev_us_football_close: float = 8.0      # points apart that still count as close (one score)...
    lev_us_football_blowout: float = 24.0   # ...and as a blowout (three scores)
    lev_basketball_close: float = 6.0
    lev_basketball_blowout: float = 20.0
    lev_soccer_close: float = 1.0
    lev_soccer_blowout: float = 3.0
    replay_dampen: float = 0.3  # during a replay inside a live game, crowd and loudness count this much (old cheers heard twice)
    recent_ad_s: float = 30.0  # an idle MAIN is not handed to a stream that was on an ad this recently (no hopping between ads)
    idle_music: float = 0.5   # a reading with music probability at least this (and no crowd) looks like an ad
    audio_ad_margin: float = 0.15  # ...and music must exceed speech by this much (stadium music under commentary is not an ad)
    audio_ad_votes: float = 3.0   # a music-only ad needs this many such readings...
    audio_ad_window: float = 5.0  # ...among the last this many (one reading is never enough: arena music, jingles)
    classifier_target_rms: float = 0.0  # >0: each stream's sound is scaled so its normal loudness equals this before the sound classifier hears it (0 = off)
    classifier_max_gain: float = 8.0    # ...but never louder than this factor, or quieter than 1/this


@dataclass
class Swap:
    promote: int  # tab id moving to the main slot
    demote: int   # tab id leaving the main slot
    reason: str = ""


@dataclass
class _Stream:
    weight: float = 1.0
    samples: deque = field(default_factory=deque)  # (time, level)
    baseline: float | None = None
    first_seen: float | None = None
    last_sample: float | None = None
    tags: tuple[float, float, float] = (0.0, 0.0, 0.0)  # crowd, speech, music
    tags_at: float = float("-inf")
    shout: float = 0.0   # 0..1 probability of shouting / yelling / screaming
    event_at: float = float("-inf")  # when a score change was last seen
    can_main: bool = True  # False if the user restricted this stream away from the MAIN slot
    on_ad: bool = False    # the player is showing its ad placeholder (seen in the picture)
    audio_ad_at: float = float("-inf")  # when the sound last looked like an ad (music, no crowd) on enough readings
    leverage: float = 0.0   # -1 (late blowout) .. +1 (late close game), set from the scoreboard; 0 = unknown
    ad_at: float = float("-inf")        # when the stream was last seen on an ad (picture or sound)
    replay: bool = False    # the picture shows a replay over a live scorebug
    music_hits: list = field(default_factory=list)  # recent readings: did each one look like an ad?
    favourite: float = 1.0  # multiplier of a favourite team playing in this stream (1.0 = none)
    override: str | None = None  # status chosen by the user (live/break/over), None = automatic
    auto_state: str = "live"     # status detected from the picture


class Scorer:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.streams: dict[int, _Stream] = {}
        self.main: int | None = None
        self.frozen = False
        self._last_swap = float("-inf")
        self._last_evict = float("-inf")
        self.hold_tab: int | None = None     # the stream the user forced onto MAIN
        self.hold_until = float("-inf")      # ...and until when it is held there
        self._hold_over_since: float | None = None   # since when the held game has looked over
        self._candidate: tuple[int, int, float] | None = None  # (replaced, challenger, since when it has qualified)
        self._previous_main: int | None = None  # MAIN before a tab registered as MAIN: comes back if that tab leaves

    def register(self, tab_id: int, weight: float = 1.0, is_main: bool = False) -> None:
        self.streams.setdefault(tab_id, _Stream()).weight = weight
        if is_main and tab_id != self.main:
            self._previous_main = self.main   # a stray window in the MAIN slot must not make the real MAIN forgotten
            self.main = tab_id

    def _set_main(self, tab_id: int) -> None:
        self.main, self._previous_main = tab_id, None

    def remove(self, tab_id: int) -> None:
        self.streams.pop(tab_id, None)
        if self._previous_main == tab_id:
            self._previous_main = None
        if self.main == tab_id:
            self.main = self._previous_main if self._previous_main in self.streams else None
            self._previous_main = None

    def set_weight(self, tab_id: int, weight: float) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].weight = weight

    def add_audio(self, tab_id: int, level: float, now: float) -> None:
        stream = self.streams.get(tab_id)
        if stream is None:
            return
        stream.samples.append((now, level))
        self._trim(stream, now)
        if stream.baseline is None:
            stream.baseline, stream.first_seen = level, now
        else:
            alpha = 1 - math.exp(-(now - stream.last_sample) / self.config.baseline_s)
            stream.baseline += alpha * (level - stream.baseline)
        stream.last_sample = now

    def baseline_of(self, tab_id: int) -> float | None:
        """The stream's normal loudness (long-term average), or None if nothing was heard yet."""
        stream = self.streams.get(tab_id)
        return None if stream is None else stream.baseline

    def set_tags(self, tab_id: int, crowd: float, speech: float, music: float, now: float, shout: float = 0.0) -> None:
        stream = self.streams.get(tab_id)
        if stream is not None:
            stream.tags, stream.tags_at, stream.shout = (crowd, speech, music), now, shout
            stream.music_hits.append(music >= self.config.idle_music and crowd < 0.1
                                         and music - speech >= self.config.audio_ad_margin)
            del stream.music_hits[: -int(self.config.audio_ad_window)]
            if sum(stream.music_hits) >= self.config.audio_ad_votes:
                stream.audio_ad_at = stream.ad_at = now

    def set_can_main(self, tab_id: int, can_main: bool) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].can_main = can_main

    def set_leverage(self, tab_id: int, value: float) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].leverage = min(max(value, -1.0), 1.0)

    def set_favourite(self, tab_id: int, multiplier: float) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].favourite = max(1.0, multiplier)

    def set_replay(self, tab_id: int, replay: bool) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].replay = replay

    def set_on_ad(self, tab_id: int, on_ad: bool, now: float | None = None) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].on_ad = on_ad
            if on_ad and now is not None:
                self.streams[tab_id].ad_at = now

    def set_event(self, tab_id: int, now: float, strength: float = 1.0) -> None:
        """A goal (strength 1.0) or a weaker event such as a big play. The event fades linearly, so a weaker one is
        a goal that already started to fade; the later start wins when events overlap."""
        if tab_id in self.streams:
            candidate = now - (1.0 - min(max(strength, 0.0), 1.0)) * self.config.event_s
            self.streams[tab_id].event_at = max(self.streams[tab_id].event_at, candidate)

    def set_status(self, tab_id: int, override: str | None) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].override = override if override in STATUS_MULT else None

    def set_auto_state(self, tab_id: int, state: str) -> None:
        if tab_id in self.streams and state in STATUS_MULT:
            self.streams[tab_id].auto_state = state

    def components(self, tab_id: int, now: float, warmup: bool = True) -> dict | None:
        """The signals and final score of a stream, or None if it has no recent audio or is still warming up
        (`warmup=False` also reports a stream that is still warming up: only for ad / break handovers)."""
        cfg = self.config
        stream = self.streams.get(tab_id)
        if stream is None:
            return None
        self._trim(stream, now)
        if not stream.samples or now - stream.last_sample > cfg.stale_s:
            return None
        if warmup and now - stream.first_seen < cfg.warmup_s:
            return None

        level = sum(v for _, v in stream.samples) / len(stream.samples)
        surge = min(max(level / max(stream.baseline, MIN_BASELINE) - 1.0, 0.0), cfg.surge_cap)
        fresh = now - stream.tags_at <= cfg.stale_s
        crowd, speech, music = stream.tags if fresh else (0.0, 0.0, 0.0)
        shout = stream.shout if fresh and music < cfg.idle_music else 0.0   # yelling voices over music are an ad, not excitement
        if stream.replay:   # the cheering on a replay belongs to the play that was already counted
            crowd, surge = crowd * cfg.replay_dampen, surge * cfg.replay_dampen

        event = max(0.0, 1.0 - (now - stream.event_at) / cfg.event_s)
        w = cfg.weights
        excitement = max(0.0, w.surge * surge + w.crowd * crowd + w.speech * speech + w.music * music + w.shout * shout + w.event * event)
        raw = excitement + w.favourite * (stream.favourite - 1.0)  # the favourite bonus is not reduced by commentary
        raw += cfg.leverage_weight * stream.leverage                 # a late close game is worth more, a late blowout less
        status = stream.override or stream.auto_state
        score = max(raw, 0.0) * stream.weight * STATUS_MULT[status]
        audio_ad = now - stream.audio_ad_at <= cfg.audio_ad_memory_s   # remembered: music probability flickers
        if stream.on_ad:
            score = -cfg.ad_penalty            # the picture says it is an ad: its audio means nothing
        elif audio_ad:
            score = -cfg.ad_penalty / 2         # music-dominated sound without a crowd: probably an ad too
        return {"score": score, "ad": stream.on_ad or audio_ad, "surge": surge, "crowd": crowd,
                "speech": speech, "music": music, "shout": shout, "event": event, "fav": stream.favourite, "lev": stream.leverage, "level": level, "status": status}

    def score(self, tab_id: int, now: float) -> float | None:
        c = self.components(tab_id, now)
        return None if c is None else c["score"]

    def decide(self, now: float, target: int | None = None) -> Swap | None:
        """Pick a stream to take over from the stream being replaced.

        Normally that is MAIN (blocked while frozen). With `target`, the stream in that slot is replaced instead
        and MAIN is left alone: used while MAIN is frozen, so the winner takes the LEFT window.
        """
        cfg = self.config
        replaced = self.main if target is None else target
        if target is None and self.frozen:
            return None
        if replaced not in self.streams or self.main not in self.streams:
            return None
        if now - self._last_swap < cfg.min_dwell_s:
            return None

        current_c = self.components(replaced, now)
        warming = current_c is None and self._has_recent_audio(replaced, now)
        if warming:
            # Still warming up (the first seconds after a restart): nothing fair to compare by loudness. But a stream
            # that the picture shows on an ad, a break or over can still hand over to a live stream without an ad.
            stream = self.streams[replaced]
            if not (stream.on_ad or (stream.override or stream.auto_state) != "live"):
                return None
            current_c = self.components(replaced, now, warmup=False)
        current_score = current_c["score"] if current_c else 0.0
        # An ad (music) or a break / over: any live stream without music is better than that.
        idle = current_c is not None and (current_c["ad"] or current_c["status"] != "live")
        def pick(hold_off: bool):
            best_id, best_c = None, None
            for tab_id in self.streams:
                if tab_id in (self.main, replaced) or not self.streams[tab_id].can_main:
                    continue
                c = self.components(tab_id, now, warmup=not warming)
                if c is None:
                    continue
                if idle and (c["status"] != "live" or c["ad"] or c["music"] >= cfg.idle_music
                             or (hold_off and now - self.streams[tab_id].ad_at < cfg.recent_ad_s)):
                    continue   # not an ad, and (first choice) not one that was on an ad a moment ago
                if best_c is None or c["score"] > best_c["score"]:
                    best_id, best_c = tab_id, c
            return best_id, best_c

        best_id, best_c = pick(hold_off=True)
        replaced_stream = self.streams[replaced]
        if best_c is None and idle and (replaced_stream.on_ad or current_c["status"] != "live"):
            # Everything else had an ad lately, but MAIN shows an ad (or a break / the end) for certain right now: any
            # stream that is playing at this moment is better than staying on it. (A sound-only guess does not count.)
            best_id, best_c = pick(hold_off=False)

        if best_c is None:
            self._candidate = None
            return None
        if target is None and self.hold_left(now) > 0 and best_c["score"] < cfg.hold_break_score:
            self._candidate = None
            return None   # the user put this stream on MAIN: only something really exciting takes it away
        if not idle:
            if (best_c["score"] < cfg.min_score
                    or best_c["score"] < max(current_score * (1 + cfg.threshold), current_score + cfg.min_gap)):
                self._candidate = None
                return None
            if self._candidate is None or self._candidate[:2] != (replaced, best_id):
                self._candidate = (replaced, best_id, now)   # first reading that qualifies: wait for confirmation
            if now - self._candidate[2] < cfg.confirm_s:
                return None
        self._candidate = None

        fmt = lambda c: " ".join(f"{k}={c[k]:.2f}" for k in ("score", "surge", "crowd", "speech", "music", "event"))
        label = "main" if target is None else "left (MAIN frozen)"
        slot = "MAIN" if target is None else "LEFT"
        reason = f"{slot + ' idle: ' if idle else ''}challenger [{fmt(best_c)}] vs {label} [{fmt(current_c) if current_c else 'no data'}]"
        swap = Swap(promote=best_id, demote=replaced, reason=reason)
        if target is None:
            self._set_main(best_id)
        self._last_swap = now
        return swap

    def force_main(self, tab_id: int, now: float) -> Swap | None:
        """The user wants this stream on MAIN. Starts a hold (see `hold_left`) and returns the swap to carry out,
        or None if the stream already is MAIN. Raises ValueError with a message for the user if it cannot be."""
        stream = self.streams.get(tab_id)
        if stream is None:
            raise ValueError("the daemon does not track this tab: press Ctrl+Shift+Y in its window, then try again")
        if not stream.can_main:
            raise ValueError("this tab is not allowed in Main (its slot rules)")
        self.hold_tab, self.hold_until = tab_id, now + self.config.manual_hold_s
        self._hold_over_since = None
        if tab_id == self.main or self.main is None:
            self._set_main(tab_id)
            return None
        swap = Swap(promote=tab_id, demote=self.main, reason="MAIN forced by the user")
        self._set_main(tab_id)
        self._last_swap = now
        return swap

    def release_hold(self) -> None:
        self.hold_tab, self.hold_until = None, float("-inf")
        self._hold_over_since = None

    def hold_left(self, now: float) -> float:
        """Seconds the stream the user forced onto MAIN is still held there (0 if there is no hold). The hold ends
        by itself when it times out, when something else is on MAIN, or when that game has looked over for a full
        `hold_over_s` (the automatic "over" is sometimes wrong: a stream the user chose must not lose MAIN to it)."""
        tab = self.hold_tab
        if tab is None:
            return 0.0
        stream = self.streams.get(tab)
        left = self.hold_until - now
        if stream is None or left <= 0 or self.main != tab:
            self.release_hold()
            return 0.0
        if (stream.override or stream.auto_state) == "over":
            self._hold_over_since = now if self._hold_over_since is None else self._hold_over_since
            if now - self._hold_over_since >= self.config.hold_over_s:
                self.release_hold()
                return 0.0
        else:
            self._hold_over_since = None
        return left

    def evict_main(self, now: float) -> Swap | None:
        """MAIN holds a stream that may not be there (a rule changed): hand MAIN to the best stream that may.
        No dwell or margin applies, but a refused attempt is only retried every few seconds."""
        if self.frozen or self.main not in self.streams or self.streams[self.main].can_main:
            return None
        if now - self._last_evict < 5.0:
            return None
        best_id, best_score = None, 0.0
        for tab_id, stream in self.streams.items():
            if tab_id == self.main or not stream.can_main:
                continue
            c = self.components(tab_id, now)
            score = -9.0 if c is None else c["score"]
            if best_id is None or score > best_score:
                best_id, best_score = tab_id, score
        if best_id is None:
            return None
        self._last_evict = now
        swap = Swap(promote=best_id, demote=self.main, reason="MAIN stream is not allowed there any more")
        self._set_main(best_id)
        self._last_swap = now
        return swap

    def _has_recent_audio(self, tab_id: int, now: float) -> bool:
        last = self.streams[tab_id].last_sample
        return last is not None and now - last <= self.config.stale_s

    def _trim(self, stream: _Stream, now: float) -> None:
        cutoff = now - self.config.window_s
        while stream.samples and stream.samples[0][0] < cutoff:
            stream.samples.popleft()
