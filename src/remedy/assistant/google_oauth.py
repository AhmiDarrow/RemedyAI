"""Google OAuth (authorization code + PKCE) for Gmail + Calendar — loopback.

Tokens stored under ``~/.remedy/auth/google.json`` (DPAPI on Windows).
Product OAuth client from env/build (end users never paste Client ID).

Not computer-use: official Google sign-in in the system browser only.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Gmail + Calendar (+ identity for account email label).
# readonly + compose: list/read + drafts only (no silent send).
SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
)

DEFAULT_REDIRECT = "http://127.0.0.1:7400/api/assistant/google/callback"

# Public OAuth client for Remedy Desktop (same idea as xAI device OAuth client).
# Not a user secret — end users never paste this. Override at build/runtime via
# REMEDY_GOOGLE_OAUTH_CLIENT_ID / REMEDY_GOOGLE_OAUTH_CLIENT_SECRET.
# Register in Google Cloud as Desktop (or Web + loopback redirect DEFAULT_REDIRECT).
DEFAULT_GOOGLE_CLIENT_ID = os.environ.get("REMEDY_GOOGLE_OAUTH_DEFAULT_CLIENT_ID", "").strip()
DEFAULT_GOOGLE_CLIENT_SECRET = os.environ.get(
    "REMEDY_GOOGLE_OAUTH_DEFAULT_CLIENT_SECRET", ""
).strip()

_pending: dict[str, dict[str, Any]] = {}
_pending_lock = threading.Lock()
_token_lock = threading.Lock()
# UI may poll success briefly; PKCE verifier must never live past exchange start.
_DONE_TTL_S = 60.0
_PENDING_TTL_S = 900.0


def _purge_pending_locked(now: float | None = None) -> None:
    """Drop expired pending rows and finished rows past UI poll window."""
    t = time.time() if now is None else now
    dead: list[str] = []
    for key, row in _pending.items():
        created = float(row.get("created_at") or 0)
        done_at = row.get("done_at")
        if done_at is not None and (t - float(done_at)) > _DONE_TTL_S:
            dead.append(key)
        elif created and (t - created) > _PENDING_TTL_S:
            dead.append(key)
    for key in dead:
        _pending.pop(key, None)


def _home(home: Path | str | None = None) -> Path:
    if home is not None and str(home).strip():
        return Path(home).expanduser().resolve()
    return Path(os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser().resolve()


def auth_dir(home: Path | str | None = None) -> Path:
    d = _home(home) / "auth"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tokens_path(home: Path | str | None = None) -> Path:
    return auth_dir(home) / "google.json"


def app_config_path(home: Path | str | None = None) -> Path:
    return auth_dir(home) / "google_oauth_app.json"


@dataclass
class GoogleAppConfig:
    """User-registered Google Cloud OAuth client (Desktop or Web + loopback)."""

    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = DEFAULT_REDIRECT

    def configured(self) -> bool:
        return bool(self.client_id.strip())

    def to_public(self) -> dict[str, Any]:
        return {
            "client_id_set": bool(self.client_id.strip()),
            "client_secret_set": bool(self.client_secret.strip()),
            "redirect_uri": self.redirect_uri or DEFAULT_REDIRECT,
            "scopes": list(SCOPES),
        }


def load_app_config(home: Path | str | None = None) -> GoogleAppConfig:
    """Resolve OAuth app credentials (product build → env → optional sealed override).

    End users never enter Client ID in Settings. Operators set env at install/build.
    """
    cid = (
        os.environ.get("REMEDY_GOOGLE_OAUTH_CLIENT_ID", "").strip()
        or os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
        or DEFAULT_GOOGLE_CLIENT_ID
    )
    secret = (
        os.environ.get("REMEDY_GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
        or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
        or DEFAULT_GOOGLE_CLIENT_SECRET
    )
    redirect = (
        os.environ.get("REMEDY_GOOGLE_OAUTH_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT
    )
    # Optional sealed override (power-user / lab only — not exposed in main Settings UI)
    raw = _read_sealed(app_config_path(home))
    if isinstance(raw, dict):
        if str(raw.get("client_id") or "").strip():
            cid = str(raw.get("client_id") or "").strip()
        if str(raw.get("client_secret") or "").strip():
            secret = str(raw.get("client_secret") or "").strip()
        if str(raw.get("redirect_uri") or "").strip():
            redirect = str(raw.get("redirect_uri") or "").strip() or DEFAULT_REDIRECT
    return GoogleAppConfig(client_id=cid, client_secret=secret, redirect_uri=redirect)


def save_app_config(
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    home: Path | str | None = None,
) -> GoogleAppConfig:
    cur = load_app_config(home)
    if client_id is not None:
        cur.client_id = str(client_id).strip()
    if client_secret is not None:
        # Empty string clears; omit means leave (callers pass explicitly).
        cur.client_secret = str(client_secret).strip()
    if redirect_uri is not None and str(redirect_uri).strip():
        cur.redirect_uri = str(redirect_uri).strip()
    path = app_config_path(home)
    payload = {
        "client_id": cur.client_id,
        "client_secret": cur.client_secret,
        "redirect_uri": cur.redirect_uri,
        "updated_at": time.time(),
    }
    plain = json.dumps(payload, indent=2).encode("utf-8")
    _write_sealed(path, plain)
    return cur


@dataclass
class GoogleTokens:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0  # unix
    token_type: str = "Bearer"
    scope: str = ""
    email: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        if self.access_token and (
            not self.expires_at or time.time() < float(self.expires_at) - 60
        ):
            return True
        return bool(self.refresh_token)

    def to_public(self) -> dict[str, Any]:
        return {
            "provider": "google",
            "connected": self.connected,
            "email": self.email,
            "has_refresh": bool(self.refresh_token),
            "expires_at": self.expires_at or None,
            "scopes": (self.scope or "").split(),
        }


def _write_sealed(path: Path, plain: bytes) -> None:
    written = False
    try:
        from remedy.interfaces.secret_store import _dpapi_available, _dpapi_protect, _harden_path

        if _dpapi_available():
            sealed = _dpapi_protect(plain)
            envelope = {
                "v": 2,
                "dpapi": base64.b64encode(sealed).decode("ascii"),
                "updated_at": time.time(),
            }
            path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
            written = True
            with contextlib.suppress(Exception):
                _harden_path(path, is_dir=False)
    except Exception as exc:
        logger.warning("Google token DPAPI protect failed: %s", exc)
    if not written:
        path.write_text(plain.decode("utf-8") + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _read_sealed(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw_bytes = path.read_bytes()
        outer = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(outer, dict):
        return None
    if outer.get("v") == 2 and outer.get("dpapi"):
        try:
            from remedy.interfaces.secret_store import _dpapi_unprotect

            plain = _dpapi_unprotect(base64.b64decode(outer["dpapi"]))
            data = json.loads(plain.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("Google token DPAPI decrypt failed: %s", exc)
            return None
    return outer


def load_tokens(home: Path | str | None = None) -> GoogleTokens:
    data = _read_sealed(tokens_path(home))
    if not data:
        return GoogleTokens()
    return GoogleTokens(
        access_token=str(data.get("access_token") or ""),
        refresh_token=str(data.get("refresh_token") or ""),
        expires_at=float(data.get("expires_at") or 0),
        token_type=str(data.get("token_type") or "Bearer"),
        scope=str(data.get("scope") or ""),
        email=str(data.get("email") or ""),
        raw=data,
    )


def save_tokens(tokens: GoogleTokens, home: Path | str | None = None) -> None:
    payload = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at,
        "token_type": tokens.token_type,
        "scope": tokens.scope,
        "email": tokens.email,
        "updated_at": time.time(),
    }
    _write_sealed(tokens_path(home), json.dumps(payload, indent=2).encode("utf-8"))


def clear_tokens(home: Path | str | None = None) -> None:
    path = tokens_path(home)
    if path.is_file():
        with contextlib.suppress(OSError):
            path.unlink()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _http_form(url: str, data: dict[str, str], timeout: float = 30.0) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "RemedyDesktop-Google-OAuth/1.0",
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


def _http_get_json(url: str, bearer: str, timeout: float = 30.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/json",
            "User-Agent": "RemedyDesktop-Google-OAuth/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def start_oauth(
    *,
    home: Path | str | None = None,
    redirect_uri: str | None = None,
) -> dict[str, Any]:
    """Begin auth-code + PKCE flow. Returns auth_url + state for the UI."""
    from remedy.assistant.privacy import require_consent

    require_consent(home)
    app = load_app_config(home)
    if not app.configured():
        raise ValueError(
            "Google sign-in is not configured for this Remedy build "
            "(set REMEDY_GOOGLE_OAUTH_CLIENT_ID)."
        )
    redir = (redirect_uri or app.redirect_uri or DEFAULT_REDIRECT).strip()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": app.client_id,
        "redirect_uri": redir,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    with _pending_lock:
        _purge_pending_locked()
        _pending[state] = {
            "code_verifier": verifier,
            "redirect_uri": redir,
            "home": str(_home(home)),
            "created_at": time.time(),
            "status": "pending",
        }
    return {
        "status": "pending",
        "state": state,
        "auth_url": auth_url,
        "redirect_uri": redir,
        "message": "Open the link, sign in with Google, then return to Remedy.",
    }


def pending_status(state: str) -> dict[str, Any]:
    with _pending_lock:
        _purge_pending_locked()
        row = _pending.get(state)
    if not row:
        return {"status": "unknown", "state": state}
    return {
        "status": row.get("status") or "pending",
        "state": state,
        "error": row.get("error"),
        "email": row.get("email"),
    }


def complete_oauth(
    *,
    code: str,
    state: str,
    home: Path | str | None = None,
) -> GoogleTokens:
    """Exchange authorization code for tokens; persist + mark pending done.

    State is single-use: PKCE verifier is consumed under lock before the token
    HTTP call so concurrent callbacks / replay cannot re-use the same state.
    A minimal success/error record (no secrets) remains briefly for UI poll.
    """
    now = time.time()
    with _pending_lock:
        _purge_pending_locked(now)
        row = _pending.get(state)
        if not row:
            raise ValueError("Invalid or expired OAuth state — start Connect again.")
        status = str(row.get("status") or "pending")
        if status in ("connected", "exchanging", "consumed", "error"):
            raise ValueError("OAuth state already used — start Connect again.")
        if now - float(row.get("created_at") or 0) > _PENDING_TTL_S:
            _pending.pop(state, None)
            raise ValueError("OAuth session expired — start Connect again.")
        code_verifier = str(row.get("code_verifier") or "")
        if not code_verifier:
            _pending.pop(state, None)
            raise ValueError("OAuth state missing verifier — start Connect again.")
        redirect_uri = str(row.get("redirect_uri") or "")
        row_home = row.get("home")
        # Consume immediately — no second exchange with this state/verifier.
        row["status"] = "exchanging"
        row.pop("code_verifier", None)

    app_home = home or row_home
    app = load_app_config(app_home)
    form: dict[str, str] = {
        "code": code,
        "client_id": app.client_id,
        "redirect_uri": redirect_uri or str(app.redirect_uri),
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    if app.client_secret:
        form["client_secret"] = app.client_secret

    try:
        data = _http_form(TOKEN_URL, form)
    except Exception as exc:
        with _pending_lock:
            _pending[state] = {
                "status": "error",
                "error": str(exc)[:200],
                "created_at": now,
                "done_at": time.time(),
                "home": str(row_home or ""),
            }
        raise

    if data.get("error"):
        err = str(data.get("error_description") or data["error"])
        with _pending_lock:
            _pending[state] = {
                "status": "error",
                "error": err[:300],
                "created_at": now,
                "done_at": time.time(),
                "home": str(row_home or ""),
            }
        raise RuntimeError(err)

    access = str(data.get("access_token") or "")
    if not access:
        with _pending_lock:
            _pending[state] = {
                "status": "error",
                "error": "missing access_token",
                "created_at": now,
                "done_at": time.time(),
                "home": str(row_home or ""),
            }
        raise RuntimeError("Google token response missing access_token")

    existing = load_tokens(app_home)
    refresh = str(data.get("refresh_token") or existing.refresh_token or "")
    expires_in = float(data.get("expires_in") or 3600)
    tokens = GoogleTokens(
        access_token=access,
        refresh_token=refresh,
        expires_at=time.time() + expires_in,
        token_type=str(data.get("token_type") or "Bearer"),
        scope=str(data.get("scope") or " ".join(SCOPES)),
    )
    try:
        info = _http_get_json(USERINFO_URL, access)
        tokens.email = str(info.get("email") or "")
    except Exception as exc:
        logger.debug("userinfo fetch failed: %s", exc)

    save_tokens(tokens, app_home)
    _sync_linked_account(tokens, app_home)

    # Minimal non-secret row for UI poll only (no verifier / tokens).
    with _pending_lock:
        _pending[state] = {
            "status": "connected",
            "email": tokens.email,
            "created_at": now,
            "done_at": time.time(),
            "home": str(row_home or ""),
        }

    return tokens


def _sync_linked_account(tokens: GoogleTokens, home: Path | str | None) -> None:
    try:
        from remedy.assistant.models import LinkedAccount
        from remedy.assistant.store import get_assistant_store

        store = get_assistant_store(home)
        acct = LinkedAccount(
            id="google_primary",
            provider="google",
            email=tokens.email,
            capabilities=["mail", "calendar"],
            status="connected" if tokens.connected else "disconnected",
            last_sync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        store.upsert_account(acct)
        if tokens.email and not store.get_prefs().default_calendar_account:
            store.patch_prefs(default_calendar_account=acct.id)
    except Exception as exc:
        logger.warning("linked account sync failed: %s", exc)


def refresh_access_token(home: Path | str | None = None) -> GoogleTokens:
    tokens = load_tokens(home)
    if not tokens.refresh_token:
        raise ValueError("No Google refresh token — Connect Google again.")
    app = load_app_config(home)
    form: dict[str, str] = {
        "client_id": app.client_id,
        "refresh_token": tokens.refresh_token,
        "grant_type": "refresh_token",
    }
    if app.client_secret:
        form["client_secret"] = app.client_secret
    data = _http_form(TOKEN_URL, form)
    access = str(data.get("access_token") or "")
    if not access:
        raise RuntimeError(str(data.get("error_description") or data.get("error") or "refresh failed"))
    tokens.access_token = access
    tokens.expires_at = time.time() + float(data.get("expires_in") or 3600)
    if data.get("scope"):
        tokens.scope = str(data["scope"])
    save_tokens(tokens, home)
    return tokens


def get_valid_access_token(home: Path | str | None = None) -> str:
    with _token_lock:
        tokens = load_tokens(home)
        if not tokens.connected and not tokens.refresh_token:
            raise ValueError("Google not connected — Settings → Personal assistant → Connect Google.")
        if tokens.access_token and (
            not tokens.expires_at or time.time() < float(tokens.expires_at) - 90
        ):
            return tokens.access_token
        tokens = refresh_access_token(home)
        return tokens.access_token


def disconnect(home: Path | str | None = None) -> None:
    tokens = load_tokens(home)
    if tokens.access_token:
        try:
            _http_form(REVOKE_URL, {"token": tokens.access_token})
        except Exception:
            pass
    clear_tokens(home)
    try:
        from remedy.assistant.models import LinkedAccount
        from remedy.assistant.store import get_assistant_store

        store = get_assistant_store(home)
        store.upsert_account(
            LinkedAccount(
                id="google_primary",
                provider="google",
                email=tokens.email,
                capabilities=["mail", "calendar"],
                status="disconnected",
            )
        )
    except Exception:
        pass


def public_status(home: Path | str | None = None) -> dict[str, Any]:
    app = load_app_config(home)
    tokens = load_tokens(home)
    return {
        **tokens.to_public(),
        "app": app.to_public(),
        "setup_hint": None if app.configured() else "not_configured",
        "sign_in_ready": app.configured(),
    }
