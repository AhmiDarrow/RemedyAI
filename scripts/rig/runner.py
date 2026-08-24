"""Run a scenario suite against one model and produce a scorecard."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from .client import RemedyClient
from .sandbox import Sandbox, make_sandbox
from .scenarios import Scenario, get_suite
from .score import Outcome, RunReport, grade, host_info, now_stamp


def run_suite(
    *,
    label: str,
    provider: str,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    suite: str = "core",
    only: list[str] | None = None,
    out_dir: Path | None = None,
    keep: bool = False,
    trace: bool = True,
    approval_mode: str = "auto",
    rmb: dict | None = None,
    on_event: Callable[[str], None] = print,
    sandbox: Sandbox | None = None,
) -> RunReport:
    """Boot a disposable Remedy, walk the ladder, tear it down."""
    scenarios = [s for s in get_suite(suite) if not only or s.id in set(only)]
    if not scenarios:
        raise SystemExit("no scenarios selected")

    owns_sandbox = sandbox is None
    sb = sandbox or make_sandbox()
    report = RunReport(
        label=label,
        provider=provider,
        model=model,
        base_url=base_url,
        suite=suite,
        started=now_stamp(),
        host=host_info(),
    )
    started = time.time()

    try:
        if owns_sandbox:
            sb.write_config(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                approval_mode=approval_mode,
            )
            if rmb:
                sb.write_rmb_json(**rmb)
            on_event(f"  booting Remedy on {sb.base} (home: {sb.home})")
            sb.start(trace=trace)
            on_event(f"  ready - driving {len(scenarios)} scenario(s)")

        client = RemedyClient(sb.base, sb.token)

        for scenario in scenarios:
            outcome = _run_one(client, sb, scenario, provider, model, on_event)
            report.outcomes.append(outcome)

    finally:
        report.seconds = time.time() - started
        if owns_sandbox:
            if keep:
                report.notes.append(f"sandbox kept at {sb.root}")
                on_event(f"  sandbox kept: {sb.root}")
            sb.cleanup(keep=keep)

    if out_dir:
        path = report.write(Path(out_dir))
        report.notes.append(f"scorecard: {path}")
        on_event(f"  scorecard: {path}")
    return report


def _run_one(
    client: RemedyClient,
    sb: Sandbox,
    scenario: Scenario,
    provider: str,
    model: str,
    on_event: Callable[[str], None],
) -> Outcome:
    """Give the scenario its own workspace + session, then grade the turn."""
    ws = sb.workspace / scenario.id
    ws.mkdir(parents=True, exist_ok=True)
    if scenario.setup:
        scenario.setup(ws)

    on_event(f"    [{scenario.tier}] {scenario.id} ...")
    try:
        session_id = client.new_session(
            title=f"rig:{scenario.id}",
            project_path=str(ws),
            provider=provider or None,
            model=model or None,
        )
    except Exception as e:
        return Outcome(
            id=scenario.id,
            tier=scenario.tier,
            weight=scenario.weight,
            passed=False,
            detail=f"session create failed: {e}",
            seconds=0.0,
            tool_calls=0,
            failed_tools=0,
            first_tool_s=None,
            status="error",
            error=str(e),
            text_chars=0,
        )

    turn = client.send(
        session_id,
        scenario.prompt,
        provider=provider or None,
        model=model or None,
        timeout=scenario.timeout,
    )
    if turn.status not in ("ok", "error", "timeout"):
        # Leave nothing generating behind for the next scenario.
        client.abort(session_id)

    if turn.status == "timeout":
        # Leave nothing generating behind, or the next scenario inherits it.
        client.abort(session_id)
    outcome = grade(scenario, turn, ws)
    mark = "PASS" if outcome.passed else "FAIL"
    on_event(
        f"      {mark}  {outcome.seconds:.0f}s  {outcome.tool_calls} tools  "
        f"- {outcome.detail[:80]}"
    )
    return outcome
