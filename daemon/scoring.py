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
SPORT_KEYS = ("football", "american football")   # same keys as the [sports.*] sections; anything else is "generic"
# The `scoring` values a sport may override: only those read where the stream's sport is known (per stream, or the challenger's).
# Everything else (server-wide intervals, models, feed, window_s/baseline_s, warmup/stale, ad detection, dwell, manual holds, event_s,
# the field goal / extra point rules of the score watcher, the game-state rules of the tracker) stays shared. Every `weights` value
# can be overridden. An override of any other key is ignored with a warning (daemon/settings.py).
OVERRIDABLE_SCORING = frozenset({
    "min_score", "threshold", "min_gap", "confirm_s", "hold_break_score",          # a challenger against MAIN: the CHALLENGER's sport
    "surge_cap", "audio_ad_goal_s", "goal_pending_s", "goal_pending_surge", "goal_pending_crowd", "anticipation_s", "cue_alone_cap", "cue_support", "cue_leverage_gain", "leverage_weight", "replay_dampen",
    "big_play_strength", "goal_banner_strength", "cue_zone_near_yd", "cue_zone_far_yd",
    "speech_cue_factor", "speech_cue_cooldown_s",
    "lev_us_football_close", "lev_us_football_blowout",
    "lev_soccer_close", "lev_soccer_blowout",
    "goal_hold_s", "goal_hold_extend_s", "goal_hold_max_s",                          # the hold of the stream that scored: ITS sport
    "goal_hold_break", "goal_hold_break_event", "goal_hold_break_level", "goal_hold_break_cue_s", "goal_yield_s", "goal_bypass_dwell", "hot_game_s", "hot_game_max_s", "hot_game_margin",   # ...and what breaks it: the challenger's sport
    "buildup_mode", "buildup_live", "buildup_window_s", "buildup_min", "buildup_cooldown_s", "buildup_surge_min",
    "buildup_surge_note", "buildup_gap", "buildup_crowd_min", "buildup_crowd_note",
    "buildup_hold_s", "buildup_hold_cue_min", "buildup_hold_margin",                 # MAIN keeps its seat after its own cue / review
    "buildup_review_hold_s", "buildup_review_min", "buildup_review_margin",
    "surge_rise_min", "surge_rise_ratio", "surge_rise_window_s", "surge_rise_votes", "surge_rise_cooldown_s",   # the rising-loudness cue: the stream's sport
    "blowout_goal_bonus", "blowout_goal_lev", "blowout_goal_event", "blowout_goal_quiet",                    # a goal in a decided game: the challenger's sport
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
    cue_chance: float = 0.25        # red zone, goal to go, field goal attempt, penalty kick (soccer)
    cue_minor: float = 0.10         # corner, flag, 4th down, big gain, added time, timeout...
    cue_free_kick: float = 0.25     # a free kick (soccer): the user finds minor (0.10) too low
    cue_surge_rise: float = 0.20    # soccer: the crowd noise is rising fast (a medium cue, heard BEFORE the commentator's words); 0 = off
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
    ad_sticky_s: float = 120.0       # a picture ad break stays an ad this long unless the scoreboard is read again (0 = the short 8 s memory only)
    ad_exit_readings: float = 3.0    # ...this many scoreboard readings (down/quarter, clock + team code, two team codes) end it
    audio_ad_memory_s: float = 12.0  # a stream that sounded like an ad stays flagged this long (music flickers)
    goal_pending_s: float = 6.0      # a first new-score reading (before the vote) backs the goal-music exemption this long unless the vote confirms it (0 = off)
    goal_pending_surge: float = 0.5  # ...only if the sound was this much above its normal level (surge) or...
    goal_pending_crowd: float = 0.3  # ...this crowd-like within audio_ad_goal_s before it (a lone misread in a quiet game or an ad changes nothing)
    audio_ad_goal_s: float = 20.0    # music within this long of a goal signal (score change, GOAL banner, feed goal, either order) is the celebration, not an ad (0 = off)
    manual_hold_s: float = 60.0    # "Make Main" from the popup holds the stream on MAIN this long
    hold_over_s: float = 60.0      # a held stream whose game has looked over for this long loses the hold
    hold_break_score: float = 1.2  # a challenger scoring at least this still takes MAIN from a held stream
    leverage_weight: float = 0.3      # closeness / time left: a late close game adds up to this to its score, a late blowout takes it away
    lev_us_football_close: float = 8.0      # points apart that still count as close (one score)...
    lev_us_football_blowout: float = 28.0   # ...and as a blowout (more than three touchdowns)
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
    buildup_crowd_min: float = 0.25 # football: a stream whose crowd probability stays this high on 3 consecutive score readings (1 s apart)...
    goal_hold_s: float = 40.0       # build-up tracker: MAIN keeps its place this long after a goal on it (score change or goal banner; 0 = off)...
    goal_hold_extend_s: float = 10.0   # ...and longer while a replay sign (replay over the scorebug, hidden scorebug, replay by clock, spoken replay) is seen: until its time + this...
    goal_hold_max_s: float = 75.0   # ...but never longer than this after the goal
    goal_hold_break: float = 0.8    # a challenger scoring at least this (a goal or a very big moment) still takes MAIN during the hold
    goal_hold_break_level: float = 0.55   # ...or (build-up tracker) a build-up level this high WITH a spoken cue younger than goal_hold_break_cue_s (0 = off)
    goal_hold_break_cue_s: float = 15.0
    goal_bypass_dwell: float = 1.0   # 1 = a challenger with a fresh goal of its own (event >= goal_hold_break_event) skips min_dwell_s in decide() (confirm_s still applies); 0 = off
    hot_game_s: float = 90.0   # 'hot game': after MAIN's own goal or a strong cue (review, penalty awarded) on it, MAIN stays hot this long (0 = off; ends if MAIN goes to an ad / break)...
    hot_game_max_s: float = 150.0   # ...hard cap on one hot episode, counted from its FIRST goal / strong cue (more cues extend it up to here only); a break or ad ends it for good
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
    surge_rise_min: float = 0.5     # soccer, rising-loudness cue: the loudness surge must be at least this (clearly above the stream's own baseline)...
    surge_rise_ratio: float = 1.5   # ...and the level must have grown by this factor over the last surge_rise_window_s seconds (a rise, not a plateau)...
    surge_rise_window_s: float = 6.0   # ...(the reading it is compared with is at least half this old)...
    surge_rise_votes: float = 3.0   # ...on this many consecutive score readings (1 s apart): one reading is never enough...
    surge_rise_cooldown_s: float = 40.0   # ...and then not again for this long on the same stream
    blowout_goal_bonus: float = 0.25   # a goal in a decided game (leverage <= -blowout_goal_lev) adds this to its score, but only while every other stream is quiet (0 = off)
    blowout_goal_lev: float = 0.3      # ...a game counts as decided when its leverage is at most minus this (-1 = late blowout)...
    blowout_goal_event: float = 0.5    # ...the goal must still be this fresh (event 1 = just now)...
    blowout_goal_quiet: float = 0.20   # ...and "quiet" means every other live stream scores below this and has no cue

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
class _Tile:
    """The picture signals of ONE video (a tile of a multiview). A single-video tab keeps them in its `_Stream` itself (which is a
    `_Tile`); a multiview tab keeps one `_Tile` per video in `_Stream.tiles`, so each tile has its own cue, event, goal hold, hot episode,
    leverage, replay, ad and game state, read with its own sport's settings."""
    anticipation_at: float = float("-inf")   # when a cue (banner before a likely score) was last seen
    anticipation_value: float = 0.0   # its weight (before leverage and the master scale)
    hot_start: float = float("-inf")   # start of the current hot episode (MAIN's own goal / strong cue), see `hot_left`
    hot_last: float = float("-inf")    # the latest goal / strong cue of that episode
    review_at: float = float("-inf")   # when a review-weight cue (>= buildup_review_min) was last seen, whatever replaced the stored cue since
    anticipation_strong: bool = False   # a football cue worth a review of a possible goal or more: it counts in full without other signs
    event_at: float = float("-inf")  # when a score change was last seen
    goal_at: float = float("-inf")   # real time of the last goal (score change or goal banner)
    pending_at: float = float("-inf")   # last reading of a higher score that the vote has not confirmed (a goal candidate)
    replay_seen_at: float = float("-inf")   # last replay sign seen while the post-goal hold ran
    goal_hold_on: bool = False       # the post-goal replay hold has started and not yet been reported over
    on_ad: bool = False    # the player is showing its ad placeholder (seen in the picture)
    leverage: float = 0.0   # -1 (late blowout) .. +1 (late close game), set from the scoreboard; 0 = unknown
    replay: bool = False    # the picture shows a replay over a live scorebug
    auto_state: str = "live"     # status detected from the picture


@dataclass
class _Stream(_Tile):
    """A tab: its audio and the user's settings, plus (as a `_Tile`) the picture signals of a single-video tab."""
    weight: float = 1.0
    samples: deque = field(default_factory=deque)  # (time, level)
    baseline: float | None = None
    first_seen: float | None = None
    last_sample: float | None = None
    tags: tuple[float, float, float] = (0.0, 0.0, 0.0)  # crowd, speech, music
    tags_at: float = float("-inf")
    shout: float = 0.0   # 0..1 probability of shouting / yelling / screaming
    can_main: bool = True  # False if the user restricted this stream away from the MAIN slot
    loud_at: float = float("-inf")      # when the sound last was a clear surge or crowd roar (for the early goal signal)
    audio_ad_at: float = float("-inf")  # when the sound last looked like an ad (music, no crowd) on enough readings
    ad_at: float = float("-inf")        # when the stream was last seen on an ad (picture or sound)
    music_hits: list = field(default_factory=list)  # recent readings: did each one look like an ad?
    favourite: float = 1.0  # multiplier of a favourite team playing in this stream (1.0 = none)
    override: str | None = None  # status chosen by the user (live/break/over), None = automatic
    tiles: dict = field(default_factory=dict)   # video index -> _Tile; empty = a single-video tab (its picture signals are the fields above)


def _combine(states) -> str:
    """State of a whole tab from the states of its videos: live if any is live."""
    states = list(states)
    if not states or "live" in states:
        return "live"
    return "break" if "break" in states else "over"


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
        self.tile_sport_of = None   # callable (tab id, video index) -> sport name: the sport of one tile of a multiview tab
        self.audible_of = None      # callable tab id -> video index that has the sound (None = not known): the audio belongs to that tile

    def cfg_for(self, tab_id: int | None):
        """The settings as the sport of this stream sees them (`Config.for_sport`); the shared ones when the sport is not known."""
        return self.config.for_sport(self.sport_of(tab_id) if self.sport_of is not None and tab_id is not None else None)

    def tile_cfg(self, tab_id: int, tile: int | None):
        """The settings as the sport of one TILE sees them; `tile` None (a single-video tab) = the sport of the tab (`cfg_for`)."""
        if tile is None or self.tile_sport_of is None:
            return self.cfg_for(tab_id)
        return self.config.for_sport(self.tile_sport_of(tab_id, tile))

    def tile_sport(self, tab_id: int, tile: int | None):
        """The sport name of a tile as the scorer reads it (None = not known)."""
        if tile is not None and self.tile_sport_of is not None:
            return self.tile_sport_of(tab_id, tile)
        return self.sport_of(tab_id) if self.sport_of is not None else None

    # ---- tiles: a multiview tab keeps one picture-signal record per video; a single-video tab uses its stream record ----

    @staticmethod
    def _recs(stream: _Stream) -> list:
        """[(video index or None, record)] of a tab: its tiles, or just the stream itself when it has none (a single video)."""
        return list(stream.tiles.items()) if stream.tiles else [(None, stream)]

    @staticmethod
    def _sel(stream: _Stream, tile: int | None) -> list:
        """The records one `tile` argument means: that tile if the tab has it, else all of them (None = the whole tab)."""
        if stream.tiles and tile in stream.tiles:
            return [(tile, stream.tiles[tile])]
        return list(stream.tiles.items()) if stream.tiles else [(None, stream)]

    @staticmethod
    def _rec_of(stream: _Stream, tile: int | None) -> _Tile:
        return stream.tiles[tile] if tile is not None and tile in stream.tiles else stream

    def _write_rec(self, stream: _Stream, tile: int | None) -> _Tile:
        """The record a setter writes to: the tile's (made when new) or, with no tile, the stream's own."""
        if tile is None:
            return stream
        rec = stream.tiles.get(tile)
        if rec is None:
            rec = stream.tiles[tile] = _Tile()
        return rec

    def set_tiles(self, tab_id: int, tiles) -> None:
        """The videos a multiview tab has now: each has a record (made when new), records of other videos are dropped; no tiles = a
        single-video tab (its tile records go and the stream's own record counts again)."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return
        keep = list(tiles)
        for key in [k for k in stream.tiles if k not in keep]:
            del stream.tiles[key]
        for key in keep:
            self._write_rec(stream, key)

    def swap_tiles(self, tab_id: int, a: int, b: int) -> None:
        """DAZN's expand moved two streams between slots: their picture records change places."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return
        moved_a, moved_b = stream.tiles.pop(a, None), stream.tiles.pop(b, None)
        if moved_b is not None:
            stream.tiles[a] = moved_b
        if moved_a is not None:
            stream.tiles[b] = moved_a

    def tab_on_ad(self, tab_id: int) -> bool:
        """The whole tab shows an ad: every video of it does."""
        stream = self.streams.get(tab_id)
        return stream is not None and all(rec.on_ad for _, rec in self._recs(stream))

    def tab_auto_state(self, tab_id: int) -> str:
        """The state the pictures show (live if any video of the tab is live), whatever the user chose."""
        stream = self.streams.get(tab_id)
        return "live" if stream is None else _combine(rec.auto_state for _, rec in self._recs(stream))

    def tab_state(self, tab_id: int) -> str:
        """The user's choice, else live if any video of the tab is live (break, then over)."""
        stream = self.streams[tab_id]
        return stream.override or _combine(rec.auto_state for _, rec in self._recs(stream))

    def replay_of(self, tab_id: int, tile: int | None = None) -> bool:
        """The picture shows a replay: on this tile (None = the tab's only video, or any tile)."""
        stream = self.streams.get(tab_id)
        return stream is not None and any(rec.replay for _, rec in self._sel(stream, tile))

    @staticmethod
    def _last_goal(stream: _Stream) -> float:
        return max(rec.goal_at for _, rec in Scorer._recs(stream))

    def _audible_tile(self, tab_id: int, stream: _Stream) -> int | None:
        """The tile that has the sound, when the tab has tiles and it is known (else None: every tile hears the tab's audio)."""
        if not stream.tiles or self.audible_of is None:
            return None
        index = self.audible_of(tab_id)
        return index if index in stream.tiles else None

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
            cfg = self.cfg_for(tab_id)
            if music < cfg.idle_music and (crowd >= cfg.goal_pending_crowd or self._surge_now(stream, cfg) >= cfg.goal_pending_surge):   # an ad's own jump in loudness is no roar
                stream.loud_at = now
            stream.music_hits.append(music >= self.config.idle_music and crowd < 0.1
                                         and music - speech >= self.config.audio_ad_margin)
            del stream.music_hits[: -int(self.config.audio_ad_window)]
            if sum(stream.music_hits) >= self.config.audio_ad_votes and not self._goal_music(tab_id, stream, now):
                stream.audio_ad_at = stream.ad_at = now

    def _cancel_music_ad(self, tab_id: int, stream: _Stream, now: float) -> None:
        """The music came first and a goal signal came after it: it is what the music celebrates, so drop the ad flag (and the
        'recently on an ad' mark that came from the same music)."""
        window = self.cfg_for(tab_id).audio_ad_goal_s
        if window > 0 and now - stream.audio_ad_at <= window:
            if stream.ad_at == stream.audio_ad_at:
                stream.ad_at = float("-inf")
            stream.audio_ad_at = float("-inf")

    def _goal_music(self, tab_id: int, stream: _Stream, now: float) -> bool:
        """The music is the celebration of a goal, not an ad: a goal signal (`goal_at`) is within `audio_ad_goal_s` before it."""
        cfg = self.cfg_for(tab_id)
        if cfg.audio_ad_goal_s > 0 and 0 <= now - self._last_goal(stream) <= cfg.audio_ad_goal_s:
            return True
        return self._goal_candidate(stream, cfg, now)

    def _goal_candidate(self, stream: _Stream, cfg, now: float) -> bool:
        """A first new-score reading is fresh (`goal_pending_s`) and the sound was loud just before it (`audio_ad_goal_s`)."""
        return (cfg.goal_pending_s > 0 and cfg.audio_ad_goal_s > 0 and now - stream.loud_at <= cfg.audio_ad_goal_s
                and any(0 <= now - rec.pending_at <= cfg.goal_pending_s for _, rec in self._recs(stream)))

    def _surge_now(self, stream: _Stream, cfg) -> float:
        if not stream.samples or stream.baseline is None:
            return 0.0
        level = sum(v for _, v in stream.samples) / len(stream.samples)
        return min(max(level / max(stream.baseline, MIN_BASELINE) - 1.0, 0.0), cfg.surge_cap)

    def set_goal_pending(self, tab_id: int, now: float, tile: int | None = None) -> None:
        """The score reader saw a higher score that its vote has not confirmed yet (a goal candidate). With a loud moment just before,
        it already counts as a goal signal for the music exemption; it lapses after `goal_pending_s` unless the vote confirms it
        (`set_event(goal=True)`), and then the ad rule applies again."""
        stream = self.streams.get(tab_id)
        if stream is not None:
            self._write_rec(stream, tile).pending_at = now
            if self._goal_candidate(stream, self.cfg_for(tab_id), now):
                self._cancel_music_ad(tab_id, stream, now)

    def set_can_main(self, tab_id: int, can_main: bool) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].can_main = can_main

    def set_leverage(self, tab_id: int, value: float, tile: int | None = None) -> None:
        if tab_id in self.streams:
            self._write_rec(self.streams[tab_id], tile).leverage = min(max(value, -1.0), 1.0)

    def set_favourite(self, tab_id: int, multiplier: float) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].favourite = max(1.0, multiplier)

    def set_anticipation(self, tab_id: int, now: float, value: float | None = None, strong: bool = False, tile: int | None = None) -> None:
        """A cue before a likely score; `value` is its weight (`daemon.cues.cue_weight`), `None` = a minor cue. A weaker cue does not
        replace a stronger one that is still fading. `tile` = the video of a multiview tab it was seen on (read with that tile's sport)."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return
        cfg = self.tile_cfg(tab_id, tile)
        rec = self._write_rec(stream, tile)
        value = cfg.weights.cue_minor if value is None else value
        fading = rec.anticipation_value * max(0.0, 1.0 - (now - rec.anticipation_at) / cfg.anticipation_s)
        if strong:
            self._hot_trigger(rec, now, cfg)   # the hot episode is kept apart from the stored cue: a weaker cue replacing it must not end it
        if value >= cfg.buildup_review_min:
            rec.review_at = now
        if value >= fading:
            rec.anticipation_at, rec.anticipation_value, rec.anticipation_strong = now, value, strong

    def set_replay(self, tab_id: int, replay: bool, now: float | None = None, tile: int | None = None) -> None:
        if tab_id in self.streams:
            self._write_rec(self.streams[tab_id], tile).replay = replay
            if replay and now is not None:
                self.note_replay(tab_id, now, tile)

    def note_replay(self, tab_id: int, now: float, tile: int | None = None) -> None:
        """A sign that the stream shows replays (replay over the scorebug, hidden scorebug, replay by clock, spoken replay). It extends
        the post-goal hold if one is running (see `goal_hold_left`); outside a hold it is ignored. With a `tile` only that tile's hold is
        extended (a replay on one video says nothing about the goal on another); without one every tile's hold that runs."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return
        for key, rec in self._sel(stream, tile):
            if self._goal_hold(rec, self.tile_cfg(tab_id, key), now) > 0:
                rec.replay_seen_at = now

    @staticmethod
    def _goal_hold(rec: _Tile, cfg, now: float) -> float:
        if cfg.goal_hold_s <= 0 or rec.goal_at == float("-inf"):
            return 0.0
        end = rec.goal_at + cfg.goal_hold_s
        if rec.replay_seen_at >= rec.goal_at:
            end = max(end, min(rec.replay_seen_at + cfg.goal_hold_extend_s, rec.goal_at + max(cfg.goal_hold_max_s, cfg.goal_hold_s)))
        return max(0.0, end - now)

    def goal_hold_left(self, tab_id: int, now: float, tile: int | None = None) -> float:
        """Seconds the post-goal replay hold of this stream still runs (0 = none). It starts at the goal (`set_event(goal=True)`), lasts
        `goal_hold_s` at the least, and each replay sign seen during it moves the end to (its time + `goal_hold_extend_s`), never later than
        `goal_hold_max_s` after the goal. Each tile of a multiview has its own hold, read with its own sport; the tab's is the longest
        (or that of `tile`)."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return 0.0
        return max((self._goal_hold(rec, self.tile_cfg(tab_id, key), now) for key, rec in self._sel(stream, tile)), default=0.0)

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
                out.append((tab_id, "over", now - self._last_goal(stream)))
        return out

    def set_on_ad(self, tab_id: int, on_ad: bool, now: float | None = None, tile: int | None = None) -> None:
        if tab_id in self.streams:
            rec = self._write_rec(self.streams[tab_id], tile)
            rec.on_ad = on_ad
            if on_ad:
                self._hot_end(rec)
                if now is not None:
                    self.streams[tab_id].ad_at = now

    def set_event(self, tab_id: int, now: float, strength: float = 1.0, goal: bool = False, tile: int | None = None) -> None:
        """A goal (strength 1.0) or a weaker event such as a big play. The event fades linearly, so a weaker one is
        a goal that already started to fade; the later start wins when events overlap. `tile` = the video of a multiview tab it was
        seen on: each tile keeps its own event and goal."""
        if tab_id in self.streams:
            rec = self._write_rec(self.streams[tab_id], tile)
            candidate = now - (1.0 - min(max(strength, 0.0), 1.0)) * self.config.event_s
            rec.event_at = max(rec.event_at, candidate)
            if goal:   # a score change or a goal banner: starts the post-goal replay hold (real time, whatever the strength)
                rec.goal_at = max(rec.goal_at, now)
                self._hot_trigger(rec, now, self.tile_cfg(tab_id, tile))
                self._cancel_music_ad(tab_id, self.streams[tab_id], now)

    def set_status(self, tab_id: int, override: str | None) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].override = override if override in STATUS_MULT else None
            if override is not None and override != "live":
                for _, rec in self._recs(self.streams[tab_id]):
                    self._hot_end(rec)

    def set_auto_state(self, tab_id: int, state: str, tile: int | None = None) -> None:
        if tab_id in self.streams and state in STATUS_MULT:
            rec = self._write_rec(self.streams[tab_id], tile)
            rec.auto_state = state
            if state != "live":
                self._hot_end(rec)

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
        fresh = now - stream.tags_at <= cfg.stale_s
        # One score per tile, each with its own sport's settings. The audio (surge, crowd, speech, music, shout, the ad-by-music memory)
        # belongs to the tile that has the sound; the other tiles of a multiview score on their picture alone. The tab scores as its best tile.
        audible = self._audible_tile(tab_id, stream)
        calcs = []
        for key, rec in self._recs(stream):
            calcs.append((key, self._tile_calc(stream, rec, self.tile_cfg(tab_id, key), key is None or audible is None or key == audible, now, level, fresh)))
        best_key, best = calcs[0]
        for key, calc in calcs[1:]:
            if calc["score"] > best["score"]:
                best_key, best = key, calc
        hearing = next((calc for key, calc in calcs if key is None or audible is None or key == audible), best)
        status = stream.override or _combine(rec.auto_state for _, rec in self._recs(stream))
        return {"score": best["score"], "ad": all(calc["ad"] for _, calc in calcs), "surge": hearing["surge"], "crowd": hearing["crowd"],
                "speech": hearing["speech"], "music": hearing["music"], "shout": hearing["shout"], "anticipation": best["cue"], "cue": best["cue_part"],
                "event": best["event"], "fav": stream.favourite, "lev": best["lev"], "level": level, "status": status,
                "tile": best_key, "sport": self.tile_sport(tab_id, best_key), "tile_scores": {key: calc["score"] for key, calc in calcs}}

    def _tile_calc(self, stream: _Stream, rec: _Tile, cfg, hears: bool, now: float, level: float, fresh: bool) -> dict:
        """The score of one tile (`rec`, read with `cfg` = its sport's settings). `hears`: it has the tab's sound."""
        surge = crowd = speech = music = shout = 0.0
        if hears:
            surge = min(max(level / max(stream.baseline, MIN_BASELINE) - 1.0, 0.0), cfg.surge_cap)
            crowd, speech, music = stream.tags if fresh else (0.0, 0.0, 0.0)
            shout = stream.shout if fresh and music < cfg.idle_music else 0.0   # yelling voices over music are an ad, not excitement
            if rec.replay:   # the cheering on a replay belongs to the play that was already counted
                crowd, surge = crowd * cfg.replay_dampen, surge * cfg.replay_dampen

        event = max(0.0, 1.0 - (now - rec.event_at) / cfg.event_s)
        cue = max(0.0, 1.0 - (now - rec.anticipation_at) / cfg.anticipation_s)
        w = cfg.weights
        support = w.surge * surge + w.crowd * crowd + w.shout * shout + w.event * event
        cue_part = w.anticipation * rec.anticipation_value * cue * min(max(1.0 + cfg.cue_leverage_gain * rec.leverage, 0.5), 1.5)
        if support < cfg.cue_support and not rec.anticipation_strong:
            # a cue alone must not take MAIN, even with the leverage bonus beside it: it only strengthens other signs of excitement
            room = cfg.min_score - 0.01 - max(0.0, cfg.leverage_weight * rec.leverage)
            cue_part = min(cue_part, cfg.cue_alone_cap, max(room, 0.0))
        talk = 0.0 if rec.anticipation_strong and cue_part > 0 else w.speech * speech   # a strong cue is what the commentators are talking about
        excitement = max(0.0, support + talk + w.music * music + cue_part)
        raw = excitement + w.favourite * (stream.favourite - 1.0)  # the favourite bonus is not reduced by commentary
        raw += cfg.leverage_weight * rec.leverage                   # a late close game is worth more, a late blowout less
        status = stream.override or rec.auto_state
        score = max(raw, 0.0) * stream.weight * STATUS_MULT[status]
        audio_ad = hears and now - stream.audio_ad_at <= cfg.audio_ad_memory_s   # remembered: music probability flickers
        if rec.on_ad:
            score = -cfg.ad_penalty            # the picture says it is an ad: its audio means nothing
        elif audio_ad:
            score = -cfg.ad_penalty / 2         # music-dominated sound without a crowd: probably an ad too
        return {"score": score, "ad": rec.on_ad or audio_ad, "surge": surge, "crowd": crowd, "speech": speech, "music": music, "shout": shout,
                "cue": cue, "cue_part": cue_part, "event": event, "lev": rec.leverage}

    def score(self, tab_id: int, now: float) -> float | None:
        c = self.components(tab_id, now)
        return None if c is None else c["score"]

    def tile_scores(self, tab_id: int, now: float) -> dict | None:
        """{video index: score} of the tiles of a multiview tab (the tab scores as the best of them), or None while the tab has no score."""
        c = self.components(tab_id, now)
        return None if c is None else dict(c["tile_scores"])

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
        goal_only = None   # inside the dwell: only a stream with a fresh goal of its own may be picked (it skips the dwell; the others wait it out)
        if now - self._last_swap < cfg.min_dwell_s:
            goal_only = self._fresh_goal_streams(now) if target is None else set()
            if not goal_only:
                return None

        current_c = self.components(replaced, now)
        warming = current_c is None and self._has_recent_audio(replaced, now)
        if warming:
            # Still warming up (the first seconds after a restart): nothing fair to compare by loudness. But a stream
            # that the picture shows on an ad, a break or over can still hand over to a live stream without an ad.
            if not (self.tab_on_ad(replaced) or self.tab_state(replaced) != "live"):
                return None
            current_c = self.components(replaced, now, warmup=False)
        current_score = current_c["score"] if current_c else 0.0
        # An ad (music) or a break / over: any live stream without music is better than that.
        idle = current_c is not None and (current_c["ad"] or current_c["status"] != "live")
        def pick(hold_off: bool):
            best_id, best_c = None, None
            for tab_id in self.streams:
                if tab_id in (self.main, replaced) or not self.streams[tab_id].can_main or (goal_only is not None and tab_id not in goal_only):
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
        if best_c is None and idle and (self.tab_on_ad(replaced) or current_c["status"] != "live"):
            # Everything else had an ad lately, but MAIN shows an ad (or a break / the end) for certain right now: any
            # stream that is playing at this moment is better than staying on it. (A sound-only guess does not count.)
            best_id, best_c = pick(hold_off=False)

        if best_c is None:
            self._candidate = None
            return None
        # A challenger is judged by ITS sport's thresholds (the sport that wants MAIN); MAIN's score is compared as it is.
        cfg = self.tile_cfg(best_id, best_c.get("tile"))   # ...the sport of its best tile
        if target is None and self.hold_left(now) > 0 and best_c["score"] < cfg.hold_break_score:
            self._candidate = None
            return None   # the user put this stream on MAIN: only something really exciting takes it away
        bonus = 0.0
        if not idle:
            if target is None and self.goal_hold_left(replaced, now) > 0 and not self.goal_hold_broken(best_c, cfg):
                self._candidate = None
                return None   # MAIN just scored and its replays still run (the tracker has the same hold, see `take_main_block`)
            gap = cfg.min_gap
            if target is None and self.hot_left(replaced, now) > 0 and not self._hot_exempt(best_id, best_c, now):
                gap = max(gap, cfg.hot_game_margin)   # MAIN is hot (its own goal / a strong cue lately): a bigger lead is needed
            bonus = self.blowout_goal_bonus(best_id, best_c, now)
            best_score = best_c["score"] + bonus
            if (best_score < cfg.min_score
                    or best_score < max(current_score * (1 + cfg.threshold), current_score + gap)):
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
        reason = f"{slot + ' idle: ' if idle else ''}{'blowout goal +%.2f: ' % bonus if bonus else ''}challenger [{fmt(best_c)}] vs {label} [{fmt(current_c) if current_c else 'no data'}]"
        swap = Swap(promote=best_id, demote=replaced, reason=reason)
        if target is None:
            self._set_main(best_id)
        self._last_swap = now
        return swap

    def blowout_goal_bonus(self, tab_id: int, c: dict | None, now: float) -> float:
        """The small bonus a goal in a DECIDED game gets (leverage takes most of its worth away): `blowout_goal_bonus` of the stream's sport
        when the stream has a fresh goal of its own (score change or goal banner, event >= `blowout_goal_event`), its leverage is at most
        minus `blowout_goal_lev`, and every OTHER live stream (MAIN included) is quiet: a score below `blowout_goal_quiet` and no cue. Else 0.
        So such a goal can win MAIN only when nothing else is going on."""
        stream = self.streams.get(tab_id)
        tile = None if c is None else c.get("tile")   # the tile that scores (its goal, its leverage, its sport)
        cfg = self.tile_cfg(tab_id, tile)
        rec = None if stream is None else self._rec_of(stream, tile)
        if stream is None or c is None or cfg.blowout_goal_bonus <= 0 or rec.goal_at == float("-inf"):
            return 0.0
        if c.get("event", 0.0) < cfg.blowout_goal_event or rec.leverage > -cfg.blowout_goal_lev:
            return 0.0
        for other in self.streams:
            if other == tab_id:
                continue
            oc = self.components(other, now)
            if oc is None or oc["ad"] or oc["status"] != "live":
                continue   # no sound, an ad, a break or over: nothing going on there
            if oc["score"] >= cfg.blowout_goal_quiet or oc["cue"] > 0:
                return 0.0
        return cfg.blowout_goal_bonus

    def _fresh_goal_streams(self, now: float) -> set:
        """The streams other than MAIN that have a fresh goal of its own (a score change or goal banner: `goal_at`, and its event still at
        least `goal_hold_break_event`), is live, not on an ad and may take MAIN: such a goal skips the dwell after the last swap (the
        confirmation still applies; only THEY, not another stream that wins the margin meanwhile). A big play (no `goal_at`) and MAIN's own goal
        (that is the goal hold) do not."""
        found = set()
        for tab_id, stream in self.streams.items():
            if tab_id == self.main or not stream.can_main:
                continue
            for key, rec in self._recs(stream):   # a goal of any one tile, read with that tile's sport
                if rec.on_ad or rec.goal_at == float("-inf"):
                    continue
                cfg = self.tile_cfg(tab_id, key)
                if cfg.goal_bypass_dwell <= 0 or now - rec.goal_at > cfg.event_s or (stream.override or rec.auto_state) != "live":
                    continue
                c = self.components(tab_id, now)
                if c is not None and max(0.0, 1.0 - (now - rec.event_at) / cfg.event_s) >= cfg.goal_hold_break_event:
                    found.add(tab_id)
        return found

    def swap_context(self, now: float) -> dict:
        """Read only, for the learning data: seconds since the last swap (None = none yet) and the challenger waiting for confirmation as
        [replaced tab, challenger tab, seconds it has qualified] (None = nobody)."""
        age = None if self._last_swap == float("-inf") else round(now - self._last_swap, 1)
        c = self._candidate
        return {"last_swap_age": age, "candidate": None if c is None else [c[0], c[1], round(now - c[2], 1)]}

    @staticmethod
    def _hot_trigger(stream, now: float, cfg) -> None:
        """MAIN's own goal or strong cue at `now`: it starts a hot episode, or (inside one) moves its end. A new episode starts only when the
        last trigger is `hot_game_s` old; a chain of cues after the cap (`hot_game_max_s`) does not start a new one."""
        if now - stream.hot_last >= cfg.hot_game_s:
            stream.hot_start = now
        stream.hot_last = max(stream.hot_last, now)

    def hot_left(self, tab_id: int, now: float) -> float:
        """Seconds MAIN `tab_id` stays 'hot' (see `hot_game_s`): from its own goal (score change / goal banner) or from a STRONG cue on it
        (a review of a possible goal, a penalty awarded). 0 when it is not, when the setting is off or when the stream shows an ad or a break.
        Each tile of a multiview has its own episode (its sport's settings); the tab is as hot as its hottest tile."""
        return self._hot_rec(tab_id, now)[0]

    def _hot_rec(self, tab_id: int, now: float) -> tuple:
        """(seconds left, the tile record that is hot) of the hottest tile of a tab; (0.0, None) when none is."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return 0.0, None
        best, best_rec = 0.0, None
        for key, rec in self._recs(stream):
            cfg = self.tile_cfg(tab_id, key)
            if cfg.hot_game_s <= 0 or rec.on_ad or (stream.override or rec.auto_state) != "live" or rec.hot_last == float("-inf"):
                continue
            left = max(0.0, min(rec.hot_last + cfg.hot_game_s, rec.hot_start + cfg.hot_game_max_s) - now)
            if left > best:
                best, best_rec = left, rec
        return best, best_rec

    @staticmethod
    def _hot_end(stream) -> None:
        """An ad or a break on the stream ends its hot episode for good (it is not resumed when the break is over)."""
        stream.hot_start = stream.hot_last = float("-inf")

    def _hot_exempt(self, tab_id: int, c: dict | None, now: float, tile: int | None = None) -> bool:
        """A challenger that still takes a hot MAIN: a fresh goal of its own, or a review-weight cue (VAR check) of the last `buildup_hold_s` s.
        Judged on the challenger's tile (that of its components `c`, else `tile`; no tile known = any tile of the tab)."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return False
        for key, rec in self._sel(stream, c.get("tile") if c is not None else tile):
            cfg = self.tile_cfg(tab_id, key)
            if c is not None and rec.goal_at != float("-inf") and c.get("event", 0.0) >= cfg.goal_hold_break_event:
                return True
            if 0 <= now - rec.review_at < cfg.buildup_hold_s:
                return True
        return False

    def take_main_block(self, tab_id: int, now: float, level: float | None = None, cue_age: float | None = None, main_level: float | None = None,
                        tile: int | None = None) -> str | None:
        """Why `tab_id` may not take MAIN by the build-up tracker right now (the guards of a normal swap), or None if it may.
        What the challenger must beat (hold_break_score, goal_hold_break) is the challenger's sport's value (that of its `tile`)."""
        cfg = self.tile_cfg(tab_id, tile)
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
        rec = self._rec_of(stream, tile)
        if rec.on_ad:
            return "on an ad"
        if (stream.override or rec.auto_state) != "live":
            return "not live"
        if self.hold_left(now) > 0:
            c = self.components(tab_id, now)
            if (c["score"] if c else 0.0) < cfg.hold_break_score:
                return "manual hold"
        if cfg.goal_yield_s > 0:
            for other, st in self.streams.items():
                last = self._last_goal(st)
                if other != tab_id and other != self.main and 0 <= now - last < cfg.goal_yield_s:
                    return f"goal on tab {other} {now - last:.0f} s ago"
        if self.goal_hold_left(self.main, now) > 0:   # MAIN just had a goal: its replays still run; stay unless a big moment is elsewhere
            strong_build_up = (cfg.goal_hold_break_level > 0 and level is not None and level >= cfg.goal_hold_break_level
                               and cue_age is not None and cue_age < cfg.goal_hold_break_cue_s)   # the tracker's level + a fresh spoken cue
            if not strong_build_up and not self.goal_hold_broken(self.components(tab_id, now), cfg):
                m = self.streams[self.main]
                ago = now - max((rec.goal_at for key, rec in self._recs(m) if self._goal_hold(rec, self.tile_cfg(self.main, key), now) > 0), default=m.goal_at)
                return f"goal on MAIN {ago:.0f} s ago" + (", replay still showing" if ago >= cfg.goal_hold_s else "")
        if level is not None and self.main in self.streams and self.hot_left(self.main, now) > 0 and not self._hot_exempt(tab_id, self.components(tab_id, now), now, tile):
            main_c = self.components(self.main, now)
            bar = max(main_c["score"] if main_c else 0.0, main_level or 0.0) + cfg.hot_game_margin
            if level < bar:
                m = self._hot_rec(self.main, now)[1]
                what = "goal" if m.goal_at == m.hot_last else "cue"
                return f"MAIN is hot ({what} {now - m.hot_last:.0f} s ago): level {level:.2f} < {bar:.2f}"
        return None

    @staticmethod
    def goal_hold_broken(c: dict | None, cfg) -> bool:
        """A challenger (its components `c`, its sport's `cfg`) may take MAIN during MAIN's post-goal hold: it scores at least
        `goal_hold_break` (a big moment) or has a fresh goal of its own (event >= `goal_hold_break_event`)."""
        score, event = (c["score"], c.get("event", 0.0)) if c else (0.0, 0.0)
        return score >= cfg.goal_hold_break or event >= cfg.goal_hold_break_event

    def take_main(self, tab_id: int, now: float, reason: str, level: float | None = None, cue_age: float | None = None, main_level: float | None = None,
                  tile: int | None = None) -> Swap | None:
        """The build-up tracker asks for MAIN (football only, the server checks the sport). Same guards as a normal swap (see
        `take_main_block`: frozen, manual hold unless hold_break_score, min_dwell_s, can_main, ad, break/over, already MAIN);
        returns the swap to carry out or None."""
        if self.take_main_block(tab_id, now, level=level, cue_age=cue_age, main_level=main_level, tile=tile) is not None:   # the same level / cue age as the caller's check
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
        if self.tab_state(tab) == "over":
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
