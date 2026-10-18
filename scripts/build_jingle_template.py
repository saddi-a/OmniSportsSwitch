"""Build the goal-jingle template from a reference recording (OFFLINE, run by hand).

    .venv/Scripts/python.exe scripts/build_jingle_template.py PATH_TO_AUDIO [--out models/jingle/jingle.npz]

PATH_TO_AUDIO: any file PyAV can decode (webm, mp3, wav...) or a .npy array of mono float samples at 32 kHz. The output goes under
`models/` (git-ignored). Never commit the recording or the template: the repo is public. The daemon loads the template at start; with no
file the jingle feature is simply off.
"""
import argparse
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from daemon.jingle import SAMPLE_RATE, TEMPLATE_PATH, JingleMatcher, build_template, save_template   # noqa: E402


def decode(path: pathlib.Path) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        return np.load(path).astype(np.float32)
    import av

    out = []
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(audio=0):
            for r in resampler.resample(frame):
                out.append(r.to_ndarray().reshape(-1))
        for r in resampler.resample(None):
            out.append(r.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("audio", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=TEMPLATE_PATH)
    args = ap.parse_args()
    audio = decode(args.audio)
    template = build_template(audio)
    save_template(args.out, template)
    m = JingleMatcher(template)
    score, _ = m.best(np.concatenate([np.zeros(8000, np.float32), audio, np.zeros(8000, np.float32)]))
    print(f"template {template.shape[0]} frames ({m.seconds:.2f} s) -> {args.out}; self-match {score:.2f}")


if __name__ == "__main__":
    main()
