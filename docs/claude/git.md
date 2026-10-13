# Git rules

## Public repo
- `saddi-a/OmniSportsSwitch` on GitHub was made PUBLIC on 2026-10-11 (so friends can use it). Everything pushed is visible to everyone: never commit logs, samples, screenshots, stream URLs or personal data.
- Cleaning the public history is a pending decision (see `PLAN.md` section 0, "Public repo").

## Branches
- Work on `develop` (renamed on 2026-10-11 from the old spike/daemon branch).
- Other sessions work on their own branches from `develop`; the main session merges them into `develop`.
- Never commit directly on `main`. It is only fast-forwarded from `develop` (`git push origin develop:main`) when the user asks. The permission system blocked this once and allowed it after the user asked explicitly.

## Commits and pushes
- Push `develop` freely, with Git Bash as a plain `git push origin develop`, never chained with a remote branch delete.
- Never force-push. Never edit git config.
- Commit with `git -c user.name=saddi-a -c user.email=72130894+saddi-a@users.noreply.github.com commit ...`.
- End commit messages with the attribution trailer the session gives (Co-Authored-By line).
- Run the tests before every commit (testing.md).
- In PowerShell `git` may not be on PATH: use Bash for git.
