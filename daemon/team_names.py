"""Team-name table: the other ways broadcasters write a team that ESPN's feed names one way (MAN on ESPN, MUN on a scorebug).

The data lives in plain text files, one per sport, in `daemon/team_tables/` (football.txt = soccer, nfl.txt, cfb.txt = FBS;
a new sport is a new file, the file name is the sport's "group"). Edit them by hand. Sections inside a file:

    [eng.1] [esp.1] ... [uefa] [nfl] [college-football]   clubs; one line per club:
                                                          ESPN abbreviation | ESPN displayName | alias, alias, doubtful?, ...
    [ambiguous <name>]                                    CODE | club; club   (see CANDIDATES below)

The displayName must be exactly what the feed sends as the team name (accents and case do not matter): `feed_map.team_names`
finds the club by it. An alias ending in "?" is DOUBTFUL: kept for the record, never used. The ESPN abbreviation and displayName
are matched anyway, so they are not repeated as aliases. A club of the Champions/Europa League is listed once, under its domestic
league; other UEFA clubs are in [uefa].

CANDIDATES: a code that can mean several clubs (REAL, FCB, MSU, OSU, GEORGIA, MIAMI, MUN...) gives NO club an alias. Instead it is
a candidate token for every club it may mean: the clubs named after the "|" (matched by the start of their displayName) plus every
club of the same sport whose name contains the code as whole words. 2-letter codes (OM, OL, ND, KC, GB, NE...) are candidates too:
a club's 2-letter ESPN abbreviation or 2-letter alias makes it a candidate for that code, and these are the ONLY tokens under 3
characters the mapper accepts (whole word, upper case in the screen text). A candidate never decides alone: `feed_map` needs both
teams of a game, and one token can never stand for both of them.

Rules kept by the tests: an alias never belongs to two clubs of the same sport; an alias that is a whole-word part of another
club's name in the same sport is a candidate, not an alias. The hand-written `feed_map.ALIASES` stay in force on top of this table
(they win and are never reduced); this table only fills gaps.

Source: ESPN's public `/teams` lists (eng.1, esp.1, ita.1, ger.1, fra.1, uefa.champions, uefa.europa, nfl, and the 138 FBS teams of
college-football) read on 2026-10-13; the aliases are curated by hand. Clubs of other seasons' Champions/Europa League are
missing until they show up in the feed (an unknown club is only "unmapped", which is safe).
"""
import dataclasses
import pathlib
import re
import unicodedata

TABLE_DIR = pathlib.Path(__file__).with_name("team_tables")


def fold(text: str) -> str:
    """Upper case, no accents (same as feed_map.fold)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))


def _tokens(text: str) -> list:
    return re.findall(r"[A-Z0-9&]+", fold(text))


@dataclasses.dataclass(frozen=True)
class Club:
    group: str                 # the sport = the data file's name (football, nfl, cfb)
    section: str
    abbr: str                  # ESPN's abbreviation
    name: str                  # ESPN's displayName
    aliases: tuple             # folded, active
    doubtful: tuple            # folded, never used


def parse(text: str, group: str):
    """-> (clubs, ambiguous) with ambiguous = {folded code: note}."""
    clubs: list[Club] = []
    ambiguous: dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        parts = [p.strip() for p in line.split("|")]
        if not section:
            raise ValueError(f"team_names: line before any section in {group}: {raw!r}")
        if section.startswith("ambiguous"):
            ambiguous[fold(parts[0])] = parts[1] if len(parts) > 1 else ""
            continue
        if len(parts) < 2:
            raise ValueError(f"team_names: bad line {raw!r} in [{section}] of {group}")
        words = [w.strip() for w in (parts[2] if len(parts) > 2 else "").split(",") if w.strip()]
        active = tuple(dict.fromkeys(fold(w) for w in words if not w.endswith("?")))
        doubtful = tuple(dict.fromkeys(fold(w[:-1].strip()) for w in words if w.endswith("?")))
        clubs.append(Club(group, section, parts[0], parts[1], active, doubtful))
    return clubs, ambiguous


def load(folder: pathlib.Path = TABLE_DIR):
    clubs: list[Club] = []
    ambiguous: dict[str, dict[str, str]] = {}
    for path in sorted(folder.glob("*.txt")):
        got, amb = parse(path.read_text(encoding="utf-8"), path.stem)
        clubs += got
        ambiguous[path.stem] = amb
    return tuple(clubs), ambiguous


CLUBS, AMBIGUOUS = load()


def _build_aliases() -> dict:
    out: dict[str, tuple] = {}
    for club in CLUBS:
        key = fold(club.name)
        merged = dict.fromkeys(out.get(key, ()) + club.aliases)
        out[key] = tuple(a for a in merged if a not in AMBIGUOUS.get(club.group, {}))
    return out


def _build_candidates() -> tuple:
    """-> ({group: {code: set of club names}}, {folded club name: tuple of codes})"""
    by_group: dict[str, dict[str, set]] = {}
    for group, codes in AMBIGUOUS.items():
        members = [c for c in CLUBS if c.group == group]
        found = by_group.setdefault(group, {})
        for code, note in codes.items():
            names = found.setdefault(code, set())
            ctok = _tokens(code)
            for entry in (e for e in note.split(";") if e.strip()):
                etok = _tokens(entry)
                names.update(c.name for c in members if etok and _tokens(c.name)[:len(etok)] == etok)
            for c in members:
                ntok = _tokens(c.name)
                if ctok and any(ntok[i:i + len(ctok)] == ctok for i in range(len(ntok) - len(ctok) + 1)):
                    names.add(c.name)
    for c in CLUBS:                                    # 2-letter ESPN codes and aliases
        for code in (fold(c.abbr),) + c.aliases:
            if len(code) == 2:
                by_group.setdefault(c.group, {}).setdefault(code, set()).add(c.name)
    by_name: dict[str, set] = {}
    for found in by_group.values():
        for code, names in found.items():
            for n in names:
                by_name.setdefault(fold(n), set()).add(code)
    return by_group, {k: tuple(sorted(v)) for k, v in by_name.items()}


ALIASES_BY_NAME = _build_aliases()        # folded displayName -> folded active aliases (the table's own layer for feed_map)
CANDIDATES, CANDIDATES_BY_NAME = _build_candidates()


def aliases_for(team_name: str) -> tuple:
    """Active aliases of the club the feed calls `team_name` (empty if the club is not in the table)."""
    return ALIASES_BY_NAME.get(fold(team_name), ())


def candidates_for(team_name: str) -> tuple:
    """Candidate tokens (ambiguous and 2-letter codes) this club may be written as; they never decide a game alone."""
    return CANDIDATES_BY_NAME.get(fold(team_name), ())
