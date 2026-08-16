"""Stack fingerprint + orientation + local PATH helpers."""

from pathlib import Path

from remedy.core.project_fingerprint import (
    fingerprint_path,
    local_bin_dirs,
    orientation_block,
    path_env_with_local_bins,
)


def test_fingerprint_godot(tmp_path: Path):
    (tmp_path / "project.godot").write_text("; godot\n", encoding="utf-8")
    (tmp_path / "Godot_v4.7.1-stable_win64_console.exe").write_bytes(b"MZ")
    fp = fingerprint_path(tmp_path)
    assert "godot" in fp.stacks
    assert fp.suggest_verify
    assert any("Godot" in h for h in fp.hints)
    lines = fp.context_lines()
    assert lines and "godot" in "\n".join(lines).lower()


def test_fingerprint_python_pytest(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    fp = fingerprint_path(tmp_path)
    assert "python" in fp.stacks
    assert fp.suggest_verify and "pytest" in fp.suggest_verify


def test_orientation_agents_and_handoff(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# Agent notes\nDo not force push.\n", encoding="utf-8")
    mem = tmp_path / "memory"
    mem.mkdir()
    notes = mem / "SESSION_NOTES"
    notes.mkdir()
    (notes / "HANDOFF_test.md").write_text("# Handoff\nDone X\n", encoding="utf-8")
    (mem / "LATEST_HANDOFF.md").write_text("HANDOFF_test.md\n", encoding="utf-8")
    block = orientation_block(tmp_path)
    assert "AGENTS.md" in block
    assert "LATEST_HANDOFF" in block


def test_local_bin_dirs_venv(tmp_path: Path):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    dirs = local_bin_dirs(tmp_path)
    assert any(d.name == "Scripts" for d in dirs)
    env = path_env_with_local_bins(tmp_path, base_env={"PATH": "/usr/bin"})
    assert str(scripts) in env.get("PATH", "")
