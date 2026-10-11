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

from daemon.scoring import Config

# A caption only counts when a whole OCR text item is exactly the keyword. Words inside a sentence do not
# (an ad saying "ready for the half time?" must not mark the game as on a break).
OVER_RE = re.compile(r"(FINA[LU1I](?:\s?[/|IlU1]?\s?\d?OT\d?)?|SCORE FINAL|FULL[\s-]?TIME|FT|FIN DU MATCH|MATCH TERMIN[EÉ]|TERMIN[EÉ]|COUP DE SIFFLET FINAL"
                     r"|FINAL DEL PARTIDO|FIN DEL PARTIDO|PARTIDO FINALIZADO|FINALIZADO|TERMINADO|FIM DE JOGO|FIM DO JOGO|FINAL DO JOGO|JOGO ENCERRADO)",
                     re.IGNORECASE)   # also "FINAL/OT", "FINAL/2OT" as OCR mangles them (FINAUOT, FINALIOT)
# Overtime (American football, basketball) has no game clock on the scoreboard, so a missing clock is not a break there.
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
              "ad break", "advertisement", "right back", "volvemos", "de retour dans", "watch now", "ondazn"]
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
SCOREBUG_MEMORY_S = 6.0   # a clock / down / quarter on screen proves a live scorebug this long: a replay in a live game
# Anticipation cues (measuring only): banners and graphics that come just before a likely score. A phrase counts when a text item is
# (nearly) just that phrase, like the big-play banners; the cue is the phrase. Red card / penalty kick are big plays and not repeated here.
ANTICIPATION_WORDS = ["red zone", "goal to go", "two minute warning", "two-minute warning", "field goal", "field goal attempt", "penalty", "flag", "flag on the play", "review", "under review", "replay review", "challenge", "big play", "var", "var check", "free kick", "corner", "corner kick", "penalty shootout", "avertissement de deux minutes", "tir de placement", "pénalité", "pénalty", "drapeau", "révision", "contestation", "assistance vidéo", "coup franc", "tir au but", "tirs au but", "penalti", "tiro libre", "saque de esquina", "escanteio", "tiro livre", "pênalti",
                      "free throw", "free throws", "technical foul", "flagrant foul", "flagrant", "buzzer beater", "timeout", "lancer franc", "lancers francs", "faute technique", "faute flagrante", "temps mort", "tiros libres", "falta técnica"]
ANTICIPATION_SLACK = 4         # extra characters allowed around the phrase
ANTICIPATION_CONFIRMATIONS = 2   # a cue must show on this many consecutive readings
FOURTH_DOWN_RE = re.compile(r"(4th|ath)\s*&\s*(\d{1,2}|goal|inches)", re.IGNORECASE)   # "Ath" is how OCR reads "4th"
GOAL_DOWN_RE = re.compile(r"[0-9A-Za-z]{1,2}(st|nd|rd|th|sr|r0)\s*&\s*goal", re.IGNORECASE)
YARDS_RE = re.compile(r"(\d{1,2})\s?yds?", re.IGNORECASE)
GAIN_RE = re.compile(r"\+\s?(\d{1,3})\s?yds?", re.IGNORECASE)
ADDED_TIME_RE = re.compile(r"\+\s?\d{1,2}")
ADDED_TIME_MINUTES = (range(45, 51), range(90, 101))   # a "+3" beside a soccer clock reading these minutes is added time
AD_CONFIRMATIONS = 2   # the phrase must show on this many consecutive readings
AD_MEMORY_S = 8.0      # a break placeholder counts this long after it was last seen


@dataclass(frozen=True)
class SportProfile:
    freeze_s: float    # clock unchanged for this long -> break
    no_clock_s: float  # no readable clock for this long -> break


PROFILES = {
    "football": SportProfile(freeze_s=20, no_clock_s=45),             # soccer: the clock only stops at half time / full time
    "american football": SportProfile(freeze_s=240, no_clock_s=90),   # clock stops after most plays and for TV timeouts
    "basketball": SportProfile(freeze_s=180, no_clock_s=60),          # clock stops on every foul and timeout
    "generic": SportProfile(freeze_s=120, no_clock_s=60),             # any other or unknown sport
}


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


class GameTracker:
    """Follows one video area (a whole stream, or one multiview tile) over time."""

    def __init__(self, sport: str | None = None, config: Config | None = None):
        self.profile = profile_for(sport)
        self.config = config or Config()   # shared with the scorer, so popup edits apply at once
        self.state = "live"          # optimistic until proven otherwise
        self._clock: str | None = None
        self._clock_changed_at: float | None = None
        self._last_clock_seen: float | None = None
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
        self.anticipation: list[str] = []   # cues reported on this reading (measuring only: nothing but a log line reads them)
        self._cue_hits: dict[str, int] = {}
        self._cue_at: dict[str, float] = {}
        self.big_play = ""   # set on the one reading where a new big-play banner appears (else "")
        self._big_play_at = float("-inf")

    def set_sport(self, sport: str | None) -> None:
        self.profile = profile_for(sport)

    def update(self, now: float, texts: list[str], clocks: list[str]) -> str:
        if self._first_seen is None:
            self._first_seen = now

        items = [re.sub(r"[^\w\s-]", "", t).strip() for t in texts]
        if any(DOWN_RE.fullmatch(t.strip()) or QUARTER_RE.fullmatch(t.strip()) for t in texts):
            self._in_play_seen = now   # down and distance or the quarter on the scorebug: this game is being played
        # "Final" next to a live scorebug is a results ticker about other games, not the end of this one.
        ticker = now - self._in_play_seen <= IN_PLAY_MEMORY_S
        self._over_hits = self._over_hits + 1 if any(OVER_RE.fullmatch(t) for t in items) and not ticker else 0
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
        lone_clock = bool(clocks) and bool(texts) and not has_scoreboard_text(texts, clocks)
        self._ad_hits = self._ad_hits + 1 if lone_clock or any(p in t for t in lowered for p in AD_PHRASES) else 0
        if self._ad_hits >= AD_CONFIRMATIONS:
            self._ad_seen = now
        on_ad = now - self._ad_seen <= AD_MEMORY_S
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
                hit = _banner_hit(text, BIG_PLAY_WORDS, BIG_PLAY_SLACK)
                if hit:
                    self.big_play, self._big_play_at = hit, now
                    break

        if clocks and not on_ad:  # the countdown shown during an ad is not the game clock
            shown = re.fullmatch(r"(\d{1,3}):\d{2}", clocks[0].strip())
            if shown:
                self.clock_minute = int(shown.group(1))
            if clocks[0] != self._clock:
                self._clock, self._clock_changed_at = clocks[0], now
            self._last_clock_seen = now

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
            self.state = "break" if frozen_for > self.profile.freeze_s else "live"
        elif now - self._overtime_seen <= OVERTIME_MEMORY_S:
            self.state = "live"   # overtime: no game clock to read
        elif now - self._down_seen <= DOWN_MEMORY_S:
            self.state = "live"   # down and distance on the scorebug: the game is being played, whatever the clock OCR says
        elif self._last_clock_seen is not None and now - self._last_clock_seen <= self.profile.no_clock_s:
            pass  # clock briefly unreadable (overlay, OCR miss): keep the previous state
        else:
            since = self._last_clock_seen if self._last_clock_seen is not None else self._first_seen
            self.state = "break" if now - since > self.profile.no_clock_s else "live"
        self._note_hidden_scorebug(now, on_ad)
        self._note_anticipation(now, texts, on_ad)
        return self.state

    def _cues_in(self, texts: list[str]) -> list[str]:
        """The anticipation cues on screen in this reading, in a stable order."""
        cfg, found = self.config, []
        has_down = any(DOWN_RE.fullmatch(t.strip()) for t in texts)
        phrases = sorted(ANTICIPATION_WORDS, key=len, reverse=True)   # "var check" before "var"
        for text in texts:
            t = text.strip()
            if _banner_hit(t, BIG_PLAY_WORDS, BIG_PLAY_SLACK):
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
                cue = _banner_hit(t, phrases, ANTICIPATION_SLACK)
            if cue:
                found.append(cue)
        return list(dict.fromkeys(found))

    def _note_anticipation(self, now: float, texts: list[str], on_ad: bool) -> None:
        """Measuring only: reports each cue once when it has shown on `ANTICIPATION_CONFIRMATIONS` readings in a row, then not again for
        `anticipation_cooldown_s`. Nothing is reported during an ad, a break, the end of the game or a replay."""
        self.anticipation = []
        if on_ad or self.state != "live" or self.replay:
            self._cue_hits = {}
            return
        seen = self._cues_in(texts)
        self._cue_hits = {cue: self._cue_hits.get(cue, 0) + 1 for cue in seen}
        for cue in seen:
            if self._cue_hits[cue] >= ANTICIPATION_CONFIRMATIONS and now - self._cue_at.get(cue, float("-inf")) >= self.config.anticipation_cooldown_s:
                self._cue_at[cue] = now
                self.anticipation.append(cue)

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
