"""Unified / Begin-Patch apply."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_apply_patch import apply_patch_text, parse_patch


def _rt(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=root),
    )


def test_parse_unified():
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    files = parse_patch(diff)
    assert len(files) == 1
    assert files[0].path == "foo.py"
    assert files[0].hunks


def test_parse_begin_patch():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: foo.py\n"
        "@@\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
        "*** End Patch\n"
    )
    files = parse_patch(patch)
    assert files[0].path == "foo.py"


def test_apply_unified(tmp_path: Path):
    (tmp_path / "foo.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    res = apply_patch_text(_rt(tmp_path), diff, root=tmp_path)
    assert res["ok"] is True, res
    assert "return 2" in (tmp_path / "foo.py").read_text(encoding="utf-8")


def test_apply_refuses_ambiguous(tmp_path: Path):
    (tmp_path / "foo.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    res = apply_patch_text(_rt(tmp_path), diff, root=tmp_path)
    assert res["ok"] is False
    assert "2 times" in str(res.get("error") or "")
    assert (tmp_path / "foo.py").read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_two_file_second_hunk_miss_leaves_first_unchanged(tmp_path: Path):
    (tmp_path / "a.py").write_text("old\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("keep\n", encoding="utf-8")
    diff = (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1 +1 @@\n"
        "-missing_this\n"
        "+newb\n"
    )
    res = apply_patch_text(_rt(tmp_path), diff, root=tmp_path)
    assert res["ok"] is False
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "old\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "keep\n"


def test_apply_patch_io_error_restores_first_file(tmp_path: Path, monkeypatch):
    (tmp_path / "a.py").write_text("old\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("keep\n", encoding="utf-8")
    from remedy.core.atomic_json import write_text_atomic as real

    n = {"i": 0}

    def boom(path, text, **k):
        n["i"] += 1
        if n["i"] == 2:
            raise OSError("disk full")
        return real(path, text, **k)

    monkeypatch.setattr("remedy.core.atomic_json.write_text_atomic", boom)
    diff = (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1 +1 @@\n"
        "-keep\n"
        "+newb\n"
    )
    res = apply_patch_text(_rt(tmp_path), diff, root=tmp_path)
    assert res["ok"] is False
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "old\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "keep\n"


def test_apply_refuses_write_jail_exception(tmp_path: Path):
    """resolve_tool_path refusals must not fall through to an absolute dest."""
    from remedy.core.errors import SecurityError

    outside = tmp_path.parent / "apply_patch_pwned.txt"
    if outside.exists():
        outside.unlink()

    def refuse(path: str, **_k):
        raise SecurityError(f"Path outside allowed roots: {path}")

    rt = SimpleNamespace(
        effective_project_path=lambda: tmp_path,
        resolve_tool_path=refuse,
        config=SimpleNamespace(home_dir=tmp_path),
    )
    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {outside}\n"
        "+pwned\n"
        "*** End Patch\n"
    )
    res = apply_patch_text(rt, patch, root=tmp_path)
    assert res["ok"] is False
    assert "jail" in str(res.get("error") or "").lower()
    assert not outside.exists()


def test_apply_preserves_dotfile_names(tmp_path: Path):
    """`.env` must not become `env` via lstrip('./')."""
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: .env\n"
        "@@\n"
        "-A=1\n"
        "+A=2\n"
        "*** End Patch\n"
    )
    res = apply_patch_text(_rt(tmp_path), patch, root=tmp_path)
    assert res["ok"] is True, res
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "A=2\n"
    assert not (tmp_path / "env").exists()


def test_apply_refuses_add_file_when_exists(tmp_path: Path):
    (tmp_path / "foo.py").write_text("old\n", encoding="utf-8")
    patch = (
        "*** Begin Patch\n"
        "*** Add File: foo.py\n"
        "+new\n"
        "*** End Patch\n"
    )
    res = apply_patch_text(_rt(tmp_path), patch, root=tmp_path)
    assert res["ok"] is False
    assert "already exists" in str(res.get("error") or "")
    assert (tmp_path / "foo.py").read_text(encoding="utf-8") == "old\n"


def test_apply_refuses_absolute_path_without_runtime(tmp_path: Path):
    outside = tmp_path.parent / "apply_patch_abs_pwned.txt"
    if outside.exists():
        outside.unlink()
    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {outside}\n"
        "+pwned\n"
        "*** End Patch\n"
    )
    res = apply_patch_text(None, patch, root=tmp_path)
    assert res["ok"] is False
    assert not outside.exists()
