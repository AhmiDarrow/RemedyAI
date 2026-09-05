"""Repo sanitization gate — refuse to ship secrets, private docs, or junk.

Runs in public CI and ``scripts/prepush.py`` (checks lane) so poor judgment
never leaves the machine. Structural checks on the tracked tree; credential
scan is high-signal only (private keys + live-looking tokens), with explicit
fakes / detectors allowlisted.

Usage:
    python scripts/check_sanitize.py
    uv run python scripts/check_sanitize.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Must match .gitignore ``!docs/ThatFile.md`` exceptions and AGENTS.md allowlist.
PUBLIC_TOP_LEVEL_DOCS = frozenset(
    {
        "ARCHITECTURE.md",
        "DESKTOP.md",
        "REMEDY_PERSONA.md",
        "SKILL_LIFECYCLE.md",
        "TELEPHONY.md",
        "TELEPHONY_TERMS.md",
        "TERMS.md",
        "THIRD_PARTY.md",
        "USAGE.md",
        "WEB_ETIQUETTE.md",
        "WINDOWS_SIGNING.md",
    }
)

FORBIDDEN_TRACKED_SUFFIXES = (
    ".pem",
    ".p12",
    ".pfx",
    ".keystore",
)
FORBIDDEN_TRACKED_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
        "credentials.json",
        "service-account.json",
    }
)
FORBIDDEN_TRACKED_PREFIXES = (
    "docs/UNRELEASED.md",
    "scripts/_",
    "community/",
)

# High-signal live credential shapes (not documentation of the prefixes).
_LIVE_TOKEN_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    # OpenAI-style project keys / long sk- tokens (exclude short detector samples).
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-ant-api\d{2}-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-(?!ant-|or-|proj-|live|test)[A-Za-z0-9]{40,}\b"),
)

_FAKE_HINT = re.compile(
    r"(?i)not-a-|example|placeholder|unit-test|dummy|fake|your[_-]?|"
    r"xxx+|replace[_-]?me|sample|test-only|signature-not-real|"
    r"local-skip|fallback-bind|exclude-token|poe-unit-test"
)

_SKIP_SECRET_SCAN_GLOBS = (
    # Pattern catalogs and redactors deliberately embed secret-shaped samples.
    "src/remedy/core/metabolism/redact.py",
    "src/remedy/core/provider_sanitize.py",
    "src/remedy/core/self_inject_guard.py",
    "scripts/check_sanitize.py",
    "scripts/self_improve_security_scan.py",
    "tests/",
    "native/go/secret/",
)

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".go",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".json",
        ".toml",
        ".md",
        ".yml",
        ".yaml",
        ".txt",
        ".rs",
        ".zig",
        ".ps1",
        ".sh",
        ".env",
        ".cfg",
        ".ini",
        ".html",
        ".css",
        ".svg",
    }
)


def _git_ls_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    raw = proc.stdout.split(b"\0")
    out: list[str] = []
    for b in raw:
        if not b:
            continue
        out.append(b.decode("utf-8", errors="replace").replace("\\", "/"))
    return out


def _unreleased_body(changelog: str) -> str:
    m = re.search(
        r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)",
        changelog,
        flags=re.MULTILINE | re.DOTALL,
    )
    return (m.group(1) if m else "").strip()


def _gitignore_doc_allowlist() -> set[str]:
    """Parse ``!docs/Name.md`` exceptions from .gitignore."""
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    found: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("!docs/") and s.endswith(".md") and "/" not in s[6:]:
            found.add(s[len("!docs/") :])
    return found


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else ""


def check_versions(errors: list[str]) -> None:
    ver = _pyproject_version()
    if not _SEMVER.match(ver):
        errors.append(f"pyproject.toml version {ver!r} is not X.Y.Z semver")
        return
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_version.py"), "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        errors.append("version surfaces are not aligned:\n" + (proc.stdout or proc.stderr).strip())


def check_changelog_unreleased(errors: list[str]) -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    body = _unreleased_body(text)
    if body:
        errors.append(
            "CHANGELOG.md [Unreleased] is not empty — move notes to "
            "docs/UNRELEASED.md (clone-only) before push:\n"
            + "\n".join(f"  {ln}" for ln in body.splitlines()[:12])
        )


def check_docs_allowlist(tracked: list[str], errors: list[str]) -> None:
    gi = _gitignore_doc_allowlist()
    if gi != PUBLIC_TOP_LEVEL_DOCS:
        missing = sorted(PUBLIC_TOP_LEVEL_DOCS - gi)
        extra = sorted(gi - PUBLIC_TOP_LEVEL_DOCS)
        if missing:
            errors.append(
                ".gitignore is missing public docs allowlist entries: "
                + ", ".join(missing)
            )
        if extra:
            errors.append(
                ".gitignore has docs exceptions not in the public allowlist: "
                + ", ".join(extra)
            )
    tracked_top = {
        p[len("docs/") :]
        for p in tracked
        if p.startswith("docs/") and p.count("/") == 1 and p.endswith(".md")
    }
    unexpected = sorted(tracked_top - PUBLIC_TOP_LEVEL_DOCS)
    if unexpected:
        errors.append(
            "tracked top-level docs/*.md outside the public allowlist "
            "(session notes / essays must stay clone-only):\n  "
            + "\n  ".join(unexpected)
        )
    missing_tracked = sorted(PUBLIC_TOP_LEVEL_DOCS - tracked_top)
    if missing_tracked:
        errors.append(
            "public allowlist docs are not tracked (restore or update allowlist):\n  "
            + "\n  ".join(missing_tracked)
        )


def check_forbidden_paths(tracked: list[str], errors: list[str]) -> None:
    bad: list[str] = []
    for path in tracked:
        name = path.rsplit("/", 1)[-1]
        lower = path.lower()
        if name in FORBIDDEN_TRACKED_NAMES or name.startswith(".env."):
            bad.append(path)
            continue
        if any(lower.endswith(suf) for suf in FORBIDDEN_TRACKED_SUFFIXES):
            bad.append(path)
            continue
        if any(path.startswith(pref) or path == pref.rstrip("/") for pref in FORBIDDEN_TRACKED_PREFIXES):
            bad.append(path)
            continue
        # Accidental sidecars / installers in the source tree.
        if path.endswith((".exe", ".dll", ".so", ".dylib")) and not path.startswith(
            ("desktop/bin/", "desktop/src-tauri/", "native/zig/", "android/")
        ):
            # Allow listed build outputs only under those trees; nothing under src/ or scripts/.
            if path.startswith(("src/", "scripts/", "tests/", "docs/", "native/go/cmd/")):
                bad.append(path)
    if bad:
        errors.append(
            "forbidden paths are tracked (secrets, private docs, or junk binaries):\n  "
            + "\n  ".join(bad[:40])
        )


def _skip_secret_scan(path: str) -> bool:
    posix = path.replace("\\", "/")
    for g in _SKIP_SECRET_SCAN_GLOBS:
        if g.endswith("/"):
            if posix.startswith(g):
                return True
        elif posix == g or posix.endswith("/" + g):
            return True
    return False


def check_live_secrets(tracked: list[str], errors: list[str]) -> None:
    hits: list[str] = []
    for path in tracked:
        if _skip_secret_scan(path):
            continue
        suffix = Path(path).suffix.lower()
        if suffix and suffix not in _TEXT_SUFFIXES:
            continue
        fp = ROOT / path
        if not fp.is_file():
            continue
        try:
            # Bound read — huge generated assets are skipped by suffix anyway.
            data = fp.read_bytes()[:2_000_000]
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if _FAKE_HINT.search(line):
                continue
            # Detectors / catalogs often list prefixes without a live token.
            if re.search(r"(?i)(startswith|prefix|pattern|regex|re\.compile|match)", line):
                if not re.search(r"-----BEGIN ", line):
                    continue
            for cre in _LIVE_TOKEN_RES:
                m = cre.search(line)
                if not m:
                    continue
                snippet = m.group(0)
                if _FAKE_HINT.search(snippet):
                    continue
                hits.append(f"{path}:{i}: {snippet[:48]}…")
                break
        if len(hits) >= 25:
            break
    if hits:
        errors.append(
            "possible live credentials in tracked files "
            "(use obvious fakes in tests; never commit owner secrets):\n  "
            + "\n  ".join(hits)
        )


def check_latest_json_shape(errors: list[str]) -> None:
    path = ROOT / "scripts" / "latest.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    ver = str(data.get("version", "")).lstrip("v")
    if not _SEMVER.match(ver):
        errors.append(f"scripts/latest.json version {data.get('version')!r} is not vX.Y.Z")
    for name, plat in (data.get("platforms") or {}).items():
        url = str(plat.get("url") or "")
        if "Remedy_Desktop_" in url and "Remedy.Desktop_" not in url:
            errors.append(f"scripts/latest.json platforms.{name}.url uses Remedy_Desktop_ (need dots)")
        if ver and f"Remedy.Desktop_{ver}_" not in url and url:
            errors.append(
                f"scripts/latest.json platforms.{name}.url does not match version {ver}"
            )


def main() -> int:
    errors: list[str] = []
    tracked = _git_ls_files()
    check_versions(errors)
    check_changelog_unreleased(errors)
    check_docs_allowlist(tracked, errors)
    check_forbidden_paths(tracked, errors)
    check_live_secrets(tracked, errors)
    check_latest_json_shape(errors)

    if errors:
        print("sanitize: FAIL")
        for err in errors:
            print()
            print(err)
        print(
            "\nsanitize: refuse to push until the tree is clean. "
            "Fix the findings above (or update the allowlist deliberately)."
        )
        return 1
    print("sanitize: OK — versions, docs allowlist, secrets, and junk paths clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
