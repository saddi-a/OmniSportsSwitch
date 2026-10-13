# Windows quirks

- Source files use CRLF line endings (docs written by agents may be LF: check the bytes before editing and keep each file's endings). Multi-line Edit calls can fail to match; patch from a Python script (read bytes, replace, write bytes) instead. Write patch scripts to files rather than inline shell.
- The Bash tool collapses double backslashes: write any file containing backslashes (Windows paths, `.venv\Scripts\python.exe`) with the Write tool, not shell echo or heredocs.
- PowerShell `Set-Content` can add a BOM or the ANSI codepage; for JSON and other files other tools read, write with the Write tool or `-Encoding utf8` without BOM (a BOM breaks JSON parsers).
- In Python strings `\b` is a backspace. Use raw strings or lookarounds in regexes.
- Install `torch` and `torchvision` from the CUDA index first, and use `--no-deps` for packages that depend on them, or pip replaces them with CPU builds.
- The bash `python` is a Microsoft Store stub; use `.venv\Scripts\python.exe`.
- In PowerShell, `git` may not be on PATH; use bash for git.
