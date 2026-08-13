"""Two independent scanners for self-improve / inbound PRs.

Pass 1 — path jail, size, binaries, workflow/secret files.
Pass 2 — content heuristics for malware, exfil, and leaked credentials.

Neither pass merges anything. CI must fail closed. Owner review is still required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ALLOWED_PREFIXES = (
    "src/remedy/",
    "tests/",
)
# Explicit single files outside those trees that a self-improve PR may touch.
ALLOWED_FILES = frozenset(
    {
        "docs/SELF_INJECT.md",
    }
)
FORBIDDEN_PREFIXES = (
    ".github/",
    "desktop/src-tauri/",
    "scripts/set_tauri_signing_secrets",
)
FORBIDDEN_NAMES = frozenset(
    {
        ".pypirc",
        ".env",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        "gh_token",
        "latest.json",
    }
)
FORBIDDEN_SUFFIXES = (
    ".pem",
    ".p12",
    ".pfx",
    ".key",
    ".nupkg",
)
MAX_FILES = 8
MAX_DIFF_LINES = 400
MAX_FORK_DIFF_LINES = 200
MAX_FILE_BYTES = 200_000

_SECRET_LINE = re.compile(
    r"(?i)("
    r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----"
    r"|api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"
    r"|secret\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xai-[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|pypi-AgE[A-Za-z0-9_\-]{20,}"
    r")"
)

# Pass 2: added-line only. Independent from path/secret file checks.
_MALICE_ADDED = re.compile(
    r"(?i)("
    r"eval\s*\(\s*base64"
    r"|exec\s*\(\s*base64"
    r"|__import__\s*\(\s*['\"]socket['\"]"
    r"|reverse\s*shell"
    r"|powershell\s+-enc"
    r"|curl[^\n]{0,80}\|\s*(sh|bash)"
    r"|wget[^\n]{0,80}\|\s*(sh|bash)"
    r"|pickle\.loads\s*\("
    r"|yaml\.load\s*\((?!.*Loader)"
    r"|subprocess\.[A-Za-z]+\([^)]*shell\s*=\s*True"
    r"|ssl\._create_unverified_context"
    r"|verify\s*=\s*False"
    r"|chmod\s+777"
    r"|os\.system\s*\(\s*(['\"]curl|['\"]wget|['\"]powershell)"
    r")"
)


@dataclass
class GuardFinding:
    pass_name: str
    severity: str  # block | warn
    path: str
    message: str


@dataclass
class GuardReport:
    ok: bool
    pass_name: str
    findings: list[GuardFinding] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    from_fork: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "pass": self.pass_name,
            "from_fork": self.from_fork,
            "files": self.files,
            "findings": [
                {
                    "severity": f.severity,
                    "path": f.path,
                    "message": f.message,
                    "pass": f.pass_name,
                }
                for f in self.findings
            ],
        }


def normalize_rel(path: str) -> str | None:
    """Return a safe repo-relative path, or None if it escapes / is absolute."""
    n = (path or "").replace("\\", "/").strip()
    if not n or n.endswith("/"):
        return None
    if "\n" in n or "\r" in n or "\x00" in n:
        return None
    if n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        return None
    parts: list[str] = []
    for p in n.split("/"):
        if p in ("", "."):
            continue
        if p == ".." or p in (".git",):
            return None
        parts.append(p)
    if not parts:
        return None
    return "/".join(parts)


def _norm(path: str) -> str:
    return normalize_rel(path) or ""


def path_allowed(path: str) -> bool:
    n = normalize_rel(path)
    if not n:
        return False
    if n in ALLOWED_FILES:
        return True
    return n.startswith(ALLOWED_PREFIXES)


def path_forbidden(path: str) -> str | None:
    n = _norm(path)
    name = n.rsplit("/", 1)[-1].lower()
    if name in FORBIDDEN_NAMES:
        return f"forbidden file name {name}"
    if n.startswith(FORBIDDEN_PREFIXES):
        return "forbidden path prefix"
    for suf in FORBIDDEN_SUFFIXES:
        if n.lower().endswith(suf):
            return f"forbidden suffix {suf}"
    return None


def scan_paths(
    files: list[str],
    *,
    from_fork: bool = False,
) -> GuardReport:
    """Pass 1: who may touch what."""
    report = GuardReport(ok=True, pass_name="path_jail", from_fork=from_fork)
    seen: list[str] = []
    for raw in files:
        n = normalize_rel(raw)
        if not n:
            report.findings.append(
                GuardFinding(
                    "path_jail",
                    "block",
                    str(raw)[:160],
                    "unsafe path (absolute, .., or control chars)",
                )
            )
            continue
        if n in seen:
            continue
        seen.append(n)
        why = path_forbidden(n)
        if why:
            report.findings.append(
                GuardFinding("path_jail", "block", n, why)
            )
            continue
        if not path_allowed(n):
            report.findings.append(
                GuardFinding(
                    "path_jail",
                    "block",
                    n,
                    "path not in allowed self-improve surface "
                    "(src/remedy/, tests/, docs/SELF_INJECT.md)",
                )
            )
    report.files = seen
    if len(seen) > MAX_FILES:
        report.findings.append(
            GuardFinding(
                "path_jail",
                "block",
                "",
                f"too many files ({len(seen)} > {MAX_FILES})",
            )
        )
    report.ok = not any(f.severity == "block" for f in report.findings)
    return report


def scan_diff_secrets_and_size(
    diff: str,
    *,
    from_fork: bool = False,
) -> GuardReport:
    """Pass 1b: size + credential leakage in the unified diff."""
    report = GuardReport(ok=True, pass_name="secrets_size", from_fork=from_fork)
    added = [
        ln
        for ln in (diff or "").splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    removed = [
        ln
        for ln in (diff or "").splitlines()
        if ln.startswith("-") and not ln.startswith("---")
    ]
    n = len(added) + len(removed)
    cap = MAX_FORK_DIFF_LINES if from_fork else MAX_DIFF_LINES
    if n > cap:
        report.findings.append(
            GuardFinding(
                "secrets_size",
                "block",
                "",
                f"diff too large ({n} lines > {cap})",
            )
        )
    for ln in added:
        if _SECRET_LINE.search(ln):
            report.findings.append(
                GuardFinding(
                    "secrets_size",
                    "block",
                    "",
                    "possible secret or credential in added line",
                )
            )
            break
    if "\0" in (diff or ""):
        report.findings.append(
            GuardFinding("secrets_size", "block", "", "binary patch rejected")
        )
    report.ok = not any(f.severity == "block" for f in report.findings)
    return report


def scan_added_behavior(diff: str, *, from_fork: bool = False) -> GuardReport:
    """Pass 2: independent malice / unsafe-API scan on added lines only."""
    report = GuardReport(ok=True, pass_name="behavior", from_fork=from_fork)
    current = ""
    for ln in (diff or "").splitlines():
        if ln.startswith("+++ b/"):
            current = ln[6:].strip()
            continue
        if not ln.startswith("+") or ln.startswith("+++"):
            continue
        body = ln[1:]
        if _MALICE_ADDED.search(body):
            report.findings.append(
                GuardFinding(
                    "behavior",
                    "block",
                    current,
                    f"suspicious added code: {body.strip()[:160]}",
                )
            )
    report.ok = not any(f.severity == "block" for f in report.findings)
    return report


def run_both_passes(
    files: list[str],
    diff: str,
    *,
    from_fork: bool = False,
) -> dict[str, Any]:
    """Run both scanners. ``ok`` is true only if every pass is green."""
    p1 = scan_paths(files, from_fork=from_fork)
    p1b = scan_diff_secrets_and_size(diff, from_fork=from_fork)
    p2 = scan_added_behavior(diff, from_fork=from_fork)
    findings = p1.findings + p1b.findings + p2.findings
    ok = p1.ok and p1b.ok and p2.ok
    return {
        "ok": ok,
        "from_fork": from_fork,
        "files": p1.files,
        "passes": {
            "path_jail": p1.to_public(),
            "secrets_size": p1b.to_public(),
            "behavior": p2.to_public(),
        },
        "blocks": [
            f.message
            for f in findings
            if f.severity == "block"
        ],
    }


def collect_git_diff(
    repo: str | Path,
    *,
    base: str | None = None,
) -> tuple[list[str], str]:
    """Return (paths, unified diff) vs *base*...HEAD or vs HEAD (worktree)."""
    import subprocess

    root = Path(repo)
    if base:
        files_cmd = ["git", "-C", str(root), "diff", "--name-only", f"{base}...HEAD"]
        diff_cmd = ["git", "-C", str(root), "diff", f"{base}...HEAD"]
    else:
        files_cmd = ["git", "-C", str(root), "diff", "--name-only", "HEAD"]
        diff_cmd = ["git", "-C", str(root), "diff", "HEAD"]
    files_p = subprocess.run(
        files_cmd, capture_output=True, text=True, timeout=30, check=False
    )
    diff_p = subprocess.run(
        diff_cmd, capture_output=True, text=True, timeout=30, check=False
    )
    files = [ln.strip() for ln in (files_p.stdout or "").splitlines() if ln.strip()]
    return files, diff_p.stdout or ""
