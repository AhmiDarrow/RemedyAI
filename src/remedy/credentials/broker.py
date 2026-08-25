"""CredentialBroker — generic subprocesses get a safe env, not the owner's tokens.

M1.4: default child environment is OS/path only. VCS / SSH / registry keys
are copied in only when a grant is issued for git/gh/npm/etc.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable, Mapping, Sequence

from remedy.credentials.grants import (
    CredentialGrant,
    CredentialRequest,
    CredentialScope,
    new_grant_id,
)

# Keys a child may always have (no secrets).
_SAFE_EXACT = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "USERPROFILE",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "USERNAME",
    "USERDOMAIN",
    "COMPUTERNAME",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "COMSPEC",
    "OS",
    "TERM",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "SYSTEMDRIVE",
    "PROGRAMFILES",
    "PROGRAMDATA",
}

_VCS_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GH_HOST",
    "GH_REPO",
    "GH_PAGER",
    "GIT_ASKPASS",
    "GIT_TERMINAL_PROMPT",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_EDITOR",
    "GIT_PAGER",
    "GIT_SEQUENCE_EDITOR",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "SSH_CONNECTION",
    "GPG_TTY",
)

_REGISTRY_KEYS = (
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "TWINE_USERNAME",
    "TWINE_PASSWORD",
    "CARGO_REGISTRY_TOKEN",
    "PYPI_TOKEN",
)

_CLOUD_KEYS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_QUOTA_PROJECT",
    "CLOUDSDK_CORE_PROJECT",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
)

_VCS_PREFIXES = ("GIT_", "GH_", "SSH_", "GPG_")
_REGISTRY_PREFIXES = ("NPM_",)
_CLOUD_PREFIXES = ("AWS_", "CLOUDSDK_")


def _copy_keys(source: Mapping[str, str], keys: Iterable[str], prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    out: dict[str, str] = {}
    want = {k.upper() for k in keys}
    for key, val in source.items():
        upper = key.upper()
        if upper in want or any(upper.startswith(p) for p in prefixes):
            out[key] = val
    return out


def safe_base_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """OS/path environment with no tokens, keys, or agent sockets."""
    import os

    raw = dict(source) if source is not None else dict(os.environ)
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key.upper() in _SAFE_EXACT:
            out[key] = val
    return out


class CredentialBroker:
    """Issues time-bound env grants. Revoke by grant_id."""

    def __init__(self) -> None:
        self._grants: dict[str, CredentialGrant] = {}

    def grant(
        self,
        request: CredentialRequest,
        ctx: object | None = None,
        *,
        source: Mapping[str, str] | None = None,
    ) -> CredentialGrant:
        _ = ctx
        import os

        raw = dict(source) if source is not None else dict(os.environ)
        provider = (request.provider or "").strip().lower()
        if provider in ("github", "git", "vcs", "ssh"):
            env = _copy_keys(raw, _VCS_KEYS, _VCS_PREFIXES)
        elif provider in ("npm", "pypi", "cargo", "registry"):
            env = _copy_keys(raw, _REGISTRY_KEYS, _REGISTRY_PREFIXES)
        elif provider in ("aws", "gcp", "cloud"):
            env = _copy_keys(raw, _CLOUD_KEYS, _CLOUD_PREFIXES)
        else:
            env = {}
        now = datetime.now(UTC)
        grant = CredentialGrant(
            grant_id=new_grant_id(),
            provider=provider or "none",
            scope=CredentialScope(
                provider=provider or "none",
                repository=request.repository,
                operations=request.operations,
            ),
            expires_at=now + (request.ttl or timedelta(minutes=15)),
            environment=env,
        )
        self._grants[grant.grant_id] = grant
        return grant

    def revoke(self, grant_id: str) -> None:
        old = self._grants.get(grant_id)
        if old is None:
            return
        self._grants[grant_id] = CredentialGrant(
            grant_id=old.grant_id,
            provider=old.provider,
            scope=old.scope,
            expires_at=old.expires_at,
            environment={},
            revoked=True,
        )

    def active(self) -> list[CredentialGrant]:
        now = datetime.now(UTC)
        return [g for g in self._grants.values() if g.alive(now)]


_BROKER = CredentialBroker()


def default_broker() -> CredentialBroker:
    return _BROKER


def apply_grants(
    base: Mapping[str, str],
    grants: Sequence[CredentialGrant] | None,
) -> dict[str, str]:
    out = dict(base)
    now = datetime.now(UTC)
    for grant in grants or ():
        if grant.alive(now):
            out.update(dict(grant.environment))
    return out


def child_environment(
    source: Mapping[str, str] | None = None,
    *,
    grants: Sequence[CredentialGrant] | None = None,
) -> dict[str, str]:
    """Safe OS env plus any live grants. Default grants are empty."""
    return apply_grants(safe_base_env(source), grants)


def grant_for_argv(
    argv: Sequence[str],
    *,
    source: Mapping[str, str] | None = None,
    broker: CredentialBroker | None = None,
) -> list[CredentialGrant]:
    """Infer grants from the executable name (git/gh → vcs, npm → registry)."""
    if not argv:
        return []
    head = str(argv[0] or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if head.endswith(".exe"):
        head = head[:-4]
    b = broker or _BROKER
    if head in ("git", "gh", "ssh"):
        return [b.grant(CredentialRequest(provider="vcs"), source=source)]
    if head in ("npm", "npx", "yarn", "pnpm", "twine", "cargo", "uv", "pip"):
        return [b.grant(CredentialRequest(provider="registry"), source=source)]
    if head in ("aws", "gcloud"):
        return [b.grant(CredentialRequest(provider="cloud"), source=source)]
    return []
