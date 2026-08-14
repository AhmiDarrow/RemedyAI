"""Classify host/shell failures so the model gets a rewrite, not a wall of stderr."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PROMPT_MARKERS = (
    "password:",
    "[y/n]",
    "(y/n)",
    "are you sure",
    "press any key",
    "enter passphrase",
    "overwrite?",
    "confirm",
)


@dataclass
class HostDiagnosis:
    code: str
    message: str
    rewritten: str = ""
    hint: str = ""
    notes: list[str] = field(default_factory=list)

    def format_block(self) -> str:
        lines = [f"HOST_DIAG {self.code}", self.message]
        if self.rewritten:
            lines.append(f"rewritten: {self.rewritten}")
        if self.hint:
            lines.append(f"hint: {self.hint}")
        for n in self.notes:
            lines.append(f"note: {n}")
        return "\n".join(lines)


def diagnose_host_failure(
    command: str,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
    translated: str = "",
    timed_out: bool = False,
    host: str = "cmd",
) -> HostDiagnosis:
    """Return a classified diagnosis for a failed host command."""
    blob = f"{stderr or ''}\n{stdout or ''}"
    low = blob.lower()
    cmd = (command or "").strip()

    if timed_out:
        if any(m in low for m in _PROMPT_MARKERS) or _looks_interactive(cmd):
            return HostDiagnosis(
                code="HOST_INTERACTIVE",
                message="Command waited on an interactive prompt and was killed.",
                hint="Use non-interactive flags (-y, --yes, --noconfirm) or host_script.",
            )
        return HostDiagnosis(
            code="HOST_TIMEOUT",
            message="Command timed out.",
            hint="Raise timeout_seconds, or run a narrower command.",
        )

    if (
        "is not recognized as an internal or external command" in low
        or "command not found" in low
        or re.search(r"^'[^\']+' is not recognized", blob, re.I | re.M)
    ):
        missing = _extract_missing(blob, cmd)
        hint = "Use host_which to resolve the binary, or host_run with a full path."
        if missing in {"grep", "head", "tail", "cat", "ls", "rm", "mkdir", "find", "test"}:
            hint = (
                f"'{missing}' is POSIX. Prefer host_mkdir / host_run / repo_search, "
                "or let the host bridge rewrite the command."
            )
        return HostDiagnosis(
            code="HOST_NOT_FOUND",
            message=f"Command not found on this host: {missing or 'unknown'}.",
            rewritten=translated or "",
            hint=hint,
        )

    if "positional parameter cannot be found" in low or "a parameter cannot be found" in low:
        return HostDiagnosis(
            code="HOST_DIALECT",
            message="PowerShell rejected POSIX flags (often mkdir -p / rm -rf).",
            rewritten=translated or "",
            hint="Do not wrap this in powershell.exe. Use host_mkdir or bash_exec (cmd host).",
        )

    if "parsererror" in low or "missing closing" in low or "unexpected token" in low:
        return HostDiagnosis(
            code="HOST_QUOTING",
            message="The host shell could not parse the command (quoting).",
            hint="Prefer host_run(argv=[...]) or host_script — never nest quotes in a string.",
        )

    if "execution of scripts is disabled" in low or "running scripts is disabled" in low:
        return HostDiagnosis(
            code="HOST_POLICY",
            message="PowerShell script execution policy blocked the file.",
            hint="Host bridge runs pwsh -File with -NoProfile. Use host_script(lang=pwsh).",
        )

    if exit_code != 0 and translated and translated != cmd:
        return HostDiagnosis(
            code="HOST_TRANSLATED_FAIL",
            message="Translated POSIX command still failed.",
            rewritten=translated,
            hint="Read stderr, or switch to host_run(argv=...) / host_script.",
        )

    if exit_code != 0:
        return HostDiagnosis(
            code="HOST_EXIT",
            message=f"exit_code={exit_code} on host={host}.",
            rewritten=translated if translated and translated != cmd else "",
            hint="Read stderr, fix flags/paths/cwd, or use a structured host_* tool.",
        )

    return HostDiagnosis(code="HOST_OK", message="ok")


def _looks_interactive(command: str) -> bool:
    low = (command or "").lower()
    return any(
        tok in low
        for tok in (
            "read-host",
            "pause",
            "more.com",
            " ssh ",
            "scp ",
            "vim ",
            "nano ",
            "less ",
        )
    )


def _extract_missing(blob: str, command: str) -> str:
    m = re.search(r"'([^']+)' is not recognized", blob)
    if m:
        return m.group(1)
    m = re.search(r"\b(\S+): (?:command )?not found", blob, re.I)
    if m:
        return m.group(1)
    tok = (command or "").strip().split(None, 1)
    return tok[0] if tok else ""
