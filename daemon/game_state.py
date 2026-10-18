"""Works out from OCR readings whether a stream is live, on a break, or over.

Pure logic: callers pass `now` in seconds and what OCR read, so it is easy to test.

Rules (see PLAN.md):
  * text like FINAL / FULL TIME marks the game over, HALF TIME / MI-TEMPS marks a break, immediately
  * a game clock that has not changed for longer than the sport allows means a break
  * a scorebug with no readable clock for too long means a break (ad, studio segment, replay package)
  * the player's own placeholder during an ad ("COMMERCIAL BREAK IN PROGRESS", "PUBLICITARIA EN CURSO" ...) means
    a break at once, and the countdown timer it shows is not a game clock
"""
import re
from dataclasses import dataclass

from daemon.cues import banner_tier
from daemon.score_watch import STAT_GRAPHIC_RE
from daemon.scoring import Config

# A caption only counts when a whole OCR text item is exactly the keyword. Words inside a sentence do not
# (an ad saying "ready for the half time?" must not mark the game as on a break).
OVER_RE = re.compile(r"(FINA[LU1I](?:\s?[/|IlU1]?\s?\d?OT\d?)?|SCORE FINAL|FULL[\s-]?TIME|FT|FIN DU MATCH|MATCH TERMIN[EÉ]|TERMIN[EÉ]|COUP DE SIFFLET FINAL"
                     r"|FINAL DEL PARTIDO|FIN DEL PARTIDO|PARTIDO FINALIZADO|FINALIZADO|TERMINADO|FIM DE JOGO|FIM DO JOGO|FINAL DO JOGO|JOGO ENCERRADO)",
                     re.IGNORECASE)   # also "FINAL/OT", "FINAL/2OT" as OCR mangles them (FINAUOT, FINALIOT)
# Overtime (American football) has no game clock on the scoreboard, so a missing clock is not a break there.
OVERTIME_RE = re.compile(r"(\d?OT|.*(OVERTIME|PROLONGATION|TEMPS SUPPL[EÉ]MENTAIRE).*)", re.IGNORECASE)
OVERTIME_MEMORY_S = 30.0
# American football down and distance on the scorebug ("3rd & 4", "Ath & 2", "Ist & GOAL" as OCR mangles them). Seen on the
# scorebug it proves a game is being played even when the clock cannot be read (the colon is often lost: "14848"). Stat
# labels ("3rd DOWNS", "3rd DOWN CONVERSIONS") have no "&" and do not match.
DOWN_RE = re.compile(r"[0-9A-Za-z]{1,2}(st|nd|rd|th|sr|r0)\s*(&\s*(\d{1,2}|goal|inches)|down)", re.IGNORECASE)   # also "2nd Down"
# The quarter on its own ("4th", read "Ath"; "1st", read "Ist") sits on the same scorebug: also proof that it is on screen.
QUARTER_RE = re.compile(r"(1st|ist|isr|lst|2nd|znd|3rd|3r0|jrd|4th|ath|q[1-4])", re.IGNORECASE)
# A score ("44"), a score pair ("2-0") or a down/quarter beside a clock means a scoreboard; ad copy beside a countdown has none.
SCOREBOARD_ITEM_RE = re.compile(r"\d{1,3}|\d{1,3}\s?[-/]\s?\d{1,3}|[A-Z]{2,4}\s+\d{1,3}\s?[-/]\s?\d{1,3}")   # last: "FRA 0-0"
# A scorebug whose digits OCR missed still shows team codes ("SGR", "REN") or a code read together with the clock ("24.47 RMA").
TEAM_CODE_RE = re.compile(r"[A-Z]{3,4}")
CLOCK_WITH_CODE_RE = re.compile(r"\d{1,3}\s?[:.,]\s?\d{2}\s+\W?[A-Z]{2,4}\W?")
DOWN_MEMORY_S = 20.0
OVERLAY_TEXTS = ("OmniSportsSwitch", "Freeze MAIN", "Re-track this tab", "held on")   # the extension's own overlay: its countdowns are not a game clock
IN_PLAY_MEMORY_S = 15.0   # a "Final" caption is ignored this long after the scorebug of a game in play was seen
PERIOD_OF = {"1st": 1, "ist": 1, "isr": 1, "lst": 1, "2nd": 2, "znd": 2, "3rd": 3, "3r0": 3, "jrd": 3, "4th": 4, "ath": 4, "q1": 1, "q2": 2, "q3": 3, "q4": 4}
PERIOD_VOTES, PERIOD_NEEDED = 5, 3   # the period is the value most of the last readings agree on
BREAK_RE = re.compile(r"(HALF[\s-]?TIME|HT|MI[\s-]?TEMPS|MITEMPS|INTERVALLE|DESCANSO|ENTRE[\s-]?TIEMPO|MEDIO[\s-]?TIEMPO|INTERVALO(?: DE JOGO)?)", re.IGNORECASE)
# The end of a quarter / period (English, French, Spanish, Portuguese; "OTR" is how OCR reads "QTR"). A shorter break than half time:
# it ends as soon as the scorebug shows play again.
QUARTER_END_RE = re.compile(r"(END OF\s+\w{1,6}\s+(QTR|OTR|QUARTER|PERIOD|PERIODE)|FIN DU\s+(\w{1,8}\s+)?QUART|FIN D[EU]\s+(LA\s+)?(\w{1,4}\s+)?P[EÉ]RIODE"
                            r"|FIN DEL\s+\w+\s+(CUARTO|TIEMPO|PERIODO)|FIM DO\s+\w+\s+(QUARTO|TEMPO|PER[IÍ]ODO))", re.IGNORECASE)
QUARTER_END_MEMORY_S = 45.0
CAPTION_CONFIRMATIONS = 2  # a caption must show on this many consecutive readings
KEYWORD_MEMORY_S = 120.0  # a FINAL / HALF TIME caption counts for this long after it was last seen

# Phrases a streaming player shows during an ad break (matched inside a longer text item, lower case).
# Seen live: "COMMERCIAL BREAK IN PROGRESS", "PUBLICITARIA EN CURSO", "INTERVALO COMERCIAL", "PUBLICITE".
AD_PHRASES = ["commercial break", "publicitaria", "intervalo comercial", "publicité", "publicite", "publicitaire", "publicidade", "pausa comercial",
              "ad break", "advertisement", "right back", "volvemos", "de retour dans", "watch now", "ondazn",
              "event break", "evento em intervalo", "interrupcion del evento", "interrupción del evento", "stream live matches", "schedule for availability"]
# Promo screens a service shows between games (DAZN: tiles with no game in them). They count as an ad ONLY with no scoreboard on screen: a live game
# frame carries "This programme contains US advertising..." or a Game Pass logo beside its scorebug. Matched inside a longer text item, lower case.
PROMO_PHRASES = ["advertising", "on dazn", "nfl game pass", "watchevery", "daznboxing", "dazn boxing"]
# Banners the broadcast flashes for a big play. A text item counts only if it is (nearly) just that phrase, so
# sentences and stat labels ("TURNOVERS") do not. Seen live: "TAKEAWAY!" after an interception.
BIG_PLAY_WORDS = ["takeaway", "interception", "intercepted", "fumble", "sack", "red card", "carton rouge", "penalty kick"]
BIG_PLAY_SLACK = 10     # extra characters allowed around the phrase in the same text item
BIG_PLAY_COOLDOWN_S = 20.0   # the same banner lingering on screen counts once
# Banners of a replay (English, French, Spanish, Portuguese). Like big-play banners a text item counts only if it is (nearly) just that
# phrase; hyphens, accents' case and punctuation are ignored ("slo-mo" matches "SLO MO" and "slomo").
REPLAY_WORDS = ["replay", "instant replay", "action replay", "slo-mo", "slow motion", "ralenti", "reprise", "reprise vidéo", "reprise video",
                "repetición", "repeticion", "ralentí", "câmera lenta", "camera lenta", "cámara lenta", "camara lenta", "repetição", "repeticao"]
REPLAY_SLACK = 4        # extra characters allowed around the phrase (a sentence about "the replay" must not count)
REPLAY_CONFIRMATIONS = 2   # the banner must show on this many consecutive readings
REPLAY_MEMORY_S = 6.0   # a replay banner counts this long after it was last seen (OCR misses it now and then)
REPLAY_GAP_S = 6.0   # a clock read this long after the one before (the scorebug was hidden) may stand alone as a replay's clock
SCOREBUG_MEMORY_S = 6.0   # a clock / down / quarter on screen proves a live scorebug this long: a replay in a live game
# Anticipation cues (measuring only): banners and graphics that come just before a likely score. A phrase counts when a text item is
# (nearly) just that phrase, like the big-play banners; the cue is the phrase. Red card / penalty kick are big plays and not repeated here.
ANTICIPATION_WORDS = ["red zone", "goal to go", "two minute warning", "two-minute warning", "field goal", "field goal attempt", "penalty", "flag", "flag on the play", "review", "under review", "replay review", "challenge", "big play", "var", "var check", "free kick", "corner", "corner kick", "penalty shootout", "avertissement de deux minutes", "tir de placement", "pénalité", "pénalty", "drapeau", "révision", "contestation", "assistance vidéo", "coup franc", "tir au but", "tirs au but", "penalti", "tiro libre", "saque de esquina", "escanteio", "tiro livre", "pênalti",
                      "timeout", "temps mort"]
# Banner cues that only exist in some sports (same idea as speech.CUE_SPORTS). A cue not listed here is valid everywhere (review, replay,
# timeout, var, foul...). Applied only when the tracker's sport is known; an unknown sport keeps every cue.
_AF, _SOC = "american football", "football"
BANNER_CUE_SPORTS = {
    **dict.fromkeys(["red zone", "goal to go", "yard line", "4th down", "big gain", "two minute warning", "two-minute warning",
                     "avertissement de deux minutes", "field goal", "field goal attempt", "tir de placement", "flag", "flag on the play",
                     "drapeau"], {_AF}),
    **dict.fromkeys(["free kick", "coup franc", "corner", "corner kick", "saque de esquina", "escanteio", "penalty shootout", "tir au but",
                     "tirs au but", "added time", "var", "var check"], {_SOC}),
    **dict.fromkeys(["penalty", "pénalité", "pénalty", "penalti", "pênalti"], {_SOC, _AF}),   # a penalty kick, or a flag in American football
}


# A review banner that names its subject: "VAR: POSSIBLE GOAL", "REVIEW: TOUCHDOWN", "PENALTY CHECK" (the subject must be a known one).
REVIEW_BANNER_RE = re.compile(r"^(var|video review|review|under review|replay review|v[ée]rification vid[ée]o|v[ée]rification|r[ée]vision)\b[\s:\-–—]*(.+)$", re.IGNORECASE)
REVIEW_BANNER_END_RE = re.compile(r"^(.+?)[\s:\-]+(check|review|v[ée]rification|r[ée]vision)$", re.IGNORECASE)
REVIEW_BANNER_MAX_CHARS = 40
ANTICIPATION_SLACK = 4         # extra characters allowed around the phrase
ANTICIPATION_CONFIRMATIONS = 2   # a cue must show on this many consecutive readings
FOURTH_DOWN_RE = re.compile(r"(4th|ath)\s*&\s*(\d{1,2}|goal|inches)", re.IGNORECASE)   # "Ath" is how OCR reads "4th"
GOAL_DOWN_RE = re.compile(r"[0-9A-Za-z]{1,2}(st|nd|rd|th|sr|r0)\s*&\s*goal", re.IGNORECASE)
YARDS_RE = re.compile(r"(\d{1,2})\s?yds?", re.IGNORECASE)
DOWN_NUMBER_RE = re.compile(r"[0-9A-Za-z]{1,2}(st|nd|rd|th|sr|r0)\s*&\s*(\d{1,2}|goal|inches)", re.IGNORECASE)   # "Ist & GOAL", "2nD & 7"
DOWN_OF_SUFFIX = {"st": 1, "sr": 1, "nd": 2, "rd": 3, "r0": 3, "th": 4}
STAT_LINE_RE = re.compile(r"\d+\s?(rec|car|att|tds?)\b|\brec\b,?|\bcomp\b", re.IGNORECASE)   # "7 REC, 68 YDS": a stat line, not a position
GOAL_WORD_RE = re.compile(r"go{1,4}a{1,2}l|but|gol|golo|gool", re.IGNORECASE)   # a whole item: "GOAL", "BUT !", "GOL" (letters only)
GOAL_PAIR_RE = re.compile(r"go{0,3}a{1,2}l", re.IGNORECASE)   # two items side by side that OCR broke GOAL into: "G" + "AL"
GOAL_LONE = {"al", "oal", "goa", "gal"}   # what is left of the banner when it hides the first team code
GOAL_BANNER_COOLDOWN_S = 30.0
DIGITS_ONLY_RE = re.compile(r"\d{1,2}")   # a scorebug digit
NOT_TEAM_CODES = {"LIVE", "MORE", "NEWS", "HOME", "PLAY", "FREE", "SHOP", "VOIR", "PLUS", "AVEC", "POUR", "DAZN", "BEIN", "WATCH", "FULL", "TIME", "HALF", "STAT", "STATS"}
CODE_PAIR_MEMORY_S = 10.0   # two team codes read together this recently mean a scorebug is on screen
END_ZONE_MAX_YD = 25   # a yard line graphic beyond the red zone says nothing about the end zone
GAIN_RE = re.compile(r"\+\s?(\d{1,3})\s?yds?", re.IGNORECASE)
ADDED_TIME_RE = re.compile(r"\+\s?\d{1,2}")
ADDED_TIME_MINUTES = (range(45, 51), range(90, 101))   # a "+3" beside a soccer clock reading these minutes is added time
LONE_CLOCK_GRACE_S = 15.0   # a clock with no scoreboard text beside it still counts as the game clock this long after a scoreboard, if it keeps running
CLOCK_SLACK_S = 3.0         # ...and it is the same clock when it moved by the elapsed time (or not at all: a stopped clock) within this slack
AD_CONFIRMATIONS = 2   # the phrase must show on this many consecutive readings
AD_MEMORY_S = 8.0      # a break placeholder counts this long after it was last seen


@dataclass(frozen=True)
class SportProfile:
    freeze_s: float    # clock unchanged for this long -> break
    no_clock_s: float  # no readable clock for this long -> break
    quiet_freeze_s: float = 0.0   # clock unchanged AND the sound quiet for this long -> break (0 = off): dead time such as a medical timeout


PROFILES = {
    "football": SportProfile(freeze_s=20, no_clock_s=45),             # soccer: the clock only stops at half time / full time
    "american football": SportProfile(freeze_s=90, no_clock_s=90, quiet_freeze_s=45),   # clock stops after most plays; a medical timeout or TV break lasts minutes
    "generic": SportProfile(freeze_s=120, no_clock_s=60),             # any other or unknown sport
}

# A clock above this many minutes is not the game clock of the sport (a stats table: "80:40"); sports not listed allow 130.
MAX_CLOCK_MIN = {"american football": 20}
MAX_CLOCK_MIN_DEFAULT = 130


def profile_for(sport: str | None) -> SportProfile:
    return PROFILES.get((sport or "").strip().lower(), PROFILES["generic"])


def _banner_hit(text: str, phrases: list[str], slack: int) -> str:
    """The phrase that `text` is (nearly) just, as whole words ignoring case, hyphens and punctuation; else ''."""
    words = re.sub(r"[^a-zà-ÿ ]", "", text.casefold()).strip()
    for phrase in phrases:
        plain = re.sub(r"[^a-zà-ÿ ]", "", phrase.casefold())
        if re.search("(?<![a-zà-ÿ])" + re.escape(plain) + "(?![a-zà-ÿ])", words) and len(words) <= len(plain) + slack:
            return phrase
    return ""


def has_scoreboard_text(texts: list[str], clocks: list[str]) -> bool:
    """True when something besides the clocks looks like a scoreboard: a score, a pair, a down or quarter, two team codes, or a team
    code read together with the clock. A countdown with none of them beside it is an ad countdown, not a game clock."""
    others = [t.strip() for t in texts if t.strip() and t.strip() not in clocks]
    if any(SCOREBOARD_ITEM_RE.fullmatch(t) or DOWN_RE.fullmatch(t) or QUARTER_RE.fullmatch(t) for t in others):
        return True
    return len({t for t in others if TEAM_CODE_RE.fullmatch(t)}) >= 2 or any(CLOCK_WITH_CODE_RE.fullmatch(t) for t in others)

CODES_MEMORY_S = 10.0   # team codes read this recently count as a scorebug still on screen


class GameTracker:
    """Follows one video area (a whole stream, or one multiview tile) over time."""

    def __init__(self, sport: str | None = None, config: Config | None = None):
        self.sport = (sport or "").strip().lower()
        self.profile = profile_for(sport)
        self.config = config or Config()   # shared with the scorer, so popup edits apply at once
        self.state = "live"          # optimistic until proven otherwise
        self._clock: str | None = None
        self._clock_changed_at: float | None = None
        self._loud_at = float("-inf")   # the last reading with a loud sound (`quiet` False); a quiet count needs no noise since the clock froze
        self._last_clock_seen: float | None = None
        self._codes_seen = float("-inf")   # when two team codes were last read together (a scorebug)
        self._first_seen: float | None = None
        self._over_seen = float("-inf")
        self._break_seen = float("-inf")
        self._qend_seen = float("-inf")
        self._qend_hits = 0
        self._overtime_seen = float("-inf")
        self._down_seen = float("-inf")
        self._in_play_seen = float("-inf")
        self._period_votes: list[int] = []
        self.clock_minute: int | None = None   # minutes on the last readable game clock (soccer: 67 for 67:23)
        self.period: int | None = None   # quarter 1..4, 5 = overtime; None until read on several readings
        self._over_hits = 0
        self._break_hits = 0
        self._ad_hits = 0
        self._board_since_ad = 0   # scoreboard readings since an ad was last confirmed (3 of them end a sticky ad)
        self._game_clock: tuple[float, float] | None = None   # (seconds, time) of the clock last read beside a scoreboard
        self._ad_seen = float("-inf")
        self.on_ad = False   # True while the player's ad placeholder is showing
        self.replay = False   # True while a replay is shown over a live game (scorebug still visible); a replay with no scorebug is a break
        self._replay_hits = 0
        self._replay_seen = float("-inf")
        self._scorebug_seen = float("-inf")
        self.scorebug_note: tuple[str, float] | None = None   # set on the reading where a hidden scorebug is reported (measuring only)
        self._gap_start: float | None = None   # when the scorebug was last seen before the current gap
        self._gap_announced = False
        self._gap_dead = False                 # the gap was an ad / break / stale: never reported
        self.clock_replay_note: tuple[str, float] | None = None   # ("start", s back) / ("end", s long) on the reading that detects a replay by its clock
        self._real_clock: tuple[float, float] | None = None   # football: (seconds, time) of the last clock that fitted the game; replays, highlights and overlays are filtered against it
        self._game_codes: frozenset = frozenset()   # football: the two team codes read beside the last real clock
        self._foreign: tuple[float, float, float] | None = None   # (first time, last time, last seconds) of an odd clock that keeps running
        self._cs_last: float | None = None     # last game clock in seconds, and when it was read
        self._cs_time = 0.0
        self._cs_dir = 0                       # +1 the clock counts up (soccer), -1 down (American football), 0 not known yet
        self._cs_pending: tuple[float, float, float, float] | None = None   # (clock, time, seconds back, seconds since the clock before) of a back step waiting for confirmation
        self._clock_replay_start: float | None = None   # when the clock replay in progress was detected
        self.anticipation: list[str] = []   # cues reported on this reading
        self.anticipation_text: dict[str, str] = {}   # ...and the banner text each came from (the subject of a review)
        self.goal_banner = ""                    # soccer: set on the one reading where a GOAL banner shows (see _read_goal_banner)
        self._goal_banner_at = float("-inf")
        self._code_pair: tuple[frozenset, float] | None = None   # the two team codes of the scorebug and when they were last read
        self.down: int | None = None            # American football: the down on the scorebug (1-4), else None
        self.goal_to_go = False                 # ...and whether it says "& GOAL"
        self.end_zone_yards: int | None = None  # ...and the distance to the end zone when a yard line graphic gives it (beside a down text)
        self._cue_texts: dict[str, str] = {}
        self._cue_hits: dict[str, int] = {}
        self._cue_at: dict[str, float] = {}
        self.big_play = ""   # set on the one reading where a new big-play banner appears (else "")
        self._big_play_at = float("-inf")

    def set_sport(self, sport: str | None) -> None:
        self.sport = (sport or "").strip().lower()
        self.profile = profile_for(sport)

    def _drop_foreign_clocks(self, now: float, texts: list[str], clocks: list[str]) -> list[str]:
        """Football: the clocks that may be the live game clock. Dropped: any clock read while the extension's own overlay is on screen
        (its countdowns '0:58' are not a clock), a clock more than `football_clock_back_s` below the last real one (a replay package or
        highlights; the 2nd half's 45:00 is fine), and one more than `football_clock_ahead_s` above what the last real one plus the elapsed
        time allows (another match's scorebug). An odd clock that keeps running for `foreign_clock_accept_s` is believed (the viewer rewound)."""
        if self.sport != "football" or not clocks:
            return clocks
        if any(o.casefold() in t.casefold() for t in texts for o in OVERLAY_TEXTS):
            return []
        lone = not has_scoreboard_text(texts, clocks)   # no scoreboard beside the clock: perhaps an ad countdown (judged by the ad rules)
        cfg, kept = self.config, []
        codes = frozenset(t for t in (re.sub(r"[^\w\s-]", "", x).strip() for x in texts) if TEAM_CODE_RE.fullmatch(t) and t not in NOT_TEAM_CODES)
        other_game = len(codes) >= 2 and len(self._game_codes) >= 2 and not (codes & self._game_codes)   # a scorebug of two other teams
        for n, c in enumerate(clocks):
            m = re.fullmatch(r"(\d{1,3}):(\d{2})", c.strip())
            if not m:
                kept.append(c)
                continue
            if int(m.group(2)) >= 60:
                continue   # '89:95' is no clock
            s = int(m.group(1)) * 60 + int(m.group(2))
            if n > 0:
                # Only the FIRST clock of a reading is the game clock (the rest are read from boards and graphics: '75:21', '89:53'):
                # the others are kept or dropped by the same range check but never become the 'last real clock'.
                if self._real_clock is None or not (s < self._real_clock[0] - cfg.football_clock_back_s or s > self._real_clock[0] + (now - self._real_clock[1]) + cfg.football_clock_ahead_s):
                    kept.append(c)
                continue
            if self._real_clock is None:
                kept.append(c)
                self._real_clock = (s, now)
                if len(codes) >= 2:
                    self._game_codes = codes
                continue
            last, then = self._real_clock
            second_half = 45 * 60 <= s < 46 * 60 and last >= 44 * 60
            odd = other_game or (s < last - cfg.football_clock_back_s and not second_half) or s > last + (now - then) + cfg.football_clock_ahead_s
            if not odd:
                kept.append(c)
                self._real_clock, self._foreign = (s, now), None
                if len(codes) >= 2:
                    self._game_codes = codes
                continue
            first, prev, prev_s = self._foreign if self._foreign is not None else (now, now, s)
            runs = now - prev <= 15.0 and abs(s - prev_s - (now - prev)) <= 20    # the same odd clock, still running
            if lone and not (self._foreign is not None and runs):
                kept.append(c)   # an odd clock alone on screen that is not an odd clock we already follow: an ad countdown, not ours to judge
                continue
            first = first if runs or self._foreign is None else now
            self._foreign = (first, now, s)
            if now - first >= cfg.foreign_clock_accept_s:
                kept.append(c)
                self._real_clock, self._foreign = (s, now), None
                self._game_codes = codes if len(codes) >= 2 else frozenset()
        return kept

    def update(self, now: float, texts: list[str], clocks: list[str], quiet: bool | None = None) -> str:
        self.clock_replay_note = None
        if quiet is False:
            self._loud_at = now
        if self._first_seen is None:
            self._first_seen = now
        clocks = self._drop_foreign_clocks(now, texts, clocks)

        items = [re.sub(r"[^\w\s-]", "", t).strip() for t in texts]
        if any(DOWN_RE.fullmatch(t.strip()) or QUARTER_RE.fullmatch(t.strip()) for t in texts):
            self._in_play_seen = now   # down and distance or the quarter on the scorebug: this game is being played
        # "Final" next to a live scorebug is a results ticker about other games, not the end of this one.
        ticker = now - self._in_play_seen <= IN_PLAY_MEMORY_S
        # A bare "FT" is also a label in stats graphics ("45'  FT" in a match-momentum chart): with the game clock still running it is not the end.
        running = bool(clocks) and self._clock_changed_at is not None and (clocks[0] != self._clock or now - self._clock_changed_at <= 3.0)
        self._over_hits = self._over_hits + 1 if any(OVER_RE.fullmatch(t) and not (running and t.upper() == "FT") for t in items) and not ticker else 0
        if ticker:
            self._over_seen = float("-inf")
        self._break_hits = self._break_hits + 1 if any(BREAK_RE.fullmatch(t) for t in items) else 0
        if any(OVERTIME_RE.fullmatch(t) for t in items):
            self._overtime_seen = now
        found = 5 if any(OVERTIME_RE.fullmatch(t) for t in items) else next(
            (PERIOD_OF[t.strip().casefold()] for t in texts if QUARTER_RE.fullmatch(t.strip())), None)
        if found is not None:
            self._period_votes = (self._period_votes + [found])[-PERIOD_VOTES:]
            best = max(set(self._period_votes), key=self._period_votes.count)
            if self._period_votes.count(best) >= PERIOD_NEEDED:
                self.period = best
        if any(DOWN_RE.fullmatch(t.strip()) or QUARTER_RE.fullmatch(t.strip()) for t in texts):
            self._down_seen = now
        if self._over_hits >= CAPTION_CONFIRMATIONS:
            self._over_seen = now
        if self._break_hits >= CAPTION_CONFIRMATIONS:
            self._break_seen = now
        self._qend_hits = self._qend_hits + 1 if any(QUARTER_END_RE.fullmatch(t) for t in items) else 0
        if self._qend_hits >= CAPTION_CONFIRMATIONS:
            self._qend_seen = now

        lowered = [t.casefold() for t in texts]
        # A countdown clock that is the ONLY text on screen (no teams, no score) is the player's ad countdown ("2:27"), not a
        # game clock: seen on two readings in a row it counts like an ad placeholder phrase.
        scoreboard = has_scoreboard_text(texts, clocks)
        lone_clock = bool(clocks) and bool(texts) and not scoreboard and not self._continues_game_clock(now, clocks)
        if scoreboard and clocks and (m := re.fullmatch(r"(\d{1,3}):(\d{2})", clocks[0].strip())):
            self._game_clock = (int(m.group(1)) * 60 + int(m.group(2)), now)
        promo = not scoreboard and any(p in t for t in lowered for p in PROMO_PHRASES)   # a promo screen with no game on it
        if texts or clocks:   # a frame the OCR read as completely empty says nothing: it must not reset the 2-in-a-row count (a DAZN carousel is read empty every other frame)
            self._ad_hits = self._ad_hits + 1 if lone_clock or promo or any(p in t for t in lowered for p in AD_PHRASES) else 0
        if self._ad_hits >= AD_CONFIRMATIONS:
            self._ad_seen = now
            self._board_since_ad = 0
        elif scoreboard:   # the same evidence the ad countdown rule asks for: a score, a pair, a down or quarter, team codes
            self._board_since_ad += 1
        # An ad break stays an ad until the game is seen again: the countdown is read on only some frames, so the short memory ran out
        # inside one break and the picture flickered AD / break / live. It ends after `ad_exit_readings` scoreboard readings, or at the cap.
        sticky = (self.config.ad_sticky_s > 0 and now - self._ad_seen <= self.config.ad_sticky_s
                  and self._board_since_ad < self.config.ad_exit_readings)
        on_ad = now - self._ad_seen <= AD_MEMORY_S or sticky
        self.on_ad = on_ad

        # A replay: the banner on two readings in a row. With the scorebug still on screen it is a replay inside the live game
        # (`replay`: its crowd noise and score graphics are old news); without one it is a studio show or a replay package = a break.
        if any(DOWN_RE.fullmatch(t.strip()) or QUARTER_RE.fullmatch(t.strip()) for t in texts) or (clocks and not on_ad):
            self._scorebug_seen = now
        self._replay_hits = self._replay_hits + 1 if not on_ad and any(_banner_hit(t, REPLAY_WORDS, REPLAY_SLACK) for t in texts) else 0
        if self._replay_hits >= REPLAY_CONFIRMATIONS:
            self._replay_seen = now
        replaying = now - self._replay_seen <= REPLAY_MEMORY_S and not on_ad
        self.replay = replaying and now - self._scorebug_seen <= SCOREBUG_MEMORY_S
        replay_break = replaying and not self.replay

        self.big_play = ""
        if not on_ad and now - self._big_play_at >= BIG_PLAY_COOLDOWN_S:
            for text in texts:
                hit = _banner_hit(text, BIG_PLAY_WORDS, BIG_PLAY_SLACK) if not self._review_banner(text.strip()) else ""   # a review is not the play
                if hit:
                    self.big_play, self._big_play_at = hit, now
                    break

        # A clock read from a stats table or leaders graphic (no scoreboard beside it), or one above what the sport's clock can show,
        # is not the game clock: it must not restart the no-clock timer, or a break filled with graphics is never noticed.
        # (has_scoreboard_text is too loose here: a stats table is full of plain numbers.)
        others = [t.strip() for t in texts if t.strip() and t.strip() not in clocks]
        board_cue = (any(DOWN_RE.fullmatch(t) or QUARTER_RE.fullmatch(t) or CLOCK_WITH_CODE_RE.fullmatch(t) for t in others)
                     or len({t for t in others if TEAM_CODE_RE.fullmatch(t)}) >= 2)
        if len({t for t in others if TEAM_CODE_RE.fullmatch(t)}) >= 2:
            self._codes_seen = now   # a scorebug with its team codes is on screen, whether or not its clock was read
        stats_only = not board_cue and any(STAT_GRAPHIC_RE.search(t) for t in texts)
        clocks = [c for c in clocks if not stats_only and not self._impossible_clock(c)]
        if clocks and not on_ad:  # the countdown shown during an ad is not the game clock
            shown = re.fullmatch(r"(\d{1,3}):\d{2}", clocks[0].strip())
            if shown:
                self.clock_minute = int(shown.group(1))
            if clocks[0] != self._clock:
                self._clock, self._clock_changed_at = clocks[0], now
            self._last_clock_seen = now
            self._track_clock_replay(now, clocks)

        if now - self._over_seen <= KEYWORD_MEMORY_S:
            self.state = "over"
        elif on_ad:
            self.state = "break"
        elif replay_break:
            self.state = "break"
        elif now - self._break_seen <= KEYWORD_MEMORY_S:
            self.state = "break"
        elif now - self._qend_seen <= QUARTER_END_MEMORY_S and self._in_play_seen < self._qend_seen:
            self.state = "break"   # end of a quarter: until the scorebug shows down and distance / the quarter again
        elif clocks and self._clock_changed_at is not None:
            frozen_for = now - self._clock_changed_at
            short = self.profile.quiet_freeze_s
            dead = bool(short) and quiet is True and frozen_for > short and now - self._loud_at > short   # frozen, and no noise for that long
            self.state = "break" if frozen_for > self.profile.freeze_s or dead else "live"
        elif now - self._overtime_seen <= OVERTIME_MEMORY_S:
            self.state = "live"   # overtime: no game clock to read
        elif now - self._down_seen <= DOWN_MEMORY_S:
            self.state = "live"   # down and distance on the scorebug: the game is being played, whatever the clock OCR says
        elif self._last_clock_seen is not None and now - self._last_clock_seen <= self.profile.no_clock_s:
            pass  # clock briefly unreadable (overlay, OCR miss): keep the previous state
        elif self._last_clock_seen is not None and now - self._codes_seen <= CODES_MEMORY_S and now - self._last_clock_seen <= 2 * self.profile.no_clock_s:
            pass  # the scorebug is still there (its team codes were read) but its clock font is not: wait twice as long before a break
        else:
            since = self._last_clock_seen if self._last_clock_seen is not None else self._first_seen
            self.state = "break" if now - since > self.profile.no_clock_s else "live"
        self._note_hidden_scorebug(now, on_ad)
        self._read_position(texts)
        self._read_goal_banner(now, texts, on_ad)
        self._note_anticipation(now, texts, on_ad)
        return self.state

    def _impossible_clock(self, clock: str) -> bool:
        m = re.fullmatch(r"(\d{1,3}):\d{2}", clock.strip())
        return bool(m) and int(m.group(1)) > MAX_CLOCK_MIN.get(self.sport, MAX_CLOCK_MIN_DEFAULT)

    def _continues_game_clock(self, now: float, clocks: list[str]) -> bool:
        """True when a clock seen without any scoreboard text beside it is the game clock going on under a graphic (a substitution
        banner hiding the team names): a scoreboard was read less than `LONE_CLOCK_GRACE_S` ago and the clock moved by the elapsed time
        (either way) or not at all. An ad countdown starts at some unrelated value."""
        if self._game_clock is None or now - self._game_clock[1] > LONE_CLOCK_GRACE_S:
            return False
        last, then = self._game_clock
        for c in clocks:
            m = re.fullmatch(r"(\d{1,3}):(\d{2})", c.strip())
            if m:
                moved = abs(int(m.group(1)) * 60 + int(m.group(2)) - last)
                if moved <= CLOCK_SLACK_S or abs(moved - (now - then)) <= CLOCK_SLACK_S:
                    return True
        return False

    def _track_clock_replay(self, now: float, clocks: list[str]) -> None:
        """A replay shown with its scorebug has the clock of the moment being replayed: it goes back `replay_clock_min_s` to
        `replay_clock_max_s` (against the direction the clock runs in) and then runs on from there. A step back confirmed by the next
        reading is reported as ("start", seconds back); ("end", seconds) comes when the live clock returns (a jump forward). A rewind of
        minutes by the viewer is not one. With `replay_by_clock_s` above 0 `self.replay` is also set for that long (0 = log only)."""
        cfg = self.config
        seconds = [int(m.group(1)) * 60 + int(m.group(2)) for c in clocks if (m := re.fullmatch(r"(\d{1,3}):(\d{2})", c.strip()))]
        if not seconds:
            return
        if self._cs_last is None:
            self._cs_last, self._cs_time = seconds[0], now
            return
        last, elapsed = self._cs_last, now - self._cs_time
        direction = self._cs_dir
        c = min(seconds, key=lambda s: abs(direction * (s - last) - elapsed) if direction else abs(s - last))   # the clock that fits best
        if self._cs_pending is not None:
            c1, t1, back, gap = self._cs_pending
            self._cs_pending = None
            if -1 <= direction * (c - c1) <= (now - t1) + 4:   # the clock runs on from the odd value: a replay
                self.clock_replay_note = ("start", back)
                self._clock_replay_start = now
                self._cs_last, self._cs_time = c, now
                self._apply_clock_replay(now)
                return
            if gap >= REPLAY_GAP_S and abs(direction * (c - last) - elapsed) <= 5:
                # one reading of an earlier clock between two gaps of the scorebug, then the live clock again: the replay is already over
                self.clock_replay_note = ("start", back)
                self._cs_last, self._cs_time = c, now
                return
        if not direction and elapsed <= 4.5 and 1 <= abs(c - last) <= elapsed + 2:
            direction = self._cs_dir = 1 if c > last else -1     # the clock moves as time passes: learn which way (once)
        if direction:
            progress = direction * (c - last)
            if self._clock_replay_start is not None and progress - elapsed >= cfg.replay_clock_min_s:
                self.clock_replay_note = ("end", now - self._clock_replay_start)   # the live clock is back
                self._clock_replay_start = None
            elif self._clock_replay_start is None and -cfg.replay_clock_max_s <= progress <= -cfg.replay_clock_min_s:
                self._cs_pending = (c, now, -progress, elapsed)   # wait for the next reading before believing it
                return
        self._cs_last, self._cs_time = c, now
        self._apply_clock_replay(now)

    def _apply_clock_replay(self, now: float) -> None:
        cfg = self.config
        if self._clock_replay_start is None:
            return
        if cfg.replay_by_clock_s > 0 and now - self._clock_replay_start <= cfg.replay_by_clock_s:
            self.replay = True
        elif now - self._clock_replay_start > max(cfg.replay_by_clock_s, 60.0):
            self._clock_replay_start = None   # no live clock seen again for a minute: the replay is long over

    def _cues_in(self, texts: list[str]) -> list[str]:
        """The anticipation cues on screen in this reading, in a stable order."""
        cfg, found = self.config, []
        self._cue_texts = {}
        has_down = any(DOWN_RE.fullmatch(t.strip()) for t in texts)
        phrases = sorted(ANTICIPATION_WORDS, key=len, reverse=True)   # "var check" before "var"
        for text in texts:
            t = text.strip()
            if _banner_hit(t, BIG_PLAY_WORDS, BIG_PLAY_SLACK) and not self._review_banner(t):
                continue   # a big play banner: already counted as one
            if FOURTH_DOWN_RE.fullmatch(t):
                found.append("4th down")
            cue = ""
            if GOAL_DOWN_RE.fullmatch(t):
                cue = "goal to go"
            elif (m := GAIN_RE.fullmatch(t)) and int(m.group(1)) >= cfg.anticipation_big_gain_yd:
                cue = "big gain"
            elif (m := YARDS_RE.fullmatch(t)) and has_down and int(m.group(1)) <= cfg.anticipation_goal_yd:
                cue = "yard line"
            elif ADDED_TIME_RE.fullmatch(t) and self.clock_minute is not None and any(self.clock_minute in r for r in ADDED_TIME_MINUTES):
                cue = "added time"
            if not cue:
                cue = _banner_hit(t, phrases, ANTICIPATION_SLACK) or self._review_banner(t)
            if cue:
                found.append(cue)
                self._cue_texts.setdefault(cue, t)
        return [c for c in dict.fromkeys(found) if not self.sport or self.sport in BANNER_CUE_SPORTS.get(c, {self.sport})]

    def _read_goal_banner(self, now: float, texts: list[str], on_ad: bool) -> None:
        """Soccer: the GOAL banner an operator flashes in the place of the scorebug's team code and digits, for one or two readings, often
        before the score changes ("GOAL", "BUT", "GOL", "GOLO", broken as "G" + "AL", or just "AL" left of the second team code).
        It needs a scorebug in sight (two team codes now, or two seen in the last `CODE_PAIR_MEMORY_S`), a live game, no stats graphic, and
        is not repeated for `GOAL_BANNER_COOLDOWN_S`. Not in other sports, nor in "GOAL TO GO", "FIELD GOAL", "GOALS", "Goal kick"
        (those are other whole items)."""
        self.goal_banner = ""
        items = [t.strip() for t in texts]
        codes = {t for t in items if TEAM_CODE_RE.fullmatch(t) and t not in NOT_TEAM_CODES and not GOAL_WORD_RE.fullmatch(t)}
        previous = self._code_pair
        if len(codes) >= 2:
            self._code_pair = (frozenset(codes), now)
        if self.sport != "football" or on_ad or self.state != "live" or now - self._goal_banner_at < GOAL_BANNER_COOLDOWN_S:
            return
        if any(STAT_GRAPHIC_RE.search(t) for t in items):
            return
        recent_pair = previous is not None and now - previous[1] <= CODE_PAIR_MEMORY_S
        if len(codes) < 2 and not (recent_pair and codes & previous[0]):
            return   # no scorebug in sight: two team codes, or one of the two seen a moment ago
        letters = [re.sub(r"[^A-Za-z]", "", t) for t in items]
        hit = next((t for t in letters if GOAL_WORD_RE.fullmatch(t)), "")
        if not hit:
            for a, b in zip(letters, letters[1:]):
                if 1 <= len(a) <= 3 and 1 <= len(b) <= 3 and len(a + b) >= 3 and GOAL_PAIR_RE.fullmatch(a + b):
                    hit = (a + b).upper()
                    break
        if not hit and recent_pair and len(codes) == 1 and next(iter(codes)) in previous[0] and not any(DIGITS_ONLY_RE.fullmatch(t) for t in items):
            # the banner hides the other team code AND the digits: a lone scorebug digit on screen means the scorebug is intact and "al" is a
            # sponsor word (live log 2026-10-11 20:24:06 BET-OSA, 19:54:34)
            hit = next((t.upper() for t in letters if t.casefold() in GOAL_LONE), "")
        if hit:
            self.goal_banner, self._goal_banner_at = hit, now

    def _read_position(self, texts: list[str]) -> None:
        """American football: the down, "& GOAL" and, when a yard line graphic ("5 YD") shows beside a down text, the distance to the end
        zone. Nothing is read in a stats graphic (a stat line "7 REC, 68 YDS", GAME LEADERS...) or in other sports."""
        self.down, self.goal_to_go, self.end_zone_yards = None, False, None
        if self.sport != "american football":
            return
        items = [t.strip() for t in texts]
        for t in items:
            m = DOWN_NUMBER_RE.fullmatch(t)
            if m:
                self.down, self.goal_to_go = DOWN_OF_SUFFIX.get(m.group(1).casefold()), m.group(2).casefold() == "goal"
                break
        if self.down is None or any(STAT_GRAPHIC_RE.search(t) or STAT_LINE_RE.search(t) for t in items):
            return
        for t in items:
            m = YARDS_RE.fullmatch(t)
            if m and int(m.group(1)) <= END_ZONE_MAX_YD:
                self.end_zone_yards = int(m.group(1))
                break

    def _review_banner(self, text: str) -> str:
        """"var" or "review" when the text is a short review banner naming a subject (see REVIEW_BANNER_RE), else ""."""
        if len(text) > REVIEW_BANNER_MAX_CHARS:
            return ""
        m = REVIEW_BANNER_RE.match(text)
        if m and banner_tier(m.group(2), self.sport):
            return "var" if m.group(1).casefold() == "var" else "review"
        m = REVIEW_BANNER_END_RE.match(text)
        if m and banner_tier(m.group(1), self.sport):
            return "review"
        return ""

    def _note_anticipation(self, now: float, texts: list[str], on_ad: bool) -> None:
        """Measuring only: reports each cue once when it has shown on `ANTICIPATION_CONFIRMATIONS` readings in a row, then not again for
        `anticipation_cooldown_s`. Nothing is reported during an ad, a break, the end of the game or a replay."""
        self.anticipation = []
        self.anticipation_text = {}
        if on_ad or self.state != "live" or self.replay:
            self._cue_hits = {}
            return
        seen = self._cues_in(texts)
        self._cue_hits = {cue: self._cue_hits.get(cue, 0) + 1 for cue in seen}
        for cue in seen:
            if self._cue_hits[cue] >= ANTICIPATION_CONFIRMATIONS and now - self._cue_at.get(cue, float("-inf")) >= self.config.anticipation_cooldown_s:
                self._cue_at[cue] = now
                self.anticipation.append(cue)
                self.anticipation_text[cue] = self._cue_texts.get(cue, cue)

    def _note_hidden_scorebug(self, now: float, on_ad: bool) -> None:
        """Measuring only (nothing reads the result but a log line): DAZN hides the scorebug during replays. Reports ("hidden", s) on the
        reading where a scorebug seen a moment ago has been missing `scorebug_hidden_s`, then ("back", s) when it returns, or
        ("cancelled", s) when an ad / break / the end ended the gap instead. Gaps of ads and breaks are never reported."""
        cfg = self.config
        self.scorebug_note = None
        if self._scorebug_seen == now:   # the scorebug is on screen
            if self._gap_announced:
                self.scorebug_note = ("back", now - self._gap_start)
            self._gap_start, self._gap_announced, self._gap_dead = None, False, False
            return
        if self._scorebug_seen == float("-inf") or self._gap_dead:
            return
        if self._gap_start is None:
            self._gap_start = self._scorebug_seen
        gap = now - self._gap_start
        if on_ad or self.state != "live":
            if self._gap_announced:
                self.scorebug_note = ("cancelled", gap)
            self._gap_announced, self._gap_dead = False, True
        elif not self._gap_announced and cfg.scorebug_hidden_s <= gap <= cfg.scorebug_memory_s:
            self._gap_announced = True
            self.scorebug_note = ("hidden", gap)


def combine(states: list[str]) -> str:
    """State of a whole tab from the states of its video areas: live if any area is live."""
    if not states or "live" in states:
        return "live"
    return "break" if "break" in states else "over"
