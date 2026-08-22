# Decision table: native vs MCP

The question for each step is: **does this need the editor's live state,
or only the files on disk?** Files on disk → native. Live state → MCP.

| Step | Native | MCP | Why |
|------|--------|-----|-----|
| Fingerprint the project | `game_project_info`, `file_read project.godot` | — | disk is the truth; no editor needed |
| Parse / headless check after an edit | `godot_check`, `cargo check`, batchmode `-quit` | — | reproducible, works in CI, exits non-zero |
| Run a scene headless / capture logs | `godot_run`, `game_playtest` | (only to press Play in the editor) | native captures stdout and screenshots itself |
| Export / build / package | `godot_export`, UAT, batchmode build | — | long, deterministic, CI-shaped |
| Tests (GUT, gdUnit4, NUnit, busted…) | `bash_exec` | — | same |
| What scene is open right now, what node is selected | — | `get_scene_tree` / `inspect_node` | only the editor knows |
| Inspector values the owner tweaked but did not save | — | `get_node_properties` | unsaved state is not on disk |
| Add/move/rename nodes while the owner watches | — (or edit `.tscn` when the editor is closed) | `create_node`, `set_property` | editing `.tscn` under an open editor causes reload prompts and lost work |
| Connect a signal to a script | either | either | MCP is friendlier live; native is fine closed. Verify with `godot_check` after both |
| Read the editor's Output/Errors panel | `godot_run` log for headless errors | `get_errors` for editor-only errors (import, plugin, tool scripts) | different sources of errors |
| Write a new script | `file_write` | `create_script` if the server wires it to a node in one go | keep file ops native unless the MCP adds attachment |
| Import new assets | `godot_import` / headless import | editor reimport via MCP if exposed | both fine; native is reproducible |
| Owner absent / CI / nightly | native only | — | no editor to talk to |

## Combining them

The usual loop when the editor is open:

```
read live state (MCP) → change (MCP or file edit) → native verify → confirm on disk (file_read) → playtest
```

Skipping "native verify" because the editor did not complain is the most
common mistake: editors tolerate half-broken scripts until Play.

## When both could work, prefer native

- It is faster to reason about (files + exit codes).
- It works when the owner closes the editor.
- Its results are Remedy's own tool output, not third-party text.

Use MCP when the owner is clearly working *in* the editor and wants to see
changes appear live, or when the information simply is not on disk.

## When not to use MCP at all

- The server wraps the CLI only (no editor addon) — native already does it.
- The server is not in `mcp_status` — do not ask the owner to install one
  to finish a task that native tools can complete.
- The task is destructive across many scenes — do it natively with a git
  diff to review, not through an editor session.
