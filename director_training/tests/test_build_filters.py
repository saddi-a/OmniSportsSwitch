"""`build --since` and `--exclude`: the data cut-off and the (tab, time range) exclusion list. Synthetic files in tmp_path only."""
import datetime
import json

import pytest

from director_training import cli, data

from .synth import write_jsonl


def ts(day, hhmm, sec=0):
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.datetime.strptime(day, "%Y-%m-%d").replace(hour=h, minute=m, second=sec).timestamp()


def make(tmp_path, day="2026-10-18"):
    spec = [(485662290, "14:15"), (485662290, "14:16"), (485662290, "14:44"), (485662290, "14:45"), (485662428, "14:30"), (485662428, "11:43"), (485662428, "11:44")]
    rows = [{"t": ts(day, hm, 30 if hm == "14:44" else 0), "tab": tab, "sport": "football"} for tab, hm in spec]
    events = [dict(r, kind="cue") for r in rows]
    write_jsonl(tmp_path / "logs" / "dataset" / f"{day}.jsonl", rows)
    write_jsonl(tmp_path / "logs" / "dataset" / f"{day}.events.jsonl", events)
    return tmp_path / "logs"


def hm(items):
    return sorted((i["tab"], datetime.datetime.fromtimestamp(i["t"]).strftime("%H:%M")) for i in items)


def test_no_option_keeps_everything(tmp_path):
    got = data.load_inputs(make(tmp_path))
    assert len(got["rows"]) == 7 and len(got["events"]) == 7


def test_since_drops_rows_and_events_before_the_cut_off(tmp_path):
    got = data.load_inputs(make(tmp_path), since=data.parse_since("2026-10-18 11:44"))
    assert (485662428, "11:43") not in hm(got["rows"]) and len(got["rows"]) == 6 and len(got["events"]) == 6
    assert (485662428, "11:44") in hm(got["rows"])                       # the cut-off minute itself counts


def test_exclude_drops_the_tab_in_the_range_both_ends_included(tmp_path):
    ex = [data.parse_exclude("485662290:14:16-14:44")]
    got = data.load_inputs(make(tmp_path), excludes=ex)
    assert hm(got["rows"]) == sorted([(485662290, "14:15"), (485662290, "14:45"), (485662428, "14:30"), (485662428, "11:43"), (485662428, "11:44")])
    assert hm(got["events"]) == hm(got["rows"])


def test_exclude_is_repeatable_and_only_for_its_own_tab_and_date(tmp_path):
    logs = make(tmp_path)
    other = make(tmp_path, day="2026-10-19")       # the same clock times on another date are not excluded by a range of the first date
    ex = [data.parse_exclude("485662290:14:16-14:44")]
    got = data.load_inputs(logs, ["2026-10-18", "2026-10-19"], excludes=ex)
    # the range has no date: it applies to the date of each dataset file by its own local time, so both days lose the same rows
    assert len(got["rows"]) == 10
    both = data.load_inputs(logs, ["2026-10-18"], excludes=[ex[0], data.parse_exclude("485662428:14:00-15:00")])
    assert hm(both["rows"]) == sorted([(485662290, "14:15"), (485662290, "14:45"), (485662428, "11:43"), (485662428, "11:44")])


def test_since_and_exclude_together(tmp_path):
    got = data.load_inputs(make(tmp_path), since=data.parse_since("2026-10-18 14:00"), excludes=[data.parse_exclude("485662290:14:16-14:44")])
    assert hm(got["rows"]) == sorted([(485662290, "14:15"), (485662290, "14:45"), (485662428, "14:30")])


@pytest.mark.parametrize("bad", ["485662290", "485662290:14:16", "x:14:16-14:44", "1:25:00-26:00", "1:14:61-15:00"])
def test_bad_exclude_is_refused(bad):
    with pytest.raises(ValueError):
        data.parse_exclude(bad)


def test_bad_since_is_refused():
    with pytest.raises(ValueError):
        data.parse_since("yesterday")


def run_cli(tmp_path, *argv):
    lines = []
    s = tmp_path / "s.toml"
    s.write_text("[scoring]\nmin_score = 0.35\n", encoding="utf-8")
    code = cli.main(["--logs-dir", str(tmp_path / "logs"), "--settings", str(s), *argv], out=lines.append)
    return code, "\n".join(lines)


def test_cli_build_with_bad_options_exits_2_and_writes_no_run(tmp_path):
    make(tmp_path)
    for argv in (["build", "--since", "soon"], ["build", "--exclude", "nope"]):
        code, out = run_cli(tmp_path, *argv)
        assert code == 2 and "bad argument" in out
    assert not (tmp_path / "logs" / "director_training").exists()


def test_cli_build_records_the_options_and_reports_no_rows_when_all_cut(tmp_path):
    make(tmp_path)
    code, out = run_cli(tmp_path, "build", "--since", "2026-10-19 00:00", "--exclude", "1:10:00-11:00")
    assert code == 0 and "no dataset rows" in out
    cfg = json.loads((tmp_path / "logs" / "director_training" / "run-0001-build" / "config.json").read_text(encoding="utf-8"))
    assert cfg["params"]["since"] == "2026-10-19 00:00" and cfg["params"]["exclude"] == ["1:10:00-11:00"]
