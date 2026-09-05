# Host Command IR fixtures (Phase 3 native cutover)

Captured from the **Python** shell-host path before the Zig port.
Zig parity tests must match these shapes:

| File | Normative fields |
|------|------------------|
| `ir_roundtrip.json` | `HostOp.to_dict` / `from_dict` values |
| `translate_cmd.json` | `expected.text` (byte-identical), `changed`, `untranslatable`, `noop` |
| `prepare_argv_scriptfile.json` | `argv_template`, script body + BOM, `kind`, IR |

## Placeholders

Machine-local executable paths are scrubbed:

| Token | Meaning |
|-------|---------|
| `<PWSH>` | `pwsh` or `powershell` resolved path |
| `<CMD>` | `cmd.exe` resolved path |
| `<GIT>` | `git` resolved path |
| `<PYTHON>` | host CPython resolved path |
| `<SCRIPT_PATH>` | UUID scratch script path |
| `<RG>` | ripgrep path inside translated text (`rg_placeholder: true`) |
| `<DEFAULT>` | OS default shell host (`cmd` on Windows, `posix` elsewhere) |

## Regenerating

```text
uv run python tests/fixtures/host_ir/_capture_host_ir.py
```

Sources: `tests/test_host_bridge.py` cases plus the IR helpers in
`src/remedy/core/computer/host_binding/_host_op.py` (scriptfile lives in Zig).

## Proof target (from NATIVE_CUTOVER_PLAN Phase 3)

> The Host Command IR fixtures (`ir.py` cases) replay byte-identical
> argv/scriptfile output from Zig.
