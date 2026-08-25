"""Tool contracts (descriptors). Handlers stay on ToolRegistry."""

from __future__ import annotations

from remedy.tools.catalog import descriptor_for, register_descriptor
from remedy.tools.descriptor import CredentialPolicy, NetworkPolicy, ToolDescriptor

__all__ = [
    "CredentialPolicy",
    "NetworkPolicy",
    "ToolDescriptor",
    "descriptor_for",
    "register_descriptor",
]
