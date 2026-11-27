---
name: github
description: >
  Work with GitHub using the gh CLI and git — PRs, issues, checks, releases,
  forks, reviews, and repo hygiene. Prefer gh over raw API curl when available.
version: 1.0.0
author: Remedy
tags: [github, gh, pr, git, ci, open-source]
tools: [bash_exec, file_read, list_dir]
---

# GitHub (gh + git)

## When to use

- Create/review/merge pull requests, open issues, check CI, browse releases
- User mentions GitHub, PR, CI, `gh`, Actions, fork, or code review on GitHub
- Pushing or publishing work that should land on GitHub

## Prerequisites

1. Prefer **`gh`** (GitHub CLI). Check: `gh --version` and `gh auth status`.
2. If not authenticated: guide the user to `gh auth login` (do not invent tokens).
3. Confirm remote: `git remote -v` and current branch: `git status -sb` / `git branch -vv`.
4. Never force-push to `main`/`master` or rewrite published history unless the user **explicitly** asks.

## Core workflows

### Status & context

```bash
gh auth status
git status -sb
git branch -vv
gh repo view --json nameWithOwner,url,defaultBranchRef
```

### Issues

```bash
gh issue list --limit 20
gh issue view <n>
gh issue create --title "..." --body "..."
gh issue comment <n> --body "..."
```

### Pull requests

```bash
# Before opening a PR: ensure branch is pushed
git push -u origin HEAD

gh pr status
gh pr list --limit 20
gh pr view
gh pr create --title "..." --body "$(cat <<'EOF'
## Summary
- ...

## Test plan
- [ ] ...
EOF
)"

gh pr checks
gh pr diff
gh pr comment --body "..."
# Merge only when user asks and checks are green (or user accepts risk)
gh pr merge --squash   # or --merge / --rebase per repo convention
```

### CI / Actions

```bash
gh run list --limit 10
gh run view <id> --log-failed
gh workflow list
```

### Releases

```bash
gh release list --limit 10
gh release view <tag>
# Create only when user requests a release
gh release create <tag> --title "..." --notes "..."
```

### Code search & browse

```bash
gh search repos "query"
gh search code "query" --repo owner/name
gh api repos/{owner}/{repo}/contents/path
```

## Safety rules

| Action | Policy |
|--------|--------|
| `gh pr create` / `issue create` | OK when user wants it; confirm title/body |
| `git push` to feature branch | OK when user is shipping work |
| `gh pr merge` | Only on explicit request; prefer squash if unknown |
| `git push --force` / `--force-with-lease` | Only if user explicitly requests |
| Secrets, PATs, `GH_TOKEN` printout | Never echo full tokens; use `gh auth` |
| Deleting branches/repos | Confirm twice; never force without ask |

## Working with Remedy itself

When the project is **RemedyAI** (this app):

1. Run tests: `uv run pytest -q` (or project’s documented test command).
2. Keep docs in sync if slash commands/versions change (`scripts/check_docs.py`).
3. Prefer conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
4. For releases, follow repo workflows (CI on push; desktop release workflow when applicable).

## Output style

- Summarize PR/issue state in plain language (title, status, checks, next step).
- Paste short `gh` command blocks the user can re-run.
- If `gh` is missing, fall back to git + browser URLs, and offer install hints for GitHub CLI.

## Related skills

- **git-status** — local dirty tree / branch tracking  
- **commit-message** — draft commit text from the diff  
- **code-review** — review changes before or during a PR  
