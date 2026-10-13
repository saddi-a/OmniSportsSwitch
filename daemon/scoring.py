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
SILENT_BASELINE = 0.008   # a baseline below this is silence (a tab still loading), not a normal level: real sound re-seeds it and restarts the warm-up
SOUND_LEVEL = 0.02        # a sample at least this loud is real sound
STATUS_MULT = {"live": 1.0, "break": 0.25, "over": 0.0}  # how much a stream counts in each game state

# Per-sport overrides (settings.toml `[scoring.football]`, `[weights."american football"]`...): see `Config.for_sport`.
SPORT_KEYS = ("football", "american football", "basketball")   # same keys as the [sports.*] sections; anything else is "generic"
# The `scoring` values a sport may override: only those read where the stream's sport is known (per stream, or the challenger's).
# Everything else (server-wide intervals, models, feed, window_s/baseline_s, warmup/stale, ad detection, dwell, manual holds, event_s,
# the field goal / extra point rules of the score watcher, the game-state rules of the tracker) stays shared. Every `weights` value
# can be overridden. An override of any other key is ignored with a warning (daemon/settings.py).
OVERRIDABLE_SCORING = frozenset({
    "min_score", "threshold", "min_gap", "confirm_s", "hold_break_score",          # a challenger against MAIN: the CHALLENGER's sport
    "surge_cap", "anticipation_s", "cue_alone_cap", "cue_support", "cue_leverage_gain", "leverage_weight", "replay_dampen",
    "big_play_strength", "goal_banner_strength", "cue_zone_near_yd", "cue_zone_far_yd",
    "speech_cue_factor", "speech_cue_cooldown_s",
    "lev_us_football_close", "lev_us_football_blowout", "lev_basketball_close", "lev_basketball_blowout",
    "lev_soccer_close", "lev_soccer_blowout",
    "goal_hold_s", "goal_hold_extend_s", "goal_hold_max_s",                          # the hold of the stream that scored: ITS sport
    "goal_hold_break", "goal_hold_break_event", "goal_hold_break_level", "goal_hold_break_cue_s", "goal_yield_s", "goal_bypass_dwell", "hot_game_s", "hot_game_margin",   # ...and what breaks it: the challenger's sport
    "buildup_mode", "buildup_live", "buildup_window_s", "buildup_min", "buildup_cooldown_s", "buildup_surge_min",
    "buildup_surge_note", "buildup_gap", "buildup_crowd_min", "buildup_crowd_note",
    "buildup_hold_s", "buildup_hold_cue_min", "buildup_hold_margin",                 # MAIN keeps its seat after its own cue / review
    "buildup_review_hold_s", "buildup_review_min", "buildup_review_margin",
})


def sport_key(sport) -> str:
    """The key of a sport for the overrides: lowercase, spaces collapsed; one of SPORT_KEYS, else "generic" (unknown / None / other)."""
    key = " ".join(str(sport or "").lower().split())
    return key if key in SPORT_KEYS else "generic"


@dataclass
class Weights:
    surge: float = 0.3
    crowd: float = 1.0
    speech: float = -0.2
    music: float = -0.5
    event: float = 0.8    # a fresh score change (goal, touchdown) read from the picture
    shout: float = 0.0    # shouting / yelling (not commentary); off until it has been checked on live games
    anticipation: float = 1.0   # master scale of every cue before a likely score (red zone, VAR, corner...); 0 = off (cues are only logged)
    cue_review_top: float = 0.50    # a review (VAR, challenge) of a possible goal / touchdown, a penalty kick or a red card
    cue_review_high: float = 0.35   # ...of an interception, fumble or turnover
    cue_review_mid: float = 0.20    # ...of offside, pass interference or a foul
    cue_review_low: float = 0.10    # ...with no subject known
    cue_chance: float = 0.25        # red zone, goal to go, field goal attempt, free throws, penalty kick (soccer)
    cue_minor: float = 0.10         # corner, flag, 4th down, big gain, added time, timeout...
    cue_free_kick: float = 0.25     # a free kick (soccer): the user finds minor (0.10) too low
    cue_zone_far: float = 0.15      # American football: a goal-to-go / yard line cue with the ball ~10 yards from the end zone
    cue_zone_near: float = 0.40     # ...and 1-3 yards from it (or a 4th down at the goal line); no distance read = cue_chance
    favourite: float = 0.4  # steady bonus per +1 of a favourite team's multiplier (x2 favourite = +0.4)


@dataclass
class Config:
    """Every tunable. Shared values are the fields; a sport may override some of them (`for_sport`)."""
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
    lev_us_football_blowout: float = 28.0   # ...and as a blowout (more than three touchdowns)
    lev_basketball_close: float = 6.0
    lev_basketball_blowout: float = 20.0
    lev_soccer_close: float = 1.0
    lev_soccer_blowout: float = 4.0
    field_goal_strength: float = 0.6  # American football: a field goal or safety counts this much of a touchdown
    extra_point_s: float = 30.0  # an extra point / two-point conversion this soon after a touchdown is not a new event
    anticipation_s: float = 18.0   # a cue boosts a stream this long (fading)
    cue_alone_cap: float = 0.30    # a cue with no other sign of excitement (crowd, surge, shouting, a score) adds at most this: below min_score
    cue_support: float = 0.10      # crowd + surge + shouting + score change worth this much lift that cap
    cue_leverage_gain: float = 0.5  # a cue is worth x(1 + gain * leverage): up to x1.5 in a late close game, down to x0.5 in a late blowout
    cue_speech_window_s: float = 20.0   # commentary heard this recently tells what a bare review is about
    goal_banner_strength: float = 0.4   # soccer: a GOAL banner counts as this fraction of a goal (fading) until a real score change takes it to 1.0
    goal_phrase_surge: float = 0.6      # MEASURING ONLY: a goal phrase heard with a loudness surge at least this big is logged as "would fire"
    goal_phrase_window_s: float = 10.0  # ...when the two are at most this many seconds apart
    cue_zone_near_yd: float = 3.0       # at this many yards from the end zone or closer a cue has its highest weight
    cue_zone_far_yd: float = 10.0       # at this many or more it has its lowest
    anticipation_cooldown_s: float = 30.0   # the same cue is not reported again this soon
    anticipation_big_gain_yd: float = 20.0  # a "+NN YDS" graphic counts as a cue from this many yards
    anticipation_goal_yd: float = 10.0      # a yard line graphic counts as a cue up to this many yards, beside a down text
    replay_by_clock_s: float = 0.0   # a clock that goes back 10-30 s while the scorebug shows is a replay: 0 = only logged, above 0 = also treated as a replay this long
    replay_clock_min_s: float = 10.0   # the smallest step back of the game clock that counts
    football_clock_back_s: float = 90.0    # football: a game clock this far BELOW the last real one is a replay / highlights (not 45:00 of the 2nd half), not the live clock
    football_clock_ahead_s: float = 900.0   # ...and one this far ABOVE what the last real one plus the elapsed time allows is another match's scorebug
    foreign_clock_accept_s: float = 300.0   # ...unless it keeps running like that for this long (you rewound or skipped the stream): then it is believed
    replay_clock_max_s: float = 30.0   # the largest (more is the viewer rewinding)
    scorebug_hidden_s: float = 6.0   # measuring only: a scorebug missing this long (seen a moment before) is logged as hidden
    scorebug_memory_s: float = 60.0  # ...if it was seen within this long before; a later first sight of the gap is stale and not logged
    replay_dampen: float = 0.3  # during a replay inside a live game, crowd and loudness count this much (old cheers heard twice)
    recent_ad_s: float = 30.0  # an idle MAIN is not handed to a stream that was on an ad this recently (no hopping between ads)
    held_change_s: float = 30.0  # a score change read while the game looked like a break is kept this long and counts if the game is live again (0 = drop it at once)
    idle_music: float = 0.5   # a reading with music probability at least this (and no crowd) looks like an ad
    audio_ad_margin: float = 0.15  # ...and music must exceed speech by this much (stadium music under commentary is not an ad)
    audio_ad_votes: float = 3.0   # a music-only ad needs this many such readings...
    audio_ad_window: float = 5.0  # ...among the last this many (one reading is never enough: arena music, jingles)
    classifier_target_rms: float = 0.0  # >0: each stream's sound is scaled so its normal loudness equals this before the sound classifier hears it (0 = off)
    speech_interval_s: float = 6.0       # the commentary listener (measuring only) transcribes each stream this often; 0 = off (needs a restart to load)
    speech_window_s: float = 8.0         # ...the last this many seconds of its sound
    semantic_min_sim: float = 0.85       # semantic reading (measuring only): a piece of commentary this similar to the high / mid reference phrases counts...
    semantic_mid_sim: float = 0.99       # ...mid level: effectively off (0 of 5 mid alarms were true in Como-Roma)
    semantic_margin: float = 0.10        # ...and must beat its similarity to ordinary match talk by this much
    speech_cue_cooldown_s: float = 20.0  # the same cue on the same stream is logged at most this often
    speech_cue_factor: float = 1.0       # soccer: a spoken free kick / chance / post / penalty / goal call counts this much of the banner cue (0 = off)
    classifier_max_gain: float = 8.0    # ...but never louder than this factor, or quieter than 1/this
    feed_mode: float = 0.0          # outside game data (ESPN): 0 = local only (nothing starts), 1 = record only (measuring, changes no score); needs a restart
    dataset_mode: float = 1.0       # 1 = record one row per stream every 2 s (and the events) to logs/dataset/<date>.jsonl for later learning, 0 = off; read live
    log_archive_days: float = 90.0  # rotated log files are copied (gzip) to logs/archive/<date>/ and kept this many days (0 = forever)
    log_archive_max_mb: float = 50000.0   # ...and the archive never grows beyond this many MB (the oldest files go first; 0 = no cap)
    feed_poll_s: float = 5.0        # a league with a live game is asked this often (never less than 5 s: the feed's own cache time)
    feed_idle_poll_s: float = 120.0 # a league with nothing live or starting soon is asked this often
    feed_summary_s: float = 10.0    # American football: the plays of each live game (up to 6) are fetched this often; 0 = off
    feed_unconfirmed_factor: float = 0.8   # BOTH mode: a score change read by OCR on a mapped game that no feed event preceded counts this much until the feed confirms it (1.0 = off)
    feed_confirm_s: float = 30.0    # ...and the feed has this long after the OCR change to confirm it
    feed_event_weight: float = 0.0  # a feed goal/touchdown on a mapped game counts this much of its strength in the scorer (0 = shadow: only logged)
    feed_lead_s: float = 10.0       # shadow gate: an event 'would fire' when the stream clock is this many seconds before it (measuring only)
    buildup_mode: float = 1.0       # build-up tracker (measuring only, changes no score): 0 = off, 1 = log "buildup would take MAIN" lines
    buildup_live: float = 1.0       # football only: 1 = a stream that reaches the build-up level really takes MAIN (Scorer.take_main), 0 = log only
    buildup_window_s: float = 60.0  # the cues of a stream are added up for this long (each fading to 0), not only the strongest one
    buildup_min: float = 0.32       # a stream not in MAIN "would take" it when its build-up level (faded cue sum + surge/crowd) reaches this...
    buildup_cooldown_s: float = 30.0   # ...and is min_gap above MAIN's score; one line per stream this often
    buildup_surge_min: float = 0.6  # soccer, measuring only: a loudness surge this high notes a small pseudo-cue in the build-up (never a score)...
    buildup_surge_note: float = 0.10   # ...of this value, at most once per buildup_cooldown_s per stream
    buildup_gap: float = 0.10       # the tracker's own 'above MAIN' gap (min_gap, 0.15, stays for the normal rule)
    buildup_crowd_min: float = 0.25 # football: a stream whose crowd probability stays this high on 2 consecutive score readings (2 s apart)...
    goal_hold_s: float = 40.0       # build-up tracker: MAIN keeps its place this long after a goal on it (score change or goal banner; 0 = off)...
    goal_hold_extend_s: float = 10.0   # ...and longer while a replay sign (replay over the scorebug, hidden scorebug, replay by clock, spoken replay) is seen: until its time + this...
    goal_hold_max_s: float = 75.0   # ...but never longer than this after the goal
    goal_hold_break: float = 0.8    # a challenger scoring at least this (a goal or a very big moment) still takes MAIN during the hold
    goal_hold_break_level: float = 0.55   # ...or (build-up tracker) a build-up level this high WITH a spoken cue younger than goal_hold_break_cue_s (0 = off)
    goal_hold_break_cue_s: float = 15.0
    goal_bypass_dwell: float = 1.0   # 1 = a challenger with a fresh goal of its own (event >= goal_hold_break_event) skips min_dwell_s in decide() (confirm_s still applies); 0 = off
    hot_game_s: float = 90.0   # 'hot game': after MAIN's own goal or a strong cue (review, penalty awarded) on it, MAIN stays hot this long (0 = off; ends if MAIN goes to an ad / break)...
    hot_game_margin: float = 0.25   # ...and a challenger must beat MAIN's score (build-up path: MAIN's score or build-up level) by this much, unless it has a fresh goal of its own or a review cue
    goal_yield_s: float = 10.0   # build-up tracker: no swap to another stream while a goal (score change / goal banner) was seen on a third stream this recently, so the normal rule can take the goal (0 = off)
    goal_hold_break_event: float = 0.5   # ...or a fresh score change / goal banner of its own this strong (a quiet stream that just scored scores ~0.74, below goal_hold_break)
    buildup_hold_s: float = 25.0    # MAIN keeps its place this long after ITS OWN spoken/banner cue (chance, free kick, post area, penalty...; 0 = off): a challenger needs...
    buildup_hold_cue_min: float = 0.15   # ...(a cue counts for the hold when its weight is at least this: surge/crowd/set-piece notes are 0.10, spoken cues 0.20-0.25)...
    buildup_hold_margin: float = 0.20    # ...a build-up level this far above MAIN's own build-up level (instead of buildup_gap)
    buildup_review_hold_s: float = 60.0  # a review (VAR check, penalty awarded: a cue of weight >= buildup_review_min) on MAIN holds it this long (never longer than buildup_window_s; 0 = off)...
    buildup_review_min: float = 0.40     # ...(see above)
    buildup_review_margin: float = 0.35  # ...and the challenger needs this much above MAIN's build-up level (a goal on the challenger is the normal swap rule, not held)
    buildup_crowd_note: float = 0.10   # ...notes a pseudo-cue of this value into the tracker, at most once per buildup_cooldown_s (never a score)

    def __post_init__(self):
        # Per-sport overrides {sport key: {name: value}}, filled from settings.toml. Plain attributes, not fields: the shared values
        # (the fields) and the defaults test stay as they were.
        self.sport_scoring: dict[str, dict[str, float]] = {}
        self.sport_weights: dict[str, dict[str, float]] = {}

    def for_sport(self, sport):
        """The values to use for a stream of this sport: its overrides where it has any, the shared values everywhere else. The result
        is a live VIEW (nothing is copied): a shared value changed later, e.g. in the popup, reaches a sport that does not override it at
        once, and an override stays when the shared value changes. No sport / unknown / "generic" / no override: `self`. Read through the
        view when you use a value (`cfg = config.for_sport(sport)`), never keep a value."""
        key = sport_key(sport)
        scoring, weights = self.sport_scoring.get(key), self.sport_weights.get(key)
        return _SportView(self, scoring or {}, weights or {}) if scoring or weights else self


class _SportView:
    """`Config` as one sport sees it (see `Config.for_sport`): attribute reads give the override or else the live shared value."""
    def __init__(self, base, overrides: dict, weight_overrides: dict | None = None):
        self._base, self._overrides = base, overrides
        if weight_overrides is not None:   # a view of a Config: `.weights` is the same kind of view of its Weights
            self.weights = _SportView(base.weights, weight_overrides) if weight_overrides else base.weights

    def __getattr__(self, name):   # only called for names that are not set in __init__
        overrides = self.__dict__["_overrides"]
        return overrides[name] if name in overrides else getattr(self.__dict__["_base"], name)


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
    anticipation_at: float = float("-inf")   # when a cue (banner before a likely score) was last seen
    anticipation_value: float = 0.0   # its weight (before leverage and the master scale)
    anticipation_strong: bool = False   # a football cue worth a review of a possible goal or more: it counts in full without other signs
    shout: float = 0.0   # 0..1 probability of shouting / yelling / screaming
    event_at: float = float("-inf")  # when a score change was last seen
    goal_at: float = float("-inf")   # real time of the last goal (score change or goal banner) on this stream
    replay_seen_at: float = float("-inf")   # last replay sign seen while the post-goal hold ran
    goal_hold_on: bool = False       # the post-goal replay hold has started and not yet been reported over
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
        self.sport_of = None   # callable tab id -> sport name (the server sets it): picks the per-sport overrides of a stream

    def cfg_for(self, tab_id: int | None):
        """The settings as the sport of this stream sees them (`Config.for_sport`); the shared ones when the sport is not known."""
        return self.config.for_sport(self.sport_of(tab_id) if self.sport_of is not None and tab_id is not None else None)

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
            if stream.baseline < SILENT_BASELINE and level >= SOUND_LEVEL:
                stream.baseline, stream.first_seen = level, now   # the audio begins after silence: that is not a surge
                stream.samples.clear()
                stream.samples.append((now, level))
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

    def set_anticipation(self, tab_id: int, now: float, value: float | None = None, strong: bool = False) -> None:
        """A cue before a likely score; `value` is its weight (`daemon.cues.cue_weight`), `None` = a minor cue. A weaker cue does not
        replace a stronger one that is still fading."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return
        cfg = self.cfg_for(tab_id)
        value = cfg.weights.cue_minor if value is None else value
        fading = stream.anticipation_value * max(0.0, 1.0 - (now - stream.anticipation_at) / cfg.anticipation_s)
        if value >= fading:
            stream.anticipation_at, stream.anticipation_value, stream.anticipation_strong = now, value, strong

    def set_replay(self, tab_id: int, replay: bool, now: float | None = None) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].replay = replay
            if replay and now is not None:
                self.note_replay(tab_id, now)

    def note_replay(self, tab_id: int, now: float) -> None:
        """A sign that the stream shows replays (replay over the scorebug, hidden scorebug, replay by clock, spoken replay). It extends
        the post-goal hold if one is running (see `goal_hold_left`); outside a hold it is ignored."""
        if self.goal_hold_left(tab_id, now) > 0:
            self.streams[tab_id].replay_seen_at = now

    def goal_hold_left(self, tab_id: int, now: float) -> float:
        """Seconds the post-goal replay hold of this stream still runs (0 = none). It starts at the goal (`set_event(goal=True)`), lasts
        `goal_hold_s` at the least, and each replay sign seen during it moves the end to (its time + `goal_hold_extend_s`), never later than
        `goal_hold_max_s` after the goal."""
        stream, cfg = self.streams.get(tab_id), self.cfg_for(tab_id)   # the hold belongs to the stream that scored: its sport
        if stream is None or cfg.goal_hold_s <= 0 or stream.goal_at == float("-inf"):
            return 0.0
        end = stream.goal_at + cfg.goal_hold_s
        if stream.replay_seen_at >= stream.goal_at:
            end = max(end, min(stream.replay_seen_at + cfg.goal_hold_extend_s, stream.goal_at + max(cfg.goal_hold_max_s, cfg.goal_hold_s)))
        return max(0.0, end - now)

    def goal_hold_changes(self, now: float) -> list[tuple[int, str, float]]:
        """(tab, "start", seconds the hold runs) / (tab, "over", seconds since the goal) for holds that started or ended since the last call."""
        out = []
        for tab_id, stream in self.streams.items():
            left = self.goal_hold_left(tab_id, now)
            if left > 0 and not stream.goal_hold_on:
                stream.goal_hold_on = True
                out.append((tab_id, "start", left))
            elif left <= 0 and stream.goal_hold_on:
                stream.goal_hold_on = False
                out.append((tab_id, "over", now - stream.goal_at))
        return out

    def set_on_ad(self, tab_id: int, on_ad: bool, now: float | None = None) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].on_ad = on_ad
            if on_ad and now is not None:
                self.streams[tab_id].ad_at = now

    def set_event(self, tab_id: int, now: float, strength: float = 1.0, goal: bool = False) -> None:
        """A goal (strength 1.0) or a weaker event such as a big play. The event fades linearly, so a weaker one is
        a goal that already started to fade; the later start wins when events overlap."""
        if tab_id in self.streams:
            candidate = now - (1.0 - min(max(strength, 0.0), 1.0)) * self.config.event_s
            self.streams[tab_id].event_at = max(self.streams[tab_id].event_at, candidate)
            if goal:   # a score change or a goal banner: starts the post-goal replay hold (real time, whatever the strength)
                self.streams[tab_id].goal_at = max(self.streams[tab_id].goal_at, now)

    def set_status(self, tab_id: int, override: str | None) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].override = override if override in STATUS_MULT else None

    def set_auto_state(self, tab_id: int, state: str) -> None:
        if tab_id in self.streams and state in STATUS_MULT:
            self.streams[tab_id].auto_state = state

    def components(self, tab_id: int, now: float, warmup: bool = True) -> dict | None:
        """The signals and final score of a stream, or None if it has no recent audio or is still warming up
        (`warmup=False` also reports a stream that is still warming up: only for ad / break handovers)."""
        cfg = self.cfg_for(tab_id)
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
        cue = max(0.0, 1.0 - (now - stream.anticipation_at) / cfg.anticipation_s)
        w = cfg.weights
        support = w.surge * surge + w.crowd * crowd + w.shout * shout + w.event * event
        cue_part = w.anticipation * stream.anticipation_value * cue * min(max(1.0 + cfg.cue_leverage_gain * stream.leverage, 0.5), 1.5)
        if support < cfg.cue_support and not stream.anticipation_strong:
            # a cue alone must not take MAIN, even with the leverage bonus beside it: it only strengthens other signs of excitement
            room = cfg.min_score - 0.01 - max(0.0, cfg.leverage_weight * stream.leverage)
            cue_part = min(cue_part, cfg.cue_alone_cap, max(room, 0.0))
        talk = 0.0 if stream.anticipation_strong and cue_part > 0 else w.speech * speech   # a strong cue is what the commentators are talking about
        excitement = max(0.0, support + talk + w.music * music + cue_part)
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
                "speech": speech, "music": music, "shout": shout, "anticipation": cue, "cue": cue_part, "event": event, "fav": stream.favourite, "lev": stream.leverage, "level": level, "status": status}

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
        if now - self._last_swap < cfg.min_dwell_s and not (target is None and self._fresh_goal_elsewhere(now)):
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
        # A challenger is judged by ITS sport's thresholds (the sport that wants MAIN); MAIN's score is compared as it is.
        cfg = self.cfg_for(best_id)
        if target is None and self.hold_left(now) > 0 and best_c["score"] < cfg.hold_break_score:
            self._candidate = None
            return None   # the user put this stream on MAIN: only something really exciting takes it away
        if not idle:
            if target is None and self.goal_hold_left(replaced, now) > 0 and not self.goal_hold_broken(best_c, cfg):
                self._candidate = None
                return None   # MAIN just scored and its replays still run (the tracker has the same hold, see `take_main_block`)
            gap = cfg.min_gap
            if target is None and self.hot_left(replaced, now) > 0 and not self._hot_exempt(best_id, best_c, now):
                gap = max(gap, cfg.hot_game_margin)   # MAIN is hot (its own goal / a strong cue lately): a bigger lead is needed
            if (best_c["score"] < cfg.min_score
                    or best_c["score"] < max(current_score * (1 + cfg.threshold), current_score + gap)):
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

    def _fresh_goal_elsewhere(self, now: float) -> bool:
        """True when a stream other than MAIN has a fresh goal of its own (a score change or goal banner: `goal_at`, and its event still at
        least `goal_hold_break_event`), is live, not on an ad and may take MAIN: such a goal skips the dwell after the last swap (the
        confirmation still applies). A big play (no `goal_at`) and MAIN's own goal (that is the goal hold) do not."""
        for tab_id, stream in self.streams.items():
            if tab_id == self.main or not stream.can_main or stream.on_ad or stream.goal_at == float("-inf"):
                continue
            cfg = self.cfg_for(tab_id)
            if cfg.goal_bypass_dwell <= 0 or now - stream.goal_at > cfg.event_s or (stream.override or stream.auto_state) != "live":
                continue
            c = self.components(tab_id, now)
            if c is not None and c["event"] >= cfg.goal_hold_break_event:
                return True
        return False

    def swap_context(self, now: float) -> dict:
        """Read only, for the learning data: seconds since the last swap (None = none yet) and the challenger waiting for confirmation as
        [replaced tab, challenger tab, seconds it has qualified] (None = nobody)."""
        age = None if self._last_swap == float("-inf") else round(now - self._last_swap, 1)
        c = self._candidate
        return {"last_swap_age": age, "candidate": None if c is None else [c[0], c[1], round(now - c[2], 1)]}

    def hot_left(self, tab_id: int, now: float) -> float:
        """Seconds MAIN `tab_id` stays 'hot' (see `hot_game_s`): from its own goal (score change / goal banner) or from a STRONG cue on it
        (a review of a possible goal, a penalty awarded). 0 when it is not, when the setting is off or when the stream shows an ad or a break."""
        stream, cfg = self.streams.get(tab_id), self.cfg_for(tab_id)
        if stream is None or cfg.hot_game_s <= 0 or stream.on_ad or (stream.override or stream.auto_state) != "live":
            return 0.0
        start = max(stream.goal_at, stream.anticipation_at if stream.anticipation_strong else float("-inf"))
        return max(0.0, start + cfg.hot_game_s - now) if start != float("-inf") else 0.0

    def _hot_exempt(self, tab_id: int, c: dict | None, now: float) -> bool:
        """A challenger that still takes a hot MAIN: a fresh goal of its own, or a review-weight cue (VAR check) of the last `buildup_hold_s` s."""
        stream, cfg = self.streams.get(tab_id), self.cfg_for(tab_id)
        if stream is None:
            return False
        if c is not None and stream.goal_at != float("-inf") and c.get("event", 0.0) >= cfg.goal_hold_break_event:
            return True
        return stream.anticipation_value >= cfg.buildup_review_min and 0 <= now - stream.anticipation_at < cfg.buildup_hold_s

    def take_main_block(self, tab_id: int, now: float, level: float | None = None, cue_age: float | None = None, main_level: float | None = None) -> str | None:
        """Why `tab_id` may not take MAIN by the build-up tracker right now (the guards of a normal swap), or None if it may.
        What the challenger must beat (hold_break_score, goal_hold_break) is the challenger's sport's value."""
        cfg = self.cfg_for(tab_id)
        stream = self.streams.get(tab_id)
        if stream is None or self.main not in self.streams:
            return "unknown stream"
        if tab_id == self.main:
            return "already MAIN"
        if self.frozen:
            return "MAIN is frozen"
        if now - self._last_swap < cfg.min_dwell_s:
            return "dwell"
        if not stream.can_main:
            return "slot rules"
        if stream.on_ad:
            return "on an ad"
        if (stream.override or stream.auto_state) != "live":
            return "not live"
        if self.hold_left(now) > 0:
            c = self.components(tab_id, now)
            if (c["score"] if c else 0.0) < cfg.hold_break_score:
                return "manual hold"
        if cfg.goal_yield_s > 0:
            for other, st in self.streams.items():
                if other != tab_id and other != self.main and 0 <= now - st.goal_at < cfg.goal_yield_s:
                    return f"goal on tab {other} {now - st.goal_at:.0f} s ago"
        if self.goal_hold_left(self.main, now) > 0:   # MAIN just had a goal: its replays still run; stay unless a big moment is elsewhere
            strong_build_up = (cfg.goal_hold_break_level > 0 and level is not None and level >= cfg.goal_hold_break_level
                               and cue_age is not None and cue_age < cfg.goal_hold_break_cue_s)   # the tracker's level + a fresh spoken cue
            if not strong_build_up and not self.goal_hold_broken(self.components(tab_id, now), cfg):
                ago = now - self.streams[self.main].goal_at
                return f"goal on MAIN {ago:.0f} s ago" + (", replay still showing" if ago >= cfg.goal_hold_s else "")
        if level is not None and self.main in self.streams and self.hot_left(self.main, now) > 0 and not self._hot_exempt(tab_id, self.components(tab_id, now), now):
            main_c = self.components(self.main, now)
            bar = max(main_c["score"] if main_c else 0.0, main_level or 0.0) + cfg.hot_game_margin
            if level < bar:
                m = self.streams[self.main]
                start = max(m.goal_at, m.anticipation_at if m.anticipation_strong else float("-inf"))
                what = "goal" if start == m.goal_at else "cue"
                return f"MAIN is hot ({what} {now - start:.0f} s ago): level {level:.2f} < {bar:.2f}"
        return None

    @staticmethod
    def goal_hold_broken(c: dict | None, cfg) -> bool:
        """A challenger (its components `c`, its sport's `cfg`) may take MAIN during MAIN's post-goal hold: it scores at least
        `goal_hold_break` (a big moment) or has a fresh goal of its own (event >= `goal_hold_break_event`)."""
        score, event = (c["score"], c.get("event", 0.0)) if c else (0.0, 0.0)
        return score >= cfg.goal_hold_break or event >= cfg.goal_hold_break_event

    def take_main(self, tab_id: int, now: float, reason: str, level: float | None = None, cue_age: float | None = None, main_level: float | None = None) -> Swap | None:
        """The build-up tracker asks for MAIN (football only, the server checks the sport). Same guards as a normal swap (see
        `take_main_block`: frozen, manual hold unless hold_break_score, min_dwell_s, can_main, ad, break/over, already MAIN);
        returns the swap to carry out or None."""
        if self.take_main_block(tab_id, now, level=level, cue_age=cue_age, main_level=main_level) is not None:   # the same level / cue age as the caller's check
            return None
        swap = Swap(promote=tab_id, demote=self.main, reason=reason)
        self._candidate = None
        self._set_main(tab_id)
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
