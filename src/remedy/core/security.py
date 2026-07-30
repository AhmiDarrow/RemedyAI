"""Security hardening: input validation, path traversal guards, safe defaults.

Provides sanitization helpers used throughout Remedy to prevent common
vulnerabilities like path traversal, injection, and unsafe defaults.
"""

from __future__ import annotations

import re
from pathlib import Path

from remedy.core.errors import SecurityError

_HOME_DIR: Path | None = None


def get_home_dir() -> Path:
    """Return ~/.remedy, creating it on first use (not at import time)."""
    global _HOME_DIR
    if _HOME_DIR is None:
        _HOME_DIR = Path("~/.remedy").expanduser()
        _HOME_DIR.mkdir(parents=True, exist_ok=True)
    return _HOME_DIR


MAX_FILENAME_LENGTH = 255
MAX_PATH_DEPTH = 32
VALID_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-./\\ ]+$")
VALID_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
VALID_TAG_RE = re.compile(r"^[a-zA-Z0-9_\- ]{1,50}$")
VALID_CHARACTER_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_protected_secret_path(path: Path | str | None) -> bool:
    """True when *path* resolves under a Remedy auth secrets directory.

    Always blocks ``~/.remedy/auth/**`` (and ``$REMEDY_HOME/auth/**``) even
    under ``access_scope=full``. Prevents file tools / session import from
    reading provider keys, OAuth tokens, or the local API bearer — including
    via junctions/symlinks that resolve into the auth tree.
    """
    if path is None:
        return False
    try:
        p = Path(path).expanduser()
        try:
            p = p.resolve(strict=False)
        except (OSError, RuntimeError):
            p = p.absolute()
    except (TypeError, ValueError, RuntimeError):
        return False

    parts_lower = [str(x).lower() for x in p.parts]
    # .../.remedy/auth/...  (default layout on any drive)
    for i, part in enumerate(parts_lower):
        if part == ".remedy" and i + 1 < len(parts_lower) and parts_lower[i + 1] == "auth":
            return True

    # Do not call secret_store.auth_dir() here — that mkdir's on import/check.
    candidates: list[Path] = []
    try:
        import os

        env_home = (os.environ.get("REMEDY_HOME") or "").strip()
        if env_home:
            candidates.append(Path(env_home).expanduser() / "auth")
    except Exception:
        pass
    try:
        candidates.append(Path.home() / ".remedy" / "auth")
    except Exception:
        pass

    for auth in candidates:
        try:
            a = auth.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            try:
                a = auth.expanduser().absolute()
            except Exception:
                continue
        try:
            if p == a or p.is_relative_to(a):
                return True
        except (ValueError, TypeError, OSError):
            try:
                p.relative_to(a)
                return True
            except Exception:
                continue
    return False


def refuse_protected_secret_path(path: Path | str | None) -> None:
    """Raise SecurityError when *path* is a protected secrets location."""
    if is_protected_secret_path(path):
        raise SecurityError(
            "Path is a protected Remedy secrets location (auth/)",
            rule="protected_secret_path",
            detail={"path": str(path)},
        )


def safe_path(user_input: str, base_dir: Path | None = None) -> Path:
    """Resolve a user-supplied path safely within a base directory.

    Prevents path traversal by rejecting paths that escape base_dir.
    """
    base = Path(base_dir or get_home_dir()).resolve()
    candidate = (base / user_input).resolve()

    try:
        candidate.relative_to(base)
    except ValueError as err:
        raise SecurityError(
            f"Path traversal detected: {user_input}",
            rule="path_traversal",
            detail={"input": user_input, "base": str(base), "resolved": str(candidate)},
        ) from err

    if len(candidate.parts) > len(base.parts) + MAX_PATH_DEPTH:
        raise SecurityError(
            f"Path too deep: {user_input}",
            rule="max_path_depth",
            detail={"depth": len(candidate.parts) - len(base.parts)},
        )

    if not VALID_PATH_RE.match(str(candidate.relative_to(base))):
        raise SecurityError(
            f"Invalid path characters: {user_input}",
            rule="path_chars",
        )

    refuse_protected_secret_path(candidate)
    return candidate


def validate_skill_name(name: str) -> str:
    """Validate a skill name string."""
    name = name.strip().lower()
    if not name:
        raise SecurityError("Empty skill name", rule="empty_name")
    if len(name) > 100:
        raise SecurityError("Skill name too long", rule="name_length", detail={"name": name[:50]})
    if not VALID_SKILL_NAME_RE.match(name):
        raise SecurityError(
            f"Invalid skill name: {name}",
            rule="skill_name_chars",
        )
    return name


def validate_tags(tags: list[str]) -> list[str]:
    """Validate and sanitize tags."""
    cleaned: list[str] = []
    seen = set()
    for tag in tags[:20]:
        tag = tag.strip().lower()
        if not tag or tag in seen:
            continue
        if not VALID_TAG_RE.match(tag):
            raise SecurityError(
                f"Invalid tag: {tag}",
                rule="tag_chars",
            )
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def validate_uuid(value: str, context: str = "id") -> str:
    """Validate a UUID string."""
    value = value.strip().lower()
    if not VALID_CHARACTER_ID_RE.match(value):
        raise SecurityError(
            f"Invalid UUID for {context}: {value}",
            rule="invalid_uuid",
            detail={"context": context},
        )
    return value


def sanitize_sql_identifier(name: str, max_len: int = 64) -> str:
    """Sanitize a string for use as a SQL identifier (table/column name)."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "", name)
    if not sanitized:
        raise SecurityError("Empty SQL identifier", rule="sql_identifier")
    if sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized[:max_len].lower()


def sanitize_search_query(query: str, max_length: int = 1000) -> str:
    """Sanitize a full-text search query."""
    if not isinstance(query, str):
        raise SecurityError("Search query must be a string", rule="type_check")

    query = query.strip()
    if not query:
        raise SecurityError("Empty search query", rule="empty_query")

    if len(query) > max_length:
        raise SecurityError(
            f"Search query too long ({len(query)} > {max_length})",
            rule="query_length",
        )

    # Strip characters that could break FTS5 MATCH
    cleaned = re.sub(r'["*]', "", query)
    if not cleaned.strip():
        raise SecurityError(
            "Search query contains only invalid characters",
            rule="query_chars",
        )

    return cleaned


def validate_memory_entry_content(content: str, max_length: int = 100_000) -> str:
    if len(content) > max_length:
        raise SecurityError(
            f"Memory entry too long ({len(content)} > {max_length})",
            rule="content_length",
        )
    return content


def validate_execution_command(command: list[str]) -> list[str]:
    if not isinstance(command, list) or not command:
        raise SecurityError("Command must be a non-empty list", rule="command_type")
    for i, arg in enumerate(command):
        if not isinstance(arg, str):
            raise SecurityError(f"Command argument {i} must be a string", rule="arg_type")
    return command


_DANGEROUS_COMMANDS = {
    # Unix privilege / disk
    "sudo", "su", "chmod", "chown", "mkfs", "dd", "fdisk",
    "passwd", "useradd", "usermod", "groupadd",
    # Windows system / privilege
    "reg", "takeown", "icacls", "net", "wmic", "sc", "schtasks",
    "vssadmin", "bcdedit", "wevtutil", "diskpart", "cipher",
    "format", "shutdown", "reboot",
}

# Hard block — true wipe / privilege / injection class (always deny)
# Precompiled: check_dangerous_command runs on every bash_exec / shell gate.
_HARD_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.I), reason)
    for p, reason in (
        (r"(^|[\s;&|])format(\s|$)", "Filesystem format"),
        (r"(^|[\s;&|])shutdown(\s|$)", "System shutdown"),
        (r"(^|[\s;&|])reboot(\s|$)", "System reboot"),
        # Unix recursive wipe of root/home
        (
            r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+(/|~|\$home)",
            "Recursive delete of system path",
        ),
        (r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)*(/|~|\$home|c:\\)", "Recursive delete of system path"),
        # Windows recursive wipe (forced flags)
        (r"del\s+/[fqs]+\s+", "Windows forced recursive delete"),
        (r"rmdir\s+/s(\s+/q)?\s+", "Windows recursive rmdir"),
        (r"rd\s+/s(\s+/q)?\s+", "Windows recursive rd"),
        (r"\|\s*(sh|bash|pwsh|powershell|cmd)(\s|$)", "Shell pipe injection"),
        (r">\s*/dev/", "Device write"),
        (r"invoke-expression|iex\s+", "PowerShell Invoke-Expression"),
        (r"remove-item\s+.*-recurse", "PowerShell recursive delete"),
        # Bare delete tools only when clearly recursive/forced — not every "del" substring in prose
        (r"(^|[\s;&|])(rm|del|erase)\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+", "Forced file deletion"),
        # Encoded payloads hide intent (complement shell write jail)
        (r"(?:^|[\s;&|])-(?:encodedcommand|enc|ec)\b", "PowerShell EncodedCommand"),
        # Short -e after powershell/pwsh only (bare -e matches grep -e / set -e — too noisy)
        (
            r"(?:powershell|pwsh)(?:\.exe)?(?:\s+[/\-\w]+)*\s+-e(?:\s|$|=)",
            "PowerShell EncodedCommand (-e)",
        ),
        (r"\badd-type\b[^\n]*-typedefinition\b", "PowerShell Add-Type injection"),
        # Download-and-drop (path often outside project; complement shell write jail)
        (r"\bcertutil\b[^\n]*-urlcache\b", "certutil URL cache download"),
        (r"\b(?:system\.)?net\.webclient\b[^\n]*download", "WebClient download"),
        (r"\bdownloadfile\s*\(", "DownloadFile invoke"),
    )
]

# Soft signals — used by callers that want ask-mode hints; not hard-blocked here
# (start-process, $(), backticks are common in legitimate Windows/dev scripts)
_SOFT_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.I), reason)
    for p, reason in (
        (r"start-process\s+", "Process launch"),
        (r"`[^`]+`", "Command substitution"),
        (r"\$\([^)]+\)", "Command substitution"),
        (r"(^|[\s;&|])(rm|del|erase|rmdir|rd)(\s|$)", "File deletion detected"),
    )
]

# Host self-preservation — Tauri projects often share the binary name ``app.exe``
# with Remedy Desktop. Indiscriminate kills take out the agent mid-turn.
# Path-scoped kills (command mentions SecretFolder / project filter) are allowed.
_SELF_KILL_ALWAYS_BLOCK: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.I), reason)
    for p, reason in (
        # Never kill the API brain
        (
            r"taskkill\s+[^\n]*/im\s+remedy(\.exe)?",
            "Killing remedy.exe (host API)",
        ),
        (
            r"stop-process[^\n]*-name\s+['\"]?remedy(\.exe)?['\"]?",
            "Stopping process named remedy (host API)",
        ),
        (
            r"get-process\s+[^\n]*remedy[^\n]*stop-process|stop-process[^\n]*get-process[^\n]*remedy",
            "Pipeline stop of remedy process",
        ),
        # Freeing Remedy's API port
        (
            r"(localport|local.?port)\s*[:=]?\s*7400[^\n]{0,200}(stop-process|taskkill|kill)",
            "Killing process on port 7400 (Remedy API)",
        ),
        (
            r"(stop-process|taskkill|kill)[^\n]{0,200}(localport|local.?port)\s*[:=]?\s*7400",
            "Killing process on port 7400 (Remedy API)",
        ),
        (
            r"remedy_serve\.lock|remedy serve.*stop|stop[^\n]*remedy serve",
            "Stopping remedy serve",
        ),
    )
]

# Indiscriminate Tauri / app.exe kills — blocked unless command scopes to a
# project path (e.g. SecretFolder in CommandLine/Path filter).
_SELF_KILL_APP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.I), reason)
    for p, reason in (
        (
            r"taskkill\s+[^\n]*/im\s+app(\.exe)?",
            "taskkill /IM app.exe kills ALL Tauri apps including Remedy Desktop",
        ),
        (
            r"stop-process\s+[^\n]*-name\s+['\"]?app(\.exe)?['\"]?",
            "Stop-Process -Name app kills Remedy Desktop (same default Tauri name)",
        ),
        (
            r"get-process\s+['\"]?app(\.exe)?['\"]?[^\n]{0,80}stop-process",
            "Get-Process app | Stop-Process kills Remedy Desktop",
        ),
        (
            r"get-process\s+[^\n]*\|\s*[^\n]*stop-process[^\n]*\bapp\b",
            "Filtered Stop-Process still targeting bare app name",
        ),
        (
            r"stop-process\s+[^\n]*\(?(get-process\s+app)",
            "Stop-Process (Get-Process app) kills Remedy Desktop",
        ),
        # "kill every tauri/app without path scope"
        (
            r"(stop-process|taskkill)[^\n]{0,120}(processname\s*-eq\s*['\"]app['\"]|\.name\s*-eq\s*['\"]app['\"])",
            "Stopping all processes named app (includes Remedy Desktop)",
        ),
    )
]

# If any of these appear, path-scoped project kill is likely intentional.
_SELF_KILL_PROJECT_SCOPE_RE = re.compile(
    r"(?i)("
    r"secretfolder|secretsticky|remedyai[/\\]desktop"
    r"|commandline\s*-match|path\s*-match|\.path\s*-match"
    r"|where-object[^\n]{0,80}(secretfolder|secretsticky|project)"
    r"|filter\s+[^\n]{0,40}(secretfolder|secretsticky)"
    r")"
)


def check_host_self_kill(command: list[str] | str) -> str | None:
    """Block shell that would kill Remedy Desktop / API (shared ``app.exe`` footgun).

    Returns a reason string if blocked, else None.
    Path-scoped kills that mention another project (e.g. SecretFolder) are allowed
    for ``app.exe`` — but never for remedy.exe / port 7400.
    """
    if isinstance(command, list):
        if not command:
            return None
        full = " ".join(str(a) for a in command)
    else:
        full = str(command or "")
    if not full.strip():
        return None

    for pattern, reason in _SELF_KILL_ALWAYS_BLOCK:
        if pattern.search(full):
            return (
                f"{reason}. Do not stop the host agent. "
                "Target only the project app by full Path/CommandLine filter "
                "(e.g. SecretFolder), never bare app.exe or port 7400."
            )

    scoped = bool(_SELF_KILL_PROJECT_SCOPE_RE.search(full))
    if not scoped:
        for pattern, reason in _SELF_KILL_APP_PATTERNS:
            if pattern.search(full):
                return (
                    f"{reason}. Use a Path/CommandLine filter for the project "
                    r'(e.g. Where-Object { $_.Path -match "SecretFolder" }) '
                    "— never Get-Process app | Stop-Process."
                )
    return None


def check_dangerous_command(command: list[str]) -> str | None:
    """Hard security gate for destructive / privilege operations.

    Returns a warning string if the command must be blocked, else None.
    Soft risks (Start-Process, $(), simple del) are intentionally not hard-blocked
    so normal Windows/dev inspection works; approval mode still covers bash_exec.
    """
    if not command:
        return None

    base = Path(str(command[0])).name.lower()
    # strip extension on Windows
    if base.endswith(".exe"):
        base = base[:-4]
    if base in _DANGEROUS_COMMANDS:
        return f"Dangerous command: {base}"

    full = " ".join(str(a) for a in command).lower()
    for pattern, reason in _HARD_DANGEROUS_PATTERNS:
        if pattern.search(full):
            return f"{reason}: {full[:100]}"

    # Self-preservation (Tauri app.exe / remedy serve) — hard block
    host_kill = check_host_self_kill(command)
    if host_kill:
        return host_kill

    return None


def check_soft_dangerous_command(command: list[str]) -> str | None:
    """Advisory risk signal (not a hard block). For logging / future ask-mode."""
    if not command:
        return None
    if check_dangerous_command(command):
        return None  # hard already covers it
    full = " ".join(str(a) for a in command).lower()
    for pattern, reason in _SOFT_DANGEROUS_PATTERNS:
        if pattern.search(full):
            return f"{reason}: {full[:100]}"
    return None
