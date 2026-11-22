"""Cap on-disk soul and residue so learning every turn cannot bloat the prompt."""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Defaults stay conservative; PR C exposes them as advanced knobs.
SOUL_MAX_BYTES = 64 * 1024
RESIDUE_MAX_BYTES = 256 * 1024
VIGIL_JOURNAL_MAX_BYTES = 64 * 1024


def compact_personhood(
    home: str | Path | None,
    *,
    soul_max_bytes: int = SOUL_MAX_BYTES,
    residue_max_bytes: int = RESIDUE_MAX_BYTES,
    journal_max_bytes: int = VIGIL_JOURNAL_MAX_BYTES,
) -> dict[str, Any]:
    """Force existing salience eviction and cap oversized local files.

    Safe to call from the unattended tick or a dream. Never raises.
    Does not inject anything into the prompt; it only trims what is on disk.
    """
    out: dict[str, Any] = {"ok": True, "soul": False, "residue": False, "journal": False}
    if not home:
        return {**out, "ok": False, "skipped": "no_home"}
    root = Path(home).expanduser()
    try:
        out["soul"] = _compact_soul(root, soul_max_bytes)
    except Exception:
        logger.debug("soul compact failed", exc_info=True)
    try:
        out["residue"] = _compact_residue(root, residue_max_bytes)
    except Exception:
        logger.debug("residue compact failed", exc_info=True)
    try:
        out["journal"] = _trim_file(root / "vigil_journal.jsonl", journal_max_bytes)
    except Exception:
        logger.debug("journal compact failed", exc_info=True)
    out["changed"] = bool(out["soul"] or out["residue"] or out["journal"])
    return out


def _compact_soul(home: Path, max_bytes: int) -> bool:
    from remedy.memory.soul.field import load_soul_field, save_soul_field, soul_dir

    path = soul_dir(home) / "field.json"
    if not path.is_file():
        return False
    sf = load_soul_field(home)
    before = 0
    with suppress(Exception):
        before = path.stat().st_size
    # retain_episodes / dream caps run in __post_init__ and save.
    save_soul_field(sf, home)
    after = before
    with suppress(Exception):
        path = soul_dir(home) / "field.json"
        if path.is_file():
            after = path.stat().st_size
            if after > max_bytes:
                # Drop oldest episodes until under the cap; retain_episodes already
                # kept the salient tail, so this is a last-resort size bound.
                while after > max_bytes and sf.episodes:
                    sf.episodes = sf.episodes[1:]
                    save_soul_field(sf, home)
                    after = path.stat().st_size
    return after < before or (before > max_bytes and after <= max_bytes)


def _compact_residue(home: Path, max_bytes: int) -> bool:
    changed = False
    with suppress(Exception):
        from remedy.memory.cas import configure_cas

        # Do not create a CAS just to compact it.
        if (home / "cas" / "objects.db").is_file():
            cas = configure_cas(home)
            if cas is not None and hasattr(cas, "compact"):
                result = cas.compact()
                if isinstance(result, dict):
                    changed = any(int(v or 0) > 0 for v in result.values() if isinstance(v, (int, float)))
                else:
                    changed = bool(result)
    # Bound any leftover residue dumps under home.
    for name in ("residue.jsonl", "cas_residue.jsonl"):
        if _trim_file(home / name, max_bytes):
            changed = True
    return changed


def _trim_file(path: Path, max_bytes: int) -> bool:
    if max_bytes <= 0 or not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= max_bytes:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    # Keep the tail so recent lines survive.
    tail = data[-max_bytes:]
    nl = tail.find(b"\n")
    if 0 <= nl < len(tail) - 1:
        tail = tail[nl + 1 :]
    from remedy.core.atomic_json import replace_with_retry, scratch_path

    tmp = scratch_path(path)
    tmp.write_bytes(tail)
    replace_with_retry(tmp, path)
    return True
