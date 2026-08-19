"""The offline demo the build reducer is exercised with.

This is the one place the whole reducer runs end to end without a model server:
a three-unit package, a deterministic stand-in worker, and real pytest as the
oracle. It is what proves the machine can go from a spec to green tests on its
own, so if the demo stops converging the reducer is broken and nothing else
would say so.

Every test passes an explicit out_dir. `run_demo` defaults to
``~/.remedy-builds-demo`` — the owner's real home — and a test must never write
there.
"""

from __future__ import annotations

import pytest

from remedy.core.builds.demo import (
    demo_correct_model,
    demo_project_spec,
    main,
    run_demo,
)

# --- the spec -----------------------------------------------------------------


def test_the_demo_describes_three_units():
    spec = demo_project_spec()
    assert [u.id for u in spec.units] == ["pkg", "currency", "report"]


def test_every_unit_has_somewhere_to_be_written():
    for unit in demo_project_spec().units:
        assert unit.path.endswith(".py")


def test_the_units_are_ordered_so_dependencies_come_first():
    """report imports currency; building it first could only fail."""
    ids = [u.id for u in demo_project_spec().units]
    assert ids.index("currency") < ids.index("report")


def test_the_dependent_unit_declares_what_it_needs():
    report = next(u for u in demo_project_spec().units if u.id == "report")
    assert "order.currency" in report.imports
    assert set(report.requires) == {"to_cents", "from_cents"}


def test_the_working_units_carry_behavioural_tests():
    """A unit with no oracle cannot be verified, only written."""
    for unit in demo_project_spec().units:
        if unit.id == "pkg":
            continue
        assert unit.tests, f"{unit.id} has no tests to verify it against"


def test_every_declared_signature_names_the_file_it_belongs_to():
    for unit in demo_project_spec().units:
        for sig in unit.declare:
            assert sig.defines_path == unit.path


# --- the stand-in worker --------------------------------------------------------


def test_the_package_marker_is_empty():
    pkg = next(u for u in demo_project_spec().units if u.id == "pkg")
    assert demo_correct_model(pkg, "", None) == ""


@pytest.mark.parametrize("unit_id", ["currency", "report"])
def test_each_unit_gets_source_that_compiles(unit_id):
    unit = next(u for u in demo_project_spec().units if u.id == unit_id)
    compile(demo_correct_model(unit, "", None), f"<{unit_id}>", "exec")


def test_the_currency_source_satisfies_its_own_tests():
    """The demo only proves anything if the stand-in is actually correct."""
    unit = next(u for u in demo_project_spec().units if u.id == "currency")
    ns: dict = {}
    exec(demo_correct_model(unit, "", None), ns)  # noqa: S102 - fixture source
    assert ns["to_cents"](1.5) == 150
    assert ns["to_cents"](0.10) == 10
    assert ns["from_cents"](150) == 1.5
    assert ns["from_cents"](10) == 0.10


def test_the_report_source_imports_what_it_declared():
    unit = next(u for u in demo_project_spec().units if u.id == "report")
    src = demo_correct_model(unit, "", None)
    assert "from order.currency import" in src


def test_the_worker_reads_nothing_but_the_unit():
    """Minimal context is the point — the closure and errors are ignored."""
    unit = next(u for u in demo_project_spec().units if u.id == "currency")
    a = demo_correct_model(unit, "", None)
    b = demo_correct_model(unit, "a completely different closure", ["errors"])
    assert a == b


# --- the driver ------------------------------------------------------------------


def test_the_demo_build_converges_to_green(tmp_path):
    """The end-to-end claim: spec in, verified package out, no model server."""
    result = run_demo(mode="mock", out_dir=tmp_path)
    assert result.ok, result.summary()


def test_the_build_writes_every_unit(tmp_path):
    result = run_demo(mode="mock", out_dir=tmp_path)
    assert set(result.files) >= {
        "order/__init__.py",
        "order/currency.py",
        "order/report.py",
    }


def test_the_result_can_describe_itself(tmp_path):
    assert run_demo(mode="mock", out_dir=tmp_path).summary()


def test_an_unknown_mode_falls_back_to_the_deterministic_worker(tmp_path):
    """A typo in --mode should not silently start reaching for a model server."""
    assert run_demo(mode="frobnicate", out_dir=tmp_path).ok


# --- the command line --------------------------------------------------------------


def test_the_cli_returns_zero_when_the_build_verifies(tmp_path, capsys):
    assert main(["--mode", "mock", "--out", str(tmp_path)]) == 0
    assert capsys.readouterr().out


def test_the_cli_prints_the_project_tree(tmp_path, capsys):
    main(["--mode", "mock", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert "order/currency.py" in out
    assert "pytest result" in out


def test_the_cli_returns_nonzero_when_the_build_does_not_verify(tmp_path, monkeypatch):
    """Exiting 0 on a failed build is how a red build looks green in CI."""

    class Failed:
        ok = False
        files: dict = {}

        def summary(self):
            return "did not converge"

    monkeypatch.setattr(
        "remedy.core.builds.demo.run_demo", lambda **kw: Failed()
    )
    assert main(["--mode", "mock", "--out", str(tmp_path)]) == 1


@pytest.mark.parametrize("mode", ["mock", "weak", "local"])
def test_every_advertised_mode_is_accepted_by_the_parser(mode, tmp_path, monkeypatch):
    seen: list = []

    class Fine:
        ok = True
        files: dict = {}

        def summary(self):
            return "ok"

    monkeypatch.setattr(
        "remedy.core.builds.demo.run_demo",
        lambda **kw: seen.append(kw) or Fine(),
    )
    monkeypatch.setattr("remedy.core.builds.demo._print_demo", lambda *a: None)
    main(["--mode", mode, "--out", str(tmp_path)])
    assert seen[0]["mode"] == mode


def test_an_unadvertised_mode_is_rejected_by_the_parser(tmp_path):
    with pytest.raises(SystemExit):
        main(["--mode", "telepathy", "--out", str(tmp_path)])


def test_the_knobs_reach_the_build(tmp_path, monkeypatch):
    seen: list = []

    class Fine:
        ok = True
        files: dict = {}

        def summary(self):
            return "ok"

    monkeypatch.setattr(
        "remedy.core.builds.demo.run_demo",
        lambda **kw: seen.append(kw) or Fine(),
    )
    monkeypatch.setattr("remedy.core.builds.demo._print_demo", lambda *a: None)
    main(
        [
            "--out", str(tmp_path),
            "--budget", "500",
            "--max-repairs", "2",
            "--defect-rate", "0.9",
            "--base-url", "http://127.0.0.1:8080/v1",
        ]
    )
    assert seen[0]["context_budget"] == 500
    assert seen[0]["max_repairs"] == 2
    assert seen[0]["defect_rate"] == 0.9
    assert seen[0]["base_url"] == "http://127.0.0.1:8080/v1"
