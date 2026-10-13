import json
import time

import pytest

from daemon import swap_review as sr
from scripts import review_swaps

DAY = "2026-10-13"


def _t(clock_str):
    return time.mktime(time.strptime(f"{DAY} {clock_str}", "%Y-%m-%d %H:%M:%S"))


def _log(logs, lines):
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "events.log").write_text("\n".join(f"{DAY} {line}" for line in lines) + "\n", encoding="utf-8")


def _swap_line(clock, promote, demote, reason="build-up: level 0.40 (cues 0.23@4s) vs main 0.09"):
    return f"{clock},000 swap: promote tab {promote}, demote tab {demote} | {reason}"


def _dataset(logs, rows):
    (logs / "dataset").mkdir(parents=True, exist_ok=True)
    (logs / "dataset" / f"{DAY}.events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_swaps_from_events_log_and_user_swap_excluded(tmp_path):
    _log(tmp_path, ["21:00:00,000 feed map tab 1 tile 0 -> uefa.champions AAA-BBB (via text)",
                    _swap_line("21:10:00", 1, 2),
                    _swap_line("21:20:00", 2, 1, "MAIN forced by the user")])
    swaps = sr.load_day(tmp_path, DAY)
    assert [s["id"] for s in swaps] == ["21:10:00", "21:20:00"]
    assert swaps[0]["games"][0] == "AAA-BBB" and not swaps[0]["user"]
    assert swaps[1]["user"] and swaps[1]["flags"] == []
    assert not sr.needs_user(swaps[1])
    assert swaps[0]["level"] == "0.40 vs 0.09"


def test_dataset_source_preferred_and_context_window(tmp_path):
    t = _t("21:10:00")
    _log(tmp_path, [_swap_line("21:10:00", 9, 9)])      # must be ignored: the dataset file exists
    _dataset(tmp_path, [
        {"t": t, "kind": "swap", "promote": 1, "demote": 2, "reason": "x", "source": "buildup"},
        {"t": t - 20, "kind": "speech_cue", "tab": 2, "cue": "chance", "text": "big chance"},
        {"t": t - 40, "kind": "speech_cue", "tab": 2, "cue": "foul", "text": "too early"},
        {"t": t + 10, "kind": "score_change", "tab": 1, "home": 1, "away": 0, "verdict": "ocr-only"},
        {"t": t + 10, "kind": "speech_cue", "tab": 3, "cue": "chance", "text": "other tab"},
        {"t": t + 70, "kind": "speech_cue", "tab": 1, "cue": "chance", "text": "too late"}])
    (s,) = sr.load_day(tmp_path, DAY)
    assert (s["promote"], s["demote"]) == (1, 2)
    assert [e["kind"] for e in s["context"]] == ["speech_cue", "score_change"]
    assert "key-event" in s["flags"] and "quiet-tie" not in s["flags"]


def test_flags_flipflop_and_quiet_tie(tmp_path):
    _log(tmp_path, [_swap_line("21:10:00", 1, 2), _swap_line("21:10:20", 2, 1), _swap_line("21:20:00", 3, 2)])
    a, b, c = sr.load_day(tmp_path, DAY)
    assert "flip-flop" in a["flags"] and "flip-flop" in b["flags"] and "flip-flop" not in c["flags"]
    assert "quiet-tie" in c["flags"] and sr.needs_user(c)
    # a flip back after more than 30 s is not a flip-flop
    _log(tmp_path, [_swap_line("21:10:00", 1, 2), _swap_line("21:11:00", 2, 1)])
    assert all("flip-flop" not in s["flags"] for s in sr.load_day(tmp_path, DAY))


def test_penalty_and_red_card_flag_but_yellow_does_not(tmp_path):
    _log(tmp_path, [_swap_line("21:10:00", 1, 2), "21:10:05,000 speech cue penalty tab 1: il va chercher le penalty",
                    _swap_line("21:30:00", 1, 2), "21:30:05,000 speech cue card tab 1: carton jaune",
                    _swap_line("21:40:00", 1, 2), "21:40:05,000 speech cue card tab 2: carton rouge"])
    a, b, c = sr.load_day(tmp_path, DAY)
    assert "key-event" in a["flags"] and "key-event" not in b["flags"] and "key-event" in c["flags"]


def test_rewind_score_change_is_not_a_key_event():
    assert not sr._is_key_event({"kind": "score_change", "verdict": "rewind"})
    assert sr._is_key_event({"kind": "goal_banner"})


def test_feedback_parser_is_tolerant(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("# header\n\n21:28:43 | ATM-MUN 485660781 | late (+ more) | \"too late | with bar\"\n  evidence: first\n  more\n"
                 "garbage line\n21:35:00 (said; x) | GAL | late | words\n12:3 | bad\n", encoding="utf-8")
    items = sr.parse_feedback(p)
    assert [i["clock"] for i in items] == ["21:28:43", "21:35:00"]
    assert items[0]["verdict"].startswith("late") and "with bar" in items[0]["words"]
    assert items[0]["evidence"] == "evidence: first more"
    assert sr.parse_feedback(tmp_path / "missing.md") == []


def test_remarks_attached_within_60_s(tmp_path):
    _log(tmp_path, [_swap_line("21:28:43", 1, 2)])
    (tmp_path / "feedback").mkdir()
    (tmp_path / "feedback" / f"{DAY}.md").write_text("21:29:30 | g | late | near\n21:31:00 | g | late | far\n", encoding="utf-8")
    (s,) = sr.load_day(tmp_path, DAY)
    assert [r["words"] for r in s["remarks"]] == ["near"]


def test_marks_append_only_last_wins(tmp_path):
    sr.add_mark(tmp_path, DAY, "21:10:00", "good", t=1.0)
    sr.add_mark(tmp_path, DAY, "21:10:00", "r", "changed my mind")
    sr.add_mark(tmp_path, DAY, "evt-21:35:17-5", "missed", "hot build-up", tab=5, kind="event")
    lines = sr.marks_path(tmp_path, DAY).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["verdict"] == "good"
    marks = sr.read_marks(tmp_path, DAY)
    assert marks["21:10:00"]["verdict"] == "regret" and marks["21:10:00"]["note"] == "changed my mind"
    assert marks["evt-21:35:17-5"]["verdict"] == "missed-chance" and marks["evt-21:35:17-5"]["tab"] == 5
    with pytest.raises(ValueError):
        sr.add_mark(tmp_path, DAY, "x", "nonsense")


def test_find_swap_nearest(tmp_path):
    _log(tmp_path, [_swap_line("21:28:43", 1, 2)])
    swaps = sr.load_day(tmp_path, DAY)
    assert sr.find_swap(swaps, "21:28:45")["id"] == "21:28:43"
    assert sr.find_swap(swaps, "21:30:00") is None and sr.find_swap(swaps, "junk") is None


def test_script_writes_only_under_review(tmp_path, capsys):
    _log(tmp_path, [_swap_line("21:10:00", 1, 2)])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    base = [DAY, "--logs", str(tmp_path)]
    assert review_swaps.main(base + ["--list"]) == 0
    assert review_swaps.main(base + ["--mark", "21:10:01", "late", "slow", "one"]) == 0
    assert review_swaps.main(base + ["--mark", "21:10:00", "bogus"]) == 2
    assert review_swaps.main(base + ["--mark", "22:00:00", "good"]) == 1
    assert review_swaps.main(base + ["--markdown"]) == 0
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    new = set(after) - set(before)
    assert {p.name for p in new} == {f"{DAY}.jsonl", f"{DAY}.md"} and all(p.parent.name == "review" for p in new)
    assert all(after[p] == before[p] for p in before)
    md = (tmp_path / "review" / f"{DAY}.md").read_text(encoding="utf-8")
    assert "0 entries to review" in md       # a plain quiet tie is not on the default list
    assert review_swaps.main(base + ["--all"]) == 0 and "21:10:00" in capsys.readouterr().out
    assert sr.read_marks(tmp_path, DAY)["21:10:00"]["note"] == "slow one"


def _cued(clocks_tabs):
    lines = []
    for c, tab in clocks_tabs:
        lines.append(f"{c[:-3]}:{int(c[-2:]) + 1:02d},000 speech cue chance tab {tab}: big chance")
    return lines


def test_interactive_marks_and_skips(tmp_path, monkeypatch):
    _log(tmp_path, [_swap_line("21:10:00", 1, 2), *_cued([("21:10:00", 1)]), _swap_line("21:20:00", 2, 1), *_cued([("21:20:00", 2)]),
                    _swap_line("21:30:00", 1, 2), *_cued([("21:30:00", 1)])])
    answers = iter(["g", "", "s", "x", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert review_swaps.main([DAY, "--logs", str(tmp_path), "--interactive"]) == 0
    marks = sr.read_marks(tmp_path, DAY)
    assert list(marks) == ["21:10:00"] and marks["21:10:00"]["verdict"] == "good"


def test_default_list_rules(tmp_path):
    _log(tmp_path, [_swap_line("21:10:00", 1, 2),                                                    # quiet: out
                    _swap_line("21:20:00", 1, 2, "MAIN idle: challenger x"), *_cued([("21:20:05", 1)]),   # idle: out even with a cue
                    _swap_line("21:30:00", 1, 2), *_cued([("21:30:10", 2)]),                         # cue within 30 s: in
                    _swap_line("21:40:00", 1, 2),                                                    # remark: in
                    _swap_line("21:50:00", 1, 2), _swap_line("21:50:20", 2, 1), _swap_line("21:50:40", 1, 2),   # chain of 3
                    _swap_line("21:50:55", 3, 1),                                                    # other tabs: new entry? quiet: out
                    ])
    (tmp_path / "feedback").mkdir()
    (tmp_path / "feedback" / f"{DAY}.md").write_text("21:40:30 | g | late | slow\n", encoding="utf-8")
    swaps = sr.load_day(tmp_path, DAY)
    entries = sr.build_entries(swaps)
    assert [e["id"] for e in entries] == ["21:30:00", "21:40:00", "21:50:00"]
    chain = entries[2]
    assert chain["chain"] and chain["members"] == ["21:50:00", "21:50:20", "21:50:40"] and chain["final"] == 1
    assert "your remark" in entries[1]["why"] and len(sr.build_entries(swaps, include_all=True)) == 6


def test_other_remarks_do_not_put_a_swap_on_the_list(tmp_path):
    _log(tmp_path, [_swap_line("21:10:00", 1, 2), _swap_line("21:20:00", 1, 2), _swap_line("21:30:00", 1, 2)])
    (tmp_path / "feedback").mkdir()
    (tmp_path / "feedback" / f"{DAY}.md").write_text("21:10:10 | g | other (free kick seen) | note\n21:20:10 | g | late / missed-chance | x\n"
                                                      "21:30:10 | g | Missed-chance (build-up) | y\n", encoding="utf-8")
    swaps = sr.load_day(tmp_path, DAY)
    assert [e["id"] for e in sr.build_entries(swaps)] == ["21:20:00", "21:30:00"]
    assert len(sr.build_entries(swaps, include_all=True)) == 3 and swaps[0]["remarks"]


def test_chain_mark_applies_to_all_members(tmp_path):
    _log(tmp_path, [_swap_line("21:50:00", 1, 2), _swap_line("21:50:20", 2, 1), _swap_line("21:50:40", 1, 2)])
    base = [DAY, "--logs", str(tmp_path)]
    assert review_swaps.main(base + ["--mark", "21:50:21", "regret", "chain"]) == 0
    lines = sr.marks_path(tmp_path, DAY).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["id"] == "21:50:00"
    assert json.loads(lines[0])["members"] == ["21:50:00", "21:50:20", "21:50:40"]
    marks = sr.read_marks(tmp_path, DAY)
    assert {marks[k]["verdict"] for k in ("21:50:00", "21:50:20", "21:50:40")} == {"regret"}
    text = sr.entry_detail(sr.build_entries(sr.load_day(tmp_path, DAY), include_all=True)[0], marks["21:50:00"])
    assert "CHAIN of 3" in text and "21:50:40" in text
