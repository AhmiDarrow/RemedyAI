"""Phase A1: full-wipe / skills-wipe must leave no ghost skill stats.

Keeps the personal-partner reinstall path honest: after skills wipe or full
purge, skill_stats.json must not resurrect ghost success rates.
"""

from __future__ import annotations

from pathlib import Path

from remedy.interfaces import uninstaller as uninst


def test_wipe_skills_removes_skill_stats(tmp_path: Path, monkeypatch):
    home = tmp_path / ".remedy"
    skills = home / "skills"
    skills.mkdir(parents=True)
    (skills / "x" / "SKILL.md").parent.mkdir()
    (skills / "x" / "SKILL.md").write_text("# x\n", encoding="utf-8")
    stats = home / "skill_stats.json"
    stats.write_text('{"version":1,"skills":{"x":{"total_executions":3}}}', encoding="utf-8")
    # Leave memory so we prove skills wipe is selective
    mem = home / "memory.db"
    mem.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(uninst, "REMEDY_HOME", home)
    uninst._wipe_skills()  # noqa: SLF001

    assert not skills.exists()
    assert not stats.exists()
    assert mem.exists()


def test_full_wipe_paths_documented_in_ps1():
    """Desktop NSIS wipe script still covers config/skills/full + skill_stats."""
    root = Path(__file__).resolve().parents[1]
    ps1 = root / "desktop" / "src-tauri" / "windows" / "uninstall_wipe.ps1"
    text = ps1.read_text(encoding="utf-8")
    assert "skill_stats.json" in text
    assert "full" in text.lower()
    assert ".remedy" in text
    # Visual decoder / llama.cpp must be stopped and removed on config or full wipe
    assert "llama-server" in text
    assert "vision" in text
    assert "Remove-VisionTree" in text or "vision" in text


def test_wipe_config_removes_vision_tree(tmp_path: Path, monkeypatch):
    home = tmp_path / ".remedy"
    vision = home / "vision" / "runtime"
    vision.mkdir(parents=True)
    (vision / "llama-server.exe").write_bytes(b"fake")
    (home / "vision" / "models" / "qwen").mkdir(parents=True)
    (home / "vision" / "models" / "qwen" / "m.gguf").write_bytes(b"gguf")
    (home / "config.toml").write_text("name='x'\n", encoding="utf-8")
    mem = home / "memory.db"
    mem.write_text("keep", encoding="utf-8")

    monkeypatch.setattr(uninst, "REMEDY_HOME", home)
    uninst._wipe_config()  # noqa: SLF001
    uninst._wipe_vision()  # noqa: SLF001

    assert not (home / "vision").exists()
    assert mem.exists()  # memory preserved on config wipe
