# How to work here

## The user and replies
- Ahmed: personal project, knows Python and C, new to web development. Keep replies short and plain. Explain implementation choices briefly.
- Use the user-facing window names (see names-and-windows.md).

## Design questions
- Ask before architecture or design decisions: give options with pros and cons and a recommendation. Implementation details are yours.
- Do NOT implement an open decision without asking (see the open items in the newest state paragraphs of `PLAN.md` section 0: the 2026-10-17 entries and "State at the end of the 2026-10-14 session"; the old 2026-10-13 list is only history in `docs/history/plan-section0-archive.md`).

## Main chat versus agents (user, 2026-10-13)
- The main chat is for planning, decisions and logging the user's feedback; agents do the work.
- Once the options are decided, hand the implementation (edits, tests, log replays, doc updates) to a background agent and stay in the chat: ask the decision questions (with options) and write the user's feedback and decisions down at once (memory and `PLAN.md` section 0). Do not run long edit/test loops inline. During live tests, delegate code rework to a background agent so the chat stays free.
- An agent prompt must be self-contained: the decisions taken, the current state (branch, folder, what is uncommitted), the tasks, what it must not touch, and the report format.
- Agents must not commit, push, or start/stop/restart the daemon unless told.

## Agent economy (user, 2026-10-13: 100% of the usage had come from subagents; a mix of "cheaper model" and "batch per area")
Every agent re-reads docs and code before it starts, so a new agent per small task is the main cost. Rules:
- **Cheaper model for routine agents**: pass `model: "sonnet"` for docs edits, read-only probes (ESPN, logs), mechanical merges and reviews, log and data analysis, renames, and test-writing from a clear spec. Keep the default (strongest) model only for design-heavy code: scoring logic, the Director Training fit and evaluation, anything with subtle timing or state.
- **Resume, do not respawn**: a follow-up on the same area goes to the existing agent with `SendMessage` (it keeps its context), not to a new agent.
- **Batch per area**: one long-lived agent per area (data pipeline = builder, review, ESPN history, Director Training; scoring = the scoring session; docs; team tables) that I keep feeding, instead of one agent per task. Give a batch of related tasks in one prompt.
- **Short prompts**: list only the files to read and the decisions already taken; do not paste long background; point to `docs/` for the rest. Ask for a short report (under about 25 lines).
- **Do tiny things inline**: an edit of a few lines, one log check, a fast-forward merge, a memory note, a commit or a push is done in the chat; spawn an agent only when the job needs many steps or long reads.
- **Check before spawning**: is an agent already running on this area? Can the answer come from a file I already know? Then do not spawn.
- **Parallel only when independent** (different files); never two agents on the same file.
- Prefer ONE read-only agent for all the facts a decision needs over several probes.

## Context economy (user, 2026-10-13: 83% of the week's usage was at over 150k context, 32% in sessions active 8+ hours)
- A long session is expensive even when cached: every reply re-reads the whole context. Keep state in FILES (PLAN.md section 0, memory, docs), not in the chat, so a session can end at any time.
- Switching to a new task = start a fresh session (`/clear`); in the middle of a task past about 150k context = `/compact`. Before ending a long session: write the hand-over (PLAN.md section 0 top paragraph: state, what is live, open decisions, next steps) and commit/push `develop`.
- Do not leave a session open for hours with background loops or waits; end it and restart with the start prompt in PLAN.md.
- Keep replies short; read files with an offset/limit instead of whole; do not paste long logs into the chat.

## After changes
- Daemon or settings changes need a daemon restart: `scripts\stop_all.ps1`, then `scripts\start_all.ps1` (`-NoEdge` keeps Edge; `stop_all.ps1 -Edge` also closes it). The user pre-approves restarts when testing or debugging needs them.
- Extension changes need the user to press "Reload extension" in the popup and re-track each tab.
- Keep `PLAN.md` section 0 up to date after every change.

## Several sessions
- The main folder is the main session, on `develop`. Other sessions (e.g. scoring, api) work in their own git worktrees on their own branches made from `develop`; the main session merges them.
- A session works only in its own worktree and branch, never in another's folder, and never touches the live daemon, `logs/` or `settings.local.json` unless told.
- Merge flow: see git.md.
