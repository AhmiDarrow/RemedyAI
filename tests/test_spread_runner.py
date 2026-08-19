"""The silent spread runner: what it refuses, and what it must never lose.

`run_spread` is the fan-out engine. A parent turn hands it a list of small
deterministic scout tasks and gets one merged digest back. Four things go
badly wrong if this module is wrong:

* A worker that spreads again multiplies workers out of the owner's control,
  so recursion has to be refused and the depth flag has to be released again
  afterwards — a leaked flag silently disables spread for the rest of the
  process.
* A crashing or misbehaving worker must degrade into one failed row, never
  take down the whole wave. The parent still needs the rows that did work.
* Pressing Stop must not leave the owner waiting on scouts that can each run
  for three minutes — every remaining task has to come back cancelled.
* Worker output is raw tool spew (search hits, .env files, git diffs). It is
  about to be pasted into a model prompt, so secrets must be redacted and
  oversized dumps capped before they leave here.

Every test below drives the runner with fakes; nothing touches the real
project tree, the job runner, or the local vision model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from remedy.core.spread import runner as runner_mod
from remedy.core.spread.runner import run_spread, spread_depth
from remedy.core.spread.types import SpreadResult, SpreadTask, WorkerResult


class FakeRuntime:
    """Just enough runtime for the runner: a project root and a path jail."""

    def __init__(self, root: Path, *, jail: Exception | None = None) -> None:
        self.root = root
        self.config = SimpleNamespace(home_dir=str(root))
        self._session_id = "spread-runner-test"
        self._jail = jail
        self.resolved: list[str] = []

    def effective_project_path(self) -> Path:
        return self.root

    def resolve_tool_path(self, path: str, **_kw: Any) -> Path:
        if self._jail is not None:
            raise self._jail
        self.resolved.append(path)
        return self.root

    def allowed_roots(self) -> list[Path]:
        return [self.root]

    def access_scope(self) -> str:
        return "project"


@pytest.fixture()
def rt(tmp_path: Path) -> FakeRuntime:
    return FakeRuntime(tmp_path)


@pytest.fixture()
def job_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every `_job` call and answer with a trivial success."""
    calls: list[dict[str, Any]] = []

    async def fake_job(runtime, kind, **kwargs):
        calls.append({"kind": kind, **kwargs})
        return f"did {kind}", True, {"kind": kind}

    monkeypatch.setattr(runner_mod, "_job", fake_job)
    return calls


# --------------------------------------------------------------------------
# spread_depth
# --------------------------------------------------------------------------


def test_the_depth_is_zero_outside_a_spread():
    assert spread_depth() == 0


def test_the_depth_reflects_the_context_variable():
    token = runner_mod._spread_depth.set(3)
    try:
        assert spread_depth() == 3
    finally:
        runner_mod._spread_depth.reset(token)


# --------------------------------------------------------------------------
# run_spread — refusals
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_task_list_is_skipped_rather_than_run(rt: FakeRuntime):
    res = await run_spread(rt, [])
    assert isinstance(res, SpreadResult)
    assert res.ok is False
    assert res.strategy == "skipped"
    assert res.reason == "no_tasks"
    assert res.wall_ms == 0.0
    assert res.tasks == []
    assert res.results == []
    assert res.merged_summary == ""


@pytest.mark.asyncio
async def test_an_empty_task_list_keeps_the_callers_reason(rt: FakeRuntime):
    res = await run_spread(rt, [], reason="planner said no")
    assert res.reason == "planner said no"


@pytest.mark.asyncio
async def test_a_skipped_spread_ignores_the_requested_strategy(rt: FakeRuntime):
    res = await run_spread(rt, [], strategy="fanout")
    assert res.strategy == "skipped"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (4, 4),
        (1, 1),
        (8, 8),
        (0, 4),  # falsy -> the default, never zero workers
        (None, 4),
        (9, 8),  # hard ceiling
        (10_000, 8),
        (-3, 1),  # floor
        ("2", 2),  # tool layers hand strings through
        (3.9, 3),
    ],
)
@pytest.mark.asyncio
async def test_the_worker_count_is_clamped_between_one_and_eight(
    rt: FakeRuntime, given: Any, expected: int
):
    res = await run_spread(rt, [], max_workers=given)
    assert res.max_workers == expected


@pytest.mark.asyncio
async def test_an_unparsable_worker_count_is_not_swallowed(rt: FakeRuntime):
    # Garbage from a caller surfaces here rather than quietly becoming 4.
    with pytest.raises(ValueError):
        await run_spread(rt, [], max_workers="lots")


@pytest.mark.asyncio
async def test_a_nested_spread_is_refused(rt: FakeRuntime, monkeypatch):
    ran: list[str] = []

    async def never(runtime, task):
        ran.append(task.id)
        return WorkerResult(id=task.id, kind=task.kind, ok=True, summary="x")

    monkeypatch.setattr(runner_mod, "_run_one", never)
    tasks = [SpreadTask(id="t1", kind="explore"), SpreadTask(id="t2", kind="diff")]

    token = runner_mod._spread_depth.set(1)
    try:
        res = await run_spread(rt, tasks, reason="nested")
    finally:
        runner_mod._spread_depth.reset(token)

    assert res.ok is False
    assert res.strategy == "skipped"
    assert res.reason == "recursive_spread_blocked"
    assert ran == [], "a blocked spread must not start any worker"
    # The tasks are echoed back so the caller can see what was dropped.
    assert res.tasks == tasks
    assert res.results == []


@pytest.mark.asyncio
async def test_the_depth_flag_is_released_after_a_normal_run(
    rt: FakeRuntime, job_calls: list[dict[str, Any]]
):
    await run_spread(rt, [SpreadTask(id="t1", kind="explore")])
    assert spread_depth() == 0, "a leaked depth flag disables spread forever"


@pytest.mark.asyncio
async def test_the_depth_flag_is_released_even_when_a_worker_explodes(
    rt: FakeRuntime, monkeypatch
):
    async def boom(runtime, task):
        raise RuntimeError("worker died")

    monkeypatch.setattr(runner_mod, "_run_one", boom)
    await run_spread(rt, [SpreadTask(id="t1", kind="explore")])
    assert spread_depth() == 0


@pytest.mark.asyncio
async def test_a_worker_that_spreads_again_is_refused_end_to_end(
    rt: FakeRuntime, monkeypatch
):
    inner: dict[str, Any] = {}

    async def spread_again(runtime, task):
        inner["depth"] = spread_depth()
        inner["result"] = await run_spread(runtime, [SpreadTask(id="x", kind="diff")])
        return WorkerResult(id=task.id, kind=task.kind, ok=True, summary="outer")

    monkeypatch.setattr(runner_mod, "_run_one", spread_again)
    outer = await run_spread(rt, [SpreadTask(id="t1", kind="explore")])

    assert outer.ok is True
    assert inner["depth"] == 1
    assert inner["result"].reason == "recursive_spread_blocked"


# --------------------------------------------------------------------------
# run_spread — aggregation and error paths
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_worker_exception_becomes_a_failed_row_not_a_raise(
    rt: FakeRuntime, monkeypatch
):
    async def boom(runtime, task):
        raise RuntimeError("scout tripped")

    monkeypatch.setattr(runner_mod, "_run_one", boom)
    res = await run_spread(rt, [SpreadTask(id="t1", kind="explore")])

    assert res.ok is False
    assert len(res.results) == 1
    row = res.results[0]
    assert row.id == "t1"
    assert row.ok is False
    assert "scout tripped" in row.summary
    assert row.model_used == "none"


@pytest.mark.asyncio
async def test_a_worker_returning_the_wrong_type_is_flagged_not_stored(
    rt: FakeRuntime, monkeypatch
):
    async def wrong(runtime, task):
        return "a bare string"

    monkeypatch.setattr(runner_mod, "_run_one", wrong)
    res = await run_spread(rt, [SpreadTask(id="t1", kind="explore")])

    assert res.ok is False
    assert res.results[0].summary == "worker returned unknown type"


@pytest.mark.asyncio
async def test_one_success_among_failures_makes_the_spread_ok(
    rt: FakeRuntime, monkeypatch
):
    async def mixed(runtime, task):
        return WorkerResult(
            id=task.id, kind=task.kind, ok=(task.id == "good"), summary=task.id
        )

    monkeypatch.setattr(runner_mod, "_run_one", mixed)
    res = await run_spread(
        rt,
        [
            SpreadTask(id="bad1", kind="explore"),
            SpreadTask(id="good", kind="explore"),
            SpreadTask(id="bad2", kind="explore"),
        ],
    )
    assert res.ok is True


@pytest.mark.asyncio
async def test_a_spread_where_everything_fails_is_not_ok(rt: FakeRuntime, monkeypatch):
    async def allbad(runtime, task):
        return WorkerResult(id=task.id, kind=task.kind, ok=False, summary="no")

    monkeypatch.setattr(runner_mod, "_run_one", allbad)
    res = await run_spread(rt, [SpreadTask(id="a", kind="explore")])
    assert res.ok is False
    assert res.strategy == "fanout"


@pytest.mark.asyncio
async def test_results_keep_task_order_across_waves(rt: FakeRuntime, monkeypatch):
    async def slow_for_early_ids(runtime, task):
        # Later tasks finish first; the runner must still report in task order.
        await asyncio.sleep(0.02 if task.id in ("t1", "t3") else 0.0)
        return WorkerResult(id=task.id, kind=task.kind, ok=True, summary=task.id)

    monkeypatch.setattr(runner_mod, "_run_one", slow_for_early_ids)
    tasks = [SpreadTask(id=f"t{i}", kind="explore") for i in range(1, 6)]
    res = await run_spread(rt, tasks, max_workers=2)

    assert [r.id for r in res.results] == ["t1", "t2", "t3", "t4", "t5"]


@pytest.mark.asyncio
async def test_no_more_than_max_workers_run_at_once(rt: FakeRuntime, monkeypatch):
    live = {"now": 0, "peak": 0}

    async def tracked(runtime, task):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.01)
        live["now"] -= 1
        return WorkerResult(id=task.id, kind=task.kind, ok=True, summary="x")

    monkeypatch.setattr(runner_mod, "_run_one", tracked)
    tasks = [SpreadTask(id=f"t{i}", kind="explore") for i in range(7)]
    await run_spread(rt, tasks, max_workers=2)

    assert live["peak"] <= 2


@pytest.mark.asyncio
async def test_an_aborted_turn_cancels_every_task_instead_of_running_it(
    rt: FakeRuntime, monkeypatch
):
    ran: list[str] = []

    async def should_not_run(runtime, task):
        ran.append(task.id)
        return WorkerResult(id=task.id, kind=task.kind, ok=True, summary="x")

    monkeypatch.setattr(runner_mod, "_run_one", should_not_run)
    monkeypatch.setattr(runner_mod, "_turn_is_aborted", lambda: True)

    tasks = [SpreadTask(id=f"t{i}", kind="explore") for i in range(3)]
    res = await run_spread(rt, tasks, max_workers=2)

    assert ran == []
    assert len(res.results) == 3
    assert all("cancelled" in r.summary for r in res.results)
    assert all(r.ok is False and r.model_used == "none" for r in res.results)
    assert res.ok is False


@pytest.mark.asyncio
async def test_the_strategy_and_reason_reach_the_result(
    rt: FakeRuntime, job_calls: list[dict[str, Any]]
):
    res = await run_spread(
        rt, [SpreadTask(id="t1", kind="explore")], reason="why", strategy="single"
    )
    assert res.strategy == "single"
    assert res.reason == "why"
    assert res.wall_ms > 0.0


@pytest.mark.asyncio
async def test_a_missing_reason_is_labelled_fanout(
    rt: FakeRuntime, job_calls: list[dict[str, Any]]
):
    res = await run_spread(rt, [SpreadTask(id="t1", kind="explore")])
    assert res.reason == "fanout"
    assert "fanout" in res.merged_summary


# --------------------------------------------------------------------------
# _turn_is_aborted
# --------------------------------------------------------------------------


def test_an_unreadable_turn_context_reads_as_not_aborted(monkeypatch):
    def boom() -> bool:
        raise RuntimeError("no turn context")

    monkeypatch.setattr("remedy.core.turn_context.is_turn_aborted", boom)
    assert runner_mod._turn_is_aborted() is False


def test_a_quiet_turn_context_reads_as_not_aborted():
    assert runner_mod._turn_is_aborted() is False


# --------------------------------------------------------------------------
# _run_one — routing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected_job"),
    [
        ("explore", "explore"),
        ("read_map", "explore"),
        ("verify", "verify"),
        ("diff", "diff"),
    ],
)
@pytest.mark.asyncio
async def test_each_deterministic_kind_reaches_its_job(
    rt: FakeRuntime, job_calls: list[dict[str, Any]], kind: str, expected_job: str
):
    task = SpreadTask(id="t1", kind=kind, path="src", query="q", command="pytest -q")
    out = await runner_mod._run_one(rt, task)

    assert job_calls[0]["kind"] == expected_job
    assert job_calls[0]["path"] == "src"
    assert out.kind == kind
    assert out.ok is True
    assert out.model_used == "none"
    assert out.elapsed_ms >= 0.0


@pytest.mark.asyncio
async def test_an_explore_task_falls_back_from_query_to_goal(
    rt: FakeRuntime, job_calls: list[dict[str, Any]]
):
    await runner_mod._run_one(rt, SpreadTask(id="t1", kind="explore", goal="the goal"))
    assert job_calls[0]["query"] == "the goal"


@pytest.mark.asyncio
async def test_a_verify_task_carries_the_command_and_not_a_query(
    rt: FakeRuntime, job_calls: list[dict[str, Any]]
):
    await runner_mod._run_one(
        rt, SpreadTask(id="t1", kind="verify", command="pytest -q", query="ignored")
    )
    assert job_calls[0]["command"] == "pytest -q"
    assert "query" not in job_calls[0]


@pytest.mark.asyncio
async def test_a_diff_task_carries_only_the_path(
    rt: FakeRuntime, job_calls: list[dict[str, Any]]
):
    await runner_mod._run_one(
        rt, SpreadTask(id="t1", kind="diff", path="pkg", command="rm -rf /")
    )
    assert job_calls[0] == {"kind": "diff", "path": "pkg"}


@pytest.mark.parametrize("raw", ["  EXPLORE  ", "Diff", "VERIFY"])
@pytest.mark.asyncio
async def test_the_kind_is_normalised_before_routing(
    rt: FakeRuntime, job_calls: list[dict[str, Any]], raw: str
):
    out = await runner_mod._run_one(rt, SpreadTask(id="t1", kind=raw))
    assert out.kind == raw.strip().lower()
    assert job_calls[0]["kind"] == raw.strip().lower()


@pytest.mark.asyncio
async def test_an_empty_kind_becomes_explore(
    rt: FakeRuntime, job_calls: list[dict[str, Any]]
):
    out = await runner_mod._run_one(rt, SpreadTask(id="t1", kind=""))
    assert out.kind == "explore"
    assert job_calls[0]["kind"] == "explore"


@pytest.mark.asyncio
async def test_an_unknown_kind_explores_but_loses_the_goal(
    rt: FakeRuntime, job_calls: list[dict[str, Any]]
):
    # Documents current behaviour: the fallback branch passes task.query only,
    # so a goal-only task of an unrecognised kind explores with an empty query.
    out = await runner_mod._run_one(
        rt, SpreadTask(id="t1", kind="telepathy", goal="find the bug")
    )
    assert job_calls[0]["kind"] == "explore"
    assert job_calls[0]["query"] == ""
    assert out.kind == "telepathy", "the reported kind is not rewritten to explore"


@pytest.mark.asyncio
async def test_an_aborted_turn_stops_a_worker_before_it_starts(
    rt: FakeRuntime, job_calls: list[dict[str, Any]], monkeypatch
):
    monkeypatch.setattr(runner_mod, "_turn_is_aborted", lambda: True)
    out = await runner_mod._run_one(rt, SpreadTask(id="t1", kind="explore"))

    assert job_calls == []
    assert out.ok is False
    assert out.summary == "cancelled: turn aborted"
    assert out.elapsed_ms == 0.0


@pytest.mark.asyncio
async def test_a_raising_helper_is_reported_not_propagated(
    rt: FakeRuntime, monkeypatch
):
    async def boom(runtime, kind, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(runner_mod, "_job", boom)
    out = await runner_mod._run_one(rt, SpreadTask(id="t1", kind="explore"))

    assert out.ok is False
    assert "worker failed: disk gone" in out.summary
    assert out.details == {}


# --------------------------------------------------------------------------
# _run_one — capping and redaction
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_long_summary_is_truncated_to_the_task_cap(
    rt: FakeRuntime, monkeypatch
):
    async def fat(runtime, kind, **kwargs):
        return "x" * 5_000, True, {}

    monkeypatch.setattr(runner_mod, "_job", fat)
    out = await runner_mod._run_one(
        rt, SpreadTask(id="t1", kind="explore", max_chars=100)
    )

    assert out.summary.startswith("x" * 100)
    assert "truncated 5000→100 chars" in out.summary


@pytest.mark.asyncio
async def test_a_summary_inside_the_cap_is_left_alone(rt: FakeRuntime, monkeypatch):
    async def small(runtime, kind, **kwargs):
        return "short answer", True, {}

    monkeypatch.setattr(runner_mod, "_job", small)
    out = await runner_mod._run_one(
        rt, SpreadTask(id="t1", kind="explore", max_chars=100)
    )
    assert out.summary == "short answer"


@pytest.mark.asyncio
async def test_a_zero_cap_falls_back_to_the_default_six_thousand(
    rt: FakeRuntime, monkeypatch
):
    async def fat(runtime, kind, **kwargs):
        return "y" * 6_500, True, {}

    monkeypatch.setattr(runner_mod, "_job", fat)
    out = await runner_mod._run_one(
        rt, SpreadTask(id="t1", kind="explore", max_chars=0)
    )
    assert "truncated 6500→6000 chars" in out.summary


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuvwxyz0123",
        "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ01234",
    ],
)
@pytest.mark.asyncio
async def test_secrets_never_leave_a_worker(rt: FakeRuntime, monkeypatch, secret: str):
    async def leaky(runtime, kind, **kwargs):
        return f"config has {secret} inline", True, {"api_key": secret}

    monkeypatch.setattr(runner_mod, "_job", leaky)
    out = await runner_mod._run_one(rt, SpreadTask(id="t1", kind="explore"))

    assert secret not in out.summary
    assert secret not in str(out.details)


@pytest.mark.asyncio
async def test_a_broken_redactor_still_drops_a_secret_shaped_summary(
    rt: FakeRuntime, monkeypatch
):
    """Fallback path: if redaction itself fails, the summary is dropped whole."""

    async def leaky(runtime, kind, **kwargs):
        return "found api_key=hunter2 in .env", True, {"note": "keep"}

    def broken(*a, **k):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(runner_mod, "_job", leaky)
    monkeypatch.setattr("remedy.core.metabolism.redact.redact_text", broken)
    out = await runner_mod._run_one(rt, SpreadTask(id="t1", kind="explore"))

    assert out.summary == "[redacted worker summary]"
    assert out.details == {}


@pytest.mark.asyncio
async def test_a_broken_redactor_leaves_an_innocent_summary_alone(
    rt: FakeRuntime, monkeypatch
):
    async def clean(runtime, kind, **kwargs):
        return "listed 4 files", True, {"note": "keep"}

    def broken(*a, **k):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(runner_mod, "_job", clean)
    monkeypatch.setattr("remedy.core.metabolism.redact.redact_text", broken)
    out = await runner_mod._run_one(rt, SpreadTask(id="t1", kind="explore"))

    assert out.summary == "listed 4 files"
    # Documents the fallback's blind spot: details are not scanned at all here.
    assert out.details == {"note": "keep"}


@pytest.mark.asyncio
async def test_non_dict_details_are_dropped(rt: FakeRuntime, monkeypatch):
    async def odd(runtime, kind, **kwargs):
        return "fine", True, ["not", "a", "dict"]

    monkeypatch.setattr(runner_mod, "_job", odd)
    out = await runner_mod._run_one(rt, SpreadTask(id="t1", kind="explore"))
    assert out.details == {}


# --------------------------------------------------------------------------
# _job
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_job_becomes_an_error_tuple_not_a_raise(
    rt: FakeRuntime, monkeypatch
):
    async def boom(runtime, kind, **kwargs):
        raise PermissionError("Path outside allowed roots")

    monkeypatch.setattr("remedy.core.jobs.run_job", boom)
    summary, ok, details = await runner_mod._job(rt, "explore", path="/etc")

    assert ok is False
    assert summary.startswith("error: ")
    assert "Path outside allowed roots" in summary
    assert details == {"error": "Path outside allowed roots"}


@pytest.mark.asyncio
async def test_an_empty_path_is_sent_to_the_job_as_dot(rt: FakeRuntime, monkeypatch):
    seen: dict[str, Any] = {}

    async def capture(runtime, kind, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(summary="ok", ok=True, details={})

    monkeypatch.setattr("remedy.core.jobs.run_job", capture)
    await runner_mod._job(rt, "explore", path="")

    assert seen["path"] == "."
    assert seen["timeout"] == 180.0


@pytest.mark.asyncio
async def test_missing_job_details_become_an_empty_dict(rt: FakeRuntime, monkeypatch):
    async def nodetails(runtime, kind, **kwargs):
        return SimpleNamespace(summary="ok", ok=1, details=None)

    monkeypatch.setattr("remedy.core.jobs.run_job", nodetails)
    summary, ok, details = await runner_mod._job(rt, "diff")

    assert summary == "ok"
    assert ok is True  # truthy job flags are normalised to real bools
    assert details == {}


# --------------------------------------------------------------------------
# _search_worker
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_search_without_a_query_is_refused(rt: FakeRuntime):
    summary, ok, details = await runner_mod._search_worker(
        rt, SpreadTask(id="s1", kind="search")
    )
    assert ok is False
    assert summary == "error: search query empty"
    assert details == {}


@pytest.mark.asyncio
async def test_a_search_outside_the_path_jail_is_refused(tmp_path: Path, monkeypatch):
    jailed = FakeRuntime(tmp_path, jail=PermissionError("outside allowed roots"))
    called: list[Any] = []

    def never(*a, **k):
        called.append(a)
        return [], "python"

    monkeypatch.setattr("remedy.core.repo_search.search_repo", never)
    summary, ok, details = await runner_mod._search_worker(
        jailed, SpreadTask(id="s1", kind="search", query="secret", path="C:/Windows")
    )

    assert ok is False
    assert summary.startswith("error: path not allowed:")
    assert details == {}
    assert called == [], "a jailed search must never reach the search engine"


@pytest.mark.asyncio
async def test_a_search_falls_back_to_project_scope_when_roots_are_unavailable(
    rt: FakeRuntime, monkeypatch
):
    seen: dict[str, Any] = {}

    def fake_search(root, query, **kwargs):
        seen.update(kwargs)
        seen["query"] = query
        return [], "python"

    def blow_up():
        raise RuntimeError("no scope configured")

    monkeypatch.setattr(rt, "allowed_roots", blow_up)
    monkeypatch.setattr("remedy.core.repo_search.search_repo", fake_search)
    monkeypatch.setattr(
        "remedy.core.repo_search.format_hits", lambda hits, **kw: "no hits"
    )

    await runner_mod._search_worker(rt, SpreadTask(id="s1", kind="search", query="tok"))

    assert seen["allowed_roots"] is None
    assert seen["access_scope"] == "project"
    assert seen["max_matches"] == 40


@pytest.mark.asyncio
async def test_a_search_uses_the_goal_when_no_query_is_given(
    rt: FakeRuntime, monkeypatch
):
    seen: dict[str, Any] = {}

    def fake_search(root, query, **kwargs):
        seen["query"] = query
        return [], "python"

    monkeypatch.setattr("remedy.core.repo_search.search_repo", fake_search)
    monkeypatch.setattr("remedy.core.repo_search.format_hits", lambda hits, **kw: "-")

    await runner_mod._search_worker(
        rt, SpreadTask(id="s1", kind="search", goal="  find the parser  ")
    )
    assert seen["query"] == "find the parser"


@pytest.mark.asyncio
async def test_a_search_with_zero_hits_still_reports_success(
    rt: FakeRuntime, monkeypatch
):
    # Documents current behaviour: "searched and found nothing" is not a failure.
    monkeypatch.setattr(
        "remedy.core.repo_search.search_repo", lambda root, q, **kw: ([], "ripgrep")
    )
    monkeypatch.setattr(
        "remedy.core.repo_search.format_hits", lambda hits, **kw: "(no matches)"
    )

    summary, ok, details = await runner_mod._search_worker(
        rt, SpreadTask(id="s1", kind="search", query="nothing")
    )
    assert ok is True
    assert details == {"engine": "ripgrep", "hits": 0}
    assert summary == "(no matches)"


@pytest.mark.asyncio
async def test_a_search_engine_crash_surfaces_as_a_failed_worker(
    rt: FakeRuntime, monkeypatch
):
    def boom(*a, **k):
        raise RuntimeError("ripgrep vanished")

    monkeypatch.setattr("remedy.core.repo_search.search_repo", boom)
    out = await runner_mod._run_one(rt, SpreadTask(id="s1", kind="search", query="x"))

    assert out.ok is False
    assert "worker failed: ripgrep vanished" in out.summary


@pytest.mark.asyncio
async def test_a_search_task_is_routed_to_the_search_worker(
    rt: FakeRuntime, monkeypatch
):
    monkeypatch.setattr(
        "remedy.core.repo_search.search_repo", lambda root, q, **kw: ([], "ripgrep")
    )
    monkeypatch.setattr("remedy.core.repo_search.format_hits", lambda hits, **kw: "hit")

    out = await runner_mod._run_one(rt, SpreadTask(id="s1", kind="search", query="x"))

    assert out.kind == "search"
    assert out.ok is True
    assert out.model_used == "none"
    assert out.details == {"engine": "ripgrep", "hits": 0}


# --------------------------------------------------------------------------
# _implement_worker
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["", ".", "./", "   "])
@pytest.mark.asyncio
async def test_an_implement_worker_refuses_a_whole_tree(
    rt: FakeRuntime, monkeypatch, path: str
):
    called: list[Any] = []

    def never(*a, **k):
        called.append(k)
        return {"ok": True}

    monkeypatch.setattr("remedy.core.build_isolated.isolated_unit_hop", never)
    summary, ok, details = await runner_mod._implement_worker(
        rt, SpreadTask(id="i1", kind="implement", path=path, goal="do it")
    )

    assert ok is False
    assert summary == "implement worker needs path= (unit file)"
    assert details == {}
    assert called == [], "no hop may start without a single unit file"


@pytest.mark.asyncio
async def test_an_implement_worker_never_enables_the_llm(rt: FakeRuntime, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_hop(runtime, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "path": kwargs["path"], "merged": True, "errors": None}

    monkeypatch.setattr("remedy.core.build_isolated.isolated_unit_hop", fake_hop)
    summary, ok, details = await runner_mod._implement_worker(
        rt, SpreadTask(id="i1", kind="implement", path="src/a.py", goal="G" * 900)
    )

    assert seen["use_llm"] is False
    assert seen["max_repairs"] == 2
    assert len(seen["behavior"]) == 400, "the behavior brief is capped before the hop"
    assert ok is True
    assert "merged=True" in summary
    assert details["path"] == "src/a.py"


@pytest.mark.asyncio
async def test_a_red_oracle_makes_the_implement_worker_fail(
    rt: FakeRuntime, monkeypatch
):
    monkeypatch.setattr(
        "remedy.core.build_isolated.isolated_unit_hop",
        lambda *a, **k: {"ok": False, "path": "src/a.py", "error": "oracle red"},
    )
    summary, ok, _ = await runner_mod._implement_worker(
        rt, SpreadTask(id="i1", kind="implement", path="src/a.py")
    )
    assert ok is False
    assert "errors=oracle red" in summary


@pytest.mark.asyncio
async def test_an_implement_task_is_routed_to_the_isolated_hop(
    rt: FakeRuntime, monkeypatch
):
    monkeypatch.setattr(
        "remedy.core.build_isolated.isolated_unit_hop",
        lambda *a, **k: {"ok": True, "path": "src/a.py", "merged": True},
    )
    out = await runner_mod._run_one(
        rt, SpreadTask(id="i1", kind="implement", path="src/a.py")
    )

    assert out.kind == "implement"
    assert out.ok is True
    # An implement worker is deterministic — it must never claim a model ran.
    assert out.model_used == "none"


# --------------------------------------------------------------------------
# _review_worker
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_short_review_never_wakes_the_local_model(rt: FakeRuntime, monkeypatch):
    async def small(runtime, kind, **kwargs):
        return "brief findings", True, {}

    called: list[str] = []

    def summarize(text, *, goal):
        called.append(goal)
        return "compressed"

    monkeypatch.setattr(runner_mod, "_job", small)
    monkeypatch.setattr(runner_mod, "_local_summarize", summarize)

    summary, ok, details = await runner_mod._review_worker(
        rt, SpreadTask(id="r1", kind="review", goal="check auth")
    )

    assert called == []
    assert summary == "brief findings"
    assert details["model_used"] == "none"


@pytest.mark.asyncio
async def test_a_failed_explore_is_not_compressed(rt: FakeRuntime, monkeypatch):
    async def failed(runtime, kind, **kwargs):
        return "e" * 4_000, False, {}

    called: list[str] = []

    def summarize(text, *, goal):
        called.append(goal)
        return "compressed"

    monkeypatch.setattr(runner_mod, "_job", failed)
    monkeypatch.setattr(runner_mod, "_local_summarize", summarize)

    summary, ok, details = await runner_mod._review_worker(
        rt, SpreadTask(id="r1", kind="review", goal="check auth")
    )

    assert ok is False
    assert called == [], "a failed job's error text must not be paraphrased away"
    assert len(summary) == 4_000
    assert details["model_used"] == "none"


@pytest.mark.asyncio
async def test_a_long_review_is_compressed_by_the_local_model(
    rt: FakeRuntime, monkeypatch
):
    async def fat(runtime, kind, **kwargs):
        return "f" * 4_000, True, {"engine": "walk"}

    monkeypatch.setattr(runner_mod, "_job", fat)
    monkeypatch.setattr(runner_mod, "_local_summarize", lambda t, *, goal: "- bullet")

    out = await runner_mod._run_one(
        rt, SpreadTask(id="r1", kind="review", goal="check auth")
    )

    assert out.summary == "- bullet"
    assert out.model_used == "local"
    assert out.details["engine"] == "walk", "job details survive the compression"


@pytest.mark.asyncio
async def test_a_silent_local_model_leaves_the_review_untouched(
    rt: FakeRuntime, monkeypatch
):
    async def fat(runtime, kind, **kwargs):
        return "g" * 4_000, True, {}

    monkeypatch.setattr(runner_mod, "_job", fat)
    monkeypatch.setattr(runner_mod, "_local_summarize", lambda t, *, goal: None)

    out = await runner_mod._run_one(
        rt, SpreadTask(id="r1", kind="review", max_chars=9_000)
    )

    assert out.summary == "g" * 4_000
    assert out.model_used == "none"


# --------------------------------------------------------------------------
# _local_summarize
# --------------------------------------------------------------------------


def test_no_local_summary_when_the_vision_runtime_is_down(monkeypatch):
    monkeypatch.setattr("remedy.vision.runtime.is_running", lambda *a, **k: False)
    assert runner_mod._local_summarize("text", goal="g") is None


def test_no_local_summary_when_the_vision_probe_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("probe failed")

    monkeypatch.setattr("remedy.vision.runtime.is_running", boom)
    assert runner_mod._local_summarize("text", goal="g") is None


def test_no_local_summary_when_the_job_queue_refuses(monkeypatch):
    monkeypatch.setattr("remedy.vision.runtime.is_running", lambda *a, **k: True)

    def boom():
        raise RuntimeError("no queue")

    monkeypatch.setattr("remedy.runtime.jobs.default_queue", boom)
    assert runner_mod._local_summarize("text", goal="g") is None


class FakeQueue:
    """Stand-in for the local nano job queue."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.jobs: list[Any] = []

    def submit(self, job, wait=False, timeout=None):
        self.jobs.append(job)
        return self.answer


@pytest.fixture()
def local_queue(monkeypatch: pytest.MonkeyPatch):
    """Pretend the vision runtime is up and hand back a scriptable queue."""
    monkeypatch.setattr("remedy.vision.runtime.is_running", lambda *a, **k: True)
    monkeypatch.setattr(
        "remedy.runtime.local_infer.ensure_handlers_registered", lambda: None
    )

    def make(answer: dict[str, Any]) -> FakeQueue:
        q = FakeQueue(answer)
        monkeypatch.setattr("remedy.runtime.jobs.default_queue", lambda: q)
        return q

    return make


def test_a_local_summary_is_bounded_and_returned(local_queue):
    q = local_queue({"ok": True, "result": {"text": "  - one\n- two  "}})
    out = runner_mod._local_summarize("F" * 9_000, goal="G" * 500)

    assert out == "- one\n- two"
    payload = q.jobs[0].payload
    assert len(payload["prompt"]) < 7_000, "the worker text is trimmed before the model"
    assert payload["max_tokens"] == 280
    assert payload["timeout_s"] == 20.0
    assert payload["base_url"].startswith("http://")


def test_a_refused_local_job_yields_no_summary(local_queue):
    local_queue({"ok": False, "error": "nano offline"})
    assert runner_mod._local_summarize("text", goal="g") is None


def test_a_blank_local_answer_yields_no_summary(local_queue):
    local_queue({"ok": True, "result": {"text": "   "}})
    assert runner_mod._local_summarize("text", goal="g") is None


def test_a_non_dict_local_answer_is_still_read_as_text(local_queue):
    local_queue({"ok": True, "result": "plain text answer"})
    assert runner_mod._local_summarize("text", goal="g") == "plain text answer"


# --------------------------------------------------------------------------
# _merge_results
# --------------------------------------------------------------------------


def test_the_digest_names_every_worker():
    rows = [
        WorkerResult(
            id="a", kind="explore", ok=True, summary="found it", elapsed_ms=12.4
        ),
        WorkerResult(
            id="b", kind="verify", ok=False, summary="tests red", elapsed_ms=9.0
        ),
    ]
    merged = runner_mod._merge_results(rows, reason="audit")

    assert merged.startswith("[spread 2 workers — audit]")
    assert "## [a] explore (ok, 12ms, model=none)" in merged
    assert "## [b] verify (fail, 9ms, model=none)" in merged
    assert "found it" in merged
    assert "tests red" in merged


def test_an_empty_worker_summary_is_marked_rather_than_dropped():
    rows = [WorkerResult(id="a", kind="diff", ok=True, summary="   \n  ")]
    merged = runner_mod._merge_results(rows, reason="")
    assert "(empty)" in merged
    assert "fanout" in merged


def test_a_digest_with_no_workers_still_has_a_header():
    merged = runner_mod._merge_results([], reason="nothing")
    assert merged.startswith("[spread 0 workers — nothing]")


def test_the_digest_is_capped_before_it_reaches_the_parent_prompt():
    rows = [
        WorkerResult(id=f"w{i}", kind="explore", ok=True, summary="z" * 2_000)
        for i in range(50)
    ]
    merged = runner_mod._merge_results(rows, reason="big")
    assert len(merged) == 48_000
