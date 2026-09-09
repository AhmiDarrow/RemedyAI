"""The system block must expose a byte-stable prefix a provider can cache.

Prompt caching pays only when the cached prefix is identical from one turn to
the next. Without an explicit boundary the breakpoint lands after per-turn
text (the session id, the workspace block, retrieved memory) and every turn
writes a cache entry it can never read back — strictly worse than not caching,
because a write costs more than an ordinary token.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.react_stream import TURN_CONTEXT_MARKER, build_runtime_system_block


def _write_home(tmp_path: Path) -> Path:
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "anthropic"\nllm_model = "claude-opus-5"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_runtime_system_block_marks_the_turn_context_boundary() -> None:
    block = build_runtime_system_block(
        system_prompt="IDENTITY",
        provider="anthropic",
        model="claude-opus-5",
        base_url="https://api.anthropic.com/v1",
        max_steps=1000,
        context="[Session isolation] session=abc",
        user_message="hello",
    )
    assert TURN_CONTEXT_MARKER in block
    head, _, tail = block.partition(TURN_CONTEXT_MARKER)
    assert "IDENTITY" in head
    assert "Connected model: claude-opus-5" in head
    # Per-turn material lives strictly after the boundary.
    assert "session=abc" in tail
    assert "session=abc" not in head


def test_stable_prefix_is_identical_across_sessions_and_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(_write_home(tmp_path)))
    from remedy.runtime.prompt_assemble import assemble_prompt

    def prefix(message: str, session_id: str) -> str:
        system = str(assemble_prompt({"message": message, "session_id": session_id})["system"])
        index = system.find(TURN_CONTEXT_MARKER)
        assert index > 0, "assembled system carries no cache boundary"
        return system[:index]

    first = prefix("Implement a --verbose flag", "session-one")
    second = prefix("Fix the failing parser test", "session-two")
    assert first == second, "the cacheable prefix must not vary by session or message"
    assert len(first) > 500, "a trivially short prefix would not be worth caching"


def test_epoch_material_is_appended_and_keeps_the_prefix_cacheable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Soft epochs must not disturb the cached prefix.

    An epoch used to prepend its checkpoint, which moved every byte after it
    and invalidated the cache on the very turns a long build depends on. The
    material now goes on the end, so the prefix stays byte-identical and each
    epoch adds only a bounded amount of text.
    """
    monkeypatch.setenv("REMEDY_HOME", str(_write_home(tmp_path)))
    from remedy.runtime.prompt_assemble import assemble_prompt, slim_epoch

    def prefix(system: str) -> str:
        index = system.find(TURN_CONTEXT_MARKER)
        assert index > 0, "assembled system carries no cache boundary"
        return system[:index]

    system = str(assemble_prompt({"message": "build the thing", "session_id": "sE"})["system"])
    original = prefix(system)
    previous_len = len(system)

    for epoch in (1, 2, 3):
        out = slim_epoch(
            {
                "system": system,
                "goal": "build the thing",
                "text": "worked on it",
                "ledger": ["- workspace.edit [ok] a.py", "- shell.exec [err] exit_code=1"],
                "session_id": "sE",
                "epoch": epoch,
                "total_steps": 64 * epoch,
            }
        )
        assert out.get("ok") is True, out.get("error")
        system = str(out["system"])
        assert prefix(system) == original, f"epoch {epoch} moved the cacheable prefix"
        growth = len(system) - previous_len
        assert 0 <= growth < 600, f"epoch {epoch} grew the system by {growth} chars"
        previous_len = len(system)
