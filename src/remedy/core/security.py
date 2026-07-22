"""Security hardening: input validation, path traversal guards, safe defaults.

Provides sanitization helpers used throughout Remedy to prevent common
vulnerabilities like path traversal, injection, and unsafe defaults.
"""

from __future__ import annotations

import re
from pathlib import Path

from remedy.core.errors import SecurityError

HOME_DIR = Path("~/.remedy").expanduser()
HOME_DIR.mkdir(parents=True, exist_ok=True)


MAX_FILENAME_LENGTH = 255
MAX_PATH_DEPTH = 32
VALID_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-./\\ ]+$")
VALID_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
VALID_TAG_RE = re.compile(r"^[a-zA-Z0-9_\- ]{1,50}$")
VALID_CHARACTER_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def safe_path(user_input: str, base_dir: Path | None = None) -> Path:
    """Resolve a user-supplied path safely within a base directory.

    Prevents path traversal by rejecting paths that escape base_dir.
    """
    base = (base_dir or HOME_DIR).resolve()
    candidate = (base / user_input).resolve()

    try:
        candidate.relative_to(base)
    except ValueError:
        raise SecurityError(
            f"Path traversal detected: {user_input}",
            rule="path_traversal",
            detail={"input": user_input, "base": str(base), "resolved": str(candidate)},
        )

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
    "sudo", "su", "chmod", "chown", "mkfs", "dd", "fdisk",
    "passwd", "useradd", "usermod", "groupadd",
}


_DANGEROUS_PATTERNS = [
    (r"(^|\s)(rm|del|erase)(\s|$)", "File deletion detected"),
    (r"(^|\s)format(\s|$)", "Filesystem format"),
    (r"(^|\s)shutdown(\s|$)", "System shutdown"),
    (r"(^|\s)reboot(\s|$)", "System reboot"),
    (r"2>/dev/null", "Error output suppression"),
    (r"\|\s*(sh|bash|pwsh)", "Shell pipe injection"),
    (r">\s*/dev/", "Device write"),
]


def check_dangerous_command(command: list[str]) -> str | None:
    """Check a command list for dangerous operations.

    Returns a warning string if dangerous, None if safe.
    """
    if not command:
        return None

    base = command[0].lower()
    if base in _DANGEROUS_COMMANDS:
        return f"Dangerous command: {base}"

    full = " ".join(str(a) for a in command).lower()
    for pattern, reason in _DANGEROUS_PATTERNS:
        import re
        if re.search(pattern, full):
            return f"{reason}: {full[:100]}"

    return None
