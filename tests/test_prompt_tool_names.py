"""Prompt text must name tools the model can actually call.

The Go Tool ABI is the live surface. Prompt constants that still instruct the
model to call retired names (``file_edit``, ``bash_exec``, ...) cost a real
tool round every time: the call is refused as unknown and the loop's
no-progress counters advance toward a safety stop.
"""

from __future__ import annotations

import pytest

# Names that no longer exist on the Tool ABI. Detection helpers may still
# mention them (they parse what a model emits); instructions may not.
RETIRED_TOOL_NAMES = (
    "file_read",
    "file_write",
    "file_edit",
    "apply_patch",
    "bash_exec",
    "list_dir",
    "repo_search",
    "plan_step_status",
    "build_drive",
    "spread_run",
    "skill_activate(",
)


def _prompt_texts() -> dict[str, str]:
    from remedy.core import plan_store, react_policy
    from remedy.core.muscle_profile import builder_system_addendum, classify_muscle

    texts: dict[str, str] = {
        "react_policy._DEFAULT_SYSTEM_BODY": react_policy._DEFAULT_SYSTEM_BODY,
        "react_policy.UNFINISHED_WORK_NUDGE": react_policy.UNFINISHED_WORK_NUDGE,
        "plan_store.PLAN_MODE_SYSTEM_ADDENDUM": plan_store.PLAN_MODE_SYSTEM_ADDENDUM,
        "plan_store.FRONTIER_BUILD_MODE_ADDENDUM": plan_store.FRONTIER_BUILD_MODE_ADDENDUM,
        "plan_store.BUILD_MODE_SYSTEM_ADDENDUM": plan_store.BUILD_MODE_SYSTEM_ADDENDUM,
    }
    # A frontier binding is the case that gets the builder contract.
    profile = classify_muscle("anthropic", "claude-opus-5")
    texts["muscle_profile.builder_system_addendum"] = builder_system_addendum(profile)
    return texts


@pytest.mark.parametrize("name", sorted(_prompt_texts()))
def test_prompt_text_names_no_retired_tools(name: str) -> None:
    text = _prompt_texts()[name]
    offenders = [t for t in RETIRED_TOOL_NAMES if t in text]
    assert not offenders, f"{name} instructs the model to call retired tools: {offenders}"


def test_tool_name_table_maps_to_live_abi_ids() -> None:
    """The table nudges format from must hold real ABI ids."""
    from remedy.core.react_policy import TOOL_NAME_TABLE

    assert TOOL_NAME_TABLE, "TOOL_NAME_TABLE must not be empty"
    for key, abi_id in TOOL_NAME_TABLE.items():
        assert abi_id, f"{key} maps to an empty id"
        # Every live id is dotted (workspace.read, shell.exec, computer.*).
        assert "." in abi_id, f"{key} -> {abi_id} is not a Tool ABI id"
        assert abi_id not in RETIRED_TOOL_NAMES, f"{key} -> {abi_id} is retired"


def test_prompt_services_are_reachable(tmp_path, monkeypatch) -> None:
    """prompt.* handlers fail soft, which hides a broken import forever.

    `should_continue` once imported a module that did not exist; every call
    returned `ok=False, continue=False`, so the re-arm gate was dead in
    production and nothing surfaced. Assert the handlers actually run.
    """
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )
    from remedy.runtime import rmdy_tool_worker as worker

    cont = worker._prompt_should_continue(
        {"goal": "build the app", "text": "done", "session_id": "s", "tool_count": 0}
    )
    assert cont.get("ok") is True, cont.get("error")

    epoch = worker._prompt_slim_epoch(
        {
            "system": "You are Remedy.",
            "goal": "build the app",
            "text": "worked",
            "session_id": "s",
            "epoch": 1,
            "total_steps": 64,
        }
    )
    assert epoch.get("ok") is True, epoch.get("error")
