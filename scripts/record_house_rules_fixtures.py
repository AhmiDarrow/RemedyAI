"""Record house_rules / setup.session behaviour for the native Go port.

The Python worker is the specification. Each case runs a sequence of tool calls
through ``rmdy_tool_worker._house_rules`` / ``_setup_session`` (the two worker
entry points that call ``core.house_rules.dispatch``) on a fresh temp home, and
writes ``native/go/mind/houserules/testdata/<case>.json``:

- ``config``: the config.toml the case starts from (parsed),
- per step: the tool, the exact input, the result, and the parsed content of
  every file under the home after the step (house_rules.toml,
  setup_session.json, the pending and prev snapshots, the audit lines, and
  config.toml).

Safety: REMEDY_HOME and HOME point at a temp folder, the host-stretch thread and
the secure store are stubbed, nothing touches the network or the real
``~/.remedy``. Every name and key below is fake.

Usage::

    python scripts/record_house_rules_fixtures.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "native" / "go" / "mind" / "houserules" / "testdata"
FAKE_KEY = "sk-FAKE-not-a-real-key-0123456789"

OWNER = {}
PEER = {"caller_origin": "peer-bot-7"}


def hr(action: str, **kw):
    return ("house_rules", {"action": action, **kw})


def ss(action: str, **kw):
    return ("setup.session", {"action": action, **kw})


# One recorded answer per question, in QUESTIONS order.
FULL = [
    {"agent_gender": "neutral", "name": "Remy", "user_name": "Alex Example"},
    {"ui_language": "de"},
    {"persona": "playful", "sarcasm_mode": True},
    {"custom_llm_name": "Test Lab", "llm_base_url": "http://localhost:9/v1", "llm_model": "fake-1", "llm_provider": "openai"},
    {"project_path": "C:/fake/project"},
    {"drives_enabled": False, "proactive_reach_out": True},
    {"inner_audit": False, "privacy_mode": True},
    {"always_send_images": True, "mind_handles_easy_chats": False, "muscle_route": "local", "vision_force_decode": True},
    {"mind_keep_gpu": True, "muscle_gpu_policy": "yield_mind"},
    {"approval_mode": "ask", "trust_profile": "conservative"},
    {"interop_auto_reply": False, "interop_inbound": True},
    {"update_check": "ask"},
    {"launch_at_login": True},
]
QIDS = ["names", "language", "tone", "provider", "workspace", "proactive", "privacy", "tokens", "gpu", "consent", "interop", "updates", "launch"]

CASES: dict[str, dict] = {}

CASES["fresh_status"] = {"steps": [hr("status"), hr(""), hr("get"), hr("summarize"), hr("offer_prompt"), hr("nonsense"), hr("finalize")]}

CASES["full_conversation"] = {
    "steps": [hr("status")]
    + [hr("record", answers=a) for a in FULL]
    + [hr("summarize"), hr("confirm"), hr("status"), hr("summarize"), hr("rollback"), hr("status")]
}

CASES["skip_one"] = {
    "steps": [
        hr("record", answers={"agent_gender": "male", "name": "Remy"}),
        hr("record", answers={}, skipped=["tone"]),
        hr("record", answers={"ui_language": "fr"}, skipped=["tone", "provider"]),
        hr("summarize"),
        hr("confirm"),
        hr("status"),
    ]
}

CASES["skip_all"] = {
    "steps": [
        hr("record", answers={"persona": "concise"}),
        hr("skip_all"),
        hr("status"),
        hr("summarize"),
        hr("confirm"),
        hr("rollback"),
    ]
}

CASES["invalid_values"] = {
    "steps": [
        # bool: strings, numbers, null, garbage (garbage falls back to the default)
        hr("record", answers={"sarcasm_mode": "maybe"}),
        hr("record", answers={"sarcasm_mode": "YES"}),
        hr("record", answers={"sarcasm_mode": 0}),
        hr("record", answers={"sarcasm_mode": 2}),
        hr("record", answers={"sarcasm_mode": None}),
        hr("record", answers={"drives_enabled": "off", "launch_at_login": "Y", "privacy_mode": 1}),
        hr("record", answers={"drives_enabled": [1], "mind_keep_gpu": "garbage"}),
        # enum: aliases, case, garbage, numbers, null
        hr("record", answers={"persona": "witty"}),
        hr("record", answers={"persona": "BALANCED"}),
        hr("record", answers={"persona": "  Long "}),
        hr("record", answers={"persona": "nonsense"}),
        hr("record", answers={"agent_gender": 5}),
        hr("record", answers={"agent_gender": None}),
        hr("record", answers={"muscle_route": "Frontier", "update_check": "sometimes"}),
        hr("record", answers={"approval_mode": "FULL", "trust_profile": ["x"]}),
        # str: padding, numbers, lists, null
        hr("record", answers={"name": "  Padded  ", "user_name": 42}),
        hr("record", answers={"project_path": ["a", "b's"], "ui_language": None}),
        hr("record", answers={"llm_model": {"k": True}, "llm_base_url": 1.5}),
        # unknown keys are dropped; sensitive non-field keys pass through raw
        hr("record", answers={"memory_encrypt": "weird", "not_a_setting": 1, "shell": "rm -rf"}),
        # whole-answer shapes
        hr("record", answers="hello"),
        hr("record", answers=["x"]),
        hr("record", patch={"persona": "fun"}),
        hr("record", answers={"persona": "minimal"}, skipped="tone"),
        hr("record", answers={}, skipped=["tone", "tone", 3]),
        hr("update_draft", patch="nope"),
        hr("update_draft", answers={"ui_language": "es"}, skipped=["language"]),
        hr("update_draft", patch={"launch_at_login": "true"}, skipped="x"),
        hr("set_mode", mode="Traditional"),
        hr("set_mode", mode="whatever"),
        hr("set_mode"),
        hr("status"),
        hr("summarize"),
        hr("confirm"),
    ]
}

CASES["secret_in_answer"] = {
    "steps": [
        hr("record", answers={"llm_api_key": FAKE_KEY, "llm_provider": "openai"}),
        hr("status"),
        hr("record", answers={"llm_api_key": "", "llm_model": "fake-2"}),
        hr("update_draft", patch={"llm_api_key": FAKE_KEY + "-2"}),
        hr("summarize"),
        hr("confirm"),
        hr("propose", patch={"llm_api_key": FAKE_KEY + "-3"}),
        hr("propose", confirm=True, patch={"llm_api_key": FAKE_KEY + "-4", "llm_model": "fake-4"}),
        hr("status"),
        hr("rollback"),
    ],
    "secrets": [FAKE_KEY],
}

CASES["peer_origin"] = {
    "steps": [
        ("house_rules", {"action": "status", **PEER}),
        ("house_rules", {"action": "summarize", **PEER}),
        ("house_rules", {"action": "offer_prompt", **PEER}),
        ("house_rules", {"action": "record", "answers": {"persona": "fun"}, **PEER}),
        ("house_rules", {"action": "update_draft", "patch": {"persona": "fun"}, **PEER}),
        ("house_rules", {"action": "skip_all", **PEER}),
        ("house_rules", {"action": "confirm", **PEER}),
        ("house_rules", {"action": "propose", "confirm": True, "patch": {"approval_mode": "full"}, **PEER}),
        ("house_rules", {"action": "decline", **PEER}),
        ("house_rules", {"action": "rollback", **PEER}),
        ("house_rules", {"action": "revisit", **PEER}),
        ("house_rules", {"action": "set_mode", "mode": "traditional", **PEER}),
        ("house_rules", {"action": "update_offer_go", **PEER}),
        ("house_rules", {"action": "finalize", **PEER}),
        ("house_rules", {"action": "record", "answers": {"persona": "fun"}, "caller_origin": "owner"}),
        ("house_rules", {"action": "record", "answers": {"persona": "fun"}, "caller_origin": " Desktop "}),
        ("house_rules", {"action": "record", "answers": {"name": "Remy"}, "caller_origin": "cli"}),
        ("house_rules", {"action": "record", "answers": {"name": "Remy2"}, "caller_origin": "webui"}),
        ("house_rules", {"action": "record", "answers": {"name": "Remy3"}, "caller_origin": "fixture"}),
        ("house_rules", {"action": "record", "answers": {"name": "Remy4"}, "caller_origin": "routine"}),
        ("house_rules", {"action": "status"}),
    ]
}

CASES["propose_flow"] = {
    "steps": [
        hr("propose", patch={"persona": "fun"}),
        hr("propose", patch={"nothing": 1}),
        hr("propose", patch={}),
        hr("propose", patch="x"),
        hr("propose", patch={"approval_mode": "full", "persona": "concise"}),
        hr("status"),
        hr("confirm", pending_id="hr-wrong"),
        hr("confirm"),
        hr("confirm"),
        hr("propose", patch={"interop_inbound": "yes", "trust_profile": "autonomous"}),
        hr("decline"),
        hr("decline"),
        hr("propose", patch={"memory_encrypt": True}),
        hr("confirm"),
        hr("propose", confirm=True, patch={"approval_mode": "ask", "muscle_gpu_policy": "share"}),
        hr("propose", patch={"muscle_route": "mind", "ui_language": "it"}),
        hr("rollback"),
        hr("rollback"),
        hr("revisit"),
        hr("status"),
        hr("confirm"),
        hr("summarize"),
    ]
}

CASES["permission_confirm_all"] = {
    "config": {"advanced": {"house_rules_permission": "confirm_all"}, "name": "Old"},
    "steps": [hr("propose", patch={"persona": "fun"}), hr("confirm"), hr("status")],
}
CASES["permission_plain"] = {
    "config": {"advanced": {"house_rules_permission": "plain"}},
    "steps": [hr("propose", patch={"persona": "fun"}), hr("propose", patch={"privacy_mode": True}), hr("confirm")],
}
CASES["knobs_off"] = {
    "config": {"advanced": {"onboarding": False, "house_rules_audit": False}, "setup_completed": True},
    "steps": [hr("status"), hr("record", answers={"persona": "fun"}), hr("skip_all"), hr("rollback")],
}
CASES["existing_config"] = {
    "config": {
        "llm_provider": "openai", "persona": "verbose", "name": "Zed", "setup_completed": True,
        "advanced": {"interop_inbound": True, "inner_audit": False, "other_knob": 7},
    },
    "steps": [
        hr("propose", patch={"interop_inbound": False}),
        hr("confirm"),
        hr("propose", patch={"persona": "default", "privacy_mode": False}),
        hr("confirm"),
        hr("skip_all"),
        hr("rollback"),
        hr("status"),
    ],
}

CASES["update_offer_not_now"] = {
    "config": {"setup_completed": True},
    "steps": [hr("status"), hr("update_offer_later"), hr("status"), hr("update_offer_go"), hr("update_offer_dismiss")],
}
CASES["update_offer_go"] = {
    "config": {"setup_completed": True},
    "steps": [
        hr("status"),
        hr("update_offer_go"),
        hr("status"),
        hr("record", answers={"persona": "playful"}),
        hr("confirm"),
        hr("update_offer_go"),
        hr("update_offer_later"),
    ],
}
CASES["update_offer_accept_quirk"] = {
    "config": {"setup_completed": True},
    "steps": [hr("update_offer_accept")],
}
CASES["update_offer_unavailable"] = {
    "steps": [hr("status"), hr("update_offer_dismiss"), hr("status")],
}
CASES["update_offer_after_complete"] = {
    "config": {"setup_completed": True},
    "steps": [hr("skip_all"), hr("status"), hr("revisit"), hr("status"), hr("update_offer_go")],
}

CASES["setup_session_aliases"] = {
    "steps": [
        ss("status"),
        ss("get"),
        ss(""),
        ss(" GET "),
        ("setup.session", {}),
        ss("offer_prompt"),
        ss("set_mode", mode="traditional"),
        ss("get"),
        ss("update_draft", patch={"name": "Remy", "ui_language": "nl"}, skipped=["tone"]),
        ss("update_draft", answers={"persona": "fun"}, caller_origin="peer-bot-7"),
        ss("skip_all"),
        ss("confirm"),
        ss("get"),
    ]
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hr-fixtures-") as base:
        os.environ["REMEDY_HOME"] = str(Path(base) / "env_home")
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(Path(base) / "profile")
        for k in list(os.environ):
            if k.endswith("_API_KEY") or "TOKEN" in k:
                del os.environ[k]
        sys.path.insert(0, str(REPO / "src"))
        logging.disable(logging.CRITICAL)

        import remedy.core.computer.host_binding as host_binding
        import remedy.interfaces.secret_store as secret_store
        import remedy.runtime.rmdy_tool_worker as worker
        from remedy.interfaces import config as cfgmod
        from remedy.interfaces.api_support import _write_config

        host_binding.stretch_home = lambda *a, **k: None  # no PC mapping thread

        def no_store(*a, **k):
            raise RuntimeError("secure store is not available in fixtures")

        secret_store.set_provider_secret = no_store
        handlers = {"house_rules": worker._house_rules, "setup.session": worker._setup_session}

        OUT.mkdir(parents=True, exist_ok=True)
        for name, case in CASES.items():
            home = Path(base) / name
            home.mkdir(parents=True)
            os.environ["REMEDY_HOME"] = str(home)
            initial = case.get("config") or {}
            if initial:
                _write_config(home / "config.toml", dict(initial))
            cfg0 = tomllib.loads((home / "config.toml").read_text("utf-8")) if initial else {}
            steps = []
            for tool, inp in case["steps"]:
                out = handlers[tool](dict(inp, home_dir=str(home)))
                steps.append({
                    "tool": tool,
                    "input": inp,
                    "result": norm(out, home),
                    "files": snapshot(home, cfgmod, case.get("secrets", [])),
                })
            rec = {"name": name, "config": norm(cfg0, home), "steps": steps}
            (OUT / f"{name}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
            print(f"{name}: {len(steps)} steps")
    return 0


def norm(obj, home: Path):
    """JSON-shape obj and replace the temp home path by <home>."""
    text = json.dumps(obj, ensure_ascii=False, default=str)
    for form in {str(home), home.as_posix()}:
        text = text.replace(json.dumps(form)[1:-1], "<home>")
    return json.loads(text)


def snapshot(home: Path, cfgmod, secrets: list[str]) -> dict:
    files: dict = {}
    for name in ("house_rules.toml", "setup_session.json", "house_rules_pending.json", "house_rules_prev.json",
                 "house_rules_audit.jsonl", "config.toml"):
        p = home / name
        if not p.is_file():
            files[name] = None
            continue
        text = p.read_text("utf-8")
        for s in secrets:
            assert s not in text, f"secret leaked into {name}"
        if name.endswith(".toml"):
            files[name] = norm(tomllib.loads(text), home)
        elif name.endswith(".jsonl"):
            files[name] = [norm(json.loads(line), home) for line in text.splitlines() if line.strip()]
        else:
            files[name] = norm(json.loads(text), home)
    return files


if __name__ == "__main__":
    raise SystemExit(main())
