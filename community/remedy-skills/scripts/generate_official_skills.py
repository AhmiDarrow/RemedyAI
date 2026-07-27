#!/usr/bin/env python3
"""Generate official, usable Remedy library skills (real workflows, not stubs)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "skills"

BUNDLED = {
    "code-review",
    "comfyui",
    "commit-message",
    "debug-error",
    "decision-journal",
    "design-critique",
    "explain-code",
    "git-status",
    "github",
    "memory-backup",
    "personal-briefing",
    "project-etiquette",
    "project-overview",
    "refactor-safe",
    "remember-me",
    "session-handoff",
    "web-research",
    "write-tests",
    "write-with-user",
    "hello-library",
}

# name -> (description, tags, tools, body_markdown)
# Bodies are full agent procedures.

def skill(
    name: str,
    desc: str,
    tags: list[str],
    tools: list[str],
    body: str,
) -> tuple:
    return (name, desc, tags, tools, body.strip())


SKILLS: list[tuple] = []

# --- Git / release ---
SKILLS += [
    skill(
        "pr-description",
        "Draft a precise PR title and body from branch commits and diff. Use before opening or updating a pull request.",
        ["git", "pr", "docs"],
        ["bash_exec", "file_read"],
        """
## When to use
User asks to open a PR, write a PR description, or summarize a branch for review.

## Steps
1. Run `git status`, `git log --oneline main..HEAD` (or `master..HEAD`), and `git diff main...HEAD --stat`.
2. Skim the full diff for user-facing behavior, risk areas, and tests.
3. Write:
   - **Title**: imperative, ≤72 characters
   - **Summary**: 2–4 sentences of *why* and *what*
   - **Changes**: concrete bullets (not \"misc fixes\")
   - **Test plan**: commands run and expected results
   - **Risk / rollout** if migrations or flags
4. Do not invent features absent from the diff. Link issue IDs if present in commits.
5. Offer paste-ready markdown; run `gh pr create` only if asked and `gh` works.

## Done when
User has a PR body they can submit without guessing what changed.
""",
    ),
    skill(
        "changelog-entry",
        "Author a Keep-a-Changelog entry from commits/diff for a version bump or release.",
        ["docs", "release"],
        ["bash_exec", "file_read", "file_write"],
        """
## When to use
Shipping a version and CHANGELOG must be updated.

## Steps
1. Read existing `CHANGELOG.md` (or scaffold Keep a Changelog).
2. Collect commits since last tag: `git log vX.Y.Z..HEAD --oneline`.
3. Classify: Added / Changed / Fixed / Security / Deprecated / Removed.
4. Write **user-facing** entries (impact), not raw commit subjects.
5. Prepend `## [X.Y.Z] - YYYY-MM-DD` and show the patch before writing to disk.

## Avoid
Listing pure refactors with no user or ops impact unless config/CLI breaks.
""",
    ),
    skill(
        "release-checklist",
        "Execute a pre-release gate: dirty tree, version alignment, tests, docs, remaining ship steps.",
        ["release", "ci"],
        ["bash_exec", "file_read"],
        """
## When to use
User says ship, release, pre-flight, or cut a tag.

## Checklist
1. `git status` — list dirty files; confirm intentional.
2. Align version surfaces (pyproject, package.json, Cargo.toml, sync scripts).
3. Run the project's test command (pytest / npm test / cargo test / go test).
4. Run lint/typecheck if present.
5. Confirm CHANGELOG has this version.
6. List manual steps left: tag `vX.Y.Z`, CI, publish, desktop release.

## Output
Pass/fail table with commands and blockers. Do **not** tag or publish unless explicitly asked.
""",
    ),
    skill(
        "semver-bump",
        "Recommend major/minor/patch from the change set and apply a consistent version bump.",
        ["release", "versioning"],
        ["bash_exec", "file_read", "file_write"],
        """
## When to use
Version bump or \"is this major or patch?\".

## Steps
1. Diff vs last tag: breaking → major; feature → minor; fix → patch.
2. Prefer project sync script if present (`scripts/sync_version.py`, `npm version`, etc.).
3. Propose version + rationale; apply after confirm (or immediately if user already chose).
4. List files touched; remind CHANGELOG + tag after tests.

## Never
Force-push tags or publish without an explicit request.
""",
    ),
    skill(
        "conventional-commits",
        "Propose or write Conventional Commit messages (feat/fix/docs/chore) matching the diff.",
        ["git", "docs"],
        ["bash_exec", "file_read"],
        """
## Format
`<type>(optional-scope): <description>` — imperative, ≤72 chars, no trailing period.

Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.

## Steps
1. Inspect staged/unstaged changes.
2. Split into logical commits if mixed concerns.
3. Body explains *why*; footers for `BREAKING CHANGE` and issue refs.
4. Commit only when the user wants a commit created.

## Note
Match repo conventions if they already use a close variant.
""",
    ),
    skill(
        "branch-hygiene",
        "Prune merged local branches, fetch --prune, and name a clean branch for the next task.",
        ["git"],
        ["bash_exec"],
        """
## Steps
1. `git fetch --prune` and `git branch -vv`.
2. List merged locals: `git branch --merged main` (or master).
3. Propose deletions only for merged branches; confirm unmerged deletes.
4. Suggest `fix/…`, `feat/…`, or `chore/…` names from the task.
5. Warn on detached HEAD or severely behind remote.

## Safety
No force-push to main/master; no hard reset of shared branches.
""",
    ),
    skill(
        "rebase-onto-main",
        "Update the current branch onto latest main/master via rebase or merge with conflict handling.",
        ["git"],
        ["bash_exec"],
        """
## Steps
1. Detect default branch (`main` or `master`).
2. Clean tree or stash with consent.
3. `git fetch` then rebase onto `origin/main` for private branches; merge if shared/preferred.
4. Resolve conflicts file-by-file; re-run tests; continue.
5. If history was rewritten and already pushed, warn that push needs `--force-with-lease` and approval.

## Never
Force-push the default branch.
""",
    ),
    skill(
        "git-bisect-helper",
        "Drive git bisect with a clear good/bad test command to find a regression-introducing commit.",
        ["git", "debug"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Agree a **test command** (exit 0 = good).
2. `git bisect start` → mark bad HEAD → mark good known SHA.
3. Each step: run test → `git bisect good|bad`.
4. On finish: show culprit `git show`, then `git bisect reset`.
5. Propose fix or targeted revert.

## Tip
Automated tests beat manual clicking for bisect reliability.
""",
    ),
    skill(
        "cherry-pick-commit",
        "Cherry-pick specific commits onto the current branch with careful conflict resolution.",
        ["git"],
        ["bash_exec"],
        """
## Steps
1. Identify SHAs with `git log` / `git show`.
2. Ensure clean tree and correct target branch.
3. `git cherry-pick <sha>` (use `-x` on shared repos for audit trail).
4. Resolve conflicts; run related tests.
5. Summarize resulting history.

## Avoid
Cherry-picking merge commits unless `-m` parent is understood.
""",
    ),
]

# --- Security / deps ---
SKILLS += [
    skill(
        "dependency-audit",
        "Audit project dependencies for known vulnerabilities and outdated high-risk packages.",
        ["security", "deps"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Detect lockfiles (package-lock, pnpm-lock, Cargo.lock, uv.lock, go.mod, poetry.lock).
2. Run ecosystem audit tools when present: `Node audit tools`, `Node audit tools`, `Python audit tools`/`uv`, `Rust audit tools`, `Go vulnerability scanners`.
3. Summarize **high/critical** first: package, issue, fixed version.
4. Recommend minimal upgrade path; avoid mass major bumps without tests.
5. Flag clearly abandoned deps when easy to see.

## Output
Severity table + recommended actions.
""",
    ),
    skill(
        "secret-scan-guidance",
        "Find likely leaked secrets in the tree and guide rotation without printing secret values.",
        ["security", "secrets"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Prefer `secret scanners`, `secret scanners`, or `secret scanners` if installed.
2. Otherwise search for common patterns (AWS keys, `ghp_`, `sk-`, private key headers) and **redact** middles in output.
3. Check history only as needed; warn about force-push rewrites.
4. Remediation order: **rotate** → remove from tree → history purge only if requested.
5. Suggest pre-commit secret hooks if missing.

## Never
Echo full live credentials into chat or commits.
""",
    ),
    skill(
        "owasp-web-checklist",
        "Security-review a web change against practical OWASP-style controls (injection, XSS, authz, CSRF, SSRF).",
        ["security", "web"],
        ["file_read"],
        """
## Checklist
1. Parameterized queries / no shell=True with user input.
2. XSS: encode output; audit HTML sinks.
3. Cookie flags for sessions (HttpOnly, Secure, SameSite).
4. Server-side authz on every sensitive action.
5. CSRF strategy for cookie sessions.
6. SSRF allowlists for outbound fetches.
7. Upload size/type controls if files involved.
8. No stack traces to clients in production.

## Output
Findings with severity and file references.
""",
    ),
    skill(
        "auth-session-review",
        "Review login, session, JWT, or OAuth handling for common authentication flaws.",
        ["security", "auth"],
        ["file_read"],
        """
## Checklist
1. Passwords: modern KDF only (bcrypt/argon2/scrypt).
2. JWT: verify sig, exp, aud/iss; short access TTL; secure refresh storage.
3. Rate-limit / lockout on login and reset.
4. OAuth: state/nonce; strict redirect URI allowlist.
5. Authorization checked server-side every request.
6. Logout revokes server-side session/refresh.

## Output
Issues + fixes; never include secret material.
""",
    ),
    skill(
        "cors-review",
        "Review CORS settings for overly broad origins and credentialed cross-origin risks.",
        ["security", "web"],
        ["file_read", "file_write"],
        """
## Steps
1. Locate CORS middleware/config.
2. Disallow `*` with credentials.
3. Allowlist exact SPA origins per environment.
4. Minimize methods/headers.
5. Verify trusted origin works and random origin fails.
""",
    ),
    skill(
        "webhook-verify",
        "Implement or review webhook receivers: signature verification, raw body, replay protection, idempotency.",
        ["security", "api"],
        ["file_read", "file_write"],
        """
## Steps
1. Verify HMAC/signature on **raw body** before JSON parse.
2. Reject stale timestamps (replay window).
3. Idempotent processing with event IDs.
4. Enqueue heavy work; acknowledge quickly when appropriate.
5. Tests: valid sig, invalid sig, replay.

## Critical
Parsing JSON before verifying signatures breaks many providers.
""",
    ),
    skill(
        "file-upload-secure",
        "Harden file uploads: authz, size/type checks, safe storage keys, download posture.",
        ["security", "backend"],
        ["file_read", "file_write"],
        """
## Checklist
1. Authz who can upload/download.
2. Server-side max size; stream to disk/object store.
3. Allowlist types; verify magic bytes when critical.
4. Random object keys; never trust raw filenames as paths.
5. Controlled download headers (`Content-Disposition`).
6. Block executables for untrusted users.
""",
    ),
    skill(
        "threat-model-lite",
        "Write a one-page threat model for a feature: assets, actors, entry points, mitigations.",
        ["security", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. Assets to protect.
2. Actors (anon, user, admin, automated abuse).
3. Entry points (HTTP, jobs, webhooks, files).
4. Top abuse cases.
5. Mitigations + residual risk.
6. Store under `docs/` or PR description.
""",
    ),
    skill(
        "sbom-generate",
        "Generate a Software Bill of Materials (CycloneDX/SPDX) using available tooling.",
        ["security", "supply-chain"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Detect ecosystems in the repo.
2. Prefer `SBOM tools`, `SBOM tools-*`, or language SBOM tools.
3. Write SBOM under `dist/` or user path.
4. Document regeneration command.
5. If tools missing, provide install commands (install only with approval).
""",
    ),
    skill(
        "license-compliance",
        "Summarize third-party licenses and flag strong copyleft risk for distribution.",
        ["legal", "deps"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Find manifests and existing NOTICE files.
2. Use license scanners when available (`license-checker`, `pip-licenses`, `cargo license`).
3. Group: permissive / weak copyleft / strong copyleft / unknown.
4. Explain distribution implications in plain language (**not legal advice**).
5. List attribution obligations for binaries.
""",
    ),
]

# --- Testing ---
SKILLS += [
    skill(
        "test-selection",
        "Select and run the smallest high-value tests for the current change set.",
        ["testing", "ci"],
        ["bash_exec", "file_read"],
        """
## Steps
1. `git diff --name-only` (and staged) to list changed files.
2. Map to nearby tests by project convention.
3. Run targeted tests first for fast feedback.
4. Recommend full suite before merge/release.
5. Report failures with file:line and next debug step.
""",
    ),
    skill(
        "flaky-test-triage",
        "Reproduce and fix flaky tests: races, time, order dependence, shared state.",
        ["testing", "debug"],
        ["bash_exec", "file_read", "file_write"],
        """
## Steps
1. Capture failure log and any seed/order info.
2. Re-run the single test in a loop when possible.
3. Hunt shared state, sleeps, network, unordered collections, parallel races.
4. Fix with isolation, fake clocks, condition waits, stable sorting.
5. Document non-obvious flake cause briefly.
""",
    ),
    skill(
        "e2e-smoke",
        "Define or run a short end-to-end smoke path for the critical user journey.",
        ["testing", "e2e"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Identify the #1 user journey.
2. Use existing the browser test runner/browser tests/etc., or a minimal checklist/script.
3. Run against local/staging as documented.
4. Capture artifacts on failure.
5. State clearly what smoke does **not** prove.
""",
    ),
    skill(
        "coverage-gap",
        "Find coverage gaps on changed critical code and add focused tests.",
        ["testing"],
        ["bash_exec", "file_read", "file_write"],
        """
## Steps
1. Run coverage tooling if available.
2. Prioritize changed files with logic/auth/money/parsers.
3. Propose 1–3 high-value tests (not 100% line chasing).
4. Implement with clear assertions.
5. Re-run targeted coverage.
""",
    ),
    skill(
        "browser-automation-safe",
        "Automate browser checks with browser-test best practices (stable selectors, no fixed sleeps).",
        ["browser", "e2e", "testing"],
        ["bash_exec", "file_read", "file_write"],
        """
## Steps
1. Prefer the browser test runner if present.
2. Role/text selectors over brittle CSS when possible.
3. Wait for conditions, not `sleep`.
4. Isolate test data; production clicks only with explicit approval.
5. Save trace/screenshot on failure.

## Safety
No purchases or destructive admin automation without confirmation.
""",
    ),
]

# --- Frontend ---
SKILLS += [
    skill(
        "frontend-a11y",
        "Audit and fix high-impact accessibility issues in UI code (names, keyboard, semantics).",
        ["frontend", "a11y"],
        ["file_read", "file_write", "bash_exec"],
        """
## Checklist
1. Controls have accessible names.
2. Images: meaningful or empty alt.
3. Keyboard order; no traps.
4. Errors tied to fields.
5. Semantic HTML first.
6. Run jsx-a11y/axe/Lighthouse when available; fix criticals first.
""",
    ),
    skill(
        "responsive-ui-pass",
        "Fix layout breakage across mobile/tablet/desktop widths.",
        ["frontend", "css"],
        ["file_read", "file_write"],
        """
## Steps
1. Inspect layout components and breakpoints.
2. Fix overflow, clipped CTAs, fixed widths, z-index fights.
3. Prefer flex/grid + `min-width: 0`.
4. Reasonable touch targets.
5. List residual known gaps.
""",
    ),
    skill(
        "design-token-sync",
        "Replace one-off colors/spacing with design tokens / CSS variables already in the project.",
        ["frontend", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. Locate token sources (CSS vars, Tailwind theme, theme modules).
2. Grep hard-coded hex/spacing.
3. Map to tokens; propose new tokens only if repeated.
4. Verify light/dark if both exist.
5. Avoid inventing a second token system.
""",
    ),
    skill(
        "bundle-size-check",
        "Find JS bundle size regressions and propose splits or dependency cuts.",
        ["frontend", "perf"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Production build; note sizes.
2. Analyzer if present (visualizer/bundle analyzers).
3. List heavy/duplicate deps.
4. Propose dynamic import and lighter alternatives.
5. Measure before/after.
""",
    ),
    skill(
        "react-performance",
        "Fix common React performance issues after identifying hot components.",
        ["frontend", "react", "perf"],
        ["file_read", "file_write"],
        """
## Steps
1. Identify hot paths (profiler or obvious parent state churn).
2. Check inline objects/functions, context breadth, list keys.
3. Virtualize long lists when needed.
4. Colocate state; memo only where measured.
5. Validate with interaction timing.
""",
    ),
    skill(
        "form-validation-ux",
        "Improve form validation and error mapping UX (inline errors, double-submit, a11y).",
        ["frontend", "ux"],
        ["file_read", "file_write"],
        """
## Steps
1. Validate on appropriate events (blur/submit).
2. Associate messages with inputs (aria-describedby).
3. Map API field errors to fields.
4. Prevent double submit; clear pending states.
5. Manage focus on success/error.
""",
    ),
    skill(
        "empty-state-design",
        "Add clear empty/error/no-results states with next actions.",
        ["frontend", "ux"],
        ["file_read", "file_write"],
        """
## Steps
1. Distinguish never-created vs filtered-empty vs failed load.
2. Primary action (create/import/clear filters).
3. Short neutral copy.
4. Consistent with design system components.
""",
    ),
    skill(
        "i18n-extract",
        "Extract UI strings into i18n catalogs and find missing locale keys.",
        ["frontend", "i18n"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Detect i18n framework.
2. Extract strings; use placeholders (ICU) not concatenation.
3. Find missing keys in other locales.
4. Keep catalogs consistent/sorted per convention.
5. Note RTL only if relevant locales exist.
""",
    ),
    skill(
        "css-specificity-debug",
        "Debug why a CSS rule loses (specificity, order, layers) and fix cleanly.",
        ["frontend", "css"],
        ["file_read", "file_write"],
        """
## Steps
1. Identify computed winning rule (devtools if available).
2. Map competing selectors and import order.
3. Fix structure/tokens before `!important`.
4. Remove dead CSS when found.
5. Document the final selector strategy.
""",
    ),
    skill(
        "visual-regression-setup",
        "Add a small visual regression set for critical screens with stable snapshots.",
        ["frontend", "testing"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Use existing visual tool if any (browser screenshots, visual review tools, etc.).
2. Cover a few critical screens only.
3. Disable animations; stabilize fonts when possible.
4. Document approval workflow for intentional changes.
5. Keep snapshots from becoming unmaintained noise.
""",
    ),
]

# --- Backend / API ---
SKILLS += [
    skill(
        "api-contract-review",
        "Review HTTP APIs for validation, authz, status codes, pagination, and error shape consistency.",
        ["api", "backend"],
        ["file_read"],
        """
## Checklist
1. Boundary validation.
2. Authn/authz on sensitive routes.
3. Correct status codes and stable error JSON.
4. Max page sizes / anti-enumeration limits.
5. No prod stack traces to clients.
6. Docs/OpenAPI updated when public.

## Output
Severity-ordered findings with paths.
""",
    ),
    skill(
        "openapi-sync",
        "Regenerate or manually sync OpenAPI with implemented routes and flag breaking changes.",
        ["api", "docs"],
        ["bash_exec", "file_read", "file_write"],
        """
## Steps
1. Find OpenAPI source of truth (generated or hand-written).
2. Sync paths/schemas to code.
3. Diff for removals/renames → breaking.
4. Examples for public endpoints.
5. Note versioning strategy.
""",
    ),
    skill(
        "db-migration-safe",
        "Write or review DB migrations using expand/contract safety and rollback notes.",
        ["database", "backend"],
        ["file_read", "file_write", "bash_exec"],
        """
## Rules
1. Add nullable → backfill → constrain (expand/contract).
2. Avoid long locks; batch big updates.
3. Don't drop columns still read by running app versions.
4. Document down migration or forward-fix.
5. Test up (and down if supported) on sample data.
""",
    ),
    skill(
        "sql-query-review",
        "Review SQL/ORM usage for N+1, injection, and missing indexes.",
        ["database", "perf", "security"],
        ["file_read", "bash_exec"],
        """
## Steps
1. Locate queries on the hot path.
2. Eliminate N+1 with join/prefetch.
3. Ensure parameterization — never string-built SQL with user input.
4. Suggest indexes with rationale (and EXPLAIN when available).
5. Avoid SELECT * on wide rows in hot paths.
""",
    ),
    skill(
        "pagination-standard",
        "Add stable list pagination (cursor preferred) with enforced max limits.",
        ["api", "backend"],
        ["file_read", "file_write"],
        """
## Steps
1. Choose cursor or page; prefer cursor for large data.
2. Enforce max limit server-side.
3. Stable sort (e.g. created_at + id).
4. Return next cursor/token.
5. Document in OpenAPI.
""",
    ),
    skill(
        "idempotent-api",
        "Make a mutating endpoint safely retryable with idempotency keys.",
        ["api", "reliability"],
        ["file_read", "file_write"],
        """
## Steps
1. Accept Idempotency-Key (or domain natural key).
2. Persist first response for a TTL.
3. Replay on duplicate; prevent double side effects under concurrency.
4. Tests for parallel duplicates.
5. Document client requirements.
""",
    ),
    skill(
        "rate-limit-design",
        "Design rate limits for public/auth endpoints with clear 429 behavior.",
        ["backend", "security"],
        ["file_read", "file_write"],
        """
## Steps
1. Per-IP and per-user/token limits as appropriate.
2. Stricter on login/password reset.
3. Return 429 + Retry-After when possible.
4. Use existing gateway/middleware if present.
5. Document defaults and bypasses (carefully).
""",
    ),
    skill(
        "cache-invalidation",
        "Design cache keys and invalidation to prevent stale reads and stampedes.",
        ["backend", "perf"],
        ["file_read", "file_write"],
        """
## Steps
1. Map read/write paths and consistency needs.
2. Key namespace including version/tenant/user when needed.
3. Invalidate or bump version on writes.
4. Stampede strategy (singleflight/soft TTL).
5. Document flush procedure for ops.
""",
    ),
    skill(
        "graceful-shutdown",
        "Implement SIGTERM-aware graceful shutdown and drain for servers/workers.",
        ["backend", "reliability"],
        ["file_read", "file_write"],
        """
## Steps
1. Trap SIGTERM/SIGINT.
2. Stop accepting new work; drain in-flight with deadline.
3. Close pools/consumers cleanly.
4. Align with orchestrator grace period.
5. Test with kill signals.
""",
    ),
    skill(
        "health-endpoints",
        "Add liveness vs readiness endpoints with appropriate dependency checks.",
        ["backend", "ops"],
        ["file_read", "file_write"],
        """
## Rules
1. Liveness = process healthy (cheap).
2. Readiness = can serve traffic.
3. Don't make liveness depend on flaky deps.
4. Document probe paths for deploy config.
""",
    ),
    skill(
        "queue-consumer-safe",
        "Build safe queue consumers: ack semantics, retries, DLQ, idempotent handlers.",
        ["backend", "ops"],
        ["file_read", "file_write"],
        """
## Steps
1. Assume at-least-once; make handlers idempotent.
2. Ack only after success; nack/retry with backoff.
3. Max attempts → dead-letter queue.
4. Metrics: lag, failures, processing time.
5. Poison message runbook.
""",
    ),
    skill(
        "cron-job-design",
        "Design scheduled jobs with overlap locks, idempotency, and failure alerts.",
        ["backend", "ops"],
        ["file_read", "file_write"],
        """
## Checklist
1. Idempotent runs.
2. Single-runner lock if multi-instance.
3. Explicit timezone (prefer UTC).
4. Success/failure metrics + alerts.
5. Manual re-run path for ops.
""",
    ),
    skill(
        "multi-tenant-isolation",
        "Audit multi-tenant isolation for cross-tenant data leaks.",
        ["security", "backend"],
        ["file_read"],
        """
## Checklist
1. Tenant key on owned rows.
2. Every query filters by auth-context tenant (not client-supplied alone).
3. Object storage prefixes per tenant.
4. Automated tests for cross-tenant deny.
5. Admin break-glass audited.

## Severity
Cross-tenant read/write is typically **critical**.
""",
    ),
]

# --- DevOps ---
SKILLS += [
    skill(
        "dockerfile-harden",
        "Write or harden container image recipes: multi-stage, non-root, pin bases, no secrets in layers.",
        ["container tooling", "devops"],
        ["file_read", "file_write"],
        """
## Checklist
1. Pin base images.
2. Multi-stage; copy runtime artifacts only.
3. Non-root USER.
4. No secrets in ENV/layers.
5. Layer order for cache; .dockerignore.
6. HEALTHCHECK when useful.
""",
    ),
    skill(
        "compose-dev-env",
        "Provide local multi-service containers for local dependencies with healthchecks and sane ports.",
        ["container tooling", "devops"],
        ["file_read", "file_write"],
        """
## Steps
1. List required services (db, redis, etc.).
2. Healthchecks + depends_on conditions.
3. Volumes for data; bind-mount app if hot reload needed.
4. `.env.example` without secrets.
5. Document `up` / `down` and ports.
""",
    ),
    skill(
        "ci-pipeline-review",
        "Review CI pipelines for caching, secret hygiene, required checks, and runtime.",
        ["ci", "devops"],
        ["file_read", "file_write"],
        """
## Checklist
1. PR + default branch triggers as needed.
2. Dependency caches keyed on lockfiles.
3. Secrets via CI store; never echoed.
4. Pin actions; prefer SHAs for high assurance.
5. Parallel jobs; fail fast on lint.
6. Align required check names with branch protection.
""",
    ),
    skill(
        "k8s-manifest-review",
        "Review container orchestration manifests for probes, resources, securityContext, and rollout safety.",
        ["k8s", "devops"],
        ["file_read"],
        """
## Checklist
1. requests/limits present.
2. Sensible liveness/readiness.
3. non-root securityContext where possible.
4. Secrets not plaintext in git.
5. RollingUpdate / PDB for critical services.
6. HPA metrics sanity if used.
""",
    ),
    skill(
        "terraform-plan-review",
        "Review infrastructure-as-code plans for destroys, public exposure, and IAM blast radius before apply.",
        ["iac", "devops"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Read plan (no apply).
2. Highlight destroys/replacements and `0.0.0.0/0`.
3. Review IAM for admin-equivalent rights.
4. Confirm remote state + locking.
5. Require explicit human approval for production apply.
""",
    ),
    skill(
        "log-level-triage",
        "Triage production issues from logs: timeline, correlation IDs, dependency health.",
        ["ops", "debug"],
        ["bash_exec", "file_read"],
        """
## Steps
1. When did it start? Last deploy/config change?
2. Correlation/trace IDs for failing requests.
3. Group errors; sample multiple.
4. Check dependency health.
5. Mitigate (rollback/flag/scale) before deep fix; keep incident notes.
""",
    ),
    skill(
        "structured-logging",
        "Introduce structured logging with levels, correlation IDs, and secret redaction.",
        ["ops", "quality"],
        ["file_read", "file_write"],
        """
## Steps
1. Use existing logging framework.
2. Structured fields (JSON or key=value).
3. Correct levels; correlation IDs from middleware.
4. Redact tokens/PII.
5. Replace print debugging on hot paths.
""",
    ),
    skill(
        "metrics-instrumentation",
        "Add RED/USE-style metrics without high-cardinality label explosions.",
        ["ops", "observability"],
        ["file_read", "file_write"],
        """
## Steps
1. Pick metrics library already in repo.
2. Latency + error counters for critical paths.
3. Avoid user-id cardinality in labels.
4. Example queries in docs.
5. Alert only on actionable symptoms.
""",
    ),
    skill(
        "tracing-spans",
        "Add distributed tracing spans across request and outbound calls.",
        ["ops", "observability"],
        ["file_read", "file_write"],
        """
## Steps
1. Detect distributed tracing/tracing setup.
2. Span HTTP/DB/tool calls with useful attributes.
3. Propagate context across async/threads.
4. Sampling suitable for prod.
5. Verify in the project's trace UI.
""",
    ),
    skill(
        "backup-restore-drill",
        "Plan or execute a non-destructive backup restore drill and document RTO/RPO.",
        ["ops", "reliability"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Locate backup mechanism.
2. Restore to non-prod target.
3. Boot app against restored data.
4. Record RTO/RPO achieved and gaps.
5. Schedule next drill.

## Safety
Never overwrite production without explicit confirmation.
""",
    ),
    skill(
        "incident-postmortem",
        "Write a blameless postmortem with timeline, root cause, and owned actions.",
        ["ops", "docs"],
        ["file_write", "file_read"],
        """
## Sections
Summary · Impact · Timeline (UTC) · Root cause · What went well/poorly · Actions (owner, date) · Detection gaps.

## Tone
Blameless; systems over individuals.
""",
    ),
    skill(
        "runbook-write",
        "Author an on-call runbook: health checks, common failures, deploy/rollback.",
        ["docs", "ops"],
        ["file_read", "file_write"],
        """
## Sections
1. Overview & owners
2. Health verification commands
3. Symptom → diagnose → fix
4. Deploy/rollback
5. Dependencies & dashboards
6. Escalation

## Style
Copy-pastable commands; written for stressed humans.
""",
    ),
]

# --- Docs / product ---
SKILLS += [
    skill(
        "adr-write",
        "Write an Architecture Decision Record for a significant technical choice.",
        ["docs", "architecture"],
        ["file_read", "file_write"],
        """
## Template
Title · Date · Status · Context · Decision · Alternatives · Consequences.

## Steps
Save under `docs/adr/` or project convention; link from architecture docs.
""",
    ),
    skill(
        "markdown-doc-structure",
        "Restructure Markdown documentation for clear heading hierarchy and working links.",
        ["docs"],
        ["file_read", "file_write"],
        """
## Steps
1. Outline H1–H3.
2. Single H1; logical nesting.
3. Fix links and code fence languages.
4. TOC for long pages if needed.
5. Remove stale version claims.
""",
    ),
    skill(
        "bug-report-template",
        "Turn a vague bug into a reproducible report: environment, steps, expected/actual.",
        ["product", "docs"],
        ["file_read", "file_write"],
        """
## Fill
Summary · Environment · Steps · Expected · Actual · Logs (redacted) · Severity · Workaround.

## Next
Hand off to debugging once reproducible.
""",
    ),
    skill(
        "user-story-split",
        "Split an epic into vertical, testable user stories with acceptance criteria.",
        ["product", "planning"],
        ["file_read"],
        """
## Steps
1. Restate user outcome.
2. Vertical slices (shippable value).
3. Acceptance criteria per story.
4. Order by riskiest assumption.
5. Non-goals and dependencies called out.
""",
    ),
    skill(
        "acceptance-criteria",
        "Write testable acceptance criteria for a feature or bugfix.",
        ["product", "testing"],
        ["file_read"],
        """
## Rules
Observable outcomes · edge cases · auth roles · negatives · limits if relevant.

## Format
Numbered list verifiable by QA or an agent.
""",
    ),
    skill(
        "feature-flag-rollout",
        "Add a feature flag with default-off rollout, metrics, and removal plan.",
        ["product", "release"],
        ["file_read", "file_write"],
        """
## Steps
1. Use existing flag system if any.
2. Default off in production.
3. Gate UI **and** server.
4. Success + abort metrics.
5. Remove flag after full rollout.
""",
    ),
    skill(
        "permissions-matrix",
        "Build a role×action permission matrix and verify server enforcement.",
        ["security", "product"],
        ["file_read", "file_write"],
        """
## Steps
1. List roles and actions.
2. Matrix allow/deny.
3. Verify code paths match.
4. Tests for critical denies.
5. Publish matrix for support/admin.
""",
    ),
    skill(
        "retro-notes",
        "Run a lightweight blameless retro and produce concrete action items.",
        ["process"],
        ["file_write"],
        """
## Steps
1. Facts timeline.
2. Keep / Improve / Ideas.
3. Actions with owner + date (no vagueness).
4. Save to agreed location.
""",
    ),
]

# --- Language ecosystems ---
SKILLS += [
    skill(
        "python-packaging",
        "Package Python projects with pyproject entry points and a clean build/install check.",
        ["python", "packaging"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Ensure pyproject metadata and src layout when appropriate.
2. Entry points under `[project.scripts]`.
3. `uv build` or `python -m build`.
4. Test install into a clean venv.
5. Publish only on explicit request.
""",
    ),
    skill(
        "python-typing-pass",
        "Raise typing quality on selected Python modules until mypy/pyright is clean.",
        ["python", "quality"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Run typechecker on target paths.
2. Fix real bugs first.
3. Annotate public APIs with modern syntax.
4. Avoid unjustified ignore comments.
5. Re-run until clean or document residual ignores.
""",
    ),
    skill(
        "nodejs-upgrade",
        "Plan and execute a Node.js runtime upgrade with CI and dependency checks.",
        ["node", "upgrade"],
        ["bash_exec", "file_read", "file_write"],
        """
## Steps
1. Read engines + CI node version.
2. Check native addons / engines constraints.
3. Upgrade, install, test, build.
4. Update CI and docs.
5. Note breaking Node changes affecting the app.
""",
    ),
    skill(
        "ts-strict-migration",
        "Incrementally enable TypeScript strictness without a big-bang freeze.",
        ["typescript", "quality"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. List disabled strict flags.
2. Enable one flag at a time; keep build green.
3. Replace `any` with `unknown` + narrowing at boundaries.
4. Document remaining escapes.
""",
    ),
    skill(
        "go-module-hygiene",
        "Tidy Go modules and verify reproducible builds/tests.",
        ["go"],
        ["bash_exec", "file_read"],
        """
## Steps
1. `go mod tidy` and review go.sum.
2. Remove unnecessary `replace`.
3. `go test ./...` and `go vet ./...`.
4. Note retracted modules.
""",
    ),
    skill(
        "rust-clippy-fix",
        "Run Clippy and fix correctness-oriented lints; re-test.",
        ["rust", "quality"],
        ["bash_exec", "file_read", "file_write"],
        """
## Steps
1. `cargo clippy` with project-standard flags.
2. Fix real bug lints first.
3. Keep style consistent with the crate.
4. Re-run tests.
""",
    ),
]

# --- LLM / agent apps ---
SKILLS += [
    skill(
        "prompt-eval-harness",
        "Build a small regression suite for prompts/agent behaviors with deterministic checks.",
        ["llm", "testing"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. 10–30 fixtures with expected properties.
2. Prefer schema/contains/forbid checks over pure vibes.
3. Script runner producing JSON results.
4. Gate critical cases in CI when feasible.
5. No real PII in fixtures.
""",
    ),
    skill(
        "rag-chunking",
        "Design document chunking and metadata for higher-quality RAG retrieval.",
        ["llm", "rag"],
        ["file_read", "file_write"],
        """
## Steps
1. Inventory document shapes.
2. Chunk on structure (headings) before blind windows.
3. Metadata: path, title, section, updated_at.
4. Define top-k + citation requirements.
5. Eval questions with expected sources.
""",
    ),
    skill(
        "tool-use-spec",
        "Specify safe tool/function contracts: schemas, side effects, confirmations, timeouts.",
        ["llm", "tools"],
        ["file_read", "file_write"],
        """
## Steps
1. List tools with JSON schemas and side-effect class.
2. Confirm destructive tools.
3. Timeouts/retries/error shapes.
4. Redact secrets in logs.
5. Unit-test allowlists (URL/fs).
""",
    ),
    skill(
        "llm-cost-guardrails",
        "Add token/cost/latency guardrails and sensible model routing.",
        ["llm", "cost"],
        ["file_read", "file_write"],
        """
## Steps
1. Meter tokens per path.
2. Cap max tokens; summarize contexts deliberately.
3. Cache embeddings/repeated prompts when safe.
4. Cheap models for classify; stronger for hard coding.
5. Budgets/alerts for multi-tenant.
""",
    ),
]

# --- Data ---
SKILLS += [
    skill(
        "csv-data-cleanup",
        "Profile and clean CSV/TSV data: encoding, types, nulls, dedupe, report.",
        ["data", "csv"],
        ["bash_exec", "file_read", "file_write"],
        """
## Steps
1. Detect encoding/delimiter.
2. Profile nulls/types/outliers.
3. Normalize dates/numbers; unify nulls; trim.
4. Dedupe on business keys if defined.
5. Write cleaned file + quality notes; keep a re-runnable script when possible.
""",
    ),
    skill(
        "json-schema-design",
        "Design tight JSON Schema / Zod / Pydantic models with bounds and examples.",
        ["api", "data"],
        ["file_read", "file_write"],
        """
## Steps
1. Collect sample payloads.
2. Required fields, enums, formats, max lengths.
3. Limit array sizes; consider additionalProperties false.
4. Examples + invalid fixtures for tests.
""",
    ),
    skill(
        "money-calculations",
        "Implement money math with integers/decimals, explicit rounding, and currency codes.",
        ["backend", "finance"],
        ["file_read", "file_write"],
        """
## Rules
1. No binary floats for money.
2. Integer minor units or Decimal.
3. Document rounding mode.
4. Store currency with amounts.
5. Invariant tests (line sums, tax).
""",
    ),
    skill(
        "datetime-timezone",
        "Fix datetime bugs by storing UTC and converting only at the edge.",
        ["quality", "backend"],
        ["file_read", "file_write"],
        """
## Steps
1. UTC in storage/APIs internal.
2. Timezone-aware types only.
3. Convert for display in user TZ.
4. Tests around DST if critical.
5. Document behavior.
""",
    ),
]

# --- Networking ---
SKILLS += [
    skill(
        "http-debugging",
        "Debug HTTP failures with curl -v, status/headers, and TLS basics (redact auth).",
        ["network", "debug"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Reproduce with verbose curl (redact secrets).
2. Compare method/URL/headers/body vs working case.
3. Check base URL, cookies, JWT clock skew.
4. TLS cert/SNI/proxy issues.
5. Write root cause + fix.
""",
    ),
    skill(
        "websocket-debug",
        "Diagnose WebSocket handshake, auth, ping/pong, and reconnect storms.",
        ["network", "debug"],
        ["file_read", "bash_exec"],
        """
## Steps
1. Confirm ws/wss and proxy idle timeouts.
2. Inspect handshake auth headers/protocols.
3. Server ping cadence; client backoff with jitter.
4. Log close codes.
5. Recommend concrete fix.
""",
    ),
    skill(
        "cross-platform-paths",
        "Fix Windows/macOS/Linux path bugs using pathlib and safe joins.",
        ["windows", "quality"],
        ["file_read", "file_write"],
        """
## Steps
1. Replace string path concat with pathlib/Path APIs.
2. Handle reserved Windows names when accepting filenames.
3. Reject `..` escapes on public APIs.
4. Add tests covering both separators when feasible.
""",
    ),
    skill(
        "encoding-fix",
        "Fix Unicode/encoding issues (UTF-8, BOM, mislabeled files).",
        ["data", "debug"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Detect encoding.
2. Standardize UTF-8 for text unless legacy requires otherwise.
3. Explicit encoding on open in Python.
4. Regression fixture with non-ASCII.
5. Avoid silent `errors=ignore` unless accepted data loss.
""",
    ),
]

# --- More productized official skills ---
SKILLS += [
    skill(
        "load-test-plan",
        "Design and run a minimal load test on critical endpoints with clear stop conditions.",
        ["perf", "testing"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Choose 1–3 endpoints + realistic mix.
2. Use load generators/vegeta/hey/locust if available.
3. Ramp; watch p95 and error rate.
4. Stop on error storms; capture bottleneck hypothesis.
5. Report numbers + next optimizations.

## Caution
Don't overload shared prod/staging without permission.
""",
    ),
    skill(
        "memory-leak-hunt",
        "Find memory growth in long-running services via profiles and retained allocations.",
        ["perf", "debug"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Reproduce growth under load.
2. Capture heap profiles (pprof/memray/clinic/etc.).
3. Look for unbounded caches and listener growth.
4. Fix with LRU/TTL/dispose.
5. Soak-test verification.
""",
    ),
    skill(
        "deadlock-debug",
        "Debug deadlocks via stack dumps and lock-order fixes.",
        ["debug", "concurrency"],
        ["file_read", "bash_exec"],
        """
## Steps
1. Capture stacks of stuck processes.
2. Identify lock inversion / missing timeouts.
3. Fix ordering or redesign synchronization.
4. Add regression test if feasible.
""",
    ),
    skill(
        "env-config-12factor",
        "Refactor configuration to env-based 12-factor style with validated startup.",
        ["backend", "config"],
        ["file_read", "file_write"],
        """
## Steps
1. Inventory config and secrets.
2. `.env.example` without secrets.
3. Typed config validation at boot.
4. Fail fast in production on missing required vars.
5. Document variables.
""",
    ),
    skill(
        "cli-ux-polish",
        "Polish CLI help, flags, exit codes, and non-interactive CI mode.",
        ["cli", "ux"],
        ["file_read", "file_write"],
        """
## Checklist
1. Accurate `--help` with examples.
2. Exit codes meaningful.
3. `-y/--yes` or env for non-interactive.
4. stdout data / stderr logs.
5. Validate flags early.
""",
    ),
    skill(
        "makefile-tasks",
        "Add Makefile/task targets wrapping real project commands (setup/test/lint/run).",
        ["tooling"],
        ["file_read", "file_write"],
        """
## Steps
1. Harvest commands from README/CI.
2. Targets: setup, test, lint, run, build, help.
3. .PHONY appropriately.
4. Keep wrappers thin.
""",
    ),
    skill(
        "pre-commit-hooks",
        "Configure pre-commit/husky hooks for format, lint, and optional secret scan.",
        ["tooling", "quality"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Use existing hook system if any.
2. Fast checks on commit; heavy tests in CI.
3. Secret scan when available.
4. Document install for contributors.
""",
    ),
    skill(
        "editorconfig-setup",
        "Add .editorconfig aligned with project formatters.",
        ["tooling"],
        ["file_read", "file_write"],
        """
## Steps
1. Detect languages.
2. UTF-8, final newline, trim trailing whitespace.
3. Indent matching prettier/black/gofmt conventions.
4. Don't fight dedicated formatters.
""",
    ),
    skill(
        "codeowners-setup",
        "Create CODEOWNERS for critical paths and align with review rules.",
        ["git", "process"],
        ["file_read", "file_write"],
        """
## Steps
1. Identify critical directories.
2. Map to teams/users.
3. Keep patterns accurate and minimal.
4. Ensure branch protection can require owners.
""",
    ),
    skill(
        "dev environment container-setup",
        "Add a dev environment container for reproducible contributor environments.",
        ["tooling", "container tooling"],
        ["file_read", "file_write"],
        """
## Steps
1. Base image matching runtime.
2. postCreate install deps.
3. Forward ports; document usage.
4. Keep build time reasonable.
""",
    ),
    skill(
        "seo-basics",
        "Apply basic technical SEO checks to marketing/docs pages.",
        ["frontend", "marketing"],
        ["file_read", "file_write"],
        """
## Checklist
Titles/descriptions · canonicals · robots/noindex intent · heading hierarchy · sitemap · basic LCP sanity.
""",
    ),
    skill(
        "email-template-review",
        "Review HTML emails for client safety, plain-text parts, and injection.",
        ["frontend", "email"],
        ["file_read"],
        """
## Checklist
Inline CSS · plain-text alternative · escape user content · no scripts · auth links hygiene · document client test plan.
""",
    ),
    skill(
        "graphql-schema-review",
        "Review GraphQL schemas/resolvers for authz, N+1, pagination, and deprecations.",
        ["api", "graphql"],
        ["file_read"],
        """
## Checklist
Field authz · DataLoader/batching · list pagination · deprecations before removal · depth limits if public · error shape.
""",
    ),
    skill(
        "oauth-app-setup",
        "Configure OAuth/OIDC clients correctly (PKCE, redirects, scopes, token storage).",
        ["auth", "security"],
        ["file_read", "file_write"],
        """
## Steps
1. Strict redirect allowlist.
2. PKCE for public clients.
3. Minimal scopes.
4. Secure token storage + refresh/revoke.
5. State/nonce CSRF protections.
""",
    ),
    skill(
        "stripe-webhook-flow",
        "Implement payment webhooks with verification and idempotent entitlement updates.",
        ["payments", "backend"],
        ["file_read", "file_write"],
        """
## Steps
1. Verify signatures on raw body.
2. Handle subscription lifecycle events needed by the product.
3. Idempotent writes of entitlements.
4. Never trust client-only payment success.
5. Fixture tests with provider samples.
""",
    ),
    skill(
        "background-job-ui",
        "Expose long-running job progress/status to users with authz and safe errors.",
        ["backend", "product"],
        ["file_read", "file_write"],
        """
## Steps
1. Persist job state (queued/running/succeeded/failed/progress).
2. Users only access own jobs.
3. Poll or push updates.
4. Redact internal errors.
5. Cleanup old jobs/artifacts.
""",
    ),
    skill(
        "data-export-user",
        "Implement authenticated user data export with async processing if large.",
        ["privacy", "backend"],
        ["file_read", "file_write"],
        """
## Steps
1. Inventory user-owned data.
2. Async job + notification when large.
3. Expiring signed download links.
4. Rate limit.
5. Document format.
""",
    ),
    skill(
        "data-deletion-user",
        "Implement account deletion with re-auth, cascade/anonymize, and session revoke.",
        ["privacy", "backend"],
        ["file_read", "file_write"],
        """
## Steps
1. Re-authenticate.
2. Cascade or anonymize per policy.
3. Delete object storage objects.
4. Revoke sessions/tokens.
5. Audit log; optional grace period.
""",
    ),
    skill(
        "audit-log-design",
        "Design audit logs for sensitive admin/user actions.",
        ["security", "ops"],
        ["file_read", "file_write"],
        """
## Steps
1. Event list (who/what/when/target/outcome).
2. Append-friendly storage.
3. Retention.
4. Query UI with strong authz.
5. Immutable enough for your threat model.
""",
    ),
    skill(
        "pii-data-handling",
        "Minimize and protect PII: access, logs redaction, retention, deletion paths.",
        ["security", "privacy"],
        ["file_read", "file_write"],
        """
## Steps
1. Data inventory.
2. Collect only needed fields.
3. No PII in logs.
4. Access control + audits for admin.
5. Export/delete paths.
6. Encryption posture documented.

## Disclaimer
Engineering guidance, not legal advice.
""",
    ),
    skill(
        "search-indexing",
        "Design app search indexing and sync (FTS or search engine) with relevance checks.",
        ["backend", "search"],
        ["file_read", "file_write"],
        """
## Steps
1. Choose DB FTS vs external search.
2. Document fields/weights.
3. Write-time sync or reindex strategy.
4. UX for eventual consistency.
5. Sample relevance queries.
""",
    ),
    skill(
        "regex-safety",
        "Review regexes for ReDoS and correctness on untrusted input.",
        ["security", "quality"],
        ["file_read", "file_write"],
        """
## Steps
1. Find regex on user input.
2. Simplify or replace catastrophic patterns.
3. Bound input length.
4. Tests with long adversarial strings.
""",
    ),
    skill(
        "algorithmic-complexity",
        "Find accidental quadratic patterns and propose better data structures.",
        ["perf"],
        ["file_read"],
        """
## Steps
1. Nested loops over large collections.
2. Estimate sizes.
3. Maps/sets/indexes/batching.
4. Time with larger fixtures.
""",
    ),
    skill(
        "benchmark-micro",
        "Create a trustworthy microbenchmark for a profiled hot function.",
        ["perf", "testing"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Confirm hotspot via profiling first.
2. Use proper harness for the language.
3. Realistic inputs; prevent DCE pitfalls.
4. Report distributions, not single runs.
""",
    ),
    skill(
        "monorepo-task-runner",
        "Fix monorepo task graphs (turbo/nx/pnpm) for filtered build/test.",
        ["monorepo", "tooling"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Detect workspace tool.
2. Define dependency pipeline.
3. Filter to changed packages in CI.
4. Cache outputs correctly.
5. Document developer commands.
""",
    ),
    skill(
        "backward-compat-api",
        "Plan backward-compatible API evolution and deprecation windows.",
        ["api", "release"],
        ["file_read"],
        """
## Rules
Additive preferred · deprecate before remove · version when breaking · contract tests · CHANGELOG communication.
""",
    ),
    skill(
        "api-client-sdk",
        "Generate or refresh a typed client from OpenAPI for consumers.",
        ["api", "docs"],
        ["bash_exec", "file_read", "file_write"],
        """
## Steps
1. Ensure OpenAPI accuracy.
2. Generate with project tool.
3. Version/publish or commit per convention.
4. Smoke critical calls.
5. Note client breaking changes.
""",
    ),
    skill(
        "onboarding-checklist",
        "Design a dismissible first-run checklist that drives activation.",
        ["product", "ux"],
        ["file_read", "file_write"],
        """
## Steps
1. 3–5 activation steps tied to value.
2. Persist completion.
3. Skip/dismiss allowed.
4. Optional funnel analytics.
5. Auto-hide when complete.
""",
    ),
    skill(
        "pagination-ui",
        "Implement accessible list pagination or load-more with URL state.",
        ["frontend", "ux"],
        ["file_read", "file_write"],
        """
## Steps
1. Sync page/cursor to URL.
2. Disabled states + loading.
3. Preserve filters.
4. Prefer Load more over pure infinite scroll for a11y unless product requires otherwise.
5. Empty/error states.
""",
    ),
    skill(
        "sla-error-budget",
        "Define practical SLIs/SLOs and an error-budget policy for a service.",
        ["ops", "sre"],
        ["file_read", "file_write"],
        """
## Steps
1. User-centric SLIs.
2. Realistic SLO.
3. Error budget + deploy policy when burned.
4. Dashboards + burn alerts.
5. Monthly review cadence.
""",
    ),
    skill(
        "grpc-api-design",
        "Design/review gRPC protos with versioning, deadlines, and idempotency.",
        ["api", "grpc"],
        ["file_read", "file_write"],
        """
## Steps
1. Proto3; reserve deleted numbers.
2. Error model conventions.
3. Deadlines/cancellation.
4. Idempotent mutating RPCs.
5. Generated clients in CI.
""",
    ),
    skill(
        "saml-sso-notes",
        "Enterprise SAML SSO integration checklist (metadata, assertions, JIT).",
        ["auth", "enterprise"],
        ["file_read", "file_write"],
        """
## Checklist
Metadata exchange · signature validation · attribute mapping · JIT vs invite · staging IdP · session behavior.

Use mature libraries; misconfiguration is common.
""",
    ),
    skill(
        "perf-profile-cpu",
        "Capture and interpret a CPU profile to find hot functions before optimizing.",
        ["perf", "debug"],
        ["bash_exec", "file_read"],
        """
## Steps
1. Reproduce load.
2. Capture profile (pprof, py-spy, perf, Chrome CPU profile, etc.).
3. Identify top cumulative samples.
4. Optimize with measurement, not guesses.
5. Compare before/after profiles.
""",
    ),
    skill(
        "contract-test-api",
        "Add consumer/provider contract tests so API changes don't silently break clients.",
        ["testing", "api"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Detect contract-test tools/OpenAPI test usage or introduce lightweight schema tests.
2. Cover critical endpoints.
3. Run in CI on PR.
4. Fail on breaking response changes.
""",
    ),
    skill(
        "fixture-factory",
        "Create maintainable test factories/fixtures instead of brittle object literals everywhere.",
        ["testing"],
        ["file_read", "file_write"],
        """
## Steps
1. Find repeated test setup.
2. Introduce factories with overrides (factory helpers, etc. or simple helpers).
3. Keep defaults valid minimal objects.
4. Refactor a few tests to prove ergonomics.
""",
    ),
    skill(
        "snapshot-test-discipline",
        "Tame snapshot tests: reduce scope, review diffs, avoid golden files that hide bugs.",
        ["testing"],
        ["file_read", "file_write"],
        """
## Steps
1. Find large/opaque snapshots.
2. Prefer explicit assertions for logic; snapshots for stable pure serializers/UI fragments.
3. Review any snapshot update line-by-line.
4. Delete obsolete snapshots.
""",
    ),
    skill(
        "docs-api-examples",
        "Add runnable request/response examples to API docs for the hardest endpoints.",
        ["docs", "api"],
        ["file_read", "file_write"],
        """
## Steps
1. Pick public or partner-facing endpoints.
2. Examples for success + common errors.
3. Keep secrets fake.
4. Ensure examples match validation rules.
""",
    ),
    skill(
        "migration-data-backfill",
        "Plan batched data backfills that won't lock production tables.",
        ["database", "ops"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Estimate row counts.
2. Batch updates with sleeps/checkpoints.
3. Idempotent backfill script.
4. Progress metrics.
5. Verify counts; schedule during low traffic if needed.
""",
    ),
    skill(
        "feature-toggle-cleanup",
        "Find stale feature flags and remove dead code paths safely.",
        ["product", "quality"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Inventory flags and default states.
2. Identify permanently on/off.
3. Delete dead branch after confirm.
4. Remove config entries.
5. Tests still pass.
""",
    ),
]


def write_skill(name: str, desc: str, tags: list[str], tools: list[str], body: str) -> None:
    if name in BUNDLED:
        return
    d = ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    tags_y = "\n".join(f"  - {t}" for t in tags)
    tools_y = "\n".join(f"  - {t}" for t in tools)
    title = name.replace("-", " ").title()
    content = f"""---
name: {name}
version: 1.0.0
description: >
  {desc}
author: Remedy Official
license: LicenseRef-Proprietary
tags:
{tags_y}
kind: native
status: discovered
tools:
{tools_y}
metadata:
  source: library
  library_id: {name}
  official: true
  security_flags: []
---

# {title}

{body}

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
"""
    (d / "SKILL.md").write_text(content, encoding="utf-8")


def main() -> None:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description=(
            "Bulk-write official skills. DANGEROUS: overwrites skills/*/SKILL.md. "
            "Requires --force. Prefer hand-edits + build_catalog for normal releases."
        )
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Required to actually write files (safety latch).",
    )
    args = p.parse_args()
    if not args.force:
        print(
            "Refusing to run: this generator overwrites skill packs.\n"
            "Re-run with --force only if you intend a full regeneration.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    ROOT.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for name, desc, tags, tools, body in SKILLS:
        if name in BUNDLED:
            continue
        write_skill(name, desc, tags, tools, body)
        names.append(name)
    print(f"Wrote {len(names)} official skills under {ROOT}")
    print("Sample:", ", ".join(names[:8]), "...")


if __name__ == "__main__":
    main()
