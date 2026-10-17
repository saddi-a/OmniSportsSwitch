"""Loads settings.toml (project root) into the scoring, multiview and per-sport rules.

Everything is optional: a missing file, section or key keeps the built-in default. Unknown keys and values of the
wrong type are ignored with a warning in the log, so a typo never stops the daemon.

The extension popup edits the same settings live: `describe` lists them (with help text and defaults) for the
popup, `apply_changes` validates and applies edits at once, and the edits are kept in settings.local.json
(next to settings.toml, never edited by hand) and applied on top of settings.toml at every start.
"""
import dataclasses
import json
import logging
import math
import pathlib
import tomllib

from daemon import game_state
from daemon.multiview import FocusConfig
from daemon.scoring import OVERRIDABLE_SCORING, SPORT_KEYS, Config, Weights

SETTINGS_PATH = pathlib.Path(__file__).resolve().parent.parent / "settings.toml"
log = logging.getLogger("daemon")


@dataclasses.dataclass
class Settings:
    scoring: Config
    focus: FocusConfig
    overrides: dict = dataclasses.field(default_factory=dict)  # setting id -> value changed in the popup
    local_path: pathlib.Path | None = None                      # where the overrides are kept


def _apply(target, values: dict, section: str) -> None:
    names = {f.name for f in dataclasses.fields(target)}
    for key, value in values.items():
        if key not in names:
            log.warning("settings: unknown key [%s] %s ignored", section, key)
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            log.warning("settings: [%s] %s must be a number, ignored", section, key)
        elif dataclasses.is_dataclass(getattr(target, key)):
            log.warning("settings: [%s] %s is a section, ignored", section, key)
        else:
            setattr(target, key, float(value))


def load_settings(path: pathlib.Path = SETTINGS_PATH) -> Settings:
    scoring, focus = Config(), FocusConfig()
    settings = Settings(scoring, focus, local_path=path.with_name(LOCAL_NAME))
    _read_toml(settings, path)
    _read_local(settings)
    return settings


def _read_toml(settings: Settings, path: pathlib.Path) -> None:
    scoring, focus = settings.scoring, settings.focus
    if not path.exists():
        return
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        log.warning("settings: %s could not be read (%s); using defaults", path.name, e)
        return

    scoring_data, weights_data = data.get("scoring", {}), data.get("weights", {})
    _apply(scoring, {k: v for k, v in scoring_data.items() if not isinstance(v, dict)}, "scoring")        # a table is a sport's overrides
    _apply(scoring.weights, {k: v for k, v in weights_data.items() if not isinstance(v, dict)}, "weights")
    _read_sport_overrides(scoring.sport_scoring, scoring_data, "scoring", {f.name for f in dataclasses.fields(Config)} - {"weights"},
                          OVERRIDABLE_SCORING)
    _read_sport_overrides(scoring.sport_weights, weights_data, "weights", {f.name for f in dataclasses.fields(Weights)}, None)
    _apply(focus, data.get("multiview", {}), "multiview")

    for name, values in data.get("sports", {}).items():
        key = name.strip().lower()
        known = game_state.PROFILES.get(key)
        if known is None:
            log.warning("settings: unknown sport [sports.%s] ignored (use football, american football or generic)", name)
            continue
        edited = dataclasses.replace(known, **{k: float(v) for k, v in values.items()
                                               if k in ("freeze_s", "no_clock_s") and isinstance(v, (int, float))})
        for k in values:
            if k not in ("freeze_s", "no_clock_s"):
                log.warning("settings: unknown key [sports.%s] %s ignored", name, k)
        game_state.PROFILES[key] = edited
    banners = data.get("banners", {})
    for key, target in _PHRASE_LISTS.items():
        phrases = banners.get(key)
        if isinstance(phrases, list) and all(isinstance(p, str) and p.strip() for p in phrases):
            target[:] = [p.strip().casefold() for p in phrases]
        elif phrases is not None:
            log.warning("settings: [banners] %s must be a list of non-empty strings, ignored", key)
    for key in set(banners) - set(_PHRASE_LISTS):
        log.warning("settings: unknown key [banners] %s ignored", key)
    known_unknown = set(data) - {"scoring", "weights", "multiview", "sports", "banners"}
    for section in known_unknown:
        log.warning("settings: unknown section [%s] ignored", section)


def _read_sport_overrides(store: dict, section_data: dict, section: str, names: set, allowed: frozenset | None) -> None:
    """Per-sport overrides: `[scoring.football]`, `[weights."american football"]`... list only the values that differ from the shared
    ones. `store` becomes {sport: {name: value}}. Same rules as the shared values (a number), plus: the sport must be football,
    american football ("generic" means the shared values), and the name must be one a sport may override
    (`scoring.OVERRIDABLE_SCORING`; every weight). Mistakes are ignored with a warning. They are read from the file only, never by the
    popup, and `reset_all` leaves them alone."""
    store.clear()
    for table, values in section_data.items():
        if not isinstance(values, dict):
            continue
        key = " ".join(str(table).lower().split())
        if key not in SPORT_KEYS:
            log.warning("settings: unknown sport [%s.%s] ignored (use football or american football; generic uses the shared values)", section, table)
            continue
        for name, value in values.items():
            if name not in names:
                log.warning("settings: unknown key [%s.%s] %s ignored", section, table, name)
            elif allowed is not None and name not in allowed:
                log.warning("settings: [%s.%s] %s cannot differ per sport (it is shared by all sports), ignored", section, table, name)
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                log.warning("settings: [%s.%s] %s must be a number, ignored", section, table, name)
            else:
                store.setdefault(key, {})[name] = float(value)


# ---------------------------------------------------------------------------------------------------------------
# Editing settings from the popup
# ---------------------------------------------------------------------------------------------------------------
LOCAL_NAME = "settings.local.json"
_DEFAULT_PROFILES = dict(game_state.PROFILES)
_PHRASE_LISTS = {"break_phrases": game_state.AD_PHRASES, "promo_phrases": game_state.PROMO_PHRASES, "big_play_words": game_state.BIG_PLAY_WORDS, "replay_words": game_state.REPLAY_WORDS,
                 "anticipation_words": game_state.ANTICIPATION_WORDS}
_DEFAULT_PHRASES = {key: list(target) for key, target in _PHRASE_LISTS.items()}

_SPORT_LABELS = {"football": "Football (soccer)", "american football": "American football", "generic": "Other sports"}

# (group title, [(setting id, label, help text)]) - this is what the popup shows, in this order.
LAYOUT = [
    ("When to swap", [
        ("scoring.threshold", "Win margin", "A challenger must beat the stream on MAIN by this fraction (0.4 = 40% better)."),
        ("scoring.min_gap", "Minimum gap", "...and by at least this much in score."),
        ("scoring.min_score", "Minimum score", "A challenger scoring below this never wins, so loudness alone cannot cause a swap."),
        ("scoring.min_dwell_s", "Time between swaps (s)", "No swap happens sooner than this after the last one."),
        ("scoring.warmup_s", "Warm-up (s)", "A new stream cannot win until it has been watched this long."),
    ]),
    ("How a stream is judged", [
        ("scoring.window_s", "Loudness window (s)", "Loudness is averaged over this many seconds."),
        ("scoring.baseline_s", "Normal loudness window (s)", "Each stream's normal loudness is averaged over about this long."),
        ("scoring.surge_cap", "Loudness surge cap", "Upper limit of the loudness signal (1.0 = loudness doubled)."),
        ("scoring.classifier_target_rms", "Level the sound (0 = off)", "Above 0, every stream's normal loudness is brought to this level before the crowd / speech / music detector hears it, so a quietly mixed broadcast is judged like a loud one. Try 0.05."),
        ("scoring.speech_interval_s", "Commentary listener every (s, 0 = off)", "Measuring only, it changes no score: the words of the commentators are written to the logs this often. Turning it on or off needs a daemon restart (the model is loaded at start)."),
        ("scoring.speech_window_s", "Commentary listener window (s)", "How many of the last seconds of sound are transcribed each time."),
        ("scoring.semantic_min_sim", "Semantic match: minimum similarity", "Measuring only: commentary at least this similar to the example phrases of a big moment (0 to 1) is written to the logs as high or mid."),
        ("scoring.semantic_mid_sim", "Semantic match: minimum for mid", "Measuring only: the stricter minimum similarity for the mid level (a notable event rather than a big moment)."),
        ("scoring.semantic_margin", "Semantic match: margin over ordinary talk", "...and it must beat its similarity to ordinary match talk by this much."),
        ("scoring.speech_cue_cooldown_s", "Spoken cue cooldown (s)", "The same spoken cue on the same stream is logged at most this often."),
        ("scoring.speech_cue_factor", "Spoken cue strength", "Soccer: a commentator calling a free kick, a chance, a shot on the post, a penalty or a goal counts this much of the same cue read from a banner. 0 = off (only logged)."),
        ("scoring.classifier_max_gain", "Levelling limit (x)", "The levelling never raises or lowers the sound by more than this factor."),
        ("scoring.event_s", "Goal boost (s)", "How long a goal or touchdown boosts a stream (it fades)."),
        ("scoring.big_play_strength", "Big play strength", "A big play banner counts this much of a goal (1.0 = the same as a goal)."),
        ("scoring.stale_s", "Silence limit (s)", "A stream with no audio for this long is ignored."),
    ]),
    ("Ads", [
        ("scoring.idle_music", "Music level for an ad", "MAIN counts as showing an ad when the music probability is at least this."),
        ("scoring.leverage_weight", "Close / late game bonus", "A late close game adds up to this to a stream's score; a late blowout takes it away. 0 turns it off."),
        ("scoring.lev_us_football_close", "American football: close (points)", "At most this many points apart counts as a close game."),
        ("scoring.lev_us_football_blowout", "American football: blowout (points)", "This many points apart or more counts as a blowout."),
        ("scoring.lev_soccer_close", "Soccer: close (goals)", "At most this many goals apart counts as a close game."),
        ("scoring.lev_soccer_blowout", "Soccer: blowout (goals)", "This many goals apart or more counts as a blowout."),
        ("scoring.field_goal_strength", "Field goal strength", "American football: a field goal or safety counts this much of a touchdown (1.0 = the same)."),
        ("scoring.extra_point_s", "Extra point window (s)", "American football: an extra point or two-point conversion this soon after a touchdown is not a new event."),
        ("scoring.scorebug_hidden_s", "Scorebug hidden (s)", "Only logged, changes nothing: a scorebug missing this long is written to the events log as hidden (DAZN hides it during replays)."),
        ("scoring.scorebug_memory_s", "Scorebug seen within (s)", "Only logged: the scorebug must have been seen this recently before the gap for it to be logged."),
        ("scoring.anticipation_s", "Anticipation boost (s)", "How long a banner before a likely score boosts a stream (it fades). Only matters when its weight is above 0."),
        ("scoring.goal_banner_strength", "GOAL banner strength", "Soccer: a GOAL banner on the scorebug counts as this fraction of a goal (fading) until the score itself changes (then it is a full goal). 0 = off."),
        ("scoring.goal_phrase_surge", "Goal phrase: surge needed", "Measuring only: a goal call in the commentary with a loudness surge this big is only logged (\"would fire\")."),
        ("scoring.goal_phrase_window_s", "Goal phrase: window (s)", "...when the call and the surge are at most this far apart."),
        ("scoring.cue_zone_near_yd", "End zone: near (yards)", "At this distance from the end zone or closer, the end-zone cue has its highest weight."),
        ("scoring.cue_zone_far_yd", "End zone: far (yards)", "At this distance or farther, the end-zone cue has its lowest weight."),
        ("scoring.cue_alone_cap", "Cue alone: most it adds", "A cue with no other sign of excitement (crowd, loudness, shouting, a score) adds at most this. Keep it under the minimum score."),
        ("scoring.cue_support", "Cue: other excitement needed", "Crowd + loudness + shouting + score change worth this much lift the cap above."),
        ("scoring.cue_leverage_gain", "Cue: close game boost", "A cue is worth x(1 + this x leverage): more in a late close game, less in a late blowout."),
        ("scoring.cue_speech_window_s", "Cue: commentary window (s)", "Commentary heard this recently tells what a bare review is about."),
        ("scoring.anticipation_cooldown_s", "Anticipation cooldown (s)", "The same banner is not reported again this soon."),
        ("scoring.anticipation_big_gain_yd", "Big gain graphic (yards)", "A \"+NN YDS\" graphic counts as a cue from this many yards."),
        ("scoring.anticipation_goal_yd", "Yard line graphic (yards)", "A yard line graphic beside a down text counts as a cue up to this many yards."),
        ("scoring.replay_by_clock_s", "Replay by clock (s)", "A game clock that goes back 10-30 s while the scorebug shows is a replay. 0 = only written to the log; above 0 = also treated as a replay (crowd dampened, graphics ignored) for this long."),
        ("scoring.replay_clock_min_s", "Replay clock step, smallest (s)", "The smallest step back of the game clock that counts as a replay."),
        ("scoring.football_clock_back_s", "Football: clock this far back is a replay (s)", "A scorebug clock this many seconds below the last real game clock is a replay or highlights, not the live game (so half time stays a break). The second half's 45:00 is allowed."),
        ("scoring.football_clock_ahead_s", "Football: clock this far ahead is another match (s)", "A scorebug clock this many seconds above what the last real clock and the elapsed time allow is another match's scorebug (highlights)."),
        ("scoring.foreign_clock_accept_s", "Odd clock believed after (s)", "An odd clock that keeps running this long is believed (you rewound or skipped the stream)."),
        ("scoring.replay_clock_max_s", "Replay clock step, largest (s)", "The largest step back that counts; more than that is you rewinding the stream."),
        ("scoring.replay_dampen", "Replay dampening", "While a replay is on screen over a live game, crowd noise and loudness count this fraction (0 = ignored, 1 = same as live)."),
        ("scoring.recent_ad_s", "No hopping between ads (s)", "MAIN is not handed to a stream that was showing an ad this recently."),
        ("scoring.held_change_s", "Keep a score change in a break (s, 0 = drop)", "A score change read while the game looked like a break (a wrong break reading right after a touchdown) is kept this long and counts if the game is live again. Results tickers stay ignored."),
        ("scoring.audio_ad_margin", "Music over speech for an ad", "A music-only ad needs music to exceed speech by this much, so stadium music under commentary is not taken for an ad."),
        ("scoring.ad_sticky_s", "Ad break stays an ad (s, 0 = off)", "A commercial seen in the picture stays an ad until the scoreboard is read again, or this long. Stops the stream flickering between ad, break and live inside one ad break."),
        ("scoring.ad_exit_readings", "Scoreboard readings that end an ad", "This many readings of a scorebug end the ad at once."),
        ("scoring.audio_ad_memory_s", "Ad memory (s)", "A stream that sounded like an ad stays flagged this long."),
        ("scoring.goal_pending_s", "Early goal reading (s, 0 = off)", "A first new-score reading, before the vote confirms it, already backs the goal-music exemption this long, if the sound was loud just before. It lapses when the vote does not confirm it."),
        ("scoring.goal_pending_surge", "Early goal: surge needed", "The sound must be this far above its normal level just before an early goal reading..."),
        ("scoring.goal_pending_crowd", "Early goal: crowd needed", "...or this crowd-like."),
        ("scoring.audio_ad_goal_s", "Goal music is no ad (s, 0 = off)", "Music within this long of a goal (a confirmed score change, GOAL banner or feed goal, in either order) is the celebration, not an ad."),
        ("scoring.ad_penalty", "Ad penalty", "An ad in the picture scores minus this (music-only ads: half of it)."),
    ]),
    ("Logs and learning data", [
        ("scoring.dataset_mode", "Record learning data (1 = on, 0 = off)", "1 = one row per stream every second plus the events (swaps, goals, cues) are written to logs/dataset/ (local only, never committed) for later learning. 0 = nothing is written. Takes effect at once."),
        ("scoring.log_archive_days", "Keep archived logs (days, 0 = forever)", "Rotated log files are compressed into logs/archive/ and kept this many days. The learning data in logs/dataset/ is never deleted."),
        ("scoring.log_archive_max_mb", "Archive size cap (MB, 0 = no cap)", "If the archive grows beyond this, the oldest archived files are deleted first (even if younger than the days above)."),
    ]),
    ("Outside game data (measuring only)", [
        ("scoring.feed_mode", "Game data mode (0 = off, 1 = record)", "0 = local only: nothing is asked from the internet. 1 = record only: ESPN's public scoreboards are read and written to logs/feed, nothing else changes. Needs a daemon restart."),
        ("scoring.feed_poll_s", "Live league every (s)", "A league with a live game is asked this often (5 s at the least: the feed refreshes its cache every 5 s)."),
        ("scoring.feed_idle_poll_s", "Quiet league every (s)", "A league with no live game and none starting soon is asked this often."),
        ("scoring.feed_summary_s", "Football plays every (s, 0 = off)", "American football: the plays of each live game are fetched this often."),
        ("scoring.feed_unconfirmed_factor", "Unconfirmed score change counts (x)", "A score change read from the screen on a game the feed knows, that no feed event came before, counts this much (1.0 = off) until the feed confirms it."),
        ("scoring.feed_confirm_s", "Feed confirms within (s)", "How long the feed has, after a score change read from the screen, to confirm it before it stays at the reduced strength."),
        ("scoring.feed_event_weight", "Feed goal counts (x, 0 = log only)", "A goal or touchdown of the feed, released when the stream clock is shortly before it, counts this much of a screen goal in the scorer. 0 = it is only written to the log."),
        ("scoring.feed_lead_s", "Shadow lead (s)", "Measuring only: a feed event is logged as \"would fire\" when the stream's clock is this many seconds before it."),
        ("scoring.buildup_live", "Build-up tracker moves Main (football only)", "1 = in football, a stream whose recent cues added together reach the build-up level takes Main, with the same guards as a normal swap (dwell, hold, ads, breaks). 0 = it only writes \"buildup would take MAIN\" to the log."),
        ("scoring.buildup_min", "Build-up level to take Main", "Football: a stream whose recent cues, added together with its loudness and crowd, reach this level (and are 0.10 above Main, `buildup_gap`) take Main when the tracker is on, and are logged as \"buildup would take MAIN\"."),
        ("scoring.buildup_surge_min", "Build-up: surge that counts as a sign", "Measuring only, soccer: a loudness surge this high is noted as a small cue in the build-up (it changes no score)."),
        ("scoring.buildup_surge_note", "Build-up: value of a surge sign", "Measuring only: the value of that note, at most once per cooldown per stream."),
        ("scoring.buildup_gap", "Build-up: gap above Main", "Football: how far above Main's score the build-up level must be. The normal swap rule keeps its own gap (min_gap)."),
        ("scoring.buildup_crowd_min", "Build-up: crowd that counts as a sign", "Football: a crowd probability this high on 3 score readings in a row (1 s apart) is noted as a small cue in the build-up (it changes no score)."),
        ("scoring.goal_hold_s", "Keep Main this long after a goal (s)", "Build-up tracker: after a goal on Main (score change or goal banner) the tracker does not leave it for at least this long, so the goal replays can finish. 0 = off."),
        ("scoring.goal_hold_extend_s", "Goal hold: extend after a replay sign (s)", "Each replay sign seen during the hold (replay over the scorebug, hidden scorebug, replay by clock, the commentator saying replay) keeps the hold until its time plus this."),
        ("scoring.goal_hold_max_s", "Goal hold: longest (s)", "The hold never lasts longer than this after the goal."),
        ("scoring.goal_hold_break", "Goal hold: score that breaks it", "A challenger scoring at least this (a goal or a very big moment) still takes Main during the hold."),
        ("scoring.goal_hold_break_level", "Goal hold: build-up level that breaks it (0 = off)", "Build-up tracker: a challenger whose build-up level is at least this AND that has a spoken cue (chance, free kick, post area...) younger than the next value takes Main during the post-goal replay hold."),
        ("scoring.goal_bypass_dwell", "A fresh goal skips the wait after a swap (1 = on)", "1 = a stream that just scored (score change or goal banner of its own, strong enough) can take Main even within `min_dwell_s` of the last swap. The confirmation time still applies."),
        ("scoring.hot_game_s", "Hot game: Main stays this long (s, 0 = off)", "After Main's own goal, or a strong cue on it (VAR review, penalty awarded), Main stays 'hot' this long: a challenger must beat it by the margin below. Ends early if Main goes to an ad or a break."),
        ("scoring.hot_game_max_s", "Hot game: longest (s)", "A hot episode never lasts longer than this from its first goal or strong cue, however many more arrive. An ad or break on Main ends it for good."),
        ("scoring.hot_game_margin", "Hot game: margin a challenger needs", "How far above Main's score (build-up tracker: Main's score or build-up level) a challenger must be while Main is hot. A challenger with a fresh goal of its own, or a review cue, is exempt."),
        ("scoring.goal_yield_s", "Build-up waits for a goal elsewhere (s, 0 = off)", "The build-up tracker does not move Main to a stream while a goal (score change or goal banner) was seen on a different stream this recently, so the normal rule can take the goal stream."),
        ("scoring.goal_hold_break_cue_s", "Goal hold: how fresh the spoken cue must be (s)", "See the value above."),
        ("scoring.goal_hold_break_event", "Goal hold: own goal that breaks it", "A challenger with a fresh score change or goal banner of its own at least this strong (1 = just now) takes Main during the hold even if its score is below the value above."),
        ("scoring.buildup_hold_s", "Main keeps its seat after its own cue (s, 0 = off)", "Football: for this long after a spoken or banner cue on Main (chance, free kick, post area, penalty...), a challenger needs a build-up level `buildup_hold_margin` above Main's own build-up level to take Main."),
        ("scoring.buildup_hold_cue_min", "Main's seat: smallest cue that counts", "Cues lighter than this (loudness, crowd and set-piece notes, 0.10) do not hold Main; spoken chances and free kicks (0.20 to 0.25) do."),
        ("scoring.buildup_hold_margin", "Main's seat: margin a challenger needs", "How far above Main's own build-up level a challenger must be while Main's own cue is fresh."),
        ("scoring.buildup_review_hold_s", "Main stays during a review (s, 0 = off)", "A review cue on Main (VAR check, penalty awarded, weight at least `buildup_review_min`) holds Main this long (at most the build-up window), so the decision can be seen."),
        ("scoring.buildup_review_min", "Review hold: smallest cue weight", "A cue at least this heavy on Main counts as a review for the longer hold."),
        ("scoring.buildup_review_margin", "Review hold: margin a challenger needs", "How far above Main's own build-up level a challenger must be while Main is under review."),
        ("scoring.buildup_crowd_note", "Build-up: value of a crowd sign", "The value of that note, at most once per cooldown per stream."),
        ("scoring.surge_rise_min", "Rising sound: smallest surge", "Soccer: the loudness must be at least this far above the stream's own normal (1.0 = doubled) for the rising-sound cue."),
        ("scoring.surge_rise_ratio", "Rising sound: growth (x)", "...and the loudness must have grown by this factor over the window below (a rise, not a steady loud crowd)."),
        ("scoring.surge_rise_window_s", "Rising sound: window (s)", "The loudness is compared with a reading at least half this old."),
        ("scoring.surge_rise_votes", "Rising sound: readings in a row", "It must hold on this many score readings in a row (1 s apart): one reading is never enough."),
        ("scoring.surge_rise_cooldown_s", "Rising sound: cooldown (s)", "After the cue fires, not again on the same stream for this long."),
        ("scoring.blowout_goal_bonus", "Goal in a decided game: bonus (0 = off)", "A goal in a game that is decided (a late blowout) adds this to its score, but only while every other stream is quiet, so it can win Main only when nothing else is going on."),
        ("scoring.blowout_goal_lev", "Decided game: leverage at most minus", "A game counts as decided when its closeness / time-left value is at most minus this (-1 = late blowout)."),
        ("scoring.blowout_goal_event", "Decided game: how fresh the goal must be", "1 = just now; the goal's boost fades over the goal boost time."),
        ("scoring.blowout_goal_quiet", "Decided game: other streams are quiet below", "Every other live stream must score below this and have no cue."),
    ]),
    ("Manual control", [
        ("scoring.manual_hold_s", "Hold time for Make Main (s)", "How long a stream you put on Main with the popup button stays there."),
        ("scoring.hold_over_s", "Game over ends a hold after (s)", "A held stream whose game has looked over for this long loses its hold. The automatic game-over reading is sometimes wrong, so it must last."),
        ("scoring.hold_break_score", "Score that breaks a hold", "A challenger scoring at least this still takes Main from a held stream. Higher = harder to break."),
    ]),
    ("Signal weights", [
        ("weights.surge", "Loudness surge", "Weight of loudness above the stream's own normal."),
        ("weights.crowd", "Crowd", "Weight of cheering, crowd and applause sound."),
        ("weights.speech", "Commentary", "Weight of speech (negative: talking is not excitement)."),
        ("weights.music", "Music", "Weight of music, ads and jingles (negative)."),
        ("weights.anticipation", "Cues: master scale", "Scale of every cue before a likely score (red zone, VAR, corner...), fading over its duration. 0 = off (cues are only logged). A cue alone never takes Main."),
        ("weights.cue_review_top", "Cue: review of a goal", "VAR / review of a possible goal or touchdown, a penalty kick or a red card (the subject is read from the banner or the commentary)."),
        ("weights.cue_review_high", "Cue: review of a turnover", "VAR / review of an interception, fumble or turnover."),
        ("weights.cue_review_mid", "Cue: review of a foul", "VAR / review of offside, pass interference or a foul."),
        ("weights.cue_review_low", "Cue: review, subject unknown", "A bare review or VAR check with no subject on screen or in the commentary."),
        ("weights.cue_chance", "Cue: scoring chance", "Red zone, goal to go, field goal attempt, a penalty kick (soccer)."),
        ("weights.cue_zone_far", "Cue: end zone, far", "American football: goal-to-go / yard line cue with the ball about 10 yards from the end zone (when the distance is read)."),
        ("weights.cue_zone_near", "Cue: end zone, near", "...and 1 to 3 yards from it, or a 4th down at the goal line. With no distance read the plain scoring chance weight is used."),
        ("weights.cue_minor", "Cue: minor", "Corner, flag, 4th down, big gain, added time, timeout."),
        ("weights.cue_free_kick", "Cue: free kick", "A free kick in soccer."),
        ("weights.cue_surge_rise", "Cue: rising crowd sound", "Soccer: the crowd noise rises fast, heard before the commentator's words (0 = off)."),
        ("weights.shout", "Shouting", "Weight of shouting, yelling and screaming sound (0 = off). Ignored when music dominates, so it never helps an ad."),
        ("weights.event", "Score change", "Weight of a fresh goal or touchdown read from the scoreboard."),
        ("weights.favourite", "Favourite bonus", "Bonus per +1 of a favourite team's multiplier (a x2 team gets +0.4)."),
    ]),
    ("Multiview tiles", [
        ("multiview.switch_ratio", "Switch ratio", "Another tile must beat the current one by this factor."),
        ("multiview.min_dwell_s", "Time between tile changes (s)", "Minimum time between tile changes in one tab."),
        ("multiview.event_weight", "Score change weight", "How much a fresh score change counts for a tile."),
        ("multiview.crowd_weight", "Crowd weight", "How much crowd noise counts for the tile that has sound."),
        ("multiview.floor", "Interest floor", "Every live tile has at least this much interest."),
        ("multiview.ad_penalty", "Ad penalty", "A tile showing an ad gets negative interest, so focus moves off it."),
    ]),
    ("Break detection by sport", [
        (f"sports.{name}.{key}", f"{_SPORT_LABELS[name]}: {text}", help_)
        for name in _DEFAULT_PROFILES
        for key, text, help_ in (("freeze_s", "clock stopped (s)", "A game clock unchanged for this long means a break."),
                                 ("no_clock_s", "no clock (s)", "A scoreboard with no readable clock for this long means a break."))
    ]),
    ("Banner phrases", [
        ("banners.promo_phrases", "Promo screen wording", "Text of a promo screen between games. Counts as an ad only when no scoreboard is on screen."),
        ("banners.break_phrases", "Ad break wording", "Text a player shows during an ad break (any case, inside a text). Add your service's wording."),
        ("banners.big_play_words", "Big play banners", "Banners flashed for a big play (the text must be just that phrase)."),
        ("banners.anticipation_words", "Anticipation banners", "Banners that come before a likely score: red zone, VAR, corner, penalty, review... (the text must be just that phrase)."),
        ("banners.replay_words", "Replay banners", "Banners shown during a replay (English, French, Spanish, Portuguese; the text must be just that phrase)."),
    ]),
]
_IDS = {item[0] for _, items in LAYOUT for item in items}


def _split(setting_id: str) -> tuple[str, str | None, str]:
    if setting_id.startswith("sports."):
        name, key = setting_id[len("sports."):].rsplit(".", 1)
        return "sports", name, key
    section, key = setting_id.split(".", 1)
    return section, None, key


def _fresh(setting_id: str):
    """The built-in default of a setting."""
    section, name, key = _split(setting_id)
    if section == "scoring":
        return getattr(Config(), key)
    if section == "weights":
        return getattr(Config().weights, key)
    if section == "multiview":
        return getattr(FocusConfig(), key)
    if section == "sports":
        return getattr(_DEFAULT_PROFILES[name], key)
    return list(_DEFAULT_PHRASES[key])


def get_value(settings: Settings, setting_id: str):
    section, name, key = _split(setting_id)
    if section == "scoring":
        return getattr(settings.scoring, key)
    if section == "weights":
        return getattr(settings.scoring.weights, key)
    if section == "multiview":
        return getattr(settings.focus, key)
    if section == "sports":
        return getattr(game_state.PROFILES[name], key)
    return list(_PHRASE_LISTS[key])


def _set(settings: Settings, setting_id: str, value) -> None:
    """Checks the value and applies it; raises ValueError (with a message for the user) if it is not acceptable."""
    if setting_id not in _IDS:
        raise ValueError("unknown setting")
    section, name, key = _split(setting_id)
    if section == "banners":
        if not isinstance(value, list) or not all(isinstance(p, str) and p.strip() for p in value):
            raise ValueError("must be a list of non-empty phrases")
        target = _PHRASE_LISTS[key]
        target[:] = [p.strip().casefold() for p in value]
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("must be a number")
    if section != "weights" and value < 0:
        raise ValueError("cannot be negative")
    value = float(value)
    if section == "scoring":
        setattr(settings.scoring, key, value)
    elif section == "weights":
        setattr(settings.scoring.weights, key, value)
    elif section == "multiview":
        setattr(settings.focus, key, value)
    else:
        game_state.PROFILES[name] = dataclasses.replace(game_state.PROFILES[name], **{key: value})


def describe(settings: Settings) -> list[dict]:
    """Every setting with its label, help text, current value and default, grouped for the popup."""
    return [{"title": title, "items": [
        {"id": sid, "label": label, "help": help_, "kind": "list" if sid.startswith("banners.") else "number",
         "value": get_value(settings, sid), "default": _fresh(sid), "changed": sid in settings.overrides}
        for sid, label, help_ in items]} for title, items in LAYOUT]


def apply_changes(settings: Settings, changes: dict) -> dict[str, str]:
    """Apply edits live and remember them. Returns {setting id: reason} for the edits that were refused."""
    errors = {}
    for sid, value in changes.items():
        try:
            _set(settings, sid, value)
        except (ValueError, KeyError, TypeError) as e:
            errors[str(sid)] = str(e)
            continue
        current = get_value(settings, sid)
        if current == _fresh(sid):
            settings.overrides.pop(sid, None)
        else:
            settings.overrides[sid] = current
    _write_local(settings)
    return errors


def reset_all(settings: Settings) -> None:
    for sid in _IDS:
        _set(settings, sid, _fresh(sid))
    settings.overrides.clear()
    _write_local(settings)


def _write_local(settings: Settings) -> None:
    path = settings.local_path
    if path is None:
        return
    try:
        if settings.overrides:
            path.write_text(json.dumps(settings.overrides, indent=1, ensure_ascii=False), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError as e:
        log.warning("settings: could not save %s (%s)", path.name, e)


def _read_local(settings: Settings) -> None:
    path = settings.local_path
    if path is None or not path.exists():
        return
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("not an object")
    except (ValueError, OSError) as e:
        log.warning("settings: %s could not be read (%s); ignored", path.name, e)
        return
    for sid, value in saved.items():
        try:
            _set(settings, sid, value)
            settings.overrides[sid] = get_value(settings, sid)
        except (ValueError, KeyError, TypeError) as e:
            log.warning("settings: saved value %s ignored (%s)", sid, e)
