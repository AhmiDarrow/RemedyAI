"""Statistics + manuscript tools: stdlib numerics, mocked compile, no scipy."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from remedy.core import agent_science_tools as st
from remedy.skills.tool_registry import ToolRegistry


@dataclass
class _Res:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class _Rt:
    root: Path
    tool_registry: ToolRegistry = field(default_factory=ToolRegistry)

    def effective_project_path(self) -> Path:
        return self.root

    def resolve_tool_path(self, path: str, *, for_write: bool = False) -> Path:
        _ = for_write
        p = Path(path).expanduser()
        return p if p.is_absolute() else self.root / p

    def allowed_roots(self) -> list[Path]:
        return [self.root]

    def write_roots(self) -> list[Path]:
        return [self.root]

    def access_scope(self) -> str:
        return "project"


def _run(coro):
    return asyncio.run(coro)


def run(rt: _Rt, tool: str, **kwargs) -> str:
    return _run(rt.tool_registry.execute(tool, **kwargs))


def run_json(rt: _Rt, tool: str, **kwargs):
    return json.loads(run(rt, tool, **kwargs))


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    root = tmp_path / "study"
    root.mkdir()
    return root


@pytest.fixture
def rt(project: Path, monkeypatch) -> _Rt:
    monkeypatch.setattr(st, "_approval_block", lambda *a, **k: None)
    r = _Rt(root=project)
    st.register_science_tools(r)
    return r


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def fake(runtime, argv, *, cwd, timeout):
        seen.append({"argv": list(argv), "cwd": Path(cwd), "timeout": timeout})
        return _Res(0, "Output written on paper.pdf\n", "")

    monkeypatch.setattr(st, "_sandbox_run", fake)
    return seen


def test_registered_in_workspace_tools() -> None:
    from remedy.core import agent_workspace_tools

    assert "register_science_tools" in inspect.getsource(agent_workspace_tools)


def test_timeouts_resolve(rt: _Rt) -> None:
    from remedy.core.tool_timeouts import tool_timeout_for

    reg = rt.tool_registry
    assert tool_timeout_for("power_analysis", reg) == 60.0
    assert tool_timeout_for("stats_assumptions", reg) == 120.0
    assert tool_timeout_for("stats_effect_size", reg) == 120.0
    assert tool_timeout_for("stats_multiplicity", reg) == 60.0
    assert tool_timeout_for("manuscript_check", reg) == 60.0
    assert tool_timeout_for("manuscript_build", reg) == 1800.0


def test_phase_lists_include_research_tools() -> None:
    from remedy.core.endless_context import EXPAND_TOOL_PACK
    from remedy.core.react_turn import _VERIFY_TOOLS

    assert {"analysis_run", "cite_check", "manuscript_build", "power_analysis"} <= set(
        EXPAND_TOOL_PACK
    )
    assert {"analysis_run", "cite_check", "manuscript_build", "manuscript_check"} <= _VERIFY_TOOLS


def test_two_sample_t_n64_d05_is_about_eighty_percent(rt: _Rt) -> None:
    """Cohen's textbook anchor: n=64/group, d=0.5, alpha=0.05 two-sided ≈ 0.80."""
    payload = run_json(
        rt,
        "power_analysis",
        test="two_sample_t",
        solve="power",
        n=64,
        effect_size=0.5,
        alpha=0.05,
    )
    assert payload["solved"] == "power"
    assert 0.78 <= payload["value"] <= 0.85
    n = run_json(
        rt,
        "power_analysis",
        test="two_sample_t",
        solve="n",
        effect_size=0.5,
        alpha=0.05,
        target_power=0.8,
    )
    assert 60 <= n["value"] <= 70


def test_one_sided_wrong_tail_is_near_alpha(rt: _Rt) -> None:
    payload = run_json(
        rt,
        "power_analysis",
        test="one_proportion",
        solve="power",
        n=100,
        effect_size=-0.2,
        alpha=0.05,
        alternative="greater",
    )
    assert payload["value"] < 0.15


def test_power_refuses_an_unknown_test(rt: _Rt) -> None:
    assert "UNKNOWN_TEST" in run(rt, "power_analysis", test="magic")


def test_holm_step_down_on_a_known_triple(rt: _Rt) -> None:
    payload = run_json(
        rt, "stats_multiplicity", pvalues="0.01,0.04,0.03", method="holm", labels="a,b,c"
    )
    by = {row["label"]: row for row in payload["rows"]}
    assert by["a"]["adjusted"] == pytest.approx(0.03)
    assert by["b"]["adjusted"] == pytest.approx(0.06)
    assert by["c"]["adjusted"] == pytest.approx(0.06)
    assert by["a"]["reject_at_alpha"] is True
    assert by["b"]["reject_at_alpha"] is False


def test_assumptions_refuse_normality_below_n20(rt: _Rt) -> None:
    payload = run_json(rt, "stats_assumptions", values="1,2,3,4,5")
    norm = next(c for c in payload["checks"] if c["name"] == "normality_of_residuals")
    assert norm["verdict"] == "not_run"
    assert "below 20" in norm["detail"]


def test_assumptions_recommend_welch_for_two_continuous_groups(rt: _Rt) -> None:
    payload = run_json(
        rt,
        "stats_assumptions",
        outcome_type="continuous",
        n_groups=2,
    )
    tests = [r["test"] for r in payload["recommended"]]
    assert any("Welch" in t for t in tests)


def test_hedges_g_from_summaries(rt: _Rt) -> None:
    payload = run_json(
        rt,
        "stats_effect_size",
        kind="hedges_g",
        n1=20,
        n2=20,
        mean1=1.0,
        mean2=0.0,
        sd1=1.0,
        sd2=1.0,
    )
    assert payload["kind"] == "hedges_g"
    # Unbiased g is a shade under Cohen's d=1
    assert 0.95 < payload["estimate"] < 1.0
    assert payload["ci_low"] < payload["estimate"] < payload["ci_high"]
    assert "method" in payload


def test_manuscript_check_auto_picks_consort_and_flags_gaps(rt: _Rt, project: Path) -> None:
    (project / "trial.md").write_text(
        "A randomised controlled trial of X vs placebo.\n"
        "Participants were randomly assigned 1:1.\n"
        "The primary outcome was pain at 12 weeks.\n",
        encoding="utf-8",
    )
    payload = run_json(rt, "manuscript_check", path="trial.md")
    assert "consort" in payload["checklists"]
    assert payload["counts"]["missing"] > 0
    assert payload["counts"]["present"] >= 1
    assert "equator-network.org" in payload["sources"]["consort"]["url"]


def test_manuscript_check_auto_without_vocabulary_does_not_guess(rt: _Rt, project: Path) -> None:
    (project / "notes.md").write_text("Hello world.\n", encoding="utf-8")
    assert "NO_CHECKLIST" in run(rt, "manuscript_check", path="notes.md")


def test_manuscript_build_uses_latexmk_and_condenses_the_log(
    rt: _Rt, project: Path, calls, monkeypatch
) -> None:
    monkeypatch.setattr(
        st,
        "_which",
        lambda name, cwd: "C:/tex/latexmk.exe" if name == "latexmk" else "",
    )
    tex = project / "paper.tex"
    tex.write_text(r"\documentclass{article}\begin{document}Hi\end{document}", encoding="utf-8")
    (project / "paper.log").write_text(
        "! Undefined control sequence.\n"
        "l.12 \\nope\n"
        "LaTeX Warning: Citation `ghost' on page 1 undefined.\n",
        encoding="utf-8",
    )
    payload = run_json(rt, "manuscript_build", path="paper.tex")
    assert payload["engine"] == "latexmk"
    assert calls and "latexmk" in calls[0]["argv"][0]
    assert payload["log"]["undefined_citations"] == ["ghost"]
    assert payload["ok"] is False
    assert any(e["message"].startswith("Undefined") for e in payload["log"]["errors"])


def test_manuscript_build_blocks_on_approval(project: Path, monkeypatch, calls) -> None:
    monkeypatch.setattr(
        st, "_approval_block", lambda *a, **k: "APPROVAL_REQUIRED id=ms1\nreason=test"
    )
    monkeypatch.setattr(st, "_which", lambda name, cwd: "/usr/bin/latexmk")
    r = _Rt(root=project)
    st.register_science_tools(r)
    (project / "paper.tex").write_text("%\n", encoding="utf-8")
    out = run(r, "manuscript_build", path="paper.tex")
    assert out.startswith("APPROVAL_REQUIRED")
    assert not calls


def test_bare_calls_return_errors_not_exceptions(rt: _Rt) -> None:
    assert "UNKNOWN_TEST" in run(rt, "power_analysis")
    assert "MISSING_P" in run(rt, "stats_multiplicity")
    assert "UNKNOWN_KIND" in run(rt, "stats_effect_size")
    assert "NO_PATH" in run(rt, "manuscript_check")
    assert "NO_PATH" in run(rt, "manuscript_build")
    assert "MISSING_INPUT" in run(rt, "stats_assumptions")
