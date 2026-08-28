"""Linux vision tarball ships relative .so version symlinks."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from remedy.vision.install import _extract_tar


def _write_tar(path: Path, members: list[tuple[str, bytes | None, str | None]]) -> None:
    """members: (name, file_bytes|None, link_target|None)."""
    with tarfile.open(path, "w:gz") as tf:
        for name, data, link in members:
            info = tarfile.TarInfo(name=name)
            if link is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = link
                info.size = 0
                tf.addfile(info)
            else:
                payload = data or b""
                info.size = len(payload)
                import io

                tf.addfile(info, io.BytesIO(payload))


def test_extract_tar_keeps_relative_so_symlinks(tmp_path: Path):
    archive = tmp_path / "llama.tar.gz"
    dest = tmp_path / "out"
    _write_tar(
        archive,
        [
            ("pkg/libllama.so.0.0.9", b"real-so", None),
            ("pkg/libllama.so.0", None, "libllama.so.0.0.9"),
            ("pkg/libllama.so", None, "libllama.so.0"),
            ("pkg/llama-server", b"#!/bin/sh\necho ok\n", None),
        ],
    )
    _extract_tar(archive, dest)
    assert (dest / "pkg" / "libllama.so.0.0.9").read_bytes() == b"real-so"
    link = dest / "pkg" / "libllama.so.0"
    assert link.is_symlink()
    assert link.readlink().as_posix() == "libllama.so.0.0.9"
    assert (dest / "pkg" / "libllama.so").resolve() == (
        dest / "pkg" / "libllama.so.0.0.9"
    ).resolve()


def test_extract_tar_blocks_absolute_symlink(tmp_path: Path):
    archive = tmp_path / "bad.tar.gz"
    dest = tmp_path / "out"
    _write_tar(
        archive,
        [
            ("pkg/evil", None, "/etc/passwd"),
        ],
    )
    with pytest.raises(ValueError, match="absolute link|link escape"):
        _extract_tar(archive, dest)


def test_extract_tar_blocks_escape_symlink(tmp_path: Path):
    archive = tmp_path / "esc.tar.gz"
    dest = tmp_path / "out"
    _write_tar(
        archive,
        [
            ("pkg/evil", None, "../../outside"),
        ],
    )
    with pytest.raises(ValueError, match="link escape|absolute link"):
        _extract_tar(archive, dest)
