"""Keep base64 image payloads out of chat text.

A single ``![alt](data:image/png;base64,…)`` from an image tool can weigh
~1 MB (close to a million tokens once it is re-sent as provider history).
Every surface that stores or replays chat text runs through here:

* :func:`extract_inline_images` — decode data URIs to files under the Remedy
  home and replace them with ``[image saved: <path>]`` (persistence path).
* :func:`strip_inline_images` — replace payloads with a short stub without
  touching disk (provider history, export).

Only payloads above ``min_bytes`` (default 2 KB) are touched; tiny icons pass.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

INLINE_IMAGE_MIN_BYTES = 2_048

_DATA_URI = re.compile(
    r"data:image/(?P<sub>[a-zA-Z0-9.+-]+);base64,(?P<b64>[A-Za-z0-9+/=\s]+)"
)
# Markdown image whose target is a data URI: ``![alt](data:image/...)``.
_MD_DATA_IMAGE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*<?(?P<uri>data:image/[a-zA-Z0-9.+-]+;base64,"
    r"[A-Za-z0-9+/=\s]+)>?\s*\)"
)

_EXT = {"jpeg": "jpg", "svg+xml": "svg"}


def has_inline_image(text: str | None) -> bool:
    if not text:
        return False
    return "data:image/" in text and "base64," in text


def _b64_len(b64: str) -> int:
    return len(b64) - sum(b64.count(ch) for ch in " \n\r\t")


def strip_inline_images(
    text: str | None,
    *,
    min_bytes: int = INLINE_IMAGE_MIN_BYTES,
    stub: str = "[inline image omitted]",
) -> str:
    """Replace large base64 image payloads with ``stub`` (no disk access)."""
    if not text or not has_inline_image(text):
        return text or ""
    min_b64 = (min_bytes * 4) // 3

    def _md(m: re.Match[str]) -> str:
        if _b64_len(m.group("uri")) < min_b64:
            return m.group(0)
        alt = (m.group("alt") or "").strip()
        return f"{stub}{f' ({alt})' if alt else ''}"

    out = _MD_DATA_IMAGE.sub(_md, text)

    def _raw(m: re.Match[str]) -> str:
        return m.group(0) if _b64_len(m.group("b64")) < min_b64 else stub

    return _DATA_URI.sub(_raw, out)


def inline_images_dir(home_dir: str | os.PathLike[str] | None = None) -> Path:
    """``<REMEDY_HOME>/comfy_out/inline`` — same tree the ComfyUI tool writes to."""
    home = (
        Path(home_dir).expanduser()
        if home_dir
        else Path(os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()
    )
    return home / "comfy_out" / "inline"


def extract_inline_images(
    text: str | None,
    *,
    home_dir: str | os.PathLike[str] | None = None,
    out_dir: str | os.PathLike[str] | None = None,
    min_bytes: int = INLINE_IMAGE_MIN_BYTES,
) -> tuple[str, list[Path]]:
    """Write embedded images to disk; return (clean text, saved paths).

    Each payload becomes ``[image saved: <path>]`` (markdown images keep the
    alt as a ``![alt](<path>)`` link so chat bubbles still render it via
    ``/api/media``). Undecodable payloads fall back to the plain stub.
    """
    if not text or not has_inline_image(text):
        return text or "", []
    dest = Path(out_dir) if out_dir is not None else inline_images_dir(home_dir)
    min_b64 = (min_bytes * 4) // 3
    saved: list[Path] = []

    def _save(sub: str, b64: str) -> Path | None:
        try:
            raw = base64.b64decode("".join(b64.split()), validate=False)
        except (binascii.Error, ValueError):
            return None
        if not raw:
            return None
        ext = _EXT.get(sub.lower(), re.sub(r"[^a-z0-9]", "", sub.lower()) or "bin")
        name = f"{hashlib.sha1(raw).hexdigest()[:16]}.{ext}"
        path = dest / name
        try:
            dest.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(raw)
        except OSError:
            logger.debug("inline image save failed: %s", path, exc_info=True)
            return None
        saved.append(path)
        return path

    def _md(m: re.Match[str]) -> str:
        uri = m.group("uri")
        if _b64_len(uri) < min_b64:
            return m.group(0)
        dm = _DATA_URI.match(uri)
        path = _save(dm.group("sub"), dm.group("b64")) if dm else None
        alt = (m.group("alt") or "image").strip() or "image"
        if path is None:
            return f"[inline image omitted] ({alt})"
        return f"![{alt}](<{path.as_posix()}>)\n[image saved: {path}]"

    out = _MD_DATA_IMAGE.sub(_md, text)

    def _raw(m: re.Match[str]) -> str:
        if _b64_len(m.group("b64")) < min_b64:
            return m.group(0)
        path = _save(m.group("sub"), m.group("b64"))
        return "[inline image omitted]" if path is None else f"[image saved: {path}]"

    return _DATA_URI.sub(_raw, out), saved


def heal_inline_images(
    text: str | None,
    *,
    home_dir: str | os.PathLike[str] | None = None,
    min_bytes: int = INLINE_IMAGE_MIN_BYTES,
) -> tuple[str, list[Path], bool]:
    """Extract leftover data-URIs. ``persist_ok`` only when files landed and none remain.

    Failed decode / disk full must not rewrite the row to a stub — that would
    delete the owner's picture (session 765c Comfy hero).
    """
    if not text or not has_inline_image(text):
        return text or "", [], False
    cleaned, saved = extract_inline_images(
        text, home_dir=home_dir, min_bytes=min_bytes
    )
    persist_ok = bool(saved) and not has_inline_image(cleaned)
    return cleaned, saved, persist_ok
