"""Reproducible-analysis tools: env probe, headless runs, ledger, profile, diff.

No sockets, no real papermill/Rscript/julia/quarto/pandas — the sandbox seam
(``agent_analysis_tools._sandbox_run``) is monkeypatched everywhere, REMEDY_HOME
points at tmp_path, and the project is a tmp directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from remedy.core import agent_analysis_tools as at
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
        return []

    def write_roots(self) -> list[Path]:
        return [self.root]

    def access_scope(self) -> str:
        return "project"


def _run(coro):
    return asyncio.run(coro)


def _make_venv(root: Path) -> Path:
    rel = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    p = root.joinpath(".venv", *rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture
def rt(project: Path, monkeypatch) -> _Rt:
    monkeypatch.setattr(at, "_approval_block", lambda *a, **k: None)
    r = _Rt(root=project)
    at.register_analysis_tools(r)
    return r


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    """Default sandbox seam: records argv, answers module probes with 'nothing'."""
    seen: list[dict] = []

    async def fake(runtime, argv, *, cwd, timeout, env_extra=None):
        seen.append(
            {"argv": list(argv), "cwd": Path(cwd), "timeout": timeout, "env": dict(env_extra or {})}
        )
        if len(argv) >= 3 and argv[1] == "-c" and at._ENV_MARKER in argv[2]:
            payload = {"modules": {}, "python": "3.12.0", "executable": argv[0]}
            return _Res(0, at._ENV_MARKER + json.dumps(payload))
        return _Res(0, "ran\n", "")

    monkeypatch.setattr(at, "_sandbox_run", fake)
    return seen


def _which_map(monkeypatch, mapping: dict[str, str]) -> None:
    monkeypatch.setattr(at, "_which", lambda name, root: mapping.get(name, ""))


# ----------------------------------------------------------------- registration


def test_registration_names(rt: _Rt) -> None:
    names = set(rt.tool_registry._handlers)
    assert {
        "analysis_env",
        "analysis_run",
        "analysis_ledger",
        "data_profile",
        "data_diff",
    } <= names


def test_long_tools_carry_their_own_timeout(rt: _Rt) -> None:
    from remedy.core.tool_timeouts import DEFAULT_TOOL_TIMEOUT_S, tool_timeout_for

    for name, expected in (
        ("analysis_run", 1800.0),
        ("analysis_env", 240.0),
        ("data_profile", 600.0),
        ("data_diff", 300.0),
    ):
        got = tool_timeout_for(name, rt.tool_registry)
        assert got == expected, name
        assert got > DEFAULT_TOOL_TIMEOUT_S


def test_no_heavy_science_imports_in_module() -> None:
    """The sidecar excludes pandas/numpy/scipy/... — this module must not import them."""
    src = Path(at.__file__).read_text(encoding="utf-8")
    bad = re.compile(
        r"^\s*(?:import|from)\s+(pandas|numpy|scipy|sklearn|matplotlib|torch|transformers)\b",
        re.M,
    )
    assert not bad.findall(src)


# --------------------------------------------------------------------- env


def test_analysis_env_probe_false_runs_no_subprocess(rt: _Rt, monkeypatch) -> None:
    async def boom(*a, **k):
        raise AssertionError("probe=false must not shell out")

    monkeypatch.setattr(at, "_sandbox_run", boom)
    out = json.loads(_run(rt.tool_registry.execute("analysis_env", probe=False)))
    assert out["probe"] is False
    assert "python" in out["runners"]
    assert any("probe=false" in n for n in out["notes"])


def test_analysis_env_reports_project_venv_and_modules(
    rt: _Rt, project: Path, monkeypatch
) -> None:
    venv = _make_venv(project)
    _which_map(monkeypatch, {})

    async def fake(runtime, argv, *, cwd, timeout, env_extra=None):
        assert argv[0] == str(venv)
        payload = {
            "modules": {"pandas": True, "numpy": True, "pyarrow": False, "papermill": False},
            "python": "3.12.4",
            "executable": str(venv),
        }
        return _Res(0, at._ENV_MARKER + json.dumps(payload))

    monkeypatch.setattr(at, "_sandbox_run", fake)
    out = json.loads(_run(rt.tool_registry.execute("analysis_env")))
    assert out["runners"]["python"]["source"] == "project .venv"
    assert out["runners"]["python"]["path"] == str(venv)
    assert out["runners"]["py:pandas"]["found"] is True
    assert out["runners"]["py:pyarrow"]["found"] is False
    assert any("notebook runner" in n for n in out["notes"])


# --------------------------------------------------------------- analysis_run


def test_run_python_uses_project_venv(rt: _Rt, project: Path, calls, monkeypatch) -> None:
    venv = _make_venv(project)
    _which_map(monkeypatch, {})
    (project / "fit.py").write_text("print('hi')\n", encoding="utf-8")
    out = json.loads(_run(rt.tool_registry.execute("analysis_run", path="fit.py")))
    assert out["engine"] == "python"
    assert out["ok"] is True
    assert calls[-1]["argv"] == [str(venv), str(project / "fit.py")]
    assert calls[-1]["env"]["REMEDY_RUN_ID"] == out["run_id"]


def test_run_python_uses_uv_when_lock_present(rt: _Rt, project: Path, calls, monkeypatch) -> None:
    (project / "uv.lock").write_text("", encoding="utf-8")
    (project / "fit.py").write_text("print('hi')\n", encoding="utf-8")
    _which_map(monkeypatch, {"uv": "/bin/uv"})
    out = json.loads(_run(rt.tool_registry.execute("analysis_run", path="fit.py")))
    assert calls[-1]["argv"][:3] == ["/bin/uv", "run", "python"]
    assert out["interpreter_source"] == "uv run"


def test_run_rscript_argv(rt: _Rt, project: Path, calls, monkeypatch) -> None:
    (project / "model.R").write_text("cat(1)\n", encoding="utf-8")
    _which_map(monkeypatch, {"Rscript": "/usr/bin/Rscript"})
    out = json.loads(_run(rt.tool_registry.execute("analysis_run", path="model.R")))
    assert out["engine"] == "rscript"
    assert calls[-1]["argv"] == ["/usr/bin/Rscript", "--vanilla", str(project / "model.R")]


def test_run_julia_uses_project_flag(rt: _Rt, project: Path, calls, monkeypatch) -> None:
    (project / "Project.toml").write_text("name = \"x\"\n", encoding="utf-8")
    (project / "sim.jl").write_text("println(1)\n", encoding="utf-8")
    _which_map(monkeypatch, {"julia": "/usr/bin/julia"})
    _run(rt.tool_registry.execute("analysis_run", path="sim.jl"))
    assert calls[-1]["argv"][0] == "/usr/bin/julia"
    assert calls[-1]["argv"][1] == f"--project={project}"


def test_run_quarto_passes_params(rt: _Rt, project: Path, calls, monkeypatch) -> None:
    (project / "paper.qmd").write_text("---\ntitle: x\n---\n", encoding="utf-8")
    _which_map(monkeypatch, {"quarto": "/usr/bin/quarto"})
    _run(
        rt.tool_registry.execute(
            "analysis_run", path="paper.qmd", params_json='{"alpha": 0.05}'
        )
    )
    argv = calls[-1]["argv"]
    assert argv[:2] == ["/usr/bin/quarto", "render"]
    assert "--execute" in argv
    assert argv[-2:] == ["-P", "alpha:0.05"]


def test_run_notebook_prefers_papermill(rt: _Rt, project: Path, calls, monkeypatch) -> None:
    (project / "nb.ipynb").write_text("{}", encoding="utf-8")
    _which_map(monkeypatch, {"papermill": "/usr/bin/papermill"})
    out = json.loads(
        _run(rt.tool_registry.execute("analysis_run", path="nb.ipynb", params_json='{"n": 3}'))
    )
    assert out["engine"] == "papermill"
    argv = calls[-1]["argv"]
    assert argv[0] == "/usr/bin/papermill"
    assert argv[1] == str(project / "nb.ipynb")
    assert argv[2] == str(project / "nb.executed.ipynb")
    assert argv[3:] == ["-p", "n", "3"]


def test_run_notebook_without_runner_says_how_to_install(
    rt: _Rt, project: Path, calls, monkeypatch
) -> None:
    _make_venv(project)
    (project / "nb.ipynb").write_text("{}", encoding="utf-8")
    _which_map(monkeypatch, {})
    out = _run(rt.tool_registry.execute("analysis_run", path="nb.ipynb"))
    assert "NO_RUNNER" in out
    assert "papermill" in out
    assert "does not install packages" in out


def test_run_refuses_shell_scripts(rt: _Rt, project: Path, calls) -> None:
    (project / "go.sh").write_text("echo hi\n", encoding="utf-8")
    out = _run(rt.tool_registry.execute("analysis_run", path="go.sh"))
    assert "USE_BASH_EXEC" in out
    assert not calls


def test_run_workdir_outside_project_is_write_jailed(
    rt: _Rt, project: Path, tmp_path: Path, calls
) -> None:
    (project / "fit.py").write_text("x=1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    out = _run(
        rt.tool_registry.execute("analysis_run", path="fit.py", workdir=str(outside))
    )
    assert "WRITE_JAIL" in out
    assert not calls


def test_registered_in_workspace_tools():
    import inspect

    from remedy.core import agent_workspace_tools

    assert "register_analysis_tools" in inspect.getsource(agent_workspace_tools)


def test_timeouts_resolve(rt: _Rt) -> None:
    from remedy.core.tool_timeouts import tool_timeout_for

    reg = rt.tool_registry
    assert tool_timeout_for("analysis_env", reg) == 240.0
    assert tool_timeout_for("analysis_run", reg) == 1800.0
    assert tool_timeout_for("analysis_ledger", reg) == 60.0
    assert tool_timeout_for("data_profile", reg) == 600.0
    assert tool_timeout_for("data_diff", reg) == 300.0


def test_run_blocks_on_approval_and_never_executes(
    project: Path, monkeypatch, calls
) -> None:
    monkeypatch.setattr(
        at, "_approval_block", lambda *a, **k: "APPROVAL_REQUIRED id=abc\nreason=test"
    )
    r = _Rt(root=project)
    at.register_analysis_tools(r)
    (project / "fit.py").write_text("x=1\n", encoding="utf-8")
    out = _run(r.tool_registry.execute("analysis_run", path="fit.py"))
    assert out.startswith("APPROVAL_REQUIRED")
    assert not calls


def test_run_collects_artifacts_and_records_the_ledger(
    rt: _Rt, project: Path, tmp_path: Path, monkeypatch
) -> None:
    venv = _make_venv(project)
    _which_map(monkeypatch, {})
    (project / "plot.py").write_text("# makes a figure\n", encoding="utf-8")

    async def fake(runtime, argv, *, cwd, timeout, env_extra=None):
        figures = Path(cwd) / "figures"
        figures.mkdir(parents=True, exist_ok=True)
        (figures / "fig1.png").write_bytes(b"\x89PNG-one")
        (Path(cwd) / "results.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (Path(cwd) / "notes.txt").write_text("not an artifact\n", encoding="utf-8")
        return _Res(0, "done\n", "")

    monkeypatch.setattr(at, "_sandbox_run", fake)
    out = json.loads(_run(rt.tool_registry.execute("analysis_run", path="plot.py", tag="v1")))

    assert out["ok"] is True
    rels = {a["rel"] for a in out["artifacts"]}
    assert "figures/fig1.png" in rels
    assert "results.csv" in rels
    assert "notes.txt" not in rels
    fig = next(a for a in out["artifacts"] if a["rel"] == "figures/fig1.png")
    assert fig["kind"] == "figure"
    assert fig["sha256"]
    assert fig["copied"] is True
    assert Path(fig["copy_path"]).is_file()

    ledger = Path(out["ledger_path"])
    assert ledger.is_file()
    # everything lands under the tmp project, never the real home
    assert str(ledger).startswith(str(project))
    assert ledger == project / ".remedy-research" / "runs" / "ledger.jsonl"
    assert str(venv) in json.loads(ledger.read_text(encoding="utf-8"))["interpreter"]
    assert Path(out["stdout_path"]).read_text(encoding="utf-8") == "done\n"
    assert json.loads(Path(out["run_record_path"]).read_text(encoding="utf-8"))["run_id"] == out[
        "run_id"
    ]

    listed = json.loads(_run(rt.tool_registry.execute("analysis_ledger", action="list")))
    assert listed["total"] == 1
    assert listed["runs"][0]["run_id"] == out["run_id"]
    assert listed["runs"][0]["tag"] == "v1"

    shown = json.loads(
        _run(rt.tool_registry.execute("analysis_ledger", action="show", run_id=out["run_id"]))
    )
    assert shown["run"]["argv"][0] == str(venv)

    arts = json.loads(
        _run(
            rt.tool_registry.execute("analysis_ledger", action="artifacts", run_id=out["run_id"])
        )
    )
    assert any(a["rel"] == "figures/fig1.png" for a in arts["artifacts"])

    verified = json.loads(
        _run(rt.tool_registry.execute("analysis_ledger", action="verify", run_id=out["run_id"]))
    )
    assert verified["status"] == "INTACT"
    assert all(f["state"] == "INTACT" for f in verified["files"])

    (project / "figures" / "fig1.png").write_bytes(b"\x89PNG-two")
    drifted = json.loads(
        _run(rt.tool_registry.execute("analysis_ledger", action="verify", run_id=out["run_id"]))
    )
    assert drifted["status"] == "DRIFTED"
    assert any(f["state"] == "DRIFTED" for f in drifted["files"])


def test_ledger_diff_and_unknown_run(rt: _Rt, project: Path, calls, monkeypatch) -> None:
    _make_venv(project)
    _which_map(monkeypatch, {})
    (project / "a.py").write_text("x=1\n", encoding="utf-8")
    (project / "b.py").write_text("x=2\n", encoding="utf-8")
    one = json.loads(_run(rt.tool_registry.execute("analysis_run", path="a.py")))
    two = json.loads(_run(rt.tool_registry.execute("analysis_run", path="b.py")))
    diff = json.loads(
        _run(
            rt.tool_registry.execute(
                "analysis_ledger",
                action="diff",
                run_id=f"{one['run_id']},{two['run_id']}",
            )
        )
    )
    assert diff["argv_same"] is False
    assert diff["inputs_changed"]
    missing = _run(rt.tool_registry.execute("analysis_ledger", action="show", run_id="nope"))
    assert "NO_SUCH_RUN" in missing


def test_run_without_record_says_it_is_untraceable(
    rt: _Rt, project: Path, calls, monkeypatch
) -> None:
    _make_venv(project)
    _which_map(monkeypatch, {})
    (project / "fit.py").write_text("x=1\n", encoding="utf-8")
    out = json.loads(
        _run(rt.tool_registry.execute("analysis_run", path="fit.py", record=False))
    )
    assert out["ledger_path"] == ""
    assert any("NOT in the ledger" in w for w in out["warnings"])


def test_run_writes_params_file_for_python(rt: _Rt, project: Path, calls, monkeypatch) -> None:
    _make_venv(project)
    _which_map(monkeypatch, {})
    (project / "fit.py").write_text("x=1\n", encoding="utf-8")
    out = json.loads(
        _run(
            rt.tool_registry.execute(
                "analysis_run", path="fit.py", params_json='{"alpha": 0.05}'
            )
        )
    )
    params = Path(out["params_file"])
    assert params.is_file()
    assert json.loads(params.read_text(encoding="utf-8")) == {"alpha": 0.05}
    assert calls[-1]["env"]["REMEDY_PARAMS_FILE"] == str(params)


# ------------------------------------------------------------- data_profile

_CSV = (
    "id,group,score,note,constant,outcome_flag\n"
    "1,a,1.0,hello,K,0\n"
    "2,a,2.0,world,K,0\n"
    "3,b,9.0,,K,1\n"
    "4,b,10.0,  padded  ,K,1\n"
    "5,b,11.0,x,K,1\n"
    "5,b,11.0,x,K,1\n"
)


@pytest.fixture
def csv_path(project: Path) -> Path:
    p = project / "data.csv"
    p.write_text(_CSV, encoding="utf-8")
    return p


def test_data_profile_stdlib_path(rt: _Rt, csv_path: Path) -> None:
    out = json.loads(
        _run(
            rt.tool_registry.execute(
                "data_profile", path="data.csv", engine="stdlib", target="outcome_flag"
            )
        )
    )
    assert out["engine_used"] == "stdlib"
    assert out["rows_scanned"] == 6
    assert out["n_columns"] == 6
    assert out["delimiter"] == ","
    cols = {c["name"]: c for c in out["columns"]}
    assert cols["score"]["inferred_dtype"] == "float"
    assert cols["score"]["mean"] == pytest.approx(7.3333333, rel=1e-5)
    assert cols["note"]["n_missing"] == 1
    assert cols["note"]["whitespace_padded_values"] == 1
    assert out["constant_columns"] == ["constant"]
    assert out["duplicate_rows"] == 1
    assert out["class_balance"]["counts"] == {"0": 2, "1": 4}
    assert out["class_balance"]["minority_class"] == "0"
    assert out["class_balance"]["imbalance_ratio"] == 2.0
    names = {s["column"] for s in out["leakage_suspects"]}
    assert "group" in names  # perfectly separates outcome_flag
    assert any("stdlib engine" in n for n in out["notes"])
    assert "SUSPECTS" in out["leakage_disclaimer"]


def test_data_profile_stdlib_flags_leak_named_column(rt: _Rt, project: Path) -> None:
    (project / "d.csv").write_text(
        "x,y_pred\n" + "".join(f"{i},{i % 2}\n" for i in range(30)), encoding="utf-8"
    )
    out = json.loads(
        _run(rt.tool_registry.execute("data_profile", path="d.csv", engine="stdlib"))
    )
    suspect = next(s for s in out["leakage_suspects"] if s["column"] == "y_pred")
    assert "name matches" in suspect["reason"]
    assert suspect["what_to_check"]


def test_data_profile_project_engine_parses_pandas_json(
    rt: _Rt, project: Path, csv_path: Path, monkeypatch
) -> None:
    venv = _make_venv(project)
    _which_map(monkeypatch, {})
    profile = {
        "rows_scanned": 6,
        "truncated": False,
        "encoding": "",
        "delimiter": ",",
        "columns": [
            {
                "name": "score",
                "inferred_dtype": "float",
                "n_missing": 0,
                "pct_missing": 0.0,
                "n_unique": 5,
                "top_values": [],
                "mean": 7.33,
                "sd": 4.5,
                "n_numeric": 6,
            },
            {
                "name": "constant",
                "inferred_dtype": "categorical",
                "n_missing": 0,
                "pct_missing": 0.0,
                "n_unique": 1,
                "top_values": [{"value": "K", "count": 6}],
            },
        ],
        "duplicate_rows": 1,
        "duplicate_rows_exact": True,
        "class_balance": {"0": 2, "1": 4},
        "purity": {"_target_unique": 2},
        "samples": {},
        "notes": ["profiled with the project's pandas 2.2.2"],
    }
    seen: list[list[str]] = []

    async def fake(runtime, argv, *, cwd, timeout, env_extra=None):
        seen.append(list(argv))
        if len(argv) >= 3 and argv[1] == "-c" and at._ENV_MARKER in argv[2]:
            return _Res(
                0,
                at._ENV_MARKER
                + json.dumps({"modules": {"pandas": True}, "python": "3.12.0", "executable": ""}),
            )
        return _Res(0, "chatter\n" + at._PROFILE_MARKER + json.dumps(profile) + "\n")

    monkeypatch.setattr(at, "_sandbox_run", fake)
    out = json.loads(
        _run(
            rt.tool_registry.execute(
                "data_profile", path="data.csv", engine="project", target="outcome_flag"
            )
        )
    )
    assert out["engine_used"] == "project"
    assert out["rows_scanned"] == 6
    assert out["constant_columns"] == ["constant"]
    assert out["class_balance"]["counts"] == {"0": 2, "1": 4}
    assert not any("stdlib engine" in n for n in out["notes"])
    # the generated helper ran under the project interpreter, from .remedy-build/tmp
    helper = seen[-1]
    assert helper[0] == str(venv)
    assert helper[1].endswith("remedy_data_profile.py")
    assert Path(helper[1]).is_file()
    assert "import_module" in Path(helper[1]).read_text(encoding="utf-8")


def test_data_profile_auto_falls_back_to_stdlib_without_pandas(
    rt: _Rt, csv_path: Path, calls
) -> None:
    out = json.loads(_run(rt.tool_registry.execute("data_profile", path="data.csv")))
    assert out["engine_used"] == "stdlib"


def test_data_profile_project_failure_is_reported(
    rt: _Rt, project: Path, csv_path: Path, monkeypatch
) -> None:
    _make_venv(project)
    _which_map(monkeypatch, {})

    async def fake(runtime, argv, *, cwd, timeout, env_extra=None):
        if len(argv) >= 3 and argv[1] == "-c" and at._ENV_MARKER in argv[2]:
            return _Res(
                0,
                at._ENV_MARKER
                + json.dumps({"modules": {"pandas": False}, "python": "3.12", "executable": ""}),
            )
        return _Res(1, "", "boom")

    monkeypatch.setattr(at, "_sandbox_run", fake)
    out = _run(rt.tool_registry.execute("data_profile", path="data.csv", engine="project"))
    assert "NEEDS_PROJECT_ENV" in out
    assert "pandas" in out


def test_data_profile_parquet_has_no_stdlib_path(rt: _Rt, project: Path) -> None:
    (project / "t.parquet").write_bytes(b"PAR1")
    out = _run(rt.tool_registry.execute("data_profile", path="t.parquet", engine="stdlib"))
    assert "NEEDS_PROJECT_ENV" in out
    assert "pyarrow" in out


def test_data_profile_missing_file(rt: _Rt) -> None:
    assert "NOT_FOUND" in _run(rt.tool_registry.execute("data_profile", path="nope.csv"))


def test_data_profile_jsonl(rt: _Rt, project: Path) -> None:
    (project / "d.jsonl").write_text(
        '{"a": 1, "b": "x"}\n{"a": 2, "b": "y"}\n', encoding="utf-8"
    )
    out = json.loads(
        _run(rt.tool_registry.execute("data_profile", path="d.jsonl", engine="stdlib"))
    )
    assert out["rows_scanned"] == 2
    assert {c["name"] for c in out["columns"]} == {"a", "b"}


# ---------------------------------------------------------------- data_diff


def test_data_diff_reports_schema_and_distribution_drift(
    rt: _Rt, project: Path, calls
) -> None:
    (project / "left.csv").write_text(
        "id,score,dropped\n" + "".join(f"{i},{i},z\n" for i in range(40)), encoding="utf-8"
    )
    (project / "right.csv").write_text(
        "id,score,added\n" + "".join(f"{i},{i + 100},q\n" for i in range(50)), encoding="utf-8"
    )
    out = json.loads(
        _run(
            rt.tool_registry.execute(
                "data_diff", left="left.csv", right="right.csv", engine="stdlib"
            )
        )
    )
    assert out["schema"]["columns_added"] == ["added"]
    assert out["schema"]["columns_removed"] == ["dropped"]
    assert out["rows"] == {
        "left": 40,
        "right": 50,
        "delta": 10,
        "left_truncated": False,
        "right_truncated": False,
    }
    score = next(c for c in out["columns"] if c["column"] == "score")
    assert score["mean_delta"] == pytest.approx(105.0, rel=1e-6)
    assert score["ks"]["d"] == pytest.approx(1.0)
    assert score["ks"]["p_asymptotic"] < 0.01
    assert "asymptotic" in score["ks"]["method"]
    assert "large-sample approximation" in score["ks"]["accuracy"]


def test_data_diff_key_overlap_is_honest_about_not_computing_it(
    rt: _Rt, project: Path, calls
) -> None:
    for name in ("l.csv", "r.csv"):
        (project / name).write_text("id,v\n1,2\n", encoding="utf-8")
    out = json.loads(
        _run(
            rt.tool_registry.execute(
                "data_diff", left="l.csv", right="r.csv", key="id", engine="stdlib"
            )
        )
    )
    assert out["key_overlap"]["keys"] == ["id"]
    assert out["key_overlap"]["computed"] is False
    assert out["key_overlap"]["why"]


def test_ks_two_sample_matches_a_hand_computed_case() -> None:
    # Two disjoint samples: the empirical CDFs never overlap, so D = 1.
    res = at.ks_two_sample([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
    assert res["d"] == pytest.approx(1.0)
    # Identical samples: D = 0, p = 1.
    same = at.ks_two_sample([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert same["d"] == pytest.approx(0.0)
    assert same["p_asymptotic"] == pytest.approx(1.0)
    assert "n = 3 < 35" in same["accuracy"]


# ------------------------------------------------------------------ locations


def test_ledger_falls_back_to_remedy_home_without_a_project(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("REMEDY_HOME", str(home))
    path = at.ledger_path_for_project(None)
    assert str(path).startswith(str(home.resolve()))
    assert path.name == "ledger.jsonl"
    assert "research" in path.parts
