"""Apply unified diffs / Begin-Patch blocks through the write jail.

The machine owns the merge: parse a patch, apply hunks, refuse partial writes.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FilePatch:
    path: str
    hunks: list[tuple[list[str], list[str]]] = field(default_factory=list)
    is_new: bool = False
    is_delete: bool = False


def parse_patch(text: str) -> list[FilePatch]:
    """Parse unified diff or ``*** Begin Patch`` / ``*** Update File:`` form."""
    raw = (text or "").replace("\r\n", "\n")
    if "*** Begin Patch" in raw or "*** Update File:" in raw:
        return _parse_begin_patch(raw)
    return _parse_unified(raw)


def _parse_begin_patch(raw: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    current: FilePatch | None = None
    old: list[str] = []
    new: list[str] = []
    in_hunk = False

    def flush_hunk() -> None:
        nonlocal old, new, in_hunk
        if current is not None and (old or new):
            current.hunks.append((old, new))
        old, new, in_hunk = [], [], False

    for line in raw.split("\n"):
        if line.startswith("*** Update File:") or line.startswith("*** Add File:"):
            flush_hunk()
            if current is not None:
                files.append(current)
            path = line.split(":", 1)[1].strip()
            current = FilePatch(path=path, is_new=line.startswith("*** Add File:"))
            continue
        if line.startswith("*** Delete File:"):
            flush_hunk()
            if current is not None:
                files.append(current)
            current = FilePatch(path=line.split(":", 1)[1].strip(), is_delete=True)
            files.append(current)
            current = None
            continue
        if line.startswith("*** End Patch") or line.startswith("*** End of File"):
            flush_hunk()
            continue
        if line.startswith("@@"):
            flush_hunk()
            in_hunk = True
            continue
        if current is None:
            continue
        if not in_hunk and (line.startswith("+") or line.startswith("-") or line.startswith(" ")):
            in_hunk = True
        if not in_hunk:
            continue
        if line.startswith("+"):
            new.append(line[1:])
        elif line.startswith("-"):
            old.append(line[1:])
        elif line.startswith("\\"):
            continue
        else:
            ctx = line[1:] if line.startswith(" ") else line
            old.append(ctx)
            new.append(ctx)
    flush_hunk()
    if current is not None:
        files.append(current)
    return [f for f in files if f.path]


def _parse_unified(raw: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    current: FilePatch | None = None
    old: list[str] = []
    new: list[str] = []

    def flush_hunk() -> None:
        nonlocal old, new
        if current is not None and (old or new):
            current.hunks.append((old, new))
        old, new = [], []

    for line in raw.split("\n"):
        if line.startswith("--- "):
            flush_hunk()
            if current is not None:
                files.append(current)
            path = line[4:].strip()
            if path.startswith("a/"):
                path = path[2:]
            if path == "/dev/null":
                current = FilePatch(path="", is_new=True)
            else:
                current = FilePatch(path=path)
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if current is None:
                current = FilePatch(path=path)
            elif path != "/dev/null":
                if current.path in {"", "/dev/null"}:
                    current.path = path
                    current.is_new = True
                elif not current.path:
                    current.path = path
            elif current.path:
                current.is_delete = True
            continue
        if line.startswith("@@"):
            flush_hunk()
            continue
        if current is None:
            continue
        if line.startswith("+"):
            new.append(line[1:])
        elif line.startswith("-"):
            old.append(line[1:])
        elif line.startswith("\\"):
            continue
        elif line.startswith(" "):
            old.append(line[1:])
            new.append(line[1:])
    flush_hunk()
    if current is not None and current.path:
        files.append(current)
    return files


def _apply_hunks(original: str, hunks: list[tuple[list[str], list[str]]]) -> tuple[bool, str, str]:
    text = original.replace("\r\n", "\n")
    for old, new in hunks:
        old_block = "\n".join(old)
        new_block = "\n".join(new)
        if not old:
            # append / new file
            if text and not text.endswith("\n"):
                text += "\n"
            text += new_block
            if new_block and not new_block.endswith("\n"):
                text += "\n"
            continue
        # Unique occurrence preferred; allow first if unique-enough
        count = text.count(old_block)
        if count == 0:
            # try without trailing newline drift
            stripped = old_block.rstrip("\n")
            if stripped and text.count(stripped) == 1:
                text = text.replace(stripped, new_block.rstrip("\n"), 1)
                continue
            return False, original, f"hunk not found:\n{old_block[:200]}"
        if count > 1:
            return False, original, f"hunk matched {count} times — not unique"
        text = text.replace(old_block, new_block, 1)
    return True, text, ""


def _resolve_patch_dest(
    rel: str,
    runtime: Any,
    root: Path | str | None,
    *,
    for_write: bool,
) -> Path:
    """Resolve *rel* through the write jail. Jail refusals must not fall through."""
    if runtime is not None and hasattr(runtime, "resolve_tool_path"):
        try:
            return Path(runtime.resolve_tool_path(rel, for_write=for_write))
        except TypeError:
            return Path(runtime.resolve_tool_path(rel))
        # SecurityError / PermissionError / ValueError propagate to the caller.
    base = Path(root) if root else Path(".")
    try:
        base_res = base.resolve()
    except OSError:
        base_res = base.absolute()
    p = Path(rel)
    dest = p if p.is_absolute() else (base_res / rel)
    try:
        dest_res = dest.resolve()
    except OSError:
        dest_res = dest
    try:
        dest_res.relative_to(base_res)
    except ValueError as exc:
        raise PermissionError(f"patch path outside root: {rel}") from exc
    return dest_res


def apply_patch_text(
    runtime: Any,
    patch: str,
    *,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Apply *patch* through the runtime write jail. All-or-nothing across files."""
    files = parse_patch(patch)
    if not files:
        return {"ok": False, "error": "no file hunks in patch", "applied": []}

    staged: list[tuple[Path, str | None, str, str]] = []
    for fp in files:
        rel = (fp.path or "").replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if not rel:
            return {"ok": False, "error": "patch file missing path", "applied": []}
        try:
            dest = _resolve_patch_dest(rel, runtime, root, for_write=True)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{rel}: write jail refused ({exc})",
                "applied": [],
            }
        if fp.is_delete:
            prev_del = ""
            existed_del = dest.is_file()
            if existed_del:
                prev_del = dest.read_text(encoding="utf-8", errors="replace")
            staged.append((dest, None, rel, "delete", prev_del, existed_del))
            continue
        prev = ""
        existed = dest.is_file()
        if existed and fp.is_new:
            return {
                "ok": False,
                "error": f"{rel}: Add File refused — path already exists",
                "applied": [],
            }
        if existed:
            prev = dest.read_text(encoding="utf-8", errors="replace")
        elif not fp.is_new and fp.hunks:
            fp.is_new = True
        ok, nxt, err = _apply_hunks(prev, fp.hunks)
        if not ok:
            return {
                "ok": False,
                "error": f"{rel}: {err}",
                "applied": [],
            }
        staged.append((dest, nxt, rel, "add" if not existed else "update", prev, existed))
    applied: list[dict[str, Any]] = []
    done: list[tuple[Path, str, bool, str]] = []
    from remedy.core.atomic_json import write_text_atomic

    try:
        for dest, nxt, rel, action, prev, existed in staged:
            if action == "delete":
                if dest.is_file():
                    dest.unlink()
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                write_text_atomic(dest, nxt or "")
            done.append((dest, prev, existed, action))
            applied.append({"path": rel, "action": action})
    except OSError as exc:
        for dest, prev, existed, _action in reversed(done):
            with suppress(OSError):
                if existed:
                    write_text_atomic(dest, prev)
                elif dest.is_file():
                    dest.unlink()
        return {
            "ok": False,
            "error": f"write failed: {exc}",
            "applied": [],
        }
    for item in applied:
        _mark_write(runtime, str(item.get("path") or ""))
    return {"ok": True, "applied": applied, "files": len(applied)}


def _mark_write(runtime: Any, rel: str) -> None:
    with suppress(Exception):
        from remedy.core.build_engine import get_build_state

        st = get_build_state(runtime)
        if st is not None:
            st.mark_write(rel)
            st.write_steps += 1
