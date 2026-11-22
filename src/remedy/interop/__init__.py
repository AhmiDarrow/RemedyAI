"""Peer-agent interop: A2A, MCP talk-to-Remedy, and contacts.

Peers are independent bots with their own memory and permissions. They are
not muscles (models Remedy runs). The inner language stays internal; peers
see plain English plus structured JSON. Every exchange is audited.
"""

from __future__ import annotations

from remedy.interop.runtime import InteropRuntime

__all__ = ["InteropRuntime"]
