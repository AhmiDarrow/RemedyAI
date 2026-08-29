"""Generic shell gets no tokens; git/gh argv still receives VCS grants."""

from __future__ import annotations

from remedy.execution.sandbox import scrub_subprocess_env, unattended_vcs_env


def test_generic_shell_drops_vcs_and_llm_tokens(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_test_keep")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test_keep")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh.sock")
    monkeypatch.setenv("XAI_API_KEY", "xai_must_drop")
    monkeypatch.setenv("OPENAI_API_KEY", "sk_must_drop")
    monkeypatch.setenv("REMEDY_HOME", "must_drop")
    env = scrub_subprocess_env()
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert "SSH_AUTH_SOCK" not in env
    assert "XAI_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "REMEDY_HOME" not in env


def test_git_argv_grants_vcs_tokens(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_test_keep")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test_keep")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh.sock")
    monkeypatch.setenv("XAI_API_KEY", "xai_must_drop")
    env = scrub_subprocess_env(argv=["git", "push"])
    assert env.get("GH_TOKEN") == "ghp_test_keep"
    assert env.get("GITHUB_TOKEN") == "ghs_test_keep"
    assert env.get("SSH_AUTH_SOCK") == "/tmp/ssh.sock"
    assert "XAI_API_KEY" not in env


def test_cloud_cli_credentials_only_with_cloud_argv(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "C:\\keys\\adc.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.setenv("AWS_PROFILE", "dev")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("GOOGLE_API_KEY", "must_drop")
    bare = scrub_subprocess_env()
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in bare
    assert "AWS_PROFILE" not in bare
    env = scrub_subprocess_env(argv=["aws", "s3", "ls"])
    assert env.get("AWS_PROFILE") == "dev"
    assert env.get("AWS_REGION") == "us-east-1"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "GOOGLE_API_KEY" not in env
    gcp = scrub_subprocess_env(argv=["gcloud", "auth", "list"])
    assert gcp.get("GOOGLE_APPLICATION_CREDENTIALS") == "C:\\keys\\adc.json"
    assert "AWS_PROFILE" not in gcp


def test_path_env_must_use_scrubbed_base(monkeypatch, tmp_path):
    """bash_exec used to copy os.environ then only scrub in SubprocessSandbox.

    Background spawn and host sessions skipped that sandbox path, so GH_TOKEN
    leaked. Callers must pass a scrubbed base into path_env_with_local_bins.
    """
    from remedy.core.project_fingerprint import path_env_with_local_bins
    from remedy.execution.sandbox import scrub_subprocess_env

    monkeypatch.setenv("GH_TOKEN", "ghp_leaked")
    raw = path_env_with_local_bins(tmp_path)
    assert raw.get("GH_TOKEN") == "ghp_leaked"
    safe = path_env_with_local_bins(
        tmp_path, base_env=scrub_subprocess_env(argv=["cmd", "/c", "echo"])
    )
    assert "GH_TOKEN" not in safe
    assert "PATH" in safe or "Path" in safe


def test_unattended_git_never_inherits_askpass_or_llm_keys(monkeypatch):
    monkeypatch.setenv("GIT_ASKPASS", r"C:\Program Files\Git\mingw64\bin\git-credential-manager.exe")
    monkeypatch.setenv("GH_TOKEN", "ghp_test_keep")
    monkeypatch.setenv("XAI_API_KEY", "xai_must_drop")
    env = unattended_vcs_env(["git"])
    assert "GIT_ASKPASS" not in env
    assert env.get("GIT_TERMINAL_PROMPT") == "0"
    assert env.get("GCM_INTERACTIVE") == "never"
    assert env.get("GH_PROMPT_DISABLED") == "1"
    assert env.get("GH_TOKEN") == "ghp_test_keep"
    assert "XAI_API_KEY" not in env


def test_ssh_agent_only_with_git_or_ssh_argv(monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh.sock")
    monkeypatch.setenv("SSH_AGENT_PID", "1234")
    assert "SSH_AUTH_SOCK" not in scrub_subprocess_env()
    env = scrub_subprocess_env(argv=["ssh"])
    assert env.get("SSH_AUTH_SOCK") == "/tmp/ssh.sock"
    assert env.get("SSH_AGENT_PID") == "1234"
