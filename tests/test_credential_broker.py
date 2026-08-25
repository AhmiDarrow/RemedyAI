"""M1.4 CredentialBroker — grants are scoped, time-bound, and revocable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from remedy.credentials.broker import CredentialBroker, child_environment
from remedy.credentials.grants import CredentialRequest


def test_grant_and_revoke(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_live")
    b = CredentialBroker()
    g = b.grant(CredentialRequest(provider="vcs", ttl=timedelta(minutes=5)))
    assert g.alive()
    assert g.environment.get("GH_TOKEN") == "ghp_live"
    b.revoke(g.grant_id)
    assert not b._grants[g.grant_id].alive()
    env = child_environment(grants=[b._grants[g.grant_id]])
    assert "GH_TOKEN" not in env


def test_expired_grant_not_applied(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_live")
    b = CredentialBroker()
    g = b.grant(CredentialRequest(provider="vcs", ttl=timedelta(seconds=-1)))
    assert not g.alive(datetime.now(UTC))
    env = child_environment(grants=[g])
    assert "GH_TOKEN" not in env
