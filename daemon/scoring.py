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

MIN_BASELINE = 0.002  # floor so near-silence cannot make the surge explode
STATUS_MULT = {"live": 1.0, "break": 0.25, "over": 0.0}  # how much a stream counts in each game state


@dataclass
class Weights:
    surge: float = 0.3
    crowd: float = 1.0
    speech: float = -0.2
    music: float = -0.5
    event: float = 0.8    # a fresh score change (goal, touchdown) read from the picture
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
    recent_ad_s: float = 45.0  # an idle MAIN is not handed to a stream that was on an ad this recently (no hopping between ads)
    idle_music: float = 0.5   # a reading with music probability at least this (and no crowd) looks like an ad
    audio_ad_votes: float = 3.0   # a music-only ad needs this many such readings...
    audio_ad_window: float = 5.0  # ...among the last this many (one reading is never enough: arena music, jingles)


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
    event_at: float = float("-inf")  # when a score change was last seen
    can_main: bool = True  # False if the user restricted this stream away from the MAIN slot
    on_ad: bool = False    # the player is showing its ad placeholder (seen in the picture)
    audio_ad_at: float = float("-inf")  # when the sound last looked like an ad (music, no crowd) on enough readings
    ad_at: float = float("-inf")        # when the stream was last seen on an ad (picture or sound)
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
        self._candidate: tuple[int, int, float] | None = None  # (replaced, challenger, since when it has qualified)

    def register(self, tab_id: int, weight: float = 1.0, is_main: bool = False) -> None:
        self.streams.setdefault(tab_id, _Stream()).weight = weight
        if is_main:
            self.main = tab_id

    def remove(self, tab_id: int) -> None:
        self.streams.pop(tab_id, None)
        if self.main == tab_id:
            self.main = None

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

    def set_tags(self, tab_id: int, crowd: float, speech: float, music: float, now: float) -> None:
        stream = self.streams.get(tab_id)
        if stream is not None:
            stream.tags, stream.tags_at = (crowd, speech, music), now
            stream.music_hits.append(music >= self.config.idle_music and crowd < 0.1)
            del stream.music_hits[: -int(self.config.audio_ad_window)]
            if sum(stream.music_hits) >= self.config.audio_ad_votes:
                stream.audio_ad_at = stream.ad_at = now

    def set_can_main(self, tab_id: int, can_main: bool) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].can_main = can_main

    def set_favourite(self, tab_id: int, multiplier: float) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].favourite = max(1.0, multiplier)

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

    def components(self, tab_id: int, now: float) -> dict | None:
        """The signals and final score of a stream, or None if it has no recent audio or is still warming up."""
        cfg = self.config
        stream = self.streams.get(tab_id)
        if stream is None:
            return None
        self._trim(stream, now)
        if not stream.samples or now - stream.last_sample > cfg.stale_s:
            return None
        if now - stream.first_seen < cfg.warmup_s:
            return None

        level = sum(v for _, v in stream.samples) / len(stream.samples)
        surge = min(max(level / max(stream.baseline, MIN_BASELINE) - 1.0, 0.0), cfg.surge_cap)
        fresh = now - stream.tags_at <= cfg.stale_s
        crowd, speech, music = stream.tags if fresh else (0.0, 0.0, 0.0)

        event = max(0.0, 1.0 - (now - stream.event_at) / cfg.event_s)
        w = cfg.weights
        excitement = max(0.0, w.surge * surge + w.crowd * crowd + w.speech * speech + w.music * music + w.event * event)
        raw = excitement + w.favourite * (stream.favourite - 1.0)  # the favourite bonus is not reduced by commentary
        status = stream.override or stream.auto_state
        score = max(raw, 0.0) * stream.weight * STATUS_MULT[status]
        audio_ad = now - stream.audio_ad_at <= cfg.audio_ad_memory_s   # remembered: music probability flickers
        if stream.on_ad:
            score = -cfg.ad_penalty            # the picture says it is an ad: its audio means nothing
        elif audio_ad:
            score = -cfg.ad_penalty / 2         # music-dominated sound without a crowd: probably an ad too
        return {"score": score, "ad": stream.on_ad or audio_ad, "surge": surge, "crowd": crowd,
                "speech": speech, "music": music, "event": event, "fav": stream.favourite, "level": level, "status": status}

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
        if current_c is None and self._has_recent_audio(replaced, now):
            return None  # still warming up: nothing fair to compare against yet
        current_score = current_c["score"] if current_c else 0.0
        # An ad (music) or a break / over: any live stream without music is better than that.
        idle = current_c is not None and (current_c["ad"] or current_c["status"] != "live")
        best_id, best_c = None, None
        for tab_id in self.streams:
            if tab_id in (self.main, replaced) or not self.streams[tab_id].can_main:
                continue
            c = self.components(tab_id, now)
            if c is None:
                continue
            if idle and (c["status"] != "live" or c["ad"] or c["music"] >= cfg.idle_music
                         or now - self.streams[tab_id].ad_at < cfg.recent_ad_s):
                continue   # not an ad, and not one that was on an ad a moment ago (the ad flag forgets after a few seconds)
            if best_c is None or c["score"] > best_c["score"]:
                best_id, best_c = tab_id, c

        if best_c is None:
            self._candidate = None
            return None
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
            self.main = best_id
        self._last_swap = now
        return swap

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
        self.main = best_id
        self._last_swap = now
        return swap

    def _has_recent_audio(self, tab_id: int, now: float) -> bool:
        last = self.streams[tab_id].last_sample
        return last is not None and now - last <= self.config.stale_s

    def _trim(self, stream: _Stream, now: float) -> None:
        cutoff = now - self.config.window_s
        while stream.samples and stream.samples[0][0] < cutoff:
            stream.samples.popleft()
