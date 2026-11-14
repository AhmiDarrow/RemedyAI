"""Reasoning effort / context settings: validation, per-model merge, and the
real settings apply path writing them to config (tmp home only)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from remedy.interfaces import generation_settings as gs


def test_effort_aliases_and_rejects_unknown() -> None:
    assert gs.normalize_effort("High") == "high"
    assert gs.normalize_effort("off") == "none"
    assert gs.normalize_effort("max") == "xhigh"
    assert gs.normalize_effort("default") == ""
    with pytest.raises(ValueError):
        gs.normalize_effort("ludicrous")


@pytest.mark.parametrize(
    "patch",
    [
        {"thinking_budget_tokens": 10},
        {"max_output_tokens": "lots"},
        {"context_window_tokens": 99_000_000},
        {"max_output_tokens": True},
        {"model_settings": "o3"},
        {"model_settings": {"o3": {"temperature": 1}}},
        {"model_settings": {"o3": {"reasoning_effort": "warp"}}},
    ],
)
def test_invalid_patches_are_refused(patch) -> None:
    with pytest.raises(ValueError):
        gs.normalize_generation_patch(dict(patch), {})


def test_patch_normalizes_and_merges_model_table() -> None:
    cfg = {"model_settings": {"o3": {"reasoning_effort": "high"}, "grok-4": {"max_output_tokens": 4096}}}
    patch = {
        "reasoning_effort": "MEDIUM",
        "max_output_tokens": "8000",
        "model_settings": {
            "o3": {"reasoning_effort": ""},
            "gemini-2.5-pro": {"thinking_budget_tokens": "2048"},
        },
    }
    gs.normalize_generation_patch(patch, cfg)
    assert patch["reasoning_effort"] == "medium"
    assert patch["max_output_tokens"] == 8000
    assert patch["model_settings"] == {
        "grok-4": {"max_output_tokens": 4096},
        "gemini-2.5-pro": {"thinking_budget_tokens": 2048},
    }


def test_snapshot_defaults_are_unset() -> None:
    snap = gs.generation_snapshot({})
    assert snap == {
        "reasoning_effort": "",
        "thinking_budget_tokens": 0,
        "max_output_tokens": 0,
        "context_window_tokens": 0,
        "model_settings": {},
    }


def test_apply_settings_update_writes_generation_keys(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("REMEDY_HOME", str(home))
    from remedy.interfaces import api_support, settings_apply

    api_support.invalidate_config_cache()
    asyncio.run(
        settings_apply.apply_settings_update(
            {
                "reasoning_effort": "low",
                "context_window_tokens": 64000,
                "model_settings": {
                    "anthropic/claude-sonnet-4-5": {"thinking_budget_tokens": 6000},
                    "gemini-2.5-pro": {"reasoning_effort": "low"},
                },
            }
        )
    )
    api_support.invalidate_config_cache()
    snap = settings_apply.public_settings_snapshot()
    assert snap["reasoning_effort"] == "low"
    assert snap["context_window_tokens"] == 64000
    assert snap["model_settings"] == {
        "anthropic/claude-sonnet-4-5": {"thinking_budget_tokens": 6000},
        "gemini-2.5-pro": {"reasoning_effort": "low"},
    }

    with pytest.raises(ValueError):
        asyncio.run(settings_apply.apply_settings_update({"max_output_tokens": 3}))
