r'''Director Training command line (offline; see director_training/README.md).

    .venv\Scripts\python.exe scripts\director_training.py build [YYYY-MM-DD ...]
    .venv\Scripts\python.exe scripts\director_training.py fit [--sport football] [--dry-run]
    .venv\Scripts\python.exe scripts\director_training.py evaluate --fit-run N
    .venv\Scripts\python.exe scripts\director_training.py ship --fit-run N --shadow-run M [--apply --target FILE | --rollback RUN]
    .venv\Scripts\python.exe scripts\director_training.py status

Each command (except status) makes logs\director_training\run-NNNN-<command>\ and adds a line to logs\director_training\runs.jsonl.
'''
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))      # before scripts/, so `director_training` is the package and not this file

from director_training.cli import main   # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
