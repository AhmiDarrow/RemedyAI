"""Stability follow-ups: overlay hops don't touch live build state, snapshot
dirs are pruned, and the memo behavioral check never writes the live tree."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_engine import BuildTurnState
from remedy.core.build_isolated import OverlayRuntime


def _rt(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: (root / p) if not Path(p).is_absolute() else Path(p),
        config=SimpleNamespace(home_dir=root),
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
    )


def test_overlay_shadows_build_state(tmp_path):
    """get_build_state on an overlay must NOT return the live runtime's state,
    so parallel hops can't race st.write_steps / write_set."""
    from remedy.core.build_engine import get_build_state

    root = tmp_path / "proj"
    root.mkdir()
    rt = _rt(root)
    live_map = {"": BuildTurnState(active=True, write_steps=5)}
    rt._build_turns = live_map
    overlay = OverlayRuntime(rt, root, root)
    # The overlay must expose its OWN empty map, not delegate to the live one,
    # so get_build_state returns None and hops can't mutate the shared state.
    assert overlay._build_turns == {}
    assert overlay._build_turns is not live_map
    assert get_build_state(overlay) is None
    # The inner runtime's map is untouched.
    assert rt._build_turns is live_map and rt._build_turns[""].write_steps == 5


def test_snapshot_dirs_are_pruned_with_the_manifest(tmp_path):
    from remedy.core.build_snapshot import _snap_root, load_manifest, snapshot_paths

    root = tmp_path / "proj"
    root.mkdir()
    target = root / "f.py"
    target.write_text("x = 0\n", encoding="utf-8")
    for i in range(95):
        target.write_text(f"x = {i}\n", encoding="utf-8")
        snapshot_paths(root, ["f.py"], note=f"s{i}")
    manifest = load_manifest(root)
    assert len(manifest) == 80
    ids = {str(s.get("snap_id")) for s in manifest}
    on_disk = {p.name for p in _snap_root(root).iterdir() if p.is_dir()}
    # No orphan snapshot dirs beyond what the manifest still references.
    assert on_disk == ids, on_disk - ids


def test_memo_behavioral_check_does_not_write_project_root(tmp_path, monkeypatch):
    """The cached-candidate behavioral re-check must run in a temp tree, never
    the live project (which would drop test_*.py and overwrite the source
    before the pre-hop snapshot)."""
    import remedy.core.build_live_hop as blh
    from remedy.core.builds.reducer import UnitSpec

    root = tmp_path / "proj"
    root.mkdir()
    (root / "widget.py").write_text("def helper():\n    return 0\n", encoding="utf-8")

    captured_roots: list[str] = []

    class _Oracle:
        def __init__(self, r, timeout_s=45.0):
            captured_roots.append(str(r))
            self.root = Path(r)

        def __call__(self, unit, state):
            # Emulate the real oracle writing into whatever root it was given.
            (self.root / "test_probe.py").write_text("x", encoding="utf-8")
            return []

    monkeypatch.setattr("remedy.core.builds.reducer.PytestOracle", _Oracle)

    def fake_try_reuse(memo_root, memo_k, *, oracle_fn, behavioral_fn, unit):
        behavioral_fn(unit, "def helper():\n    return 1\n")
        return None

    monkeypatch.setattr(blh, "make_runtime_llm_model", lambda rt: (lambda u, c, e: ""))
    import remedy.core.build_hop_memo as memo
    monkeypatch.setattr(memo, "try_reuse", fake_try_reuse)

    _ = UnitSpec  # ensure import shape is valid
    blh.live_unit_hop(
        _rt(root),
        path="widget.py",
        symbol="helper",
        behavior="return 1",
        tests="def test_helper():\n    import widget\n    assert widget.helper() == 1\n",
        use_llm=True,
        max_repairs=1,
    )
    # The oracle ran against a temp dir, and no probe file leaked into the tree.
    assert captured_roots, "behavioral check did not run"
    assert all(str(root) not in r for r in captured_roots), captured_roots
    assert not (root / "test_probe.py").exists()
