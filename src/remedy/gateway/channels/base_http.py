"""Shared aiohttp session lifecycle for messenger adapters."""

from __future__ import annotations

from typing import Any


class HttpSessionMixin:
    """Mixin: one reusable ClientSession per adapter instance."""

    _session: Any = None
    _http_timeout_s: float = 60.0

    async def ensure_http(self):
        import aiohttp

        if self._session is None or getattr(self._session, "closed", True):
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._http_timeout_s, sock_connect=10),
                headers={"Connection": "keep-alive"},
            )
        return self._session

    async def close_http(self) -> None:
        if self._session is not None and not getattr(self._session, "closed", True):
            await self._session.close()
        self._session = None
