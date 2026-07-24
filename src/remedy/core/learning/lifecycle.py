"""Skill lifecycle protocol — intelligent create, evolve, and prune.

Philosophy
----------
A single lucky run must **never** become a trusted ACTIVE skill. Learned skills
enter **probation** (DISCOVERED → VALIDATED → ACTIVE) and only promote after
repeated success across sessions. Bad skills demote, then prune.

**Effort weight:** If solving took many failed attempts, recoveries, and tool
diversity, that knowledge is *hard-won*. High-effort skills are easier to
accept (still require final success) and much harder to demote/prune so we
do not throw away expensive learning.

Stages
------
DISCOVERED  Candidate written after a good trace; not preferred in routing.
VALIDATED   Passed static validation + at least one successful re-use.
ACTIVE      Reliable under the promotion thresholds.
DISABLED    Failed under demotion thresholds (kept for audit / possible revive).
DEPRECATED  Soft-removed; eligible for prune after stale period.

Promotion / demotion (defaults; scaled by effort)
-------------------------------------------------
Promote:  n >= 5, success_rate >= 0.80, sessions >= 2  (high effort: slightly easier)
Demote:   n >= 5, success_rate < 0.50  OR  streak failures  (high effort: needs more evidence)
Prune:    hopeless / stale DISABLED — high effort resists prune longer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from remedy.models import Skill, SkillStatus

# -- thresholds (tunable) ----------------------------------------------------

MIN_STEPS_TO_LEARN = 3
MIN_STEP_SUCCESS_RATE = 0.75  # easy traces must be clean
MIN_STEP_SUCCESS_RATE_HARD = 0.40  # hard-won: many fails before success OK
MIN_SUCCESSFUL_STEPS = 3
REQUIRE_OVERALL_SUCCESS = True

# Never promote from a single trace
PROMOTE_MIN_EXECUTIONS = 5
PROMOTE_MIN_SUCCESS_RATE = 0.80
PROMOTE_MIN_SESSIONS = 2

DEMOTE_MIN_EXECUTIONS = 5
DEMOTE_MAX_SUCCESS_RATE = 0.50
DEMOTE_CONSECUTIVE_FAILURES = 3

# Probation: first N executions stay off ACTIVE even if rate looks perfect
PROBATION_MIN_EXECUTIONS = 3

# Prune candidates after this many total failures with zero success, or stale disabled
PRUNE_ZERO_SUCCESS_MIN_N = 3
PRUNE_DISABLED_AFTER_DAYS = 30

# Effort bands (0.0 – 1.0)
# HIGH is reachable with a few failed approaches + recoveries (not only extreme traces).
EFFORT_LOW = 0.25
EFFORT_MEDIUM = 0.45
EFFORT_HIGH = 0.62


@dataclass
class LifecycleDecision:
    """Result of evaluating a skill or trace."""

    action: str  # accept | reject | promote | demote | prune | hold
    reason: str
    new_status: SkillStatus | None = None
    confidence: float = 0.0
    effort: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EffortScore:
    """How hard something was to learn (0 = trivial, 1 = very hard-won)."""

    score: float
    failed_steps: int = 0
    recovery_count: int = 0
    unique_tools: int = 0
    total_steps: int = 0
    duration_ms: float = 0.0
    approach_switches: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def band(self) -> str:
        if self.score >= EFFORT_HIGH:
            return "high"
        if self.score >= EFFORT_MEDIUM:
            return "medium"
        if self.score >= EFFORT_LOW:
            return "low"
        return "trivial"

    @property
    def is_hard_won(self) -> bool:
        return self.score >= EFFORT_HIGH


@dataclass
class SkillHealth:
    """Runtime health snapshot for a learned skill."""

    skill_name: str
    status: SkillStatus
    total: int = 0
    successes: int = 0
    failures: int = 0
    sessions: int = 0
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    auto_generated: bool = False
    # 0–1 from creation metadata; protects hard-won skills
    effort_weight: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.successes / self.total


def compute_effort_score(
    *,
    steps: list[Any],
    total_duration_ms: float = 0.0,
) -> EffortScore:
    """Score how much work went into reaching a solution.

    High when the agent tried many tools, failed and recovered, switched
    approaches, or spent a long time — i.e. knowledge we must not casually drop.
    """
    if not steps:
        return EffortScore(score=0.0, reasons=["empty trace"])

    failed = [s for s in steps if not getattr(s, "success", True)]
    [s for s in steps if getattr(s, "success", True)]
    tools = [str(getattr(s, "tool_name", "") or "") for s in steps]
    unique_tools = len({t for t in tools if t})

    # Recovery: failure followed by a different tool that succeeds
    recoveries = 0
    approach_switches = 0
    for i, s in enumerate(steps[:-1]):
        nxt = steps[i + 1]
        t0 = str(getattr(s, "tool_name", "") or "")
        t1 = str(getattr(nxt, "tool_name", "") or "")
        if t0 and t1 and t0 != t1:
            approach_switches += 1
        if (
            not getattr(s, "success", True)
            and getattr(nxt, "success", True)
            and t0 != t1
        ):
            recoveries += 1

    n = len(steps)
    fail_n = len(failed)
    # Components in [0, 1]
    fail_component = min(1.0, fail_n / 5.0)  # 5+ failures before success → full
    recovery_component = min(1.0, recoveries / 3.0)
    tool_component = min(1.0, max(0, unique_tools - 1) / 5.0)
    length_component = min(1.0, max(0, n - 3) / 12.0)
    switch_component = min(1.0, approach_switches / 6.0)
    duration = float(total_duration_ms or 0.0)
    if duration <= 0:
        duration = sum(float(getattr(s, "duration_ms", 0) or 0) for s in steps)
    # ~2+ minutes of tool work counts as high duration effort
    duration_component = min(1.0, duration / 120_000.0)

    score = (
        0.30 * fail_component
        + 0.24 * recovery_component
        + 0.14 * tool_component
        + 0.12 * length_component
        + 0.12 * switch_component
        + 0.08 * duration_component
    )
    # Cap; require some success signal or it's just a mess (handled by gates)
    score = max(0.0, min(1.0, score))

    reasons: list[str] = []
    if fail_n:
        reasons.append(f"{fail_n} failed attempt(s)")
    if recoveries:
        reasons.append(f"{recoveries} recovery path(s)")
    if unique_tools >= 3:
        reasons.append(f"{unique_tools} distinct tools")
    if approach_switches >= 3:
        reasons.append(f"{approach_switches} approach switches")
    if duration >= 60_000:
        reasons.append(f"{duration/1000:.0f}s of work")
    if n >= 8:
        reasons.append(f"{n}-step trace")
    if not reasons:
        reasons.append("straightforward path")

    return EffortScore(
        score=score,
        failed_steps=fail_n,
        recovery_count=recoveries,
        unique_tools=unique_tools,
        total_steps=n,
        duration_ms=duration,
        approach_switches=approach_switches,
        reasons=reasons,
    )


class SkillLifecyclePolicy:
    """Central policy for learn / promote / demote / prune decisions."""

    def __init__(
        self,
        *,
        promote_min_n: int = PROMOTE_MIN_EXECUTIONS,
        promote_min_rate: float = PROMOTE_MIN_SUCCESS_RATE,
        promote_min_sessions: int = PROMOTE_MIN_SESSIONS,
        demote_min_n: int = DEMOTE_MIN_EXECUTIONS,
        demote_max_rate: float = DEMOTE_MAX_SUCCESS_RATE,
        demote_streak: int = DEMOTE_CONSECUTIVE_FAILURES,
    ) -> None:
        self.promote_min_n = promote_min_n
        self.promote_min_rate = promote_min_rate
        self.promote_min_sessions = promote_min_sessions
        self.demote_min_n = demote_min_n
        self.demote_max_rate = demote_max_rate
        self.demote_streak = demote_streak

    # -- creation gates ------------------------------------------------------

    def should_accept_trace(
        self,
        *,
        step_count: int,
        successful_steps: int,
        overall_success: bool,
        step_success_rate: float,
        has_reusable_pattern: bool,
        title: str = "",
        effort: EffortScore | float | None = None,
    ) -> LifecycleDecision:
        """Decide whether a trace may become a skill candidate at all.

        Easy traces need a clean success rate. Hard-won traces (many failed
        approaches before a working path) may have a lower step success rate
        but still deserve codification — we do not want to lose that work.
        """
        if isinstance(effort, EffortScore):
            eff = effort
        elif isinstance(effort, (int, float)):
            eff = EffortScore(score=float(effort), reasons=[])
        else:
            eff = EffortScore(score=0.0, reasons=["unspecified"])

        if step_count < MIN_STEPS_TO_LEARN:
            return LifecycleDecision(
                "reject",
                f"Trace too short ({step_count} < {MIN_STEPS_TO_LEARN} steps)",
                confidence=0.1,
                effort=eff.score,
            )
        if REQUIRE_OVERALL_SUCCESS and not overall_success:
            return LifecycleDecision(
                "reject",
                "Task did not complete successfully — refusing to codify a failed run",
                confidence=0.15,
                effort=eff.score,
                details={"title": title},
            )
        if successful_steps < MIN_SUCCESSFUL_STEPS:
            return LifecycleDecision(
                "reject",
                f"Not enough successful steps ({successful_steps} < {MIN_SUCCESSFUL_STEPS})",
                confidence=0.2,
                effort=eff.score,
            )

        # Effort-aware cleanliness bar
        min_rate = (
            MIN_STEP_SUCCESS_RATE_HARD
            if eff.is_hard_won
            else (
                0.55
                if eff.score >= EFFORT_MEDIUM
                else MIN_STEP_SUCCESS_RATE
            )
        )
        # Hard-won with recoveries: still need at least one recovery signal
        if step_success_rate < min_rate:
            return LifecycleDecision(
                "reject",
                (
                    f"Step success rate too low ({step_success_rate:.0%} < {min_rate:.0%}) "
                    f"for effort={eff.band}"
                ),
                confidence=0.25,
                effort=eff.score,
            )
        if eff.is_hard_won and eff.recovery_count < 1 and eff.failed_steps < 2:
            # High score without failures is odd — treat as medium
            pass
        # Prefer patterns for easy traces; hard-won may be a unique long path
        if (
            not has_reusable_pattern
            and not eff.is_hard_won
            and step_success_rate < 0.95
            and eff.score < EFFORT_MEDIUM
        ):
            return LifecycleDecision(
                "reject",
                "No recurring tool pattern and low effort — avoid lucky one-offs",
                confidence=0.35,
                effort=eff.score,
            )

        conf = min(
            0.95,
            0.40
            + 0.20 * step_success_rate
            + 0.20 * eff.score
            + (0.10 if has_reusable_pattern else 0.0)
            + min(0.10, successful_steps * 0.02),
        )
        why = (
            f"Trace accepted (effort={eff.band} {eff.score:.2f}: "
            + ", ".join(eff.reasons[:4])
            + "); skill enters probation"
        )
        return LifecycleDecision(
            "accept",
            why,
            new_status=SkillStatus.DISCOVERED,
            confidence=conf,
            effort=eff.score,
            details={
                "successful_steps": successful_steps,
                "step_success_rate": step_success_rate,
                "has_pattern": has_reusable_pattern,
                "effort_band": eff.band,
                "effort_reasons": list(eff.reasons),
            },
        )

    def initial_status_for_learned(
        self, confidence: float, *, effort: float = 0.0
    ) -> SkillStatus:
        """Always probation — never ACTIVE from a single generation.

        Hard-won high-confidence traces may start VALIDATED (still not ACTIVE).
        """
        if confidence >= 0.85 or (effort >= EFFORT_HIGH and confidence >= 0.7):
            return SkillStatus.VALIDATED
        return SkillStatus.DISCOVERED

    # -- effort-scaled evolution thresholds ----------------------------------

    def _scaled_demote_streak(self, effort: float) -> int:
        # Hard-won: need more consecutive fails before demote (protect investment)
        if effort >= EFFORT_HIGH:
            return self.demote_streak + 3  # 6
        if effort >= EFFORT_MEDIUM:
            return self.demote_streak + 1  # 4
        return self.demote_streak

    def _scaled_demote_min_n(self, effort: float) -> int:
        if effort >= EFFORT_HIGH:
            return self.demote_min_n + 4  # 9
        if effort >= EFFORT_MEDIUM:
            return self.demote_min_n + 2
        return self.demote_min_n

    def _scaled_promote_min_n(self, effort: float) -> int:
        # Hard-won: slightly faster promote once multi-session proof exists
        if effort >= EFFORT_HIGH:
            return max(4, self.promote_min_n - 1)
        return self.promote_min_n

    def _scaled_prune_zero_n(self, effort: float) -> int:
        if effort >= EFFORT_HIGH:
            return PRUNE_ZERO_SUCCESS_MIN_N + 5  # very reluctant
        if effort >= EFFORT_MEDIUM:
            return PRUNE_ZERO_SUCCESS_MIN_N + 2
        return PRUNE_ZERO_SUCCESS_MIN_N

    def _scaled_prune_days(self, effort: float) -> int:
        if effort >= EFFORT_HIGH:
            return PRUNE_DISABLED_AFTER_DAYS * 3  # 90d
        if effort >= EFFORT_MEDIUM:
            return int(PRUNE_DISABLED_AFTER_DAYS * 1.5)
        return PRUNE_DISABLED_AFTER_DAYS

    # -- evolution gates -----------------------------------------------------

    def evaluate_health(self, health: SkillHealth) -> LifecycleDecision:
        """Promote / demote / hold / prune based on multi-run evidence + effort."""
        name = health.skill_name
        status = health.status
        effort = float(health.effort_weight or 0.0)
        demote_streak = self._scaled_demote_streak(effort)
        demote_n = self._scaled_demote_min_n(effort)
        promote_n = self._scaled_promote_min_n(effort)
        prune_n = self._scaled_prune_zero_n(effort)
        prune_days = self._scaled_prune_days(effort)

        # Hard demote on failure streak (effort raises the bar)
        if health.consecutive_failures >= demote_streak:
            if status in (SkillStatus.ACTIVE, SkillStatus.VALIDATED, SkillStatus.DISCOVERED):
                return LifecycleDecision(
                    "demote",
                    (
                        f"{name}: {health.consecutive_failures} consecutive failures "
                        f"(threshold {demote_streak}, effort={effort:.2f}) — demoting"
                    ),
                    new_status=SkillStatus.DISABLED,
                    confidence=health.success_rate,
                    effort=effort,
                    details={"consecutive_failures": health.consecutive_failures},
                )

        # Promote only with breadth (sessions) + volume + rate
        if (
            health.total >= promote_n
            and health.success_rate >= self.promote_min_rate
            and health.sessions >= self.promote_min_sessions
            and health.consecutive_failures == 0
            and status != SkillStatus.ACTIVE
            and status != SkillStatus.DEPRECATED
        ):
            return LifecycleDecision(
                "promote",
                (
                    f"{name}: promote to ACTIVE "
                    f"(n={health.total}, rate={health.success_rate:.0%}, "
                    f"sessions={health.sessions}, effort={effort:.2f})"
                ),
                new_status=SkillStatus.ACTIVE,
                confidence=health.success_rate,
                effort=effort,
            )

        # Intermediate: DISCOVERED → VALIDATED after a few clean successes
        # Hard-won: need fewer reuses to leave pure DISCOVERED
        probation_n = 2 if effort >= EFFORT_HIGH else PROBATION_MIN_EXECUTIONS
        if (
            status == SkillStatus.DISCOVERED
            and health.total >= probation_n
            and health.success_rate >= self.promote_min_rate
            and health.successes >= probation_n
        ):
            return LifecycleDecision(
                "promote",
                f"{name}: probation passed → VALIDATED (effort={effort:.2f})",
                new_status=SkillStatus.VALIDATED,
                confidence=health.success_rate,
                effort=effort,
            )

        # Demote unreliable ACTIVE / VALIDATED (effort raises n)
        if (
            health.total >= demote_n
            and health.success_rate < self.demote_max_rate
            and status in (SkillStatus.ACTIVE, SkillStatus.VALIDATED)
        ):
            return LifecycleDecision(
                "demote",
                (
                    f"{name}: demote to DISABLED "
                    f"(n={health.total}, rate={health.success_rate:.0%}, "
                    f"effort={effort:.2f})"
                ),
                new_status=SkillStatus.DISABLED,
                confidence=health.success_rate,
                effort=effort,
            )

        # Prune hopeless — hard-won needs more failed reuses before we give up
        if health.total >= prune_n and health.successes == 0:
            return LifecycleDecision(
                "prune",
                f"{name}: {health.total} runs with zero successes — prune "
                f"(effort={effort:.2f})",
                new_status=SkillStatus.DEPRECATED,
                confidence=0.0,
                effort=effort,
            )

        if status == SkillStatus.DISABLED and health.last_failure_at:
            age = datetime.now(UTC) - health.last_failure_at
            # If hard-won and we ever had successes, never prune on time alone
            if (
                age >= timedelta(days=prune_days)
                and health.successes == 0
            ):
                return LifecycleDecision(
                    "prune",
                    f"{name}: disabled and stale ({age.days}d) — prune "
                    f"(effort={effort:.2f})",
                    new_status=SkillStatus.DEPRECATED,
                    confidence=0.0,
                    effort=effort,
                )
            if (
                effort >= EFFORT_HIGH
                and health.successes > 0
                and age < timedelta(days=prune_days * 2)
            ):
                return LifecycleDecision(
                    "hold",
                    (
                        f"{name}: hard-won DISABLED held for revive window "
                        f"({health.successes} prior successes, effort={effort:.2f})"
                    ),
                    new_status=status,
                    confidence=health.success_rate,
                    effort=effort,
                )

        return LifecycleDecision(
            "hold",
            (
                f"{name}: keep {status.value} "
                f"(n={health.total}, rate={health.success_rate:.0%}, effort={effort:.2f})"
            ),
            new_status=status,
            confidence=health.success_rate,
            effort=effort,
        )

    def health_from_stats(
        self,
        skill: Skill,
        *,
        total: int,
        successes: int,
        failures: int,
        sessions: int,
        consecutive_failures: int = 0,
        last_success_at: datetime | None = None,
        last_failure_at: datetime | None = None,
    ) -> SkillHealth:
        meta = skill.manifest.metadata or {}
        effort = float(meta.get("effort_weight") or meta.get("effort") or 0.0)
        return SkillHealth(
            skill_name=skill.manifest.name,
            status=skill.manifest.status,
            total=total,
            successes=successes,
            failures=failures,
            sessions=sessions,
            consecutive_failures=consecutive_failures,
            last_success_at=last_success_at,
            last_failure_at=last_failure_at,
            auto_generated=bool(meta.get("auto_generated")),
            effort_weight=effort,
        )
