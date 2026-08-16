"""GitHub/git auth must survive subprocess env scrub (git push partner path)."""

from __future__ import annotations

from remedy.execution.sandbox import scrub_subprocess_env


def test_gh_and_github_tokens_preserved(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_test_keep")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test_keep")
    monkeypatch.setenv("XAI_API_KEY", "xai_must_drop")
    monkeypatch.setenv("OPENAI_API_KEY", "sk_must_drop")
    monkeypatch.setenv("REMEDY_HOME", "must_drop")
    env = scrub_subprocess_env()
    assert env.get("GH_TOKEN") == "ghp_test_keep"
    assert env.get("GITHUB_TOKEN") == "ghs_test_keep"
    assert "XAI_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "REMEDY_HOME" not in env


def test_cloud_cli_credentials_preserved(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "C:\\keys\\adc.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.setenv("AWS_PROFILE", "dev")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("GOOGLE_API_KEY", "must_drop")
    env = scrub_subprocess_env()
    assert env.get("GOOGLE_APPLICATION_CREDENTIALS") == "C:\\keys\\adc.json"
    assert env.get("GOOGLE_CLOUD_PROJECT") == "my-proj"
    assert env.get("AWS_PROFILE") == "dev"
    assert env.get("AWS_REGION") == "us-east-1"
    assert "GOOGLE_API_KEY" not in env


def test_ssh_agent_preserved(monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh.sock")
    monkeypatch.setenv("SSH_AGENT_PID", "1234")
    env = scrub_subprocess_env()
    assert env.get("SSH_AUTH_SOCK") == "/tmp/ssh.sock"
    assert env.get("SSH_AGENT_PID") == "1234"
