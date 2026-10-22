"""The shadow spoken-cue veto: tiny fake models only (no real data, no network, no downloads); it must never change a cue."""
import asyncio
import importlib.util
import logging
import pathlib

import numpy as np
import pytest

from daemon import settings as settings_module
from daemon import speech_veto
from daemon.scoring import Config, Scorer
from daemon.server import Server
from daemon.settings import load_settings
from daemon.speech_veto import CModel, GModel, SpeechVeto

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "train_speech_veto.py"


class Fixed:
    def __init__(self, p):
        self.p, self.calls = p, []

    def predict(self, text, cues):
        self.calls.append((text, list(cues)))
        return self.p


def load_script():
    spec = importlib.util.spec_from_file_location("train_speech_veto", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_score_is_the_mean_and_the_threshold_decides_the_veto():
    veto = SpeechVeto(threshold=0.39, c_model=Fixed(0.2), g_model=Fixed(0.4))
    r = veto.score("un exemple", ["goal"])
    assert r["p_c"] == 0.2 and r["p_g"] == 0.4 and r["p_mean"] == pytest.approx(0.3) and r["veto"] is True
    assert SpeechVeto(c_model=Fixed(0.4), g_model=Fixed(0.4)).score("x", ["goal"])["veto"] is False
    assert SpeechVeto(c_model=Fixed(0.39), g_model=Fixed(0.39)).score("x", ["goal"])["veto"] is False   # exactly at the threshold: kept
    assert veto.score("x", ["goal"], threshold=0.25)["veto"] is False                                 # an explicit threshold wins


def test_the_models_see_only_the_known_cue_names():
    c = Fixed(0.9)
    SpeechVeto(c_model=c, g_model=Fixed(0.9)).score("text", ["goal", "touchdown"])
    assert c.calls == [("text", ["goal", "touchdown"])]       # the wrapper passes through; the features filter:
    assert speech_veto.c_text("A  B", ["post area", "touchdown"]) == "a b CUE_post_area"
    assert speech_veto.g_text("A  B", ["post area", "touchdown"]) == "post area | A  B"


def test_missing_weights_disable_it_with_one_warning(tmp_path, caplog):
    veto = SpeechVeto(directory=tmp_path / "nothing")
    with caplog.at_level(logging.WARNING, logger="omni.speech_veto"):
        assert veto.score("x", ["goal"]) is None
        assert veto.score("x", ["goal"]) is None
    assert veto.disabled
    assert len([r for r in caplog.records if "switched off" in r.getMessage()]) == 1


def test_a_failing_model_never_raises_and_switches_it_off(caplog):
    class Broken:
        def predict(self, text, cues):
            raise RuntimeError("boom")

    veto = SpeechVeto(c_model=Broken(), g_model=Fixed(0.5))
    with caplog.at_level(logging.WARNING, logger="omni.speech_veto"):
        assert veto.score("x", ["goal"]) is None
        assert veto.score("x", ["goal"]) is None
    assert veto.disabled and len([r for r in caplog.records if "switched off" in r.getMessage()]) == 1


def tiny_rows():
    now = ["quelle occasion pour lui", "il frappe, quel arret", "c'est le but, il marque", "il a touché le poteau", "penalty pour eux",
           "belle frappe, il repousse"] * 4
    not_now = ["il avait marqué sur penalty tout à l'heure", "pas de penalty, rien du tout", "sans occasion pour l'instant", "au match aller le but",
               "ce qui amène la faute de tout à l'heure", "on revoit le corner de la première mi-temps"] * 4
    rows = [{"text": t, "raw": ["chance"], "y": True} for t in now] + [{"text": t, "raw": ["goal"], "y": False} for t in not_now]
    return rows


def tiny_g(tmp_path):
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import DistilBertConfig, DistilBertForSequenceClassification, PreTrainedTokenizerFast

    words = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "|", "goal", "chance", "but", "penalty"]
    tk = Tokenizer(models.WordLevel({w: i for i, w in enumerate(words)}, unk_token="[UNK]"))
    tk.pre_tokenizer = pre_tokenizers.Whitespace()
    tok = PreTrainedTokenizerFast(tokenizer_object=tk, unk_token="[UNK]", pad_token="[PAD]", cls_token="[CLS]", sep_token="[SEP]")
    model = DistilBertForSequenceClassification(DistilBertConfig(vocab_size=len(words), dim=16, n_layers=1, n_heads=2, hidden_dim=32,
                                                                 max_position_embeddings=64, num_labels=2))
    model.save_pretrained(tmp_path / speech_veto.G_DIR)
    tok.save_pretrained(tmp_path / speech_veto.G_DIR)


def test_real_classes_round_trip_through_the_weights_folder(tmp_path):
    script = load_script()
    rows = tiny_rows()
    script.fit_c(rows).save(tmp_path / speech_veto.C_FILE)
    tiny_g(tmp_path)
    veto = SpeechVeto(directory=tmp_path)
    r = veto.score("quelle occasion pour lui", ["chance"])
    assert r is not None and not veto.disabled
    assert 0 <= r["p_c"] <= 1 and 0 <= r["p_g"] <= 1 and r["p_mean"] == pytest.approx((r["p_c"] + r["p_g"]) / 2)
    assert r["p_c"] > veto.score("pas de penalty, rien du tout", ["goal"])["p_c"]   # the toy C learned something
    assert isinstance(veto.c, CModel) and isinstance(veto.g, GModel)


def test_the_training_script_selects_the_experiments_rows(tmp_path):
    import json

    script = load_script()
    rows = [{"ts": "2026-10-11 10:00:00", "label": "now", "text": "a", "cues_regex_raw": ["goal", "touchdown"]},
            {"ts": "2026-10-12 10:00:00", "label": "not_now", "text": "b", "cues_regex_raw": ["foul"]},
            {"ts": "2026-10-12 10:00:00", "label": "unclear", "text": "c", "cues_regex_raw": ["foul"]},
            {"ts": "2026-10-12 10:00:00", "label": "now", "text": "d", "cues_regex_raw": ["goal"], "context": "american_football"},
            {"ts": "2026-10-12 10:00:00", "label": "now", "text": "e", "cues_regex_raw": ["goal"], "reason": "nfl td"}]
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    used = script.load_rows(path)
    assert [r["text"] for r in used] == ["a", "b"] and used[0]["raw"] == ["goal"] and used[0]["y"] and used[1]["day"] == "2026-10-12"
    assert script.precision_recall([0.9, 0.2, 0.5], [True, True, False], 0.39) == (0.5, 0.5, 2)


def test_the_settings_round_trip(tmp_path):
    assert Config().speech_veto_shadow == 1 and Config().speech_veto_threshold == 0.39
    settings = load_settings(tmp_path / "settings.toml")
    ids = [item["id"] for group in settings_module.describe(settings) for item in group["items"]]
    assert "scoring.speech_veto_shadow" in ids and "scoring.speech_veto_threshold" in ids
    assert settings_module.apply_changes(settings, {"scoring.speech_veto_shadow": 0, "scoring.speech_veto_threshold": 0.5}) == {}
    again = load_settings(tmp_path / "settings.toml")
    assert again.scoring.speech_veto_shadow == 0 and again.scoring.speech_veto_threshold == 0.5


class Listener:
    device = "fake"

    def transcribe(self, samples):
        return "Penalty for Roma, what a corner", "en"


class Spy(Scorer):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = []

    def set_anticipation(self, tab_id, now, value=None, strong=False, tile=None):
        self.calls.append((tab_id, value, strong, tile))
        super().set_anticipation(tab_id, now, value, strong, tile)


async def run_loop(veto, shadow, caplog):
    scorer = Spy(Config(speech_interval_s=0.01, speech_window_s=1.0, speech_cue_cooldown_s=60.0, speech_veto_shadow=shadow))
    server = Server(scorer, speech=Listener(), speech_veto=veto)
    server.speech_buffers[5] = np.zeros(32000, dtype=np.float32)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(server._speech_loop())
        await asyncio.sleep(1.4)
        task.cancel()
    cues = sorted(r.getMessage().split(" tab ")[0] for r in caplog.records if r.getMessage().startswith("speech cue"))
    veto_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("speech veto tab")]
    return cues, scorer.calls, veto_lines, server


@pytest.mark.asyncio
async def test_the_cue_decision_is_the_same_with_the_shadow_on_and_off(caplog):
    veto = SpeechVeto(c_model=Fixed(0.1), g_model=Fixed(0.2))       # it would veto everything
    cues_on, calls_on, lines_on, server = await run_loop(veto, 1, caplog)
    cues_off, calls_off, lines_off, _ = await run_loop(veto, 0, caplog)
    cues_none, calls_none, lines_none, _ = await run_loop(None, 1, caplog)
    assert cues_on == cues_off == cues_none == ["speech cue corner", "speech cue penalty"]
    assert calls_on == calls_off == calls_none and calls_on                  # the scorer got exactly the same anticipation calls
    assert len(lines_on) == 1 and "p=0.15 (C=0.10 G=0.20) would_veto=1" in lines_on[0] and "['penalty', 'corner']" in lines_on[0]
    assert "| Penalty for Roma, what a corner" in lines_on[0]
    assert lines_off == [] and lines_none == []


@pytest.mark.asyncio
async def test_a_broken_veto_does_not_touch_the_cues(caplog):
    class Boom:
        def score(self, *a):
            raise RuntimeError("boom")

    cues, calls, lines, _ = await run_loop(Boom(), 1, caplog)
    assert cues == ["speech cue corner", "speech cue penalty"] and calls and lines == []
