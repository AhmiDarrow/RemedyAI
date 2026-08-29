"""True mutant kill score — surface mutants must die under scoped tests.

Frontier D: import-cone size is not oracle strength. Inject bad edits
(delete assert, flip compare, rename symbol), re-run tests; survivors mean
the oracle is too weak to claim DONE.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


def _apply_mutants(source: str) -> list[tuple[str, str]]:
    """Return list of (mutant_id, mutated_source) for Python text."""
    mutants: list[tuple[str, str]] = []
    lines = source.splitlines(keepends=True)
    if not lines:
        return mutants

    # M1: flip first comparison operator
    flipped = False
    out: list[str] = []
    for ln in lines:
        if not flipped and re.search(r"(?<![<>=!])==(?!=)", ln):
            out.append(re.sub(r"(?<![<>=!])==(?!=)", "!=", ln, count=1))
            flipped = True
        elif not flipped and "!=" in ln:
            out.append(ln.replace("!=", "==", 1))
            flipped = True
        elif not flipped and re.search(r"(?<![<>=])<=(?!=)", ln):
            out.append(re.sub(r"(?<![<>=])<=(?!=)", ">", ln, count=1))
            flipped = True
        elif not flipped and re.search(r"(?<![<>=])>=(?!=)", ln):
            out.append(re.sub(r"(?<![<>=])>=(?!=)", "<", ln, count=1))
            flipped = True
        else:
            out.append(ln)
    if flipped:
        mutants.append(("flip_compare", "".join(out)))

    # M2: neutralize first assert / return True-ish
    out2: list[str] = []
    neut = False
    for ln in lines:
        if not neut and re.match(r"^\s*assert\s+", ln):
            indent = re.match(r"^(\s*)", ln).group(1)  # type: ignore[union-attr]
            out2.append(f"{indent}pass  # mutant: assert killed\n")
            neut = True
        else:
            out2.append(ln)
    if neut:
        mutants.append(("kill_assert", "".join(out2)))

    # M3: change first return literal
    out3: list[str] = []
    ret = False
    for ln in lines:
        if not ret and re.search(r"return\s+True\b", ln):
            out3.append(re.sub(r"return\s+True\b", "return False", ln, count=1))
            ret = True
        elif not ret and re.search(r"return\s+False\b", ln):
            out3.append(re.sub(r"return\s+False\b", "return True", ln, count=1))
            ret = True
        elif not ret and re.search(r"return\s+0\b", ln):
            out3.append(re.sub(r"return\s+0\b", "return 1", ln, count=1))
            ret = True
        elif not ret and re.search(r"return\s+1\b", ln):
            out3.append(re.sub(r"return\s+1\b", "return 0", ln, count=1))
            ret = True
        else:
            out3.append(ln)
    if ret:
        mutants.append(("flip_return", "".join(out3)))

    # M4: rename first def (break callers/tests looking for symbol)
    m = re.search(r"(?m)^def\s+([A-Za-z_]\w*)\s*\(", source)
    if m:
        name = m.group(1)
        if not name.startswith("_") and name not in ("test",):
            mut = re.sub(
                rf"(?m)^def\s+{re.escape(name)}\s*\(",
                f"def {name}_mutant(",
                source,
                count=1,
            )
            mutants.append(("rename_def", mut))

    return mutants[:6]


def _run_pytest(root: Path, test_args: list[str], *, timeout_s: float = 45.0) -> bool:
    from remedy.core.build_python import python_cmd_for_subprocess

    py = python_cmd_for_subprocess(root)
    if not py:
        return False
    try:
        from remedy.execution.process import hidden_subprocess_kwargs

        proc = subprocess.run(
            [*py, "-m", "pytest", "-q", "-p", "no:cacheprovider", *test_args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            **hidden_subprocess_kwargs(),
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def mutant_kill_score(
    root: Path | str,
    write_set: list[str],
    *,
    test_command_paths: list[str] | None = None,
    max_files: int = 4,
    max_mutants_per_file: int = 4,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Run surface mutants on write_set; return kill rate.

    Works in a temp copy of the project (shallow: only copies write_set + tests).
    """
    root = Path(root)
    if root.is_file():
        root = root.parent

    targets: list[Path] = []
    for w in write_set or []:
        p = Path(w)
        if not p.is_absolute():
            p = root / p
        if p.is_file() and p.suffix == ".py" and "test" not in p.name:
            targets.append(p)
        if len(targets) >= max_files:
            break

    if not targets:
        return {
            "ok": False,
            "error": "no mutable .py sources in write_set",
            "killed": 0,
            "survived": 0,
            "total": 0,
            "kill_rate": 0.0,
            "details": [],
        }

    # Baseline tests must pass first (optional paths)
    test_args = list(test_command_paths or [])
    if not test_args:
        # discover simple test dirs
        if (root / "tests").is_dir():
            test_args = ["tests"]
        elif (root / "test").is_dir():
            test_args = ["test"]

    details: list[dict[str, Any]] = []
    killed = 0
    survived = 0

    with tempfile.TemporaryDirectory(prefix="remedy-mutant-") as tmp:
        tmp_root = Path(tmp) / "proj"
        # Copy minimal tree: targets + tests + pyproject if any
        tmp_root.mkdir(parents=True)
        for p in targets:
            try:
                rel = p.relative_to(root)
            except Exception:
                rel = Path(p.name)
            dest = tmp_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)

        for tdir in ("tests", "test"):
            src_t = root / tdir
            if src_t.is_dir():
                with suppress(Exception):
                    shutil.copytree(src_t, tmp_root / tdir, dirs_exist_ok=True)

        for marker in ("pyproject.toml", "pytest.ini", "conftest.py", "setup.cfg"):
            if (root / marker).is_file():
                with suppress(Exception):
                    shutil.copy2(root / marker, tmp_root / marker)

        # Also copy packages needed for imports (shallow: parent packages)
        for p in targets:
            try:
                rel = p.relative_to(root)
            except Exception:
                continue
            # copy sibling modules in same package
            parent = root / rel.parent
            if parent.is_dir():
                for sib in parent.glob("*.py"):
                    dest = tmp_root / rel.parent / sib.name
                    if not dest.exists():
                        with suppress(Exception):
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(sib, dest)

        # Ensure src layout package inits
        for _init in tmp_root.rglob("__init__.py"):
            pass
        # create missing __init__.py along package paths
        for p in list(tmp_root.rglob("*.py")):
            for parent in p.parents:
                if parent == tmp_root:
                    break
                init = parent / "__init__.py"
                if not init.exists() and parent.name not in ("tests", "test"):
                    with suppress(Exception):
                        init.write_text("", encoding="utf-8")

        baseline_ok = True
        if test_args:
            baseline_ok = _run_pytest(tmp_root, test_args, timeout_s=timeout_s)

        if not baseline_ok:
            return {
                "ok": False,
                "error": "baseline tests red — cannot score mutants",
                "killed": 0,
                "survived": 0,
                "total": 0,
                "kill_rate": 0.0,
                "baseline_ok": False,
                "details": [],
            }

        for p in targets:
            try:
                rel_posix = p.relative_to(root).as_posix()
            except Exception:
                rel_posix = p.name
            orig = (tmp_root / rel_posix).read_text(
                encoding="utf-8", errors="replace"
            )
            for mid, mut_src in _apply_mutants(orig)[:max_mutants_per_file]:
                mut_path = tmp_root / rel_posix
                mut_path.write_text(mut_src, encoding="utf-8")
                still_green = _run_pytest(tmp_root, test_args, timeout_s=timeout_s) if test_args else True
                # restore
                mut_path.write_text(orig, encoding="utf-8")
                if still_green:
                    survived += 1
                    status = "survived"
                else:
                    killed += 1
                    status = "killed"
                details.append({"file": rel_posix, "mutant": mid, "status": status})

    total = killed + survived
    rate = (killed / total) if total else 0.0
    return {
        "ok": True,
        "baseline_ok": True,
        "killed": killed,
        "survived": survived,
        "total": total,
        "kill_rate": round(rate, 4),
        "details": details,
        "strong": rate >= 0.5 and total >= 1,
        "message": (
            f"Mutant kill_rate={rate:.0%} ({killed}/{total}). "
            + ("Oracle strong enough for DONE." if rate >= 0.5 and survived == 0 else
               "Survivors → strengthen tests before DONE." if survived else
               "Partial kill — prefer more behavioral tests.")
        ),
    }


def format_mutant_message(result: dict[str, Any]) -> dict[str, str]:
    lines = [
        "[Build engine · MUTANT KILL SCORE]",
        result.get("message") or "",
        f"killed={result.get('killed')} survived={result.get('survived')} "
        f"rate={result.get('kill_rate')}",
    ]
    for d in (result.get("details") or [])[:12]:
        lines.append(f"  · {d.get('file')} {d.get('mutant')}: {d.get('status')}")
    if result.get("survived"):
        lines.append(
            "Machine blocks pure DONE while mutants survive. "
            "Add asserts that fail under these surface edits."
        )
    return {"role": "user", "content": "\n".join(lines)}
