"""apply_settings_update must not re-snap LLM fields on unrelated patches."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.toml"
    cfg.write_text(
        "\n".join(
            [
                'llm_provider = "deepseek"',
                'llm_model = "deepseek-v4-flash"',
                'llm_base_url = "https://api.deepseek.com/v1"',
                'project_path = "."',
                'harness_mode = "auto"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    # Clear any cached config path
    from remedy.interfaces import api_support as api_support

    api_support.invalidate_config_cache()
    return home


def test_unrelated_patch_does_not_rewrite_llm(isolated_home: Path) -> None:
    from remedy.interfaces.settings_apply import apply_settings_update

    out = asyncio.run(
        apply_settings_update({"harness_mode": "manual", "sarcasm_mode": True})
    )
    assert out["status"] == "saved"
    # File still holds original model (not snapped to a default catalog id)
    text = (isolated_home / "config.toml").read_text(encoding="utf-8")
    assert "deepseek-v4-flash" in text
    assert 'llm_provider = "deepseek"' in text
    assert "harness_mode" in text
    # Response changes should not claim llm_model was updated
    changes = out.get("changes") or []
    assert "llm_model" not in changes
    assert "llm_provider" not in changes


def test_llm_patch_still_normalizes(isolated_home: Path) -> None:
    from remedy.interfaces.settings_apply import apply_settings_update

    out = asyncio.run(
        apply_settings_update(
            {
                "llm_provider": "deepseek",
                "llm_model": "not-a-real-model-zzz",
            }
        )
    )
    assert out["status"] == "saved"
    # Invalid model should snap to a valid deepseek default
    assert out.get("llm_provider") == "deepseek"
    assert out.get("llm_model")
    assert out["llm_model"] != "not-a-real-model-zzz"
