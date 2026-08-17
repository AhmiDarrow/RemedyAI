"""First-run 'no model key' guard must never block a working setup.

binding_looks_unconfigured drives a plain "add a key in Settings" message on a
genuinely unconfigured cloud provider — but must return False for every working
configuration (keyed, base_url proxy, local, RMB), or it would be a capability
regression.
"""

from __future__ import annotations

import pytest

from remedy.core.llm_binding import LlmBinding, binding_looks_unconfigured


def test_flags_unconfigured_cloud_provider() -> None:
    b = LlmBinding(provider="openai", model="gpt-4o", base_url="", api_key="")
    assert binding_looks_unconfigured(b) is True


@pytest.mark.parametrize(
    "bind",
    [
        LlmBinding(provider="openai", model="gpt-4o", base_url="", api_key="sk-x"),
        LlmBinding(provider="anthropic", model="claude", base_url="", api_key="k"),
        LlmBinding(provider="openai", model="gpt-4o", base_url="http://127.0.0.1:9", api_key=""),
        LlmBinding(provider="ollama", model="llama3", base_url="", api_key=""),
        LlmBinding(provider="llamacpp", model="q", base_url="", api_key=""),
        LlmBinding(provider="rmb", model="muscle", base_url="", api_key=""),
    ],
)
def test_never_flags_a_working_setup(bind: LlmBinding) -> None:
    assert binding_looks_unconfigured(bind) is False
