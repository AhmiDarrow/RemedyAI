"""Tool contracts (descriptors). Handlers stay on ToolRegistry.

Keep this module lazy so ``from remedy.tools import comfyui`` does not
import PolicyEngine (circular: catalog → policy → engine → catalog).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CredentialPolicy",
    "NetworkPolicy",
    "ToolDescriptor",
    "ToolInvocation",
    "descriptor_for",
    "register_descriptor",
]


def __getattr__(name: str) -> Any:
    if name in ("CredentialPolicy", "NetworkPolicy", "ToolDescriptor"):
        from remedy.tools import descriptor as _d

        return getattr(_d, name)
    if name in ("descriptor_for", "register_descriptor"):
        from remedy.tools import catalog as _c

        return getattr(_c, name)
    if name == "ToolInvocation":
        from remedy.tools.invocation import ToolInvocation

        return ToolInvocation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
