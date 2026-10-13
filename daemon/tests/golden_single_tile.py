"""A recorded, synthetic single-tile sequence (four one-video tabs: soccer with a goal and a VAR banner, American football with a replay
and a red-zone cue, an ad, a quiet MAIN) driven through the Server exactly as the loops do. `run()` returns the scores and the swaps; the
file `fixtures/golden_single_tile.json` holds what the code produced BEFORE the per-tile work (Stage 2 of docs/per-tile-sport.md), so the
test fails if a single-tile tab ever scores or swaps differently. Regenerate only on purpose: `python -m daemon.tests.golden_single_tile`."""
import json
import pathlib
import tempfile

from daemon.scoring import Config, Scorer
from daemon.server import Server
import daemon.server as server_module

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "golden_single_tile.json"
KEYS = ("score", "surge", "crowd", "speech", "music", "shout", "event", "cue", "anticipation", "lev", "status", "ad", "level")


def _board(t):
    soccer = ["COMO", "1" if t >= 100 else "0", "ROMA", "0"]
    if 150 <= t < 160:
        soccer.append("VAR CHECK")
    nfl = ["HOUSTON", "7" if t < 170 else "14", "K-STATE", "3", "1ST & GOAL"]
    if 120 <= t < 135:
        nfl.append("REPLAY")
    ad = ["COMMERCIAL BREAK IN PROGRESS"] if 60 <= t < 90 else ["COMO", "0", "ROMA", "0"]
    clock = f"{20 + (t // 60):02d}:{t % 60:02d}"
    return {(2, 0): {"texts": soccer, "clocks": [clock]}, (3, 0): {"texts": nfl, "clocks": ["10:45"]}, (4, 0): {"texts": ad, "clocks": [clock]}}


def run() -> list:
    saved = server_module.LOG_DIR
    server_module.LOG_DIR = pathlib.Path(tempfile.mkdtemp()) / "logs"
    server_module.LOG_DIR.mkdir()
    try:
        server = Server(Scorer(Config()))
        for tab, sport in ((1, "Football"), (2, "Football"), (3, "American football"), (4, None)):
            server.handle_message({"type": "register", "tab_id": tab, "is_main": tab == 1, "sport": sport})
        out = []
        for second in range(0, 240):
            now = 1000.0 + second
            for tab in (1, 2, 3, 4):
                loud = 0.05 + (0.12 if (tab == 2 and 100 <= second < 108) or (tab == 3 and 170 <= second < 176) else 0.0)
                server.scorer.add_audio(tab, loud, now)
                crowd = 0.8 if loud > 0.1 else 0.05
                music = 0.9 if (tab == 4 and 60 <= second < 90) else 0.02
                server.scorer.set_tags(tab, crowd, 0.2, music, now)
            if second % 3 == 0:
                server._update_game_states(_board(second), now)
            if second == 140:
                server.scorer.set_anticipation(2, now, 0.25)
                server.buildup.note(2, now, 0.25, "spoken:chance")
            if second == 141:
                server.scorer.set_anticipation(2, now, 0.5, strong=True)
                server.buildup.note(2, now, 0.5, "spoken:penalty awarded")
            if second == 200:
                server.scorer.note_replay(2, now)
            taken = server._log_scores(now) if second % 2 == 0 else None
            swap = taken or server.scorer.decide(now)
            if second % 4 == 0 or swap:
                row = {"s": second, "main": server.scorer.main}
                for tab in (1, 2, 3, 4):
                    c = server.scorer.components(tab, now)
                    row[str(tab)] = None if c is None else {k: (round(c[k], 4) if isinstance(c[k], float) else c[k]) for k in KEYS}
                    row["hold" + str(tab)] = round(server.scorer.goal_hold_left(tab, now), 2)
                if swap:
                    row["swap"] = [swap.promote, swap.demote, swap.reason]
                out.append(row)
        return out
    finally:
        server_module.LOG_DIR = saved


if __name__ == "__main__":
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(run(), indent=0, sort_keys=True), encoding="utf-8")
    print("written", FIXTURE)
