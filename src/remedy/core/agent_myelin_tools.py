"""Tools: myelin_status / crystallize / run / verify — crystallized cognition.

Registered from register_soul_tools (same organism surface). Authoring and
running sheaths execute code, so both flow through the approvals gate —
the same regime as the build engine's host powers.
"""

from __future__ import annotations

import json
from typing import Any


def register_myelin_tools(runtime: Any) -> None:
    def _home():
        return getattr(getattr(runtime, "config", None), "home_dir", None) or getattr(
            runtime, "home_dir", None
        )

    def _gate(tool_name: str, summary: str) -> str | None:
        # Security gate: fail CLOSED. If the approvals machinery is
        # unavailable or raises, executing model-authored code is blocked.
        try:
            from remedy.core.agent_computer_tools import _computer_approval_gate

            return _computer_approval_gate(runtime, tool_name, summary)
        except Exception as exc:
            return (
                f"BLOCKED: approval gate unavailable ({exc.__class__.__name__}) — "
                f"{tool_name} refuses to execute without a working approvals "
                "check. Tell the user; do not retry blindly."
            )

    async def myelin_status() -> str:
        from remedy.memory.myelin import myelin_status as _status

        return json.dumps(_status(_home()), indent=2, ensure_ascii=False)

    async def myelin_crystallize(
        name: str = "",
        description: str = "",
        script: str = "",
        test: str = "",
        trigger: str = "",
    ) -> str:
        from remedy.memory.myelin import crystallize

        blocked = _gate(
            "myelin_crystallize",
            f"crystallize sheath {name!r} ({len(script)}ch script, "
            f"{len(test)}ch test) — runs the test",
        )
        if blocked:
            return blocked
        muscle = ""
        try:
            from remedy.core.llm_binding import get_llm_binding

            b = get_llm_binding(runtime)
            muscle = "/".join(x for x in (str(b.provider or ""), str(b.model or "")) if x)
        except Exception:
            muscle = ""
        return json.dumps(
            crystallize(
                name=name,
                description=description,
                script=script,
                test=test,
                trigger=trigger,
                muscle=muscle,
                home=_home(),
            ),
            indent=2,
            ensure_ascii=False,
        )

    async def myelin_run(slug: str = "", args: str = "") -> str:
        from remedy.memory.myelin import run_sheath

        blocked = _gate("myelin_run", f"run sheath {slug!r} args={args[:120]!r}")
        if blocked:
            return blocked
        arg_list: list[str] = []
        raw = (args or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                arg_list = [str(a) for a in parsed] if isinstance(parsed, list) else [raw]
            except json.JSONDecodeError:
                arg_list = raw.split()
        return json.dumps(
            run_sheath(slug, arg_list, _home()), indent=2, ensure_ascii=False
        )

    async def myelin_verify(slug: str = "") -> str:
        from remedy.memory.myelin import verify_sheath

        blocked = _gate("myelin_verify", f"re-run test for sheath {slug!r}")
        if blocked:
            return blocked
        return json.dumps(verify_sheath(slug, _home()), indent=2, ensure_ascii=False)

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "myelin_status",
        "List Remedy's crystallized skills (sheaths) and myelination "
        "candidates — pathways the partner repeats that deserve becoming a "
        "tested local skill. Use when candidates appear in context, or when "
        "asked what she has learned to do on her own.",
        myelin_status,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "myelin_crystallize",
        "Crystallize a recurring task into a permanent local skill: write "
        "run.py (the method, argv in / stdout out) and test.py (exits 0 on "
        "pass). The machine runs the test; only green counts as verified. "
        "Extract the METHOD, not one answer — future runs need no model.",
        myelin_crystallize,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name (slugged)"},
                "description": {"type": "string"},
                "script": {"type": "string", "description": "run.py source"},
                "test": {"type": "string", "description": "test.py source (exit 0 = pass)"},
                "trigger": {
                    "type": "string",
                    "description": "Pathway signature this covers (from candidates)",
                },
            },
            "required": ["name", "script", "test"],
        },
    )
    reg.register_builtin_handler(
        "myelin_run",
        "Run a crystallized sheath locally (no model needed). args: JSON "
        "array or space-separated argv. Prefer this over re-reasoning a "
        "task a verified sheath already covers.",
        myelin_run,
        {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "args": {"type": "string"},
            },
            "required": ["slug"],
        },
    )
    reg.register_builtin_handler(
        "myelin_verify",
        "Re-run a sheath's test to confirm the competence still holds "
        "(vigil also does this on her own nights).",
        myelin_verify,
        {
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
        },
    )
