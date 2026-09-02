"""Fail-closed path families for the RemedyConnect proxy.

Hard denies run before pane flags so a phone can never become the computer
host poller or mint ``local_api_token``. Pane-off denies are prefix families,
not a single string match.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote

from remedy.connect.panes import normalize_panes

# Host-poller / job-claim surface. Query variants of jobs/next live here too.
_JOBS_NEXT_PREFIXES = (
    "/api/computer/jobs/next",
)
_HOST_PREFIXES = (
    "/api/computer/host",
)
_HOST_POLLER_PREFIXES = (
    "/api/computer/ui/command",
    "/api/computer/jobs/",
    "/api/computer/a11y/",
)
_BOOTSTRAP_PREFIXES = (
    "/api/auth/local-bootstrap",
)

# Connect management (pair QR, bind, pause, revoke). Phone uses /connect/me
# and /connect/preview only; those are excluded in _connect_mgmt().
_CONNECT_ME_PATHS = frozenset(
    {
        "/connect/me",
        "/api/connect/me",
        "/connect/preview",
        "/api/connect/preview",
    }
)

# Dedicated credential writers. /api/settings body lock never sees these.
_CREDENTIAL_PREFIXES = (
    "/api/auth",
    "/api/providers",
    "/api/assistant",
    "/api/webhooks",
    "/api/webhook",
)
_CREDENTIAL_PATHS = (
    "/api/memory/persona-wipe",
    "/api/memory/import",
    "/api/partner/identity/import",
    "/api/partner/identity/export",
    "/api/skills/import",
    "/api/sessions/import",
)

# Workspace rails. Unknown future prefixes fail closed when the pane is off.
_RAILS_PREFIXES = (
    "/api/workspace",
    "/api/files",
    "/api/scratch",
    "/api/terminal",
    "/api/browser",
    "/api/computer/ui/command",
    "/api/media",
)

_CHAT_PREFIXES = (
    "/api/chat",
    # Hive spawn/assign runs tool-using foragers; gate it like chat so a phone
    # with every pane off cannot start work the owner never sees.
    "/api/hive",
)
_SESSIONS_PREFIX = "/api/sessions"
_LIVE_UI_PREFIXES = (
    "/api/events",
    "/api/partner",
)


_BLOCKED_METHODS = frozenset({"CONNECT", "TRACE", "TRACK"})

# Server-control surface. A phone can drive Remedy but must NEVER end the
# server or fire app commands (restart / shutdown / quit flavors). Hard-denied
# before pane flags so no pane combination can re-enable them. ``/api/stop``
# is NOT here: routes/connect.py defines it as "abort the phone's own turn"
# and the phone's Stop button falls back to it when no session id is known.
_SERVER_KILL_PREFIXES = (
    "/api/shutdown",
    "/api/quit",
    "/api/restart",
    "/api/exit",
    "/api/app/command",
)


def sanitize_origin_path(path: str) -> str | None:
    """Relative URL path only. ``None`` means refuse (absolute / traversal)."""
    raw = (path or "").strip() or "/"
    if "?" in raw:
        raw = raw.split("?", 1)[0]
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    if "\\" in raw or "\x00" in raw:
        return None
    low = raw.lower()
    if "://" in low or low.startswith("//") or low.startswith("http:") or low.startswith("https:"):
        return None
    for _ in range(4):
        nxt = unquote(raw)
        if nxt == raw:
            break
        raw = nxt
        if "://" in raw.lower() or raw.startswith("//"):
            return None
    # A ``#`` / ``?`` that only appeared after percent-decoding (``%23``,
    # ``%3F``) would be re-parsed by the upstream client as fragment / query,
    # truncating the path *after* the deny check ran. Nothing legitimate needs
    # them (or a leftover ``%``) in a decoded path: refuse.
    if "#" in raw or "?" in raw or "%" in raw or "\\" in raw or "\x00" in raw:
        return None
    if not raw.startswith("/"):
        raw = "/" + raw
    parts: list[str] = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    # Original case: session ids and workspace file names are case-sensitive
    # on the sidecar. Callers that *match* lowercase via ``_norm_path``.
    return "/" + "/".join(parts)


def _norm_path(path: str) -> str:
    safe = sanitize_origin_path(path)
    return safe.lower() if safe is not None else "/__invalid__"


def _norm_query(query: str) -> str:
    return (query or "").lstrip("?").strip()


def _query_keys(query: str) -> set[str]:
    if not query:
        return set()
    try:
        parsed = parse_qs(_norm_query(query), keep_blank_values=True)
    except Exception:
        return {p.split("=", 1)[0].lower() for p in _norm_query(query).split("&") if p}
    return {str(k).lower() for k in parsed}


def _starts(path: str, prefixes: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        p = prefix.rstrip("/")
        if path == p or path.startswith((p + "/", p + "?", p + "#")):
            return True
    return False


def _connect_mgmt(path: str) -> bool:
    """True for Connect management that must never cross the phone pipe."""
    if path in _CONNECT_ME_PATHS:
        return False
    if path == "/connect" or path.startswith("/connect/"):
        return True
    return path == "/api/connect" or path.startswith("/api/connect/")


def _wipe_or_import(path: str) -> bool:
    """Import/wipe as a path-segment family, not one route string."""
    for seg in path.split("/"):
        if not seg:
            continue
        if seg in ("wipe", "import"):
            return True
        if seg.endswith("-wipe") or seg.endswith("-import"):
            return True
        if seg.startswith("wipe-") or seg.startswith("import-"):
            return True
    return False


def _credential_writer(path: str) -> bool:
    if _starts(path, _CREDENTIAL_PREFIXES):
        return True
    if _starts(path, _CREDENTIAL_PATHS):
        return True
    return _wipe_or_import(path)


def _is_stop_or_approval(method: str, path: str) -> bool:
    """Approvals and turn-control stay reachable; server control never is.

    App-command (restart/shutdown/quit) is hard-denied in
    ``_SERVER_KILL_PREFIXES``. A phone may abort a turn via
    ``/api/sessions/{id}/abort`` or the connect-scoped ``POST /api/stop``
    (which aborts only the phone's own session), never end the server.
    """
    if path == "/api/stop" and (method or "").upper() == "POST":
        return True
    if path.startswith("/api/approvals"):
        return True
    if path.endswith("/abort") and path.startswith("/api/sessions/"):
        return True
    if "/abort" in path and path.startswith("/api/sessions/"):
        return True
    return path in ("/api/turn-active",)


def _jobs_next_family(path: str, query: str) -> bool:
    if _starts(path, _JOBS_NEXT_PREFIXES):
        return True
    # Path accidentally carrying the query string, or sibling claim shapes.
    if "/computer/jobs/next" in path:
        return True
    keys = _query_keys(query)
    return bool(path.startswith("/api/computer/jobs") and keys & {"wait_ms", "driver", "only", "take"})


# Provider/model switch is safe for the phone: no secrets, no connect keys.
# The body lock above rejects everything sensitive; these keys only retarget
# which model the PC uses.
_PROVIDER_SAFE_KEYS = frozenset({"llm_provider", "llm_model", "provider", "model"})


_SETTINGS_LOCK_KEYS = frozenset(
    {
        "connect_enabled",
        "connect_bind_host",
        "connect_bind_port",
        "connect_relay_url",
        "connect_paused",
        "connect_panes",
        "connect_allow_ipv6",
        "connect_rdv_enabled",
        "llm_api_key",
        "api_key",
        "http_bootstrap",
        "provider_keys",
        "messengers",
        "assistant",
    }
)


def settings_body_safe_provider(body: bytes) -> bool:
    """True when a /api/settings body only switches provider/model."""
    if not body:
        return False
    try:
        import json

        obj = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return False
    if not isinstance(obj, dict) or not obj:
        return False
    for key, value in obj.items():
        if str(key).strip().lower() not in _PROVIDER_SAFE_KEYS:
            return False
        if value is not None and not isinstance(value, str):
            return False
    return True


def settings_write_locked(body: bytes) -> str | None:
    """Even with the settings-write pane on, Connect cannot retarget the pipe."""
    if not body:
        return None
    try:
        import json

        obj = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return "settings:locked"
    if not isinstance(obj, dict):
        return "settings:locked"
    for key in obj:
        if str(key).strip().lower() in _SETTINGS_LOCK_KEYS:
            return "settings:locked"
    return None


def connect_forbidden(
    method: str,
    path: str,
    query: str,
    panes: dict | None,
) -> str | None:
    """Return a reason string when the phone must get 403, else ``None``."""
    method_u = (method or "GET").strip().upper()
    if method_u in _BLOCKED_METHODS:
        return "method"
    norm = _norm_path(path)
    if norm == "/__invalid__":
        return "path"
    q = _norm_query(query)
    flags = normalize_panes(panes)

    if _jobs_next_family(norm, q):
        return "host-poller:jobs/next"
    if _starts(norm, _HOST_PREFIXES):
        return "host-poller:host"
    if _starts(norm, _HOST_POLLER_PREFIXES):
        return "host-poller"
    if _starts(norm, _BOOTSTRAP_PREFIXES) or "local-bootstrap" in norm:
        return "auth:local-bootstrap"
    if _starts(norm, _SERVER_KILL_PREFIXES):
        return "server:kill"
    if _connect_mgmt(norm):
        return "connect:mgmt"

    if _is_stop_or_approval(method_u, norm):
        return None

    if method_u == "POST" and (
        norm == "/api/computer/capture" or norm.startswith("/api/computer/capture/")
    ):
        if not flags.get("computer_preview"):
            return "pane:computer_preview"

    if method_u in ("PUT", "PATCH", "DELETE") and norm.startswith("/api/settings"):
        if not flags.get("settings_write"):
            return "pane:settings_write"

    # Read-only provider glance for the phone Settings pane (no secrets).
    if method_u == "GET" and norm in ("/api/providers/connected", "/api/providers/free"):
        return None

    if _credential_writer(norm) and not flags.get("settings_write"):
        return "pane:settings_write"

    if not flags.get("rails") and _starts(norm, _RAILS_PREFIXES):
        return "pane:rails"

    if not flags.get("chat") and _starts(norm, _CHAT_PREFIXES):
        return "pane:chat"

    if not flags.get("sessions") and norm.startswith(_SESSIONS_PREFIX):
        return "pane:sessions"

    if not flags.get("live_ui") and _starts(norm, _LIVE_UI_PREFIXES):
        return "pane:live_ui"

    # Fail closed on everything the phone has no pane for. Every route the
    # phone app calls is in a family above or in _PHONE_FAMILIES; anything
    # else under /api (memory, updates, claimidx, future routes) is not
    # proxied with the owner's Bearer just because nobody listed it yet.
    if (
        norm.startswith("/api/")
        and not _starts(norm, _PHONE_FAMILIES)
        and not _credential_writer(norm)  # already gated on settings_write above
    ):
        return "unknown:family"

    return None


# Families the phone may reach once the pane checks above pass. Unlisted
# /api prefixes are refused (see the tail of ``connect_forbidden``).
_PHONE_FAMILIES = (
    *_RAILS_PREFIXES,
    *_CHAT_PREFIXES,
    *_LIVE_UI_PREFIXES,
    _SESSIONS_PREFIX,
    "/api/approvals",
    "/api/turn-active",
    "/api/stop",
    "/api/goals",
    "/api/models",
    "/api/ping",
    "/api/health",
    "/api/status",
    "/api/providers/connected",
    "/api/providers/free",
    "/api/settings",
    "/api/computer/capture",
    "/api/connect/me",
    "/api/connect/preview",
)
