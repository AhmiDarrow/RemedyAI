"""Hop snapshots + reverse bisect — localize which write broke the build.

Frontier E: every materialize can be reversed; red waves bisect to the last
green frontier without human blame assignment.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic


@dataclass
class SnapEntry:
    snap_id: str
    paths: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    ok_after: bool | None = None  # set after subsequent verify
    note: str = ""
    parent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _snap_root(project: Path) -> Path:
    d = project / ".remedy-build" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(project: Path) -> Path:
    return _snap_root(project) / "manifest.json"


def load_manifest(project: Path | str) -> list[dict[str, Any]]:
    project = Path(project)
    p = _manifest_path(project)
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return list(raw.get("snaps") or []) if isinstance(raw, dict) else []
    except Exception:
        return []


def _save_manifest(project: Path, snaps: list[dict[str, Any]]) -> None:
    p = _manifest_path(project)
    write_json_atomic(p, {"snaps": snaps[-80:], "updated": time.time()})


def snapshot_paths(
    project: Path | str,
    paths: list[str],
    *,
    note: str = "",
    parent_id: str = "",
) -> dict[str, Any]:
    """Copy current contents of *paths* into a new snapshot; return meta."""
    project = Path(project)
    if project.is_file():
        project = project.parent
    root = _snap_root(project)
    # time.time() on Windows can collide for two snaps in one test/turn.
    blob = f"{time.time_ns()}:{note}:{','.join(paths)}:{os.urandom(8).hex()}"
    sid = hashlib.sha1(blob.encode()).hexdigest()[:12]
    dest = root / sid
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for raw in paths or []:
        rel = str(raw).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        src = project / rel if not Path(raw).is_absolute() else Path(raw)
        try:
            if not src.is_file():
                # record missing as empty tombstone
                (dest / (rel.replace("/", "__") + ".missing")).write_text("", encoding="utf-8")
                saved.append(rel)
                continue
            # flat storage with encoded name + sidecar path
            key = rel.replace("/", "__")
            shutil.copy2(src, dest / key)
            (dest / (key + ".path")).write_text(rel, encoding="utf-8")
            saved.append(rel)
        except Exception:
            continue
    entry = SnapEntry(snap_id=sid, paths=saved, note=note[:200], parent_id=parent_id)
    snaps = load_manifest(project)
    snaps.append(entry.to_dict())
    _save_manifest(project, snaps)
    return entry.to_dict()


def restore_snapshot(project: Path | str, snap_id: str) -> dict[str, Any]:
    """Restore files from *snap_id* onto the project tree."""
    project = Path(project)
    if project.is_file():
        project = project.parent
    dest = _snap_root(project) / snap_id
    if not dest.is_dir():
        return {"ok": False, "error": f"snapshot {snap_id} not found", "restored": []}
    restored: list[str] = []
    for path_file in dest.glob("*.path"):
        try:
            rel = path_file.read_text(encoding="utf-8").strip()
            key = path_file.name[: -len(".path")]
            body = dest / key
            if not body.is_file():
                continue
            # Path jail: the sidecar is agent-writable — never restore to an
            # absolute path or outside the project root.
            if not rel or Path(rel).is_absolute() or rel.startswith(("/", "\\")):
                continue
            target = (project / rel).resolve()
            try:
                target.relative_to(project.resolve())
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(body, target)
            restored.append(rel)
        except Exception:
            continue
    # missing markers → delete if present? skip destructive delete
    return {"ok": True, "snap_id": snap_id, "restored": restored}


def mark_snapshot_ok(project: Path | str, snap_id: str, ok: bool) -> None:
    project = Path(project)
    snaps = load_manifest(project)
    for s in snaps:
        if s.get("snap_id") == snap_id:
            s["ok_after"] = bool(ok)
    _save_manifest(project, snaps)


def last_green_snapshot(project: Path | str) -> dict[str, Any] | None:
    snaps = load_manifest(project)
    for s in reversed(snaps):
        if s.get("ok_after") is True:
            return s
    return None


def bisect_red_wave(
    project: Path | str,
    *,
    verify_fn: Any | None = None,
) -> dict[str, Any]:
    """Find last green snap and optionally restore it.

    *verify_fn* if provided: callable(project) -> bool, used for true bisect.
    Without it, returns last snap with ok_after=True or parent chain advice.
    """
    project = Path(project)
    snaps = load_manifest(project)
    if not snaps:
        return {"ok": False, "error": "no snapshots", "action": "none"}

    green = last_green_snapshot(project)
    if green and verify_fn is None:
        return {
            "ok": True,
            "mode": "last_green",
            "snap": green,
            "advice": f"restore_snapshot({green.get('snap_id')}) then repair forward",
        }

    # Binary search if verify_fn provided
    if verify_fn is not None and len(snaps) >= 2:
        lo, hi = 0, len(snaps) - 1
        last_good = None
        while lo <= hi:
            mid = (lo + hi) // 2
            sid = snaps[mid].get("snap_id")
            restore_snapshot(project, str(sid))
            try:
                good = bool(verify_fn(project))
            except Exception:
                good = False
            if good:
                last_good = snaps[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return {
            "ok": True,
            "mode": "bisect",
            "snap": last_good,
            "first_red": snaps[hi + 1] if last_good and hi + 1 < len(snaps) else snaps[0],
        }

    # Heuristic: previous snap before latest
    if len(snaps) >= 2:
        return {
            "ok": True,
            "mode": "previous",
            "snap": snaps[-2],
            "latest": snaps[-1],
            "advice": f"restore {snaps[-2].get('snap_id')} (pre-latest)",
        }
    return {"ok": False, "error": "only one snapshot", "snap": snaps[-1]}


def auto_snapshot_before_write(
    project: Path | str,
    paths: list[str],
    *,
    note: str = "pre-write",
) -> dict[str, Any]:
    """Convenience: snapshot existing files before mutation."""
    return snapshot_paths(project, paths, note=note)
