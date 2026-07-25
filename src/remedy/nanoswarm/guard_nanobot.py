"""Guard nanobot — soft risk scoring for shell/write/skill actions.

Does not block (security hard-blocks stay in check_dangerous_command).
Enriches approval reasons and surfaces risk level for Full+ / Continuity UI.
"""

from __future__ import annotations

import re
from typing import Any

# (pattern, points, label)
_RISK_RULES: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*r|-r|--recursive)", re.I), 40, "recursive delete"),
    (re.compile(r"\b(del|erase)\s+/[sqf]", re.I), 35, "forced delete"),
    (re.compile(r"\b(rmdir|rd)\s+/s", re.I), 35, "recursive rmdir"),
    (re.compile(r"Remove-Item.{0,60}(-Recurse|-Force)", re.I), 35, "PowerShell recursive remove"),
    (re.compile(r"\bgit\s+(push\s+--force|reset\s+--hard|clean\s+-fd)", re.I), 30, "destructive git"),
    (re.compile(r"\b(drop\s+database|truncate\s+table|delete\s+from)\b", re.I), 40, "destructive SQL"),
    (re.compile(r"\b(format|diskpart|cipher\s+/w)\b", re.I), 50, "disk destroy"),
    (re.compile(r"\b(shutdown|reboot|restart-computer)\b", re.I), 45, "system power"),
    (re.compile(r"\b(reg\s+delete|takeown|icacls)\b", re.I), 35, "Windows ACL/registry"),
    (re.compile(r"\b(curl|wget|iwr|invoke-webrequest).{0,40}\|\s*(sh|bash|pwsh|iex)", re.I), 45, "pipe remote to shell"),
    (re.compile(r"\b(invoke-expression|iex)\b", re.I), 40, "dynamic code exec"),
    (re.compile(r"\b(npm\s+publish|pip\s+upload|twine\s+upload)\b", re.I), 25, "publish package"),
    (re.compile(r"\b(chmod\s+777|chown\s+root)\b", re.I), 25, "privilege widen"),
    (re.compile(r"[;&|`].{0,20}(rm|del|format)\b", re.I), 20, "chained destructive"),
    (re.compile(r"\b(C:\\Windows|C:\\Program Files|/etc|/usr)\b", re.I), 20, "system path"),
]

_WRITE_SENSITIVE = re.compile(
    r"(?i)(\.env|credentials|secret|id_rsa|\.pem|config\.toml|auth[/\\])"
)


class GuardNanobot:
    """Score tool risk; suggest clearer approval reasons."""

    def __init__(self) -> None:
        self.assessments = 0
        self.last: dict[str, Any] | None = None

    def assess(
        self,
        *,
        tool_name: str = "",
        command: str = "",
        path: str = "",
        content_preview: str = "",
    ) -> dict[str, Any]:
        tool = (tool_name or "").strip().lower()
        cmd = (command or path or "").strip()
        score = 0
        labels: list[str] = []

        if tool in ("bash_exec", "shell", "run"):
            score += 15
            labels.append("shell")
        elif tool in ("file_write", "write"):
            score += 10
            labels.append("write")
        elif tool in ("skill_run", "skill_script"):
            score += 12
            labels.append("skill script")

        blob = f"{cmd}\n{content_preview or ''}"
        for pat, pts, label in _RISK_RULES:
            if pat.search(blob):
                score += pts
                labels.append(label)

        if tool == "file_write" and _WRITE_SENSITIVE.search(cmd or path or ""):
            score += 25
            labels.append("sensitive path")

        score = min(100, score)
        if score >= 70:
            level = "critical"
        elif score >= 40:
            level = "high"
        elif score >= 20:
            level = "medium"
        else:
            level = "low"

        reason = None
        if level in ("high", "critical") or tool in ("bash_exec", "file_write", "skill_run"):
            uniq = []
            for lb in labels:
                if lb not in uniq:
                    uniq.append(lb)
            reason = f"Guard {level} risk ({score}): " + ", ".join(uniq[:5])

        out = {
            "bot": "guard",
            "tool_name": tool,
            "score": score,
            "level": level,
            "labels": labels[:8],
            "reason": reason,
            "requires_ask_hint": level in ("medium", "high", "critical")
            or tool in ("bash_exec", "file_write", "skill_run"),
        }
        self.assessments += 1
        self.last = out
        return out

    def enrich_ask_reason(
        self,
        base_reason: str | None,
        *,
        tool_name: str = "",
        command: str = "",
        path: str = "",
    ) -> str | None:
        """Merge Guard assessment into an existing approval reason (or create one)."""
        ass = self.assess(tool_name=tool_name, command=command, path=path)
        guard_r = ass.get("reason")
        if not base_reason and not guard_r:
            return None
        if base_reason and guard_r and guard_r not in base_reason:
            return f"{base_reason} · {guard_r}"
        return base_reason or guard_r

    def status(self) -> dict[str, Any]:
        return {
            "bot": "guard",
            "assessments": self.assessments,
            "last_level": (self.last or {}).get("level"),
            "last_score": (self.last or {}).get("score"),
        }
