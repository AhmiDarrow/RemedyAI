"""Shadow rehearsal — predict high-blast impact before commit.

Layers ON TOP of write jail and approvals; never replaces them.
Never runs on L0/L1. Fail-closed when unsure for mutation-class acts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Tools that always warrant a shadow check
_HIGH_BLAST_TOOLS = frozenset(
    {
        "bash_exec",
        "file_write",
        "file_edit",
        "file_edit_batch",
        "computer_click",
        "computer_type",
        "computer_key",
        "computer_drag",
        "computer_act",
    }
)

_DESTRUCTIVE_SHELL = re.compile(
    r"(?ix)\b("
    r"rm\s+-rf|remove-item\s+.*-recurse|format-volume|"
    r"del\s+/[sf]|rd\s+/s|git\s+reset\s+--hard|"
    r"git\s+clean\s+-fd|drop\s+table|mkfs\.|dd\s+if="
    r")\b"
)


@dataclass(frozen=True)
class ShadowResult:
    outcome: str  # pass | soft_warn | hard_block
    reason: str
    blast: str = "low"  # low | medium | high | critical
    tool: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "blast": self.blast,
            "tool": self.tool,
        }

    @property
    def blocked(self) -> bool:
        return self.outcome == "hard_block"


def should_shadow(
    tool_name: str,
    *,
    tier: int = 2,
    strict: bool = False,
) -> bool:
    if tier < 2:
        return False
    name = tool_name or ""
    if name in _HIGH_BLAST_TOOLS:
        return True
    # Strict mode (governor): also shadow navigate / skill_run / job_run
    if strict and name in (
        "computer_navigate",
        "skill_run",
        "job_run",
        "bash_exec",
    ):
        return True
    return False


def rehearse(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    tier: int = 2,
    work_roots: list[str] | None = None,
    map_hint: dict[str, Any] | None = None,
    strict: bool = False,
) -> ShadowResult:
    """Local dry-run impact predict. Deterministic; no network."""
    name = (tool_name or "").strip()
    args = arguments or {}
    if not should_shadow(name, tier=tier, strict=strict):
        return ShadowResult("pass", "not_high_blast", "low", name)

    if name == "bash_exec":
        cmd = str(args.get("command") or args.get("cmd") or "")
        if _DESTRUCTIVE_SHELL.search(cmd):
            return ShadowResult(
                "hard_block",
                "destructive_shell_pattern",
                "critical",
                name,
            )
        # Write jail will still run; soft warn if no work roots bound
        if work_roots is not None and len(work_roots) == 0 and len(cmd) > 0:
            # project unbound — jail may be open; warn
            return ShadowResult(
                "soft_warn",
                "shell_with_no_work_roots",
                "medium",
                name,
            )
        # Opaque / mutation still pass to real jail
        return ShadowResult("pass", "shell_ok_pending_jail", "medium", name)

    if name in ("file_write", "file_edit", "file_edit_batch"):
        paths: list[str] = []
        path = str(args.get("path") or "")
        if path:
            paths.append(path)
        if name == "file_edit_batch":
            edits = args.get("edits") or args.get("files") or []
            if isinstance(edits, list):
                for e in edits:
                    if isinstance(e, dict) and e.get("path"):
                        paths.append(str(e["path"]))
        if paths and work_roots:
            try:
                from pathlib import Path

                for path in paths:
                    p = Path(path).expanduser()
                    try:
                        p = p.resolve()
                    except Exception:
                        p = p.absolute()
                    ok = False
                    for r in work_roots:
                        try:
                            rp = Path(r).expanduser().resolve()
                            p.relative_to(rp)
                            ok = True
                            break
                        except Exception:
                            continue
                    if not ok:
                        return ShadowResult(
                            "hard_block",
                            "path_outside_work_roots",
                            "high",
                            name,
                        )
            except Exception:
                return ShadowResult(
                    "soft_warn",
                    "path_resolve_uncertain",
                    "medium",
                    name,
                )
        return ShadowResult("pass", "file_write_ok", "medium", name)

    if name.startswith("computer_"):
        # If browser map says unsettled, soft warn on type/click
        if map_hint and map_hint.get("browser_settled") is False:
            if name in ("computer_type", "computer_click", "computer_act"):
                return ShadowResult(
                    "soft_warn",
                    "browser_not_settled",
                    "medium",
                    name,
                )
        return ShadowResult("pass", "computer_ok", "medium", name)

    return ShadowResult("pass", "default", "low", name)
