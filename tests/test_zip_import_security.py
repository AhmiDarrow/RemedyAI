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


def test_zip_member_stream_cap(tmp_path: Path):
    """Stream counter rejects members that exceed max_member_bytes while reading."""
    zpath = tmp_path / "fat.zip"
    payload = b"x" * 50_000
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("blob.bin", payload)
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zpath, "r") as zf, pytest.raises(ValueError, match="size cap|too large"):
        _safe_extract_zip(zf, dest, max_member_bytes=10_000)
    # Partial file must not remain
    leftovers = list(dest.rglob("*"))
    assert not any(p.is_file() for p in leftovers)


def test_zip_symlink_member_blocked(tmp_path: Path):
    """Unix symlink mode in external_attr must be refused."""
    zpath = tmp_path / "link.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        info = zipfile.ZipInfo("evil-link")
        # S_IFLNK = 0o120000 stored in high 16 bits of external_attr
        info.external_attr = (0o120000 << 16)
        zf.writestr(info, b"/tmp/escape")
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zpath, "r") as zf, pytest.raises(ValueError, match="symlink"):
        _safe_extract_zip(zf, dest)
    assert not (dest / "evil-link").exists()


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
