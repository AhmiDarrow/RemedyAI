"""Auto-seed a minimal falsification oracle when none exists.

Boundary push: the machine does not wait for the model to invent tests.
It plants a smoke oracle that imports mutated modules, then runs it.
"""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any


def _safe_modname(rel: str) -> str | None:
    """Turn path/to/foo.py into importable-ish name for smoke (best effort)."""
    rel = rel.replace("\\", "/").strip("/")
    if not rel.endswith(".py"):
        return None
    if rel.endswith("__init__.py"):
        rel = rel[: -len("__init__.py")].rstrip("/")
    else:
        rel = rel[: -len(".py")]
    parts = [p for p in rel.split("/") if p and p != "."]
    # strip src/ layout
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return None
    if any(not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", p) for p in parts):
        return None
    return ".".join(parts)


def seed_python_smoke_oracle(
    runtime: Any,
    write_set: list[str],
    *,
    home: str | Any = None,
) -> dict[str, Any]:
    """Create tests/test_remedy_build_smoke.py importing write_set modules.

    Returns {ok, path, command, imports, error}.
    """
    try:
        root = Path(runtime.effective_project_path())
        if root.is_file():
            root = root.parent
    except Exception as e:
        return {"ok": False, "error": f"no project: {e}", "command": "", "path": ""}

    if not root.is_dir():
        return {"ok": False, "error": "project root not a directory", "command": "", "path": ""}

    imports: list[str] = []
    for w in write_set or []:
        try:
            p = Path(w)
            if not p.is_absolute():
                p = (root / p).resolve()
            else:
                p = p.resolve()
            rel = p.relative_to(root.resolve()).as_posix()
        except Exception:
            rel = str(w).replace("\\", "/")
        if "tests/" in rel or rel.startswith("test_"):
            continue
        mod = _safe_modname(rel)
        if mod and mod not in imports:
            imports.append(mod)
        if len(imports) >= 8:
            break

    tests_dir = root / "tests"
    try:
        tests_dir.mkdir(parents=True, exist_ok=True)
        # ensure tests is a package for some layouts
        init = tests_dir / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": str(e), "command": "", "path": ""}

    smoke_path = tests_dir / "test_remedy_build_smoke.py"
    lines = [
        '"""Auto-seeded by Remedy build engine — minimal falsification oracle.',
        "",
        "Machine-generated smoke: import mutated modules. Expand with real asserts.",
        '"""',
        "from __future__ import annotations",
        "",
        "import importlib",
        "import pytest",
        "",
    ]
    if not imports:
        lines += [
            "def test_remedy_build_smoke_placeholder():",
            '    """Project has writes but no importable module paths yet."""',
            "    assert True",
            "",
        ]
    else:
        lines.append("@pytest.mark.parametrize(\"modname\", [")
        for m in imports:
            lines.append(f'    "{m}",')
        lines.append("])")
        lines.append("def test_import_mutated_module(modname: str) -> None:")
        lines.append('    """Smoke: mutated modules must import without error."""')
        lines.append("    importlib.import_module(modname)")
        lines.append("")

    body = "\n".join(lines)
    try:
        # Prefer jail-aware write if available
        try:
            dest = runtime.resolve_tool_path(
                str(smoke_path.relative_to(root)), for_write=True
            )
        except Exception:
            dest = smoke_path
        Path(dest).write_text(body, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}", "command": "", "path": ""}

    rel_smoke = "tests/test_remedy_build_smoke.py"
    cmd = f"pytest -q {rel_smoke}"
    return {
        "ok": True,
        "path": str(Path(dest)),
        "rel": rel_smoke,
        "command": cmd,
        "imports": imports,
        "error": "",
    }


def format_seed_oracle_message(result: dict[str, Any]) -> dict[str, str]:
    if not result.get("ok"):
        return {
            "role": "user",
            "content": (
                "[Build engine · ORACLE SEED FAILED]\n"
                f"{result.get('error')}\n"
                "Manually add a test file or set verify_command."
            ),
        }
    imports = ", ".join(result.get("imports") or []) or "(placeholder)"
    return {
        "role": "user",
        "content": (
            "[Build engine · ORACLE SEEDED · machine]\n"
            f"Wrote `{result.get('rel')}` importing: {imports}\n"
            f"Verify command set to: `{result.get('command')}`\n"
            "Machine will run this smoke oracle. Expand tests with real asserts when ready."
        ),
    }
