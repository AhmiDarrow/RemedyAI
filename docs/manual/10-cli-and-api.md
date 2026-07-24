# CLI & HTTP API (power users)

Desktop is recommended for daily use. CLI and API share the same home data when `--home` / `REMEDY_HOME` match.

## Install CLI

```bash
pip install remedy-ai
# not: pip install remedy  (unrelated package)
```

From source:

```bash
git clone https://github.com/AhmiDarrow/RemedyAI.git
cd RemedyAI
uv sync   # or: pip install -e .
remedy --version
```

## Core commands

```bash
remedy config init
remedy config show
remedy auth login xai
remedy serve --host 127.0.0.1 --port 7400
remedy skill list
remedy memory search "query"
remedy mcp serve          # MCP stdio host for Cursor / Claude Desktop
```

Desktop sidecar already runs `serve` with `--skip-setup`. Do not start a second server on 7400 while Desktop is open unless you know what you are doing.

### MCP host (export skills to other apps)

```bash
remedy mcp serve
# equivalent console script (packaged with remedy-ai):
remedy-mcp
```

Configure Cursor/Claude Desktop with a stdio MCP server (same Python env as `remedy`):

```json
{
  "mcpServers": {
    "remedy": {
      "command": "remedy-mcp",
      "args": []
    }
  }
}
```

Tools: `remedy_skill_list`, `remedy_skill_search`, `remedy_skill_get`, `remedy_skill_run` (opt-in), `remedy_plan_list`, `remedy_plan_show`. Bundled skills (including **github**) are discoverable via `remedy_skill_list`.

- Quarantined skills cannot load full bodies or run scripts until **Trust** in Remedy.
- Script run requires `REMEDY_MCP_ALLOW_RUN=1` (owner opt-in).
- Local-only — no multi-tenant / cloud gateway.

## Local API (default port 7400)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | Health |
| GET | `/api/auth/local-bootstrap` | Loopback token bootstrap |
| GET/PUT | `/api/settings` | Settings |
| GET/POST | `/api/sessions` | Sessions |
| POST | `/api/sessions/{id}/messages/stream` | SSE chat |
| POST | `/api/sessions/{id}/command` | Slash commands |
| GET | `/api/models` | Models for active provider |
| GET | `/api/providers` | Provider catalog |
| GET | `/api/skills` | Skills list |
| GET | `/api/skills/learning/summary` | Recently learned skills |
| GET | `/api/skills/metrics/reuse` | Skill re-use (activations) |
| GET | `/api/plans` · `/api/plans/latest` | Structured task plans |
| GET | `/api/checkpoints` · `/latest` | Mid-task checkpoints |
| GET | `/docs` | OpenAPI (Swagger) |

Auth header when enabled:

```http
Authorization: Bearer <local_api_token>
```

## SSE events (stream)

`token`, `thinking`, `tool_call`, `tool_result`, `done`, `error`

## Channels & plugins

CLI can enable Telegram / Discord / Slack gateways and load plugins — see repo `docs/USAGE.md` for full flags. Desktop chat does not require those channels.

## Related

- [Commands](11-reference-commands) · [Providers](03-providers-and-auth) · repo `docs/DESKTOP.md` for build details
