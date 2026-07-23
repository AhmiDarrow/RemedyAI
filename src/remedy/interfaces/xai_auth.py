"""xAI (Grok) authentication: API key + OAuth device-code (OpenCode-style).

API key path uses console keys (XAI_API_KEY / xai-…).
OAuth path uses accounts.x.ai device authorization for SuperGrok / X Premium+.

OAuth client id defaults to the public Grok Build / open-agent ecosystem client
used by OpenClaw and peers. Override with REMEDY_XAI_OAUTH_CLIENT_ID if you
register your own application with xAI.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUTH_SERVER = "https://accounts.x.ai"
API_BASE = "https://api.x.ai/v1"
DEVICE_CODE_URL = f"{AUTH_SERVER}/oauth2/device/code"
TOKEN_URL = f"{AUTH_SERVER}/oauth2/token"
# Public client id used by Grok Build / OpenClaw-class agents for device OAuth.
# https://github.com/openclaw/openclaw/issues/84504
DEFAULT_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"

_refresh_lock = threading.Lock()
_poll_sessions: dict[str, dict[str, Any]] = {}
_poll_lock = threading.Lock()


def _client_id() -> str:
    return (
        os.environ.get("REMEDY_XAI_OAUTH_CLIENT_ID", "").strip()
        or DEFAULT_CLIENT_ID
    )


def auth_dir(home: Path | None = None) -> Path:
    base = Path(home or os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()
    d = base / "auth"
    d.mkdir(parents=True, exist_ok=True)
    return d


def auth_path(home: Path | None = None) -> Path:
    return auth_dir(home) / "xai.json"


@dataclass
class XaiCredentials:
    auth_method: str = "none"  # none | api_key | oauth
    api_key: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: float | None = None  # unix seconds
    token_type: str = "Bearer"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        if self.auth_method == "api_key" and self.api_key:
            return True
        if self.auth_method == "oauth" and self.access_token:
            if self.expires_at and time.time() >= float(self.expires_at) - 60:
                return bool(self.refresh_token)
            return True
        return False

    def bearer_token(self) -> str | None:
        """Token suitable for Authorization: Bearer …"""
        if self.auth_method == "api_key" and self.api_key:
            return self.api_key
        if self.auth_method == "oauth" and self.access_token:
            return self.access_token
        return None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider": "xai",
            "auth_method": self.auth_method,
            "connected": self.connected,
            "has_api_key": bool(self.api_key),
            "has_oauth": bool(self.access_token or self.refresh_token),
            "expires_at": self.expires_at,
        }


def load_credentials(home: Path | None = None) -> XaiCredentials:
    path = auth_path(home)
    if not path.exists():
        # Env bootstrap
        env_key = (
            os.environ.get("XAI_API_KEY", "").strip()
            or os.environ.get("REMEDY_XAI_API_KEY", "").strip()
        )
        if env_key:
            return XaiCredentials(auth_method="api_key", api_key=env_key)
        return XaiCredentials()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return XaiCredentials()
    return XaiCredentials(
        auth_method=str(data.get("auth_method") or "none"),
        api_key=data.get("api_key"),
        access_token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        expires_at=data.get("expires_at"),
        token_type=str(data.get("token_type") or "Bearer"),
        raw=data,
    )


def save_credentials(creds: XaiCredentials, home: Path | None = None) -> None:
    path = auth_path(home)
    payload = {
        "auth_method": creds.auth_method,
        "api_key": creds.api_key,
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "expires_at": creds.expires_at,
        "token_type": creds.token_type,
        "updated_at": time.time(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def clear_credentials(home: Path | None = None) -> None:
    path = auth_path(home)
    if path.exists():
        path.unlink()


def save_api_key(api_key: str, home: Path | None = None) -> XaiCredentials:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("API key is empty")
    creds = XaiCredentials(auth_method="api_key", api_key=key)
    save_credentials(creds, home=home)
    return creds


def _http_form(url: str, data: dict[str, str], timeout: float = 30.0) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "RemedyDesktop-xAI-Auth/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
        except json.JSONDecodeError:
            parsed = {"error": err_body or str(e), "status": e.code}
        parsed.setdefault("status", e.code)
        raise RuntimeError(json.dumps(parsed)) from e


def start_device_login(home: Path | None = None) -> dict[str, Any]:
    """Begin OAuth device-code flow. Returns user_code + verification URLs."""
    client_id = _client_id()
    data = _http_form(
        DEVICE_CODE_URL,
        {
            "client_id": client_id,
            "scope": "openid profile email offline_access",
        },
    )
    device_code = data.get("device_code")
    user_code = data.get("user_code")
    if not device_code or not user_code:
        raise RuntimeError(f"Unexpected device-code response: {data}")

    interval = int(data.get("interval") or 5)
    expires_in = int(data.get("expires_in") or 900)
    verification_uri = (
        data.get("verification_uri")
        or data.get("verification_url")
        or f"{AUTH_SERVER}/oauth2/device"
    )
    verification_uri_complete = data.get("verification_uri_complete") or (
        f"{verification_uri}?user_code={urllib.parse.quote(str(user_code))}"
    )

    session_id = str(user_code)
    with _poll_lock:
        _poll_sessions[session_id] = {
            "device_code": device_code,
            "client_id": client_id,
            "interval": interval,
            "expires_at": time.time() + expires_in,
            "home": str(home) if home else None,
            "status": "pending",
            "error": None,
        }

    # Background poller
    threading.Thread(
        target=_poll_until_done,
        args=(session_id,),
        daemon=True,
        name="xai-oauth-poll",
    ).start()

    return {
        "session_id": session_id,
        "user_code": user_code,
        "device_code": device_code,
        "verification_uri": verification_uri,
        "verification_uri_complete": verification_uri_complete,
        "interval": interval,
        "expires_in": expires_in,
        "status": "pending",
        "message": (
            f"Open {verification_uri_complete} and approve access, "
            f"or go to {verification_uri} and enter code {user_code}."
        ),
    }


def _poll_until_done(session_id: str) -> None:
    with _poll_lock:
        sess = dict(_poll_sessions.get(session_id) or {})
    if not sess:
        return
    device_code = sess["device_code"]
    client_id = sess["client_id"]
    interval = max(3, int(sess.get("interval") or 5))
    expires_at = float(sess.get("expires_at") or (time.time() + 900))
    home = Path(sess["home"]) if sess.get("home") else None

    while time.time() < expires_at:
        time.sleep(interval)
        try:
            data = _http_form(
                TOKEN_URL,
                {
                    "grant_type": GRANT_DEVICE,
                    "device_code": device_code,
                    "client_id": client_id,
                },
            )
        except RuntimeError as e:
            try:
                err = json.loads(str(e))
            except json.JSONDecodeError:
                err = {"error": str(e)}
            err_code = str(err.get("error") or err.get("error_description") or "")
            if "authorization_pending" in err_code or "slow_down" in err_code:
                if "slow_down" in err_code:
                    interval = min(interval + 2, 15)
                continue
            if "expired" in err_code or "access_denied" in err_code:
                with _poll_lock:
                    if session_id in _poll_sessions:
                        _poll_sessions[session_id]["status"] = "error"
                        _poll_sessions[session_id]["error"] = err_code
                return
            # Unknown error — keep pending briefly
            logger.debug("xAI token poll error: %s", err)
            continue

        access = data.get("access_token")
        if not access:
            continue
        expires_in = int(data.get("expires_in") or 3600)
        creds = XaiCredentials(
            auth_method="oauth",
            access_token=access,
            refresh_token=data.get("refresh_token"),
            expires_at=time.time() + expires_in,
            token_type=str(data.get("token_type") or "Bearer"),
            raw=data,
        )
        save_credentials(creds, home=home)
        with _poll_lock:
            if session_id in _poll_sessions:
                _poll_sessions[session_id]["status"] = "connected"
                _poll_sessions[session_id]["error"] = None
        logger.info("xAI OAuth login succeeded")
        return

    with _poll_lock:
        if session_id in _poll_sessions:
            _poll_sessions[session_id]["status"] = "error"
            _poll_sessions[session_id]["error"] = "expired_token"


def login_status(session_id: str | None = None, home: Path | None = None) -> dict[str, Any]:
    creds = load_credentials(home)
    out: dict[str, Any] = {"credentials": creds.to_public_dict()}
    if session_id:
        with _poll_lock:
            sess = _poll_sessions.get(session_id)
        if sess:
            out["session"] = {
                "session_id": session_id,
                "status": sess.get("status"),
                "error": sess.get("error"),
            }
            if sess.get("status") == "connected":
                out["credentials"] = load_credentials(home).to_public_dict()
        else:
            out["session"] = {
                "session_id": session_id,
                "status": "unknown",
                "error": None,
            }
    return out


def refresh_if_needed(home: Path | None = None) -> XaiCredentials:
    """Refresh OAuth access token if near expiry. Thread-safe."""
    with _refresh_lock:
        creds = load_credentials(home)
        if creds.auth_method != "oauth" or not creds.refresh_token:
            return creds
        if creds.expires_at and time.time() < float(creds.expires_at) - 120:
            return creds
        try:
            data = _http_form(
                TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": creds.refresh_token,
                    "client_id": _client_id(),
                },
            )
        except RuntimeError as e:
            logger.warning("xAI token refresh failed: %s", e)
            return creds
        access = data.get("access_token")
        if not access:
            return creds
        expires_in = int(data.get("expires_in") or 3600)
        creds.access_token = access
        if data.get("refresh_token"):
            creds.refresh_token = data["refresh_token"]
        creds.expires_at = time.time() + expires_in
        save_credentials(creds, home=home)
        return creds


def resolve_bearer(home: Path | None = None) -> str | None:
    """Best available bearer for xAI API calls."""
    creds = refresh_if_needed(home)
    token = creds.bearer_token()
    if token:
        return token
    env_key = (
        os.environ.get("XAI_API_KEY", "").strip()
        or os.environ.get("REMEDY_XAI_API_KEY", "").strip()
    )
    return env_key or None
