"""Zip Slip protection + quarantine on skill import."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from remedy.skills.exporter import SkillExporter, _safe_extract_zip


def test_zip_slip_blocked(tmp_path: Path):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
        zf.writestr("ok/SKILL.md", "---\nname: x\ndescription: d\n---\n\nbody\n")
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zpath, "r") as zf, pytest.raises(ValueError, match="Zip Slip"):
        _safe_extract_zip(zf, dest)
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_ok(tmp_path: Path):
    zpath = tmp_path / "good.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(
            "pack-me/SKILL.md",
            "---\nname: pack-me\ndescription: exportable\n---\n\n# Hi\n",
        )
    dest = tmp_path / "extract"
    dest.mkdir()
    with zipfile.ZipFile(zpath, "r") as zf:
        _safe_extract_zip(zf, dest)
    assert (dest / "pack-me" / "SKILL.md").is_file()


def test_import_quarantine_sets_flag(tmp_path: Path):
    # Build a clean pack via exporter
    src = tmp_path / "src" / "safe-skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: safe-skill\ndescription: ok\n---\n\n# Body\n",
        encoding="utf-8",
    )
    from remedy.skills.loader import load_skill_from_dir

    skill = load_skill_from_dir(src)
    out = tmp_path / "out"
    out.mkdir()
    exp = SkillExporter(out)
    zpath = exp.export_pack([skill])
    dest = tmp_path / "skills"
    dest.mkdir()
    imported = exp.import_pack_quarantine(zpath, dest)
    assert len(imported) >= 1
    assert imported[0].manifest.metadata.get("quarantine") is True
