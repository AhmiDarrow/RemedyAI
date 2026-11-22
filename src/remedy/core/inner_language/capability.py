"""Level flags and stubs for later gist (L2) and latent (L3) paths.

Level 1 is text, for hosted and local models. Level 2 (gist / memory
tokens) and level 3 (hidden-state passing) are reserved. Level 3 may
only be offered when both parent and child are local. Nothing here
sends a latent payload today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChannelCapability:
    level: int = 1
    gist: bool = False
    latent: bool = False
    local: bool = False

    def offer_latent(self) -> bool:
        return bool(self.local and self.latent and self.level >= 3)


def current_capability(*, local: bool = False) -> ChannelCapability:
    """Level 1 only, until a later PR implements gist/latent for local models."""
    return ChannelCapability(level=1, gist=False, latent=False, local=bool(local))


def pack_gist(_payload: dict[str, Any]) -> None:
    """Reserved. Gist-token compression is not implemented."""
    return None


def pack_latent(_payload: dict[str, Any], *, local: bool = False) -> None:
    """Reserved. Hidden-state passing is not implemented (local only, later)."""
    if not local:
        return None
    return None


def unpack_reserved(_blob: Any) -> None:
    return None
