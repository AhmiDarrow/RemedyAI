"""Content-addressed hop memo."""

from __future__ import annotations

from pathlib import Path

from remedy.core.build_hop_memo import lookup_hop, memo_key, store_hop, try_reuse
from remedy.core.builds.reducer import OracleError, UnitSpec


def test_memo_roundtrip(tmp_path: Path):
    key = memo_key(path="a.py", symbol="f", behavior="return 1", tests="", closure="ctx")
    assert lookup_hop(tmp_path, key) is None
    store_hop(tmp_path, key, "def f():\n    return 1\n", ok=True, path="a.py")
    assert lookup_hop(tmp_path, key) == "def f():\n    return 1\n"
    store_hop(tmp_path, key, "bad", ok=False, path="a.py")
    assert lookup_hop(tmp_path, key) is None


def test_try_reuse_rejects_oracle_red(tmp_path: Path):
    key = memo_key(path="a.py", symbol="f")
    store_hop(tmp_path, key, "not valid python (", ok=True, path="a.py")

    def oracle(unit, src):  # noqa: ARG001
        return [OracleError("f", "syntax")] if "(" in src else []

    unit = UnitSpec(id="f", path="a.py")
    assert try_reuse(tmp_path, key, oracle_fn=oracle, unit=unit) is None
