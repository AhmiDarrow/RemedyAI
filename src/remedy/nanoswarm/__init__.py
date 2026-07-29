"""Remedy Nano Swarm — specialized local workers around the main agent.

Bots: Token, Pattern, Memory, Skill (always-on, deterministic), Router
(optional text assist on the shared bundled SmolVLM2 / llama-server).
"""

from __future__ import annotations

from remedy.nanoswarm.coordinator import NanoSwarm, get_swarm

__all__ = ["NanoSwarm", "get_swarm"]
