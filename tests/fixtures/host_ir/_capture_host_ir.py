"""One-shot capture of Host Command IR fixtures from the live Python path.

Run: uv run python tests/fixtures/host_ir/_capture_host_ir.py
Writes JSON under tests/fixtures/host_ir/. Not part of the pytest suite.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from remedy.execution.host.ir import (
    HostOp,
    mkdir_op,
    raw_op,
    run_op,
    script_op,
    which_op,
)
from remedy.execution.host.runner import prepare_host_command, prepare_host_op
from remedy.execution.host.translate import translate_posix_to_host

ROOT = Path(__file__).resolve().parent


def _norm_exe(token: str, script_path: Path | None) -> str:
    if script_path is not None and token == str(script_path):
        return "<SCRIPT_PATH>"
    name = Path(token).name.lower()
    if name.startswith(("pwsh", "powershell")):
        return "<PWSH>"
    if name in {"cmd", "cmd.exe"} or name.startswith("cmd."):
        return "<CMD>"
    if name.startswith("git"):
        return "<GIT>"
    if name.startswith(("python", "py.exe")) or name in {"python", "python3", "py"}:
        return "<PYTHON>"
    return token


def _write(name: str, payload: dict) -> Path:
    path = ROOT / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)

    # --- IR roundtrip (to_dict / from_dict) ---
    ir_ops = [
        ("run_pytest", run_op(["python", "-m", "pytest", "-q"], cwd=".")),
        ("mkdir_src_tests", mkdir_op(["src", "tests"])),
        ("which_git", which_op("git")),
        ("script_pwsh_hi", script_op("pwsh", "Write-Output 'hi'", cwd="C:/tmp")),
        ("raw_dir", raw_op("dir /b", host="cmd")),
        (
            "chain_mkdir_git",
            HostOp(kind="chain", ops=[mkdir_op(["a"]), run_op(["git", "status"])]),
        ),
        (
            "env_path",
            HostOp(kind="env", name="PATH", text="%PATH%", env={"FOO": "bar"}),
        ),
        ("unknown_kind_coerced", HostOp.from_dict({"kind": "nope", "argv": [1, 2]})),
        ("from_none", HostOp.from_dict(None)),
    ]
    ir_cases = []
    for case_id, op in ir_ops:
        d = op.to_dict()
        back = HostOp.from_dict(d).to_dict()
        ir_cases.append(
            {
                "id": case_id,
                "input": d,
                "expected_dict": back,
            }
        )
    _write(
        "ir_roundtrip.json",
        {
            "source": "remedy.execution.host.ir",
            "captured_at": "2026-09-03",
            "notes": (
                "HostOp.to_dict/from_dict contract. Zig parse/roundtrip must emit "
                "byte-identical JSON object shapes (key order may differ; values must match)."
            ),
            "schema": {
                "kind": "run|mkdir|which|env|script|raw|chain",
                "argv": "list[str] (run)",
                "paths": "list[str] (mkdir)",
                "name": "str (which|env)",
                "lang": "pwsh|cmd|python (script)",
                "body": "str (script)",
                "host": "cmd|pwsh|posix (raw)",
                "text": "str (raw)",
                "cwd": "str",
                "env": "dict[str,str]",
                "ops": "list[HostOp] (chain)",
            },
            "cases": ir_cases,
        },
    )

    # --- Translate (deterministic cmd rewrite text) ---
    translate_cmds = [
        "mkdir -p src/foo tests",
        "rm -rf build",
        'export FOO=bar && echo hi >/dev/null',
        "rm -rf build && echo next",
        'rm foo"&calc',
        'cat foo"&calc',
        "ls",
        "cat README.md",
        "pwd",
        "which git",
        "which 'foo&calc'",
        "start README.md",
        'start "" notes.md',
        "explorer README.md",
        "cmd /c start index.html",
        "start package.json",
        "mkdir -p a && true",
        "echo $(pwd)",
        "chmod +x run.sh",
        "chmod +x a && mkdir -p b",
        "grep foo bar.py",
        "grep foo",
        "dir | grep foo",
        "find . -name *.py",
        "test -f app.py",
        "[ -f app.py ]",
        "true",
        "false",
        "touch a.txt",
        "cp -r src dest",
        "mv a b",
        "cp src dest",
        "ls src tests",
        "cat a.txt b.txt",
        "command -v git",
    ]
    def _scrub_rg(text: str) -> tuple[str, bool]:
        """Replace absolute ripgrep paths with <RG> for portable fixtures."""
        import re

        scrubbed, n = re.subn(
            r'"[^"]*[/\\]rg(?:\.exe)?"',
            '"<RG>"',
            text,
            flags=re.IGNORECASE,
        )
        return scrubbed, n > 0

    translate_cases = []
    for cmd in translate_cmds:
        r = translate_posix_to_host(cmd, host="cmd")
        text, used_rg = _scrub_rg(r.text)
        expected: dict = {
            "text": text,
            "changed": r.changed,
            "notes": list(r.notes),
            "untranslatable": r.untranslatable,
            "noop": r.noop,
        }
        if used_rg:
            expected["rg_placeholder"] = True
        translate_cases.append(
            {
                "id": f"cmd:{cmd}",
                "input": {"command": cmd, "host": "cmd"},
                "expected": expected,
            }
        )
    r_posix = translate_posix_to_host("mkdir -p a", host="posix")
    translate_cases.append(
        {
            "id": "posix:mkdir -p a",
            "input": {"command": "mkdir -p a", "host": "posix"},
            "expected": {
                "text": r_posix.text,
                "changed": r_posix.changed,
                "notes": list(r_posix.notes),
                "untranslatable": r_posix.untranslatable,
                "noop": r_posix.noop,
            },
        }
    )
    _write(
        "translate_cmd.json",
        {
            "source": "remedy.execution.host.translate.translate_posix_to_host",
            "captured_at": "2026-09-03",
            "notes": (
                "Byte-identical `expected.text` is the Phase 3 Zig translate proof. "
                "Notes may be advisory; text/changed/untranslatable/noop are normative. "
                "grep→rg path is machine-dependent when ripgrep is installed — "
                "those cases record the live rewrite; Zig should match the same "
                "decision table (prefer rg when found, else findstr)."
            ),
            "cases": translate_cases,
        },
    )

    # --- prepare_host_command / prepare_host_op ---
    prepare_cases = []
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)

        for case_id, op in (
            ("script_pwsh_hi", script_op("pwsh", "Write-Output 'hi'")),
            ("script_cmd_echo", script_op("cmd", "@echo hi")),
        ):
            prep = prepare_host_op(op, scratch_dir=scratch)
            body = b""
            if prep.script_path and prep.script_path.is_file():
                body = prep.script_path.read_bytes()
            prepare_cases.append(
                {
                    "id": case_id,
                    "kind": "prepare_op",
                    "input": {"op": op.to_dict()},
                    "expected": {
                        "kind": prep.kind,
                        "argv_template": [
                            _norm_exe(a, prep.script_path) for a in prep.argv
                        ],
                        "script_suffix": (
                            prep.script_path.suffix if prep.script_path else None
                        ),
                        "script_body_utf8_sig": (
                            body.decode("utf-8-sig") if body else ""
                        ),
                        "script_has_bom": body.startswith(b"\xef\xbb\xbf"),
                        "host": prep.host,
                        "ir": prep.ir.to_dict(),
                    },
                }
            )

        for case_id, command in (
            ("cmd_get_childitem", "Get-ChildItem -Name"),
            ("cmd_ps_wrapper_unwrap", "powershell.exe -NoProfile -Command Get-Date"),
        ):
            prep = prepare_host_command(command, scratch_dir=scratch)
            body = prep.script_path.read_bytes() if prep.script_path else b""
            prepare_cases.append(
                {
                    "id": case_id,
                    "kind": "prepare_command",
                    "input": {"command": command},
                    "expected": {
                        "kind": prep.kind,
                        "argv_template": [
                            _norm_exe(a, prep.script_path) for a in prep.argv
                        ],
                        "script_suffix": (
                            prep.script_path.suffix if prep.script_path else None
                        ),
                        "script_body_utf8_sig": body.decode("utf-8-sig") if body else "",
                        "script_has_bom": body.startswith(b"\xef\xbb\xbf"),
                        "notes": list(prep.notes),
                        "host": prep.host,
                        "ir": prep.ir.to_dict(),
                    },
                }
            )

    prep_mkdir = prepare_host_command("mkdir -p src/x", host="cmd")
    prepare_cases.append(
        {
            "id": "translated_mkdir_src_x",
            "kind": "prepare_command",
            "input": {"command": "mkdir -p src/x", "host": "cmd"},
            "expected": {
                "kind": prep_mkdir.kind,
                "argv_template": [
                    *(_norm_exe(a, None) for a in prep_mkdir.argv[:-1]),
                    prep_mkdir.argv[-1] if prep_mkdir.argv else "",
                ],
                "display": prep_mkdir.display,
                "translated": prep_mkdir.translated,
                "notes": list(prep_mkdir.notes),
                "ir": prep_mkdir.ir.to_dict(),
                "host": prep_mkdir.host,
            },
        }
    )

    prep_chmod = prepare_host_command("chmod +x run.sh", host="cmd")
    prepare_cases.append(
        {
            "id": "noop_chmod",
            "kind": "prepare_command",
            "input": {"command": "chmod +x run.sh", "host": "cmd"},
            "expected": {
                "kind": prep_chmod.kind,
                "argv": list(prep_chmod.argv),
                "display": prep_chmod.display,
                "notes": list(prep_chmod.notes),
                "ir": prep_chmod.ir.to_dict(),
                "host": prep_chmod.host,
            },
        }
    )

    prep_run = prepare_host_op(run_op(["git", "status"]))
    prepare_cases.append(
        {
            "id": "op_run_git_status",
            "kind": "prepare_op",
            "input": {"op": run_op(["git", "status"]).to_dict()},
            "expected": {
                "kind": prep_run.kind,
                "argv_template": [_norm_exe(a, None) for a in prep_run.argv],
                "ir": prep_run.ir.to_dict(),
                # Platform default (cmd on Windows, posix elsewhere) — not a Windows-only literal.
                "host": "<DEFAULT>",
            },
        }
    )

    prep_mk = prepare_host_op(mkdir_op(["src", "tests"]))
    prepare_cases.append(
        {
            "id": "op_mkdir",
            "kind": "prepare_op",
            "input": {"op": mkdir_op(["src", "tests"]).to_dict()},
            "expected": {
                "kind": prep_mk.kind,
                "argv": list(prep_mk.argv),
                "display": prep_mk.display,
                "ir": prep_mk.ir.to_dict(),
            },
        }
    )

    # Plain argv (python -m) — normalize interpreter path
    prep_py = prepare_host_command("python -m py_compile app.py", host="cmd")
    prepare_cases.append(
        {
            "id": "plain_argv_py_compile",
            "kind": "prepare_command",
            "input": {"command": "python -m py_compile app.py", "host": "cmd"},
            "expected": {
                "kind": prep_py.kind,
                "argv_template": [_norm_exe(a, None) for a in prep_py.argv],
                "notes": list(prep_py.notes),
                "ir": {
                    "kind": prep_py.ir.kind,
                    "argv_template": [_norm_exe(a, None) for a in prep_py.ir.argv],
                },
                "host": prep_py.host,
            },
        }
    )

    _write(
        "prepare_argv_scriptfile.json",
        {
            "source": "remedy.execution.host.runner + scriptfile",
            "captured_at": "2026-09-03",
            "notes": (
                "Normative for Zig prepare: argv_template (with <PWSH>/<CMD>/<SCRIPT_PATH>/"
                "<GIT>/<PYTHON> placeholders), script body text, BOM flag, kind, IR. "
                "Script path basename is UUID — only suffix and body are compared. "
                "pwsh launch argv MUST be -NoProfile -NonInteractive "
                "-ExecutionPolicy Bypass -File <path> — never -Command."
            ),
            "cases": prepare_cases,
        },
    )

    print(f"wrote fixtures under {ROOT}")
    print(f"  ir_roundtrip.json: {len(ir_cases)} cases")
    print(f"  translate_cmd.json: {len(translate_cases)} cases")
    print(f"  prepare_argv_scriptfile.json: {len(prepare_cases)} cases")


if __name__ == "__main__":
    main()
