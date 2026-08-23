"""Confused-deputy / owner-lock gates on agent update_settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.interfaces.config import infer_provider_from_base_url


def test_attacker_subdomain_does_not_infer_xai():
    assert infer_provider_from_base_url("https://x.ai.attacker.tld/v1") is None
    assert infer_provider_from_base_url("https://api.x.ai.evil/v1") is None
    assert infer_provider_from_base_url("https://openai.com.evil/v1") is None
    assert infer_provider_from_base_url("https://anthropic.com.evil/v1") is None


def test_catalog_hosts_still_infer():
    assert infer_provider_from_base_url("https://api.x.ai/v1") == "xai"
    assert infer_provider_from_base_url("https://api.openai.com/v1") == "openai"
    assert infer_provider_from_base_url("https://api.anthropic.com/v1") == "anthropic"
    assert infer_provider_from_base_url("http://127.0.0.1:11434/v1") == "ollama"
    assert infer_provider_from_base_url("http://127.0.0.1:1234/v1") is None


def test_127_evil_host_is_not_loopback_or_ollama():
    from remedy.interfaces.config import is_loopback_hostname

    assert is_loopback_hostname("127.evil.com") is False
    assert is_loopback_hostname("127.0.0.1.attacker.com") is False
    assert is_loopback_hostname("127.0.0.1") is True
    assert is_loopback_hostname("::1") is True
    assert infer_provider_from_base_url("https://127.evil.com/v1") is None
    assert infer_provider_from_base_url("https://127.evil.com:11434/v1") is None


def _rt_with_settings(tmp_path: Path):
    from remedy.core.agent_settings_tools import register_settings_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        "\n".join(
            [
                'llm_provider = "xai"',
                'llm_model = "grok-4.5"',
                'llm_base_url = "https://api.x.ai/v1"',
                'approval_mode = "ask"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    class RT:
        def __init__(self) -> None:
            self.tool_registry = ToolRegistry()

        def project_path_is_unset(self) -> bool:
            return True

        def effective_project_path(self) -> Path:
            return tmp_path

    rt = RT()
    register_settings_tools(rt)
    APPROVALS.set_mode("ask")
    return rt, home


@pytest.mark.asyncio
async def test_update_settings_foreign_base_url_needs_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    from remedy.interfaces import api_support

    api_support.invalidate_config_cache()
    rt, home = _rt_with_settings(tmp_path)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    api_support.invalidate_config_cache()

    out = await rt.tool_registry.execute(
        "update_settings",
        llm_base_url="https://x.ai.attacker.tld/v1",
    )
    assert "APPROVAL_REQUIRED" in out
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "attacker.tld" not in text
    assert "api.x.ai" in text


@pytest.mark.asyncio
async def test_update_settings_sleev_remote_same_patch_needs_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    from remedy.interfaces import api_support

    api_support.invalidate_config_cache()
    rt, home = _rt_with_settings(tmp_path)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    api_support.invalidate_config_cache()

    out = await rt.tool_registry.execute(
        "update_settings",
        sleev_allow_remote_gateway=True,
        sleev_gateway_url="http://10.0.0.5:17321",
    )
    assert "APPROVAL_REQUIRED" in out
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "10.0.0.5" not in text
    assert "sleev_allow_remote_gateway" not in text or "true" not in text.lower()


@pytest.mark.asyncio
async def test_update_settings_allow_all_needs_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    from remedy.interfaces import api_support

    api_support.invalidate_config_cache()
    rt, home = _rt_with_settings(tmp_path)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    api_support.invalidate_config_cache()

    out = await rt.tool_registry.execute(
        "update_settings",
        messengers={"telegram": {"allow_all": True}},
    )
    assert "APPROVAL_REQUIRED" in out
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "allow_all" not in text


def _approval_id(blob: str) -> str:
    for line in blob.splitlines():
        if line.startswith("APPROVAL_REQUIRED id="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"no approval id in: {blob!r}")


@pytest.mark.asyncio
async def test_update_settings_127_evil_host_needs_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    from remedy.interfaces import api_support

    api_support.invalidate_config_cache()
    rt, home = _rt_with_settings(tmp_path)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    api_support.invalidate_config_cache()

    out = await rt.tool_registry.execute(
        "update_settings",
        llm_base_url="https://127.evil.com/v1",
    )
    assert "APPROVAL_REQUIRED" in out
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "127.evil.com" not in text
    assert "api.x.ai" in text


@pytest.mark.asyncio
async def test_approved_base_url_does_not_unlock_other_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from remedy.core.approvals import APPROVALS
    from remedy.interfaces import api_support

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    api_support.invalidate_config_cache()
    rt, home = _rt_with_settings(tmp_path)
    rt._session_id = "sess-replay"
    monkeypatch.setenv("REMEDY_HOME", str(home))
    api_support.invalidate_config_cache()

    first = await rt.tool_registry.execute(
        "update_settings",
        llm_base_url="https://127.evil.com/v1",
    )
    assert "APPROVAL_REQUIRED" in first
    APPROVALS.resolve(_approval_id(first), approve=True, scope="always")

    replay = await rt.tool_registry.execute(
        "update_settings",
        llm_base_url="https://other.attacker.tld/v1",
    )
    assert "APPROVAL_REQUIRED" in replay
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "other.attacker.tld" not in text
    assert "127.evil.com" not in text

    sleev_a = await rt.tool_registry.execute(
        "update_settings",
        sleev_gateway_url="http://10.0.0.5:17321",
    )
    assert "APPROVAL_REQUIRED" in sleev_a
    APPROVALS.resolve(_approval_id(sleev_a), approve=True, scope="always")
    sleev_b = await rt.tool_registry.execute(
        "update_settings",
        sleev_gateway_url="http://10.0.0.9:17321",
    )
    assert "APPROVAL_REQUIRED" in sleev_b

    tg = await rt.tool_registry.execute(
        "update_settings",
        messengers={"telegram": {"allow_all": True}},
    )
    assert "APPROVAL_REQUIRED" in tg
    APPROVALS.resolve(_approval_id(tg), approve=True, scope="always")
    dc = await rt.tool_registry.execute(
        "update_settings",
        messengers={"discord": {"allow_all": True}},
    )
    assert "APPROVAL_REQUIRED" in dc


@pytest.mark.asyncio
async def test_update_settings_web_tools_needs_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    from remedy.interfaces import api_support

    api_support.invalidate_config_cache()
    rt, home = _rt_with_settings(tmp_path)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    api_support.invalidate_config_cache()

    out = await rt.tool_registry.execute(
        "update_settings",
        web_tools_enabled=True,
    )
    assert "APPROVAL_REQUIRED" in out
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "web_tools_enabled" not in text or "true" not in text.lower()

    boot = await rt.tool_registry.execute(
        "update_settings",
        http_bootstrap=True,
    )
    assert "APPROVAL_REQUIRED" in boot
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "http_bootstrap" not in text or "true" not in text.lower()
