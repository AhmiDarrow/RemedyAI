"""Microsoft Teams (Bot Framework): webhook inbound + connector outbound."""

from __future__ import annotations

import logging
import time
from typing import Any

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.base_http import HttpSessionMixin
from remedy.gateway.channels.emit_util import emit_message
from remedy.gateway.router import ChannelAdapter
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)

# Bot Framework connector hosts only — never store arbitrary attacker serviceUrl.
_BF_SERVICE_HOST_SUFFIXES = (
    ".botframework.com",
    ".botframework.us",
    ".botframework.azure.cn",
)


def _is_allowed_botframework_service_url(url: str) -> bool:
    from urllib.parse import urlparse

    u = (url or "").strip()
    if not u.lower().startswith("https://"):
        return False
    host = (urlparse(u).hostname or "").lower()
    if not host:
        return False
    if host in ("smba.trafficmanager.net", "directline.botframework.com"):
        return True
    return any(host == s[1:] or host.endswith(s) for s in _BF_SERVICE_HOST_SUFFIXES)


def _jwt_payload_unverified(token: str) -> dict[str, Any] | None:
    """Decode JWT payload without signature verify (claims only; signature separate)."""
    from remedy.gateway.channels.jwt_rs256 import decode_jwt_payload_unverified

    return decode_jwt_payload_unverified(token)


# Known Bot Framework / Azure AD token issuers (claim structure + JWKS sig).
_BF_ISS_SUFFIXES = (
    "sts.windows.net",
    "login.microsoftonline.com",
    "login.microsoft.com",
    "api.botframework.com",
)


def _jwt_claims_structurally_valid(
    claims: dict[str, Any],
    *,
    app_id: str,
    now: float | None = None,
) -> bool:
    """Fail-closed claim checks without cryptographic signature verification.

    - ``aud`` must be present and match app_id (or ``api://{app_id}``)
    - ``exp`` must be present and not expired (60s skew)
    - ``nbf`` if present must not be in the future (60s skew)
    - ``iss`` if present must look like a Bot Framework / Azure AD issuer
    """
    app_id = (app_id or "").strip()
    if not app_id or not isinstance(claims, dict):
        return False
    aud = claims.get("aud")
    if isinstance(aud, list):
        auds = [str(a) for a in aud if a is not None]
    elif aud is not None and str(aud).strip():
        auds = [str(aud)]
    else:
        auds = []
    # Fail closed: missing aud previously accepted any forged payload.
    if not auds:
        return False
    if app_id not in auds and f"api://{app_id}" not in auds:
        return False

    ts = time.time() if now is None else float(now)
    exp = claims.get("exp")
    if exp is None or exp is False or exp == "":
        return False
    try:
        exp_f = float(exp)
    except (TypeError, ValueError):
        return False
    if ts >= exp_f + 60:
        return False

    nbf = claims.get("nbf")
    if nbf is not None and nbf is not False and nbf != "":
        try:
            if ts + 60 < float(nbf):
                return False
        except (TypeError, ValueError):
            return False

    iss = claims.get("iss")
    if iss is not None and str(iss).strip():
        from urllib.parse import urlparse

        raw_iss = str(iss).strip()
        host = (urlparse(raw_iss).hostname or "").lower()
        if not host:
            # Some tokens use issuer without a scheme — treat as hostname path.
            host = raw_iss.lower().split("/")[0]
        if not any(host == s or host.endswith("." + s) for s in _BF_ISS_SUFFIXES):
            return False
    return True


class TeamsChannel(HttpSessionMixin, ChannelAdapter):
    """Outbound uses Bot Framework connector; inbound via /api/webhooks/teams."""

    def __init__(
        self,
        gateway,
        *,
        app_id: str = "",
        app_password: str = "",
        tenant_id: str = "",
        allow_ids: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__(ChannelKind.TEAMS, gateway)
        self.app_id = (app_id or "").strip()
        self.app_password = (app_password or "").strip()
        self.tenant_id = (tenant_id or "").strip() or "botframework.com"
        self._allowed = parse_ids(allow_ids)
        self.allow_all = bool(allow_all)
        self._token: str = ""
        self._token_exp: float = 0.0
        # Last conversation reference for replies when target not set
        self._last_service_url: str = ""
        self._last_conversation_id: str = ""

    async def start(self) -> None:
        await super().start()
        if self.app_id and self.app_password:
            logger.info("Teams channel active (inbound=webhook, outbound=connector)")
        else:
            logger.info("Teams channel: stub mode (missing app_id/password)")

    async def stop(self) -> None:
        await self.close_http()
        await super().stop()

    async def _bearer(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return self._token
        session = await self.ensure_http()
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.app_id,
            "client_secret": self.app_password,
            "scope": "https://api.botframework.com/.default",
        }
        async with session.post(url, data=data) as resp:
            body = await resp.json()
        tok = str(body.get("access_token") or "")
        if not tok:
            logger.warning("Teams token failed: %s", str(body)[:160])
            return ""
        self._token = tok
        self._token_exp = now + float(body.get("expires_in") or 3600)
        return tok

    async def send(self, message: str, target: str | None = None) -> bool:
        """Send to conversation id (target) or last inbound conversation."""
        if not (self.app_id and self.app_password):
            return True
        conv = (target or self._last_conversation_id or "").strip()
        service = self._last_service_url.rstrip("/")
        if not conv or not service:
            logger.warning("Teams send: no conversation reference yet")
            return False
        token = await self._bearer()
        if not token:
            return False
        try:
            session = await self.ensure_http()
            url = f"{service}/v3/conversations/{conv}/activities"
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"type": "message", "text": (message or "")[:28000]},
            ) as resp:
                return resp.status in (200, 201, 202)
        except Exception as e:
            logger.error("Teams send failed: %s", e)
            return False

    async def send_typing(self, target: str | None = None) -> None:
        conv = (target or self._last_conversation_id or "").strip()
        service = self._last_service_url.rstrip("/")
        if not conv or not service:
            return
        token = await self._bearer()
        if not token:
            return
        try:
            session = await self.ensure_http()
            url = f"{service}/v3/conversations/{conv}/activities"
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"type": "typing"},
            ) as resp:
                _ = resp.status
        except Exception:
            pass

    def verify_inbound_auth(self, authorization: str | None) -> bool:
        """Bot Framework JWT gate: claims + RS256 JWKS signature (stdlib).

        Requires Bearer JWT when ``app_id`` is configured. Fail-closed:
        - ``aud`` / ``exp`` / ``nbf`` / ``iss`` structure checks
        - RS256 signature against Bot Framework / Azure AD JWKS (cached 6h)

        Not on the desktop chat hot path — only Teams webhook POSTs.
        Set ``REMEDY_TEAMS_SKIP_JWT=1`` only for local tunnel debugging.
        Set ``REMEDY_TEAMS_SKIP_JWKS=1`` to claim-check only (legacy; not recommended).
        """
        import os

        if str(os.environ.get("REMEDY_TEAMS_SKIP_JWT", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return True
        if not self.app_id:
            # Stub / misconfigured — do not accept public traffic
            return False
        auth = (authorization or "").strip()
        if not auth.lower().startswith("bearer "):
            logger.warning("Teams webhook missing Bearer Authorization")
            return False
        token = auth[7:].strip()
        claims = _jwt_payload_unverified(token)
        if not claims:
            logger.warning("Teams webhook Authorization is not a JWT")
            return False
        if not _jwt_claims_structurally_valid(claims, app_id=self.app_id):
            logger.warning(
                "Teams JWT claim check failed (aud/exp/nbf/iss fail-closed)"
            )
            return False
        # Signature verify (skip only when explicitly debugging claim structure)
        skip_jwks = str(os.environ.get("REMEDY_TEAMS_SKIP_JWKS", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if skip_jwks:
            logger.warning("Teams JWT JWKS signature skipped (REMEDY_TEAMS_SKIP_JWKS)")
            return True
        from remedy.gateway.channels.jwt_rs256 import verify_jwt_rs256_jwks

        if not verify_jwt_rs256_jwks(token, allow_network=True):
            logger.warning("Teams JWT RS256/JWKS verification failed")
            return False
        return True

    async def handle_activity(self, activity: dict[str, Any]) -> bool:
        """Handle Bot Framework activity JSON from webhook.

        Auth: Bearer JWT structure + aud, allowlist/allow_all, trusted serviceUrl.
        """
        if (activity.get("type") or "") != "message":
            return False
        text = (activity.get("text") or "").strip()
        if not text:
            return False
        conv = activity.get("conversation") or {}
        conv_id = str(conv.get("id") or "")
        from_id = str((activity.get("from") or {}).get("id") or "")
        service_url = str(activity.get("serviceUrl") or "").rstrip("/")
        if service_url and _is_allowed_botframework_service_url(service_url):
            self._last_service_url = service_url
        elif service_url:
            logger.warning("Teams ignored untrusted serviceUrl host: %s", service_url[:120])
            # Reject activities that only offer an untrusted reply endpoint
            # when we have no prior trusted service URL to fall back to.
            if not self._last_service_url:
                return False
        if conv_id:
            self._last_conversation_id = conv_id
        if not self._allowed and not self.allow_all:
            logger.info(
                "Teams ignore (empty allowlist, allow_all=false) conv=%s",
                conv_id or from_id,
            )
            return False
        if not is_allowed(
            allowlist=self._allowed,
            allow_all=self.allow_all,
            candidates=[conv_id, from_id],
            channel="teams",
        ):
            return False
        await emit_message(
            self.gateway,
            ChannelKind.TEAMS,
            message=text,
            chat_id=conv_id or from_id,
            source_id=from_id or conv_id,
            username=(activity.get("from") or {}).get("name"),
            extra={"user_id": from_id, "service_url": service_url},
        )
        return True
