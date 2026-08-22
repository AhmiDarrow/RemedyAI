# Godot MCP setup

There is no single official Godot MCP server. Community projects exist on
GitHub and npm/PyPI under names like `godot-mcp`, `godot-mcp-server`,
`@<scope>/godot-mcp`. They differ in two ways that matter to you:

1. **Editor-bound** servers talk to a running Godot editor through an
   addon (`addons/<name>/` enabled in Project Settings → Plugins) over a
   local WebSocket/TCP port. They expose live scene-tree and editor
   actions. Editor closed ⇒ tools error.
2. **CLI-wrapping** servers just run the `godot` binary headless (launch,
   run scene, read project). They do not see editor state — and Remedy's
   native `godot_run` / `godot_check` / `godot_export` already cover that
   better. Do not add one of these for its own sake.

Ask the owner which they installed; read its README when the repo is
available locally (`node_modules/<pkg>/README.md` or the clone).

## The config line

```toml
# config.toml
mcp_servers = ["godot=npx -y <package-name>"]
```

Variants: `"godot=node C:/path/to/server/dist/index.js"`,
`"godot=uvx <python-package>"`, or a table with `env` (e.g.
`GODOT_PATH`) and `cwd` when the server needs them. After editing the
config, Remedy picks the server up on the next start or the first
`mcp_status` call (lazy connect).

## Editor side (owner does this)

- Copy/enable the addon the server ships into `addons/` of the project;
  enable in Project → Project Settings → Plugins.
- Open the project in Godot 4.x and leave the editor running.
- Some servers need the project path or a port via env; the README says.

## Typical tool names (vary per server — read `mcp_status`)

| Purpose | Often named |
|---------|-------------|
| scene tree of the open scene | `get_scene_tree`, `list_nodes`, `get_scene_info` |
| node properties | `get_node_properties`, `inspect_node` |
| add / remove / rename node | `create_node`, `add_node`, `delete_node`, `rename_node` |
| set property / connect signal | `set_property`, `update_node`, `connect_signal` |
| run / stop the game from the editor | `run_project`, `play_scene`, `stop_project` |
| editor output / errors | `get_debug_output`, `get_errors`, `get_output_log` |
| script read/write | `read_script`, `create_script`, `edit_script` |

After `mcp_status` shows names, call them as `mcp_godot_<name>` with the
JSON arguments the tool schema describes.

## Confirm it is live

1. `mcp_status` → `godot: connected, N tools`.
2. Call the cheapest read tool (scene tree / project info). A real answer
   with node names that match the `.tscn` on disk means the editor link
   works.
3. Errors like `ECONNREFUSED`, `editor not connected`, `timeout` ⇒ editor
   or addon is not running. Tell the owner; use native tools meanwhile.

## Unity / Unreal

Same pattern: community `unity-mcp` / `unreal-mcp` servers pair an editor
plugin with an MCP process; config line identical with another name.
Native fallback is batchmode / UAT (see the `unity` and `unreal` skills).
