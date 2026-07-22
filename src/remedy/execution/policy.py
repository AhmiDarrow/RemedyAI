"""Execution policy & permissions for tool/skill sandboxing.

Governs what tools can run, with what arguments, in which contexts.
Policy rules are evaluated per-call — deny takes precedence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PolicyAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PolicyRule:
    tool_name: str
    action: PolicyAction
    reason: str = ""
    source: str = ""
    scope: str = "session"  # session, user, global


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""
    requires_approval: bool = False
    matching_rule: PolicyRule | None = None


class ExecutionPolicy:
    """Rules-based permission model for tool execution.

    Rules are matched by tool name (supports "*" wildcard).
    Deny rules always take precedence over allow rules.
    """

    def __init__(self, default_action: PolicyAction = PolicyAction.ALLOW) -> None:
        self.default_action = default_action
        self._rules: list[PolicyRule] = []
        self._always_require_approval: set[str] = set()

    # -- rule management -----------------------------------------------------

    def add_rule(self, rule: PolicyRule) -> None:
        self._rules.append(rule)

    def allow(self, tool_name: str, reason: str = "", scope: str = "session") -> PolicyRule:
        rule = PolicyRule(tool_name=tool_name, action=PolicyAction.ALLOW, reason=reason, scope=scope)
        self._rules.append(rule)
        return rule

    def deny(self, tool_name: str, reason: str = "", scope: str = "session") -> PolicyRule:
        rule = PolicyRule(tool_name=tool_name, action=PolicyAction.DENY, reason=reason, scope=scope)
        self._rules.append(rule)
        return rule

    def require_approval(self, tool_name: str) -> None:
        self._always_require_approval.add(tool_name)

    def clear_rules(self, scope: str | None = None) -> None:
        if scope:
            self._rules = [r for r in self._rules if r.scope != scope]
        else:
            self._rules.clear()

    # -- rule evaluation -----------------------------------------------------

    def evaluate(self, tool_name: str, context: dict[str, Any] | None = None) -> PolicyDecision:
        ctx = context or {}

        # Find all matching rules (wildcard or exact)
        deny_reason = ""
        allow_reason = ""
        denied = False
        allowed = False
        last_matched: PolicyRule | None = None

        for rule in sorted(self._rules, key=lambda r: (r.scope != "global", r.scope != "user")):
            if self._match(rule.tool_name, tool_name):
                last_matched = rule
                if rule.action == PolicyAction.DENY:
                    denied = True
                    deny_reason = rule.reason or f"Denied by rule for {rule.tool_name}"
                elif rule.action == PolicyAction.ALLOW:
                    allowed = True
                    allow_reason = rule.reason

        needs_approval = tool_name in self._always_require_approval

        if denied:
            return PolicyDecision(
                allowed=False,
                reason=deny_reason,
                requires_approval=False,
                matching_rule=last_matched,
            )

        if allowed or self.default_action == PolicyAction.ALLOW:
            return PolicyDecision(
                allowed=True,
                reason=allow_reason or "Allowed by default",
                requires_approval=needs_approval,
                matching_rule=last_matched,
            )

        return PolicyDecision(
            allowed=False,
            reason="Default policy is deny",
            requires_approval=False,
        )

    def check(self, tool_name: str, context: dict[str, Any] | None = None) -> bool:
        return self.evaluate(tool_name, context).allowed

    # -- helpers -------------------------------------------------------------

    def rules_for(self, tool_name: str) -> list[PolicyRule]:
        return [r for r in self._rules if self._match(r.tool_name, tool_name)]

    def denied_tools(self) -> set[str]:
        return {r.tool_name for r in self._rules if r.action == PolicyAction.DENY}

    def allowed_tools(self) -> set[str]:
        return {r.tool_name for r in self._rules if r.action == PolicyAction.ALLOW}

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    @staticmethod
    def _match(pattern: str, tool_name: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return tool_name.startswith(pattern[:-1])
        return pattern == tool_name


def default_policy() -> ExecutionPolicy:
    """Safe default: allow common tools, deny destructive ones."""
    policy = ExecutionPolicy(default_action=PolicyAction.ALLOW)

    # Always require approval for destructive operations
    for tool in ("bash_exec", "file_write", "file_delete", "docker_exec"):
        policy.require_approval(tool)

    # Explicitly deny dangerous patterns by default
    policy.deny("sudo_*", "Privilege escalation blocked")
    policy.deny("raw_sql_exec", "Raw SQL execution blocked")

    return policy
