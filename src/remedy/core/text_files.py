"""Language-agnostic text vs binary detection for search and file tools.

Do not maintain a language extension allowlist as an exclusive gate. Prefer
content sniffing so GDScript, Zig, Elixir, Makefiles, etc. stay searchable.
"""

from __future__ import annotations

from pathlib import Path

# Read at most this many bytes when sniffing.
SNIFF_BYTES = 8192
# Skip huge files in pure-Python search unless explicitly targeted.
DEFAULT_MAX_SEARCH_FILE_BYTES = 2_000_000
# If more than this fraction of sampled bytes are "control-ish", treat as binary.
_BINARY_CTRL_RATIO = 0.30


def is_probably_text(
    source: Path | bytes | bytearray | memoryview,
    *,
    max_bytes: int = SNIFF_BYTES,
) -> bool:
    """Return True when *source* looks like searchable text (not binary).

    *source* may be a path or raw bytes. Empty content is treated as text.
    """
    data: bytes
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source[:max_bytes])
    else:
        path = Path(source)
        try:
            if not path.is_file():
                return False
            size = path.stat().st_size
            if size == 0:
                return True
            with path.open("rb") as fh:
                data = fh.read(max_bytes)
        except OSError:
            return False

    if not data:
        return True
    # NUL in the sample is a strong binary signal (and breaks line tools).
    if b"\x00" in data:
        return False

    # Count non-text-ish control bytes (exclude common whitespace).
    ctrl = 0
    for b in data:
        if b < 9 or (13 < b < 32 and b != 27):
            # allow \t(9) \n(10) \v \f \r(13); treat other C0 as binary-ish
            if b not in (9, 10, 11, 12, 13):
                ctrl += 1
        elif b == 127:
            ctrl += 1
    if len(data) > 0 and (ctrl / len(data)) > _BINARY_CTRL_RATIO:
        return False
    return True


def should_search_file(
    path: Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_SEARCH_FILE_BYTES,
) -> bool:
    """True if *path* is a regular file worth opening for text search."""
    try:
        if not path.is_file():
            return False
        size = path.stat().st_size
        if size > max_file_bytes:
            return False
    except OSError:
        return False
    return is_probably_text(path)
