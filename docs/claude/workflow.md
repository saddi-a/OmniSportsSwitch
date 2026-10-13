# How to work here

## The user and replies
- Ahmed: personal project, knows Python and C, new to web development. Keep replies short and plain. Explain implementation choices briefly.
- Use the user-facing window names (see names-and-windows.md).

## Design questions
- Ask before architecture or design decisions: give options with pros and cons and a recommendation. Implementation details are yours.
- Do NOT implement an open decision without asking (see the "open decisions" in `PLAN.md` section 0).

## Main chat versus agents (user, 2026-10-13)
- The main chat is for planning, decisions and logging the user's feedback; agents do the work.
- Once the options are decided, hand the implementation (edits, tests, log replays, doc updates) to a background agent and stay in the chat: ask the decision questions (with options) and write the user's feedback and decisions down at once (memory and `PLAN.md` section 0). Do not run long edit/test loops inline. During live tests, delegate code rework to a background agent so the chat stays free.
- An agent prompt must be self-contained: the decisions taken, the current state (branch, folder, what is uncommitted), the tasks, what it must not touch, and the report format.
- Agents must not commit, push, or start/stop/restart the daemon unless told.

## After changes
- Daemon or settings changes need a daemon restart: `scripts\stop_all.ps1`, then `scripts\start_all.ps1` (`-NoEdge` keeps Edge; `stop_all.ps1 -Edge` also closes it). The user pre-approves restarts when testing or debugging needs them.
- Extension changes need the user to press "Reload extension" in the popup and re-track each tab.
- Keep `PLAN.md` section 0 up to date after every change.

## Several sessions
- The main folder is the main session, on `develop`. Other sessions (e.g. scoring, api) work in their own git worktrees on their own branches made from `develop`; the main session merges them.
- A session works only in its own worktree and branch, never in another's folder, and never touches the live daemon, `logs/` or `settings.local.json` unless told.
- Merge flow: see git.md.
