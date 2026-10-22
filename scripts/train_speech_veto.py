"""Train the SHADOW spoken-cue veto models (OFFLINE, run by hand).

    .venv/Scripts/python.exe scripts/train_speech_veto.py LABELLED.jsonl [--out models/speech_veto] [--cv]

LABELLED.jsonl: one row per spoken-cue window with `text`, `cues_regex_raw`, `label` (now / not_now / unclear), `ts`, `context`, `reason`.
Rows used: now vs not_now only (no unclear, no American football). Trains model C (TF-IDF + logistic regression) and G (fine-tuned
distilbert-base-multilingual-cased, 4 epochs, lr 5e-5, class-weighted, on the GPU when there is one) on ALL usable rows and saves them
under `--out` (default `models/speech_veto`, git-ignored). `--cv` first prints the leave-one-day-out precision / recall of C, G and their
mean at the daemon's threshold. NEVER commit the labelled file, the weights or any transcript: the repo is public.
`HF_HOME` may point at a cache holding the checkpoint (it is downloaded once otherwise); the daemon needs no network afterwards.
"""
import argparse
import json
import pathlib
import random
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from daemon.speech_veto import C_FILE, G_DIR, MAX_LENGTH, MODEL_DIR, THRESHOLD, CModel, c_text, g_text, keep_cues   # noqa: E402

CHECKPOINT = "distilbert/distilbert-base-multilingual-cased"
EPOCHS, LEARNING_RATE, BATCH = 4, 5e-5, 16


def load_rows(path) -> list[dict]:
    """The rows of the experiment: now / not_now, not American football, with the soccer cue names the models know."""
    out = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("label") not in ("now", "not_now") or r.get("context") == "american_football" or str(r.get("reason", "")).startswith("nfl"):
            continue
        r["raw"], r["y"], r["day"] = keep_cues(r.get("cues_regex_raw") or []), r["label"] == "now", str(r["ts"])[:10]
        out.append(r)
    return out


def fit_c(rows) -> CModel:
    from scipy.sparse import hstack
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    texts = [c_text(r["text"], r["raw"]) for r in rows]
    vectorizers = [TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, min_df=2),
                   TfidfVectorizer(ngram_range=(1, 3), sublinear_tf=True, min_df=2, token_pattern=r"(?u)\b\w+\b")]
    x = hstack([v.fit_transform(texts) for v in vectorizers]).tocsr()
    classifier = LogisticRegression(C=3.0, max_iter=2000, class_weight="balanced").fit(x, [r["y"] for r in rows])
    return CModel(vectorizers, classifier)


def fit_g(rows, checkpoint: str = CHECKPOINT, epochs: int = EPOCHS, lr: float = LEARNING_RATE, seed: int = 0):
    """Returns (tokenizer, model) fine-tuned on `rows` (same recipe as the experiment)."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(seed), random.seed(seed), np.random.seed(seed)
    tok = AutoTokenizer.from_pretrained(checkpoint)
    model = AutoModelForSequenceClassification.from_pretrained(checkpoint, num_labels=2).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    y = np.array([r["y"] for r in rows]).astype(int)
    steps = epochs * ((len(rows) + BATCH - 1) // BATCH)
    sched = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
    weight = torch.tensor([(y == 1).sum() / len(y) * 2, (y == 0).sum() / len(y) * 2]).float().to(device)
    model.train()
    for _ in range(epochs):
        perm = np.random.permutation(len(rows))
        for b in range(0, len(rows), BATCH):
            ix = perm[b:b + BATCH]
            x = tok([g_text(rows[i]["text"], rows[i]["raw"]) for i in ix], truncation=True, max_length=MAX_LENGTH, padding=True,
                    return_tensors="pt").to(device)
            loss = torch.nn.functional.cross_entropy(model(**x).logits, torch.tensor(y[ix]).to(device), weight=weight)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(), sched.step(), opt.zero_grad()
    return tok, model.to("cpu").eval()


def predict_g(tok, model, rows) -> list[float]:
    import torch

    out = []
    with torch.inference_mode():
        for b in range(0, len(rows), 32):
            x = tok([g_text(r["text"], r["raw"]) for r in rows[b:b + 32]], truncation=True, max_length=MAX_LENGTH, padding=True, return_tensors="pt")
            out += model(**x).logits.float().softmax(-1)[:, 1].tolist()
    return out


def precision_recall(p, y, threshold) -> tuple[float, float, int]:
    keep = np.asarray(p) >= threshold
    y = np.asarray(y, bool)
    tp = int((keep & y).sum())
    return tp / max(int(keep.sum()), 1), tp / max(int(y.sum()), 1), int(keep.sum())


def cross_check(rows) -> None:
    """Leave-one-day-out over the rows that carry at least one cue; prints P / R of C, G, mean (kept = p >= threshold)."""
    use = [r for r in rows if r["raw"]]
    days = sorted({r["day"] for r in use})
    pc, pg = np.zeros(len(use)), np.zeros(len(use))
    for d in days:
        te = [i for i, r in enumerate(use) if r["day"] == d]
        tr = [r for r in rows if r["day"] != d]
        if len({r["y"] for r in tr}) < 2:
            continue
        c = fit_c(tr)
        pc[te] = [c.predict(use[i]["text"], use[i]["raw"]) for i in te]
        tok, model = fit_g(tr)
        pg[te] = predict_g(tok, model, [use[i] for i in te])
    y = [r["y"] for r in use]
    print(f"leave-one-day-out over {len(use)} cue rows, {len(days)} days, {sum(y)} now (kept = p >= {THRESHOLD}):")
    for name, p in (("C", pc), ("G", pg), ("mean", (pc + pg) / 2)):
        prec, rec, kept = precision_recall(p, y, THRESHOLD)
        print(f"  {name:5s} P={prec:.2f} R={rec:.2f} kept={kept}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("labelled")
    ap.add_argument("--out", default=str(MODEL_DIR))
    ap.add_argument("--cv", action="store_true", help="print leave-one-day-out P/R first (retrains G once per day: minutes)")
    args = ap.parse_args()
    rows = load_rows(args.labelled)
    print(f"{len(rows)} usable rows ({sum(r['y'] for r in rows)} now)")
    if args.cv:
        cross_check(rows)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fit_c(rows).save(out / C_FILE)
    tok, model = fit_g(rows)
    model.save_pretrained(out / G_DIR)
    tok.save_pretrained(out / G_DIR)
    print("saved to", out)


if __name__ == "__main__":
    main()
