"""The docker command line, which *is* the sandbox.

There is no other boundary here. If `--network none` goes missing, code running
in the sandbox has the internet; if `--read-only` goes, it can write to the
image; if `--memory` goes, it can take the host down. None of those failures
look like anything — the command runs, the output comes back, and the isolation
that was supposed to be there simply is not.

So these tests assert the argv. No container is ever started: the spawn is
intercepted.
"""

from __future__ import annotations

import pytest

from remedy.execution.docker import DockerSandbox


class FakeProc:
    def __init__(self, rc=0, out=b"hello", err=b"") -> None:
        self.returncode = rc
        self._out, self._err = out, err
        self.killed = False

    async def communicate(self):
        return self._out, self._err

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.fixture()
def spawn(monkeypatch):
    """Capture the argv docker would have been started with."""
    calls: list[list[str]] = []
    proc = FakeProc()

    async def fake_exec(*argv, **kw):
        calls.append(list(argv))
        return proc

    monkeypatch.setattr(
        "remedy.execution.process.create_hidden_subprocess_exec", fake_exec
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    return calls


def argv_of(calls):
    return calls[-1]


def pair(argv, flag):
    """The value following *flag*, or None."""
    return argv[argv.index(flag) + 1] if flag in argv else None


# --- availability --------------------------------------------------------------


def test_docker_absent_is_reported_not_pretended(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert DockerSandbox().available is False


def test_docker_present_is_detected(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    assert DockerSandbox().available is True


@pytest.mark.asyncio
async def test_executing_without_docker_fails_rather_than_running_on_the_host(
    monkeypatch,
):
    """Falling back to the host would run unsandboxed code as the owner."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = await DockerSandbox().execute(["echo", "hi"])
    assert result.exit_code == -1
    assert "not available" in result.stderr


# --- the isolation flags --------------------------------------------------------


@pytest.mark.asyncio
async def test_the_container_has_no_network_by_default(spawn):
    await DockerSandbox().execute(["echo", "hi"])
    assert pair(argv_of(spawn), "--network") == "none"


@pytest.mark.asyncio
async def test_the_container_filesystem_is_read_only_by_default(spawn):
    await DockerSandbox().execute(["echo", "hi"])
    assert "--read-only" in argv_of(spawn)


@pytest.mark.asyncio
async def test_memory_is_capped_by_default(spawn):
    await DockerSandbox().execute(["echo", "hi"])
    assert pair(argv_of(spawn), "--memory") == "256m"


@pytest.mark.asyncio
async def test_cpu_is_capped_by_default(spawn):
    await DockerSandbox().execute(["echo", "hi"])
    assert pair(argv_of(spawn), "--cpus") == "1.0"


@pytest.mark.asyncio
async def test_the_container_is_removed_after_the_run(spawn):
    """Without --rm the host accumulates dead containers forever."""
    await DockerSandbox().execute(["echo", "hi"])
    assert "--rm" in argv_of(spawn)


@pytest.mark.asyncio
async def test_every_isolation_flag_is_present_together(spawn):
    """One of them going missing is the whole failure mode."""
    await DockerSandbox().execute(["echo", "hi"])
    argv = argv_of(spawn)
    for flag in ("--rm", "--network", "--memory", "--cpus", "--read-only"):
        assert flag in argv, f"{flag} is missing — the sandbox is weaker than it claims"


# --- deliberate relaxations ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_writable_container_can_be_asked_for(spawn):
    await DockerSandbox(read_only=False).execute(["echo", "hi"])
    assert "--read-only" not in argv_of(spawn)


@pytest.mark.asyncio
async def test_a_network_can_be_asked_for(spawn):
    await DockerSandbox(network="bridge").execute(["echo", "hi"])
    assert pair(argv_of(spawn), "--network") == "bridge"


@pytest.mark.asyncio
async def test_the_limits_are_configurable(spawn):
    await DockerSandbox(memory_limit="2g", cpu_limit="4.0").execute(["echo", "hi"])
    argv = argv_of(spawn)
    assert pair(argv, "--memory") == "2g"
    assert pair(argv, "--cpus") == "4.0"


# --- what actually runs -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_command_is_passed_after_the_image(spawn):
    await DockerSandbox(image="python:3.12-slim").execute(["python", "-c", "print(1)"])
    argv = argv_of(spawn)
    assert argv[argv.index("python:3.12-slim") + 1 :] == ["python", "-c", "print(1)"]


@pytest.mark.asyncio
async def test_a_workspace_is_mounted_and_made_the_working_directory(spawn):
    await DockerSandbox().execute(["echo", "hi"])
    argv = argv_of(spawn)
    assert pair(argv, "-w") == "/workspace"
    assert any(a.endswith(":/workspace") for a in argv)


@pytest.mark.asyncio
async def test_requested_mounts_are_passed_through(spawn):
    await DockerSandbox().execute(["echo", "hi"], mounts=[("/host/data", "/data")])
    assert "/host/data:/data" in argv_of(spawn)


@pytest.mark.asyncio
async def test_environment_variables_are_passed_through(spawn):
    await DockerSandbox().execute(["echo", "hi"], env={"MODE": "test"})
    assert "MODE=test" in argv_of(spawn)


# --- results and failures ----------------------------------------------------------


@pytest.mark.asyncio
async def test_output_comes_back_decoded(spawn):
    result = await DockerSandbox().execute(["echo", "hi"])
    assert result.stdout == "hello"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_undecodable_output_does_not_raise(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    async def fake_exec(*argv, **kw):
        return FakeProc(out=b"\xff\xfe not utf8")

    monkeypatch.setattr(
        "remedy.execution.process.create_hidden_subprocess_exec", fake_exec
    )
    assert isinstance((await DockerSandbox().execute(["x"])).stdout, str)


@pytest.mark.asyncio
async def test_a_container_that_overruns_is_killed_and_reported(monkeypatch):
    """A hung container must not hold the turn open indefinitely."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    proc = FakeProc()

    async def never(*a, **kw):
        import asyncio

        await asyncio.sleep(10)

    proc.communicate = never

    async def fake_exec(*argv, **kw):
        return proc

    monkeypatch.setattr(
        "remedy.execution.process.create_hidden_subprocess_exec", fake_exec
    )
    result = await DockerSandbox().execute(["sleep", "60"], timeout_seconds=0.1)
    assert result.exit_code == -1
    assert "timed out" in result.stderr
    assert proc.killed is True


@pytest.mark.asyncio
async def test_a_missing_docker_binary_mid_run_is_reported(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    async def gone(*a, **kw):
        raise FileNotFoundError("docker")

    monkeypatch.setattr("remedy.execution.process.create_hidden_subprocess_exec", gone)
    result = await DockerSandbox().execute(["echo", "hi"])
    assert result.exit_code == -1
    assert "not found" in result.stderr


@pytest.mark.asyncio
async def test_the_scratch_directory_is_cleaned_up_even_on_failure(monkeypatch, tmp_path):
    made: list[str] = []
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    real_mkdtemp = __import__("tempfile").mkdtemp

    def track(*a, **kw):
        d = real_mkdtemp(*a, **kw)
        made.append(d)
        return d

    monkeypatch.setattr("tempfile.mkdtemp", track)

    async def gone(*a, **kw):
        raise FileNotFoundError("docker")

    monkeypatch.setattr("remedy.execution.process.create_hidden_subprocess_exec", gone)
    await DockerSandbox().execute(["echo", "hi"])

    import os

    assert made
    assert not os.path.exists(made[0]), "the scratch mount was left behind"


# --- cleanup ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_kills_a_prune_that_hangs(monkeypatch):
    """wait_for cancels the *await*, not the process. A hung ``docker
    container prune`` was left running (and its pipes open) after cleanup
    returned, the same leak the execute/availability paths already fixed."""
    import asyncio

    class HangingProc:
        def __init__(self) -> None:
            self.killed = False
            self.waits = 0

        async def wait(self):
            self.waits += 1
            if not self.killed:
                await asyncio.sleep(3600)
            return -9

        def kill(self):
            self.killed = True

    proc = HangingProc()

    async def fake_exec(*argv, **kw):
        return proc

    monkeypatch.setattr(
        "remedy.execution.process.create_hidden_subprocess_exec", fake_exec
    )

    real_wait_for = asyncio.wait_for

    async def quick_wait_for(aw, timeout):
        # Shorten only cleanup's 30s prune budget; leave every other wait alone.
        return await real_wait_for(aw, timeout=0.01 if timeout == 30.0 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", quick_wait_for)

    await real_wait_for(DockerSandbox().cleanup(), timeout=5.0)
    assert proc.killed, "the hung prune was not killed"
    assert proc.waits >= 2, "the killed prune was not reaped"
