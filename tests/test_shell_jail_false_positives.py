"""Shell write jail — live false positives from the 2026-08-20 session.

Regex fragments, URLs, npm specs, relative paths and cmd switches were read as
``X:\\…`` drive paths; ``cmd /c "dir …"`` and ``python -c "print(...)"`` were
jailed although nothing is written. Each vector here must pass, and the
matching real out-of-root writes must still deny.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.shell_write_jail import (
    check_shell_write_jail,
    extract_path_candidates,
    inline_code_has_write,
    looks_like_mutation,
    oneshot_is_readonly,
)


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "GuitarApp"
    p.mkdir()
    (p / "scripts").mkdir()
    (p / "scripts" / "check.py").write_text("print('ok')\n", encoding="utf-8")
    (p / "package.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    return p


def _jail(cmd: str, proj: Path) -> str | None:
    return check_shell_write_jail(cmd, write_roots=[proj.resolve()], cwd=proj)


# ---------------------------------------------------------------- candidates


@pytest.mark.parametrize(
    "cmd",
    [
        # regex fragments inside quoted python
        r'''python -c "import re; print(re.search(r'y:\s*(\d+)', 'y: 12'))"''',
        r'''python -c "import re; print(re.findall(r'ABC:\bm\(', 'x'))"''',
        r'''python -c "print('C:\n')"''',
        # URLs
        "curl -L https://raw.githubusercontent.com/foo/bar/main/x.ts",
        "curl -sS http://127.0.0.1:5173",
        "curl https://files.freemusicarchive.org/a/b.mp3",
        # npm specs / relative paths / cmd switches
        "npm install @tauri-apps/cli@2 @tauri-apps/api@2",
        "git add src/lib/guitarpro.ts src-tauri/src/main.rs",
        r"dir /b /ad C:\Users\Administrator",
        "node -e \"console.log(require('./package.json').version)\"",
        r"python scripts\check.py",
    ],
)
def test_no_bogus_drive_or_root_candidates(cmd: str) -> None:
    bogus = [
        c
        for c in extract_path_candidates(cmd)
        if c.lower().startswith(("s:", "p:", "y:", "/cli", "/api", "/lib", "/main", "/ad", "/src"))
        or c in (r"C:\n", r"C:\bm\(", r"\check.py", "/package.json")
    ]
    assert not bogus, f"{cmd!r} → {bogus}"


def test_real_drive_paths_still_extracted() -> None:
    assert r"C:\Users\Public\x.txt" in extract_path_candidates(r"copy a C:\Users\Public\x.txt")
    assert "C:/Users/Public/x" in extract_path_candidates("echo x > C:/Users/Public/x")
    assert r"\Temp\pwn.txt" in extract_path_candidates(r"echo pwn > \Temp\pwn.txt")
    assert "/tmp/pwn" in extract_path_candidates("wget -O /tmp/pwn http://x")
    assert any(
        c.lower().startswith(r"c:\program files")
        for c in extract_path_candidates(r'copy a "C:\Program Files (x86)\App\x.dll"')
    )
    # -FilePath:C:\… (PowerShell colon-attached operand) is a dest
    assert r"C:\Users\Public\x.txt" in extract_path_candidates(
        r"Out-File -FilePath:C:\Users\Public\x.txt"
    )


# ------------------------------------------------------------- allow vectors


ALLOW: list[str] = [
    r'''python -c "import re; print(re.search(r'y:\s*(\d+)', 'y: 12'))"''',
    r'''python -c "import re; print(re.findall(r'ABC:\bm\(', 'x'))"''',
    r'''python -c "print('C:\n')"''',
    r'''python -c "import sys; print(sys.version)"''',
    r'''python -c "import json,pathlib; print(json.loads(pathlib.Path('package.json').read_text())['version'])"''',
    r'''python -c "import os; print(os.environ.get('PATH'))"''',
    "node -e \"console.log(require('./package.json').version)\"",
    "curl -L https://raw.githubusercontent.com/foo/bar/main/x.ts -o src/lib/x.ts",
    "curl -sS http://127.0.0.1:5173",
    "curl -o music.mp3 https://files.freemusicarchive.org/a/b.mp3",
    "npm install @tauri-apps/cli@2 @tauri-apps/api@2",
    "git add src/lib/guitarpro.ts src-tauri/src/main.rs",
    r'cmd /c "dir /b /ad C:\Users\Administrator"',
    r'cmd /c "dir /b C:\Users\Administrator\Desktop\*Dark*"',
    'cmd /c "netstat -ano | findstr :5173"',
    r'Get-ChildItem -Path "$env:USERPROFILE\Downloads"',
    r'Get-ChildItem -Path "$env:USERPROFILE\Downloads" | Where-Object { $_.Name -like "*.mp3" }',
    r"python scripts\check.py",
    r"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.EXE scripts\check.py",
]


@pytest.mark.parametrize("cmd", ALLOW, ids=[c[:48] for c in ALLOW])
def test_live_false_positives_allowed(proj: Path, cmd: str) -> None:
    hit = _jail(cmd, proj)
    assert hit is None, f"false jail for {cmd!r} → {hit}"


# -------------------------------------------------------------- deny vectors


DENY: list[str] = [
    r"copy a.txt C:\Users\Administrator\Downloads\a.txt",
    r"curl -o C:\other\pwn.exe https://files.freemusicarchive.org/a/b.mp3",
    r"curl https://x -o ..\pwn.exe",
    r"Set-Content C:\foo\x.txt hi",
    r"echo x > C:\5.0",
    r'cmd /c "echo hi > C:\Users\Public\x.txt"',
    r'cmd /c "copy a C:\Users\Public\x.txt"',
    r'''python -c "open(r'C:\Users\Public\x','w').write('x')"''',
    r'''python -c "import shutil; shutil.copy('a', r'C:\Users\Public\a')"''',
    r'''python -c "import os; os.remove(r'C:\Users\Public\a')"''',
    r'''python -c "import subprocess; subprocess.run('del C:\\x', shell=True)"''',
    r'''python -c "print(1)" > C:\Users\Public\out.txt''',
    r'''copy a C:\Users\Public\b ; python -c "print(1)"''',
    "node -e \"require('fs').writeFileSync('C:/Users/Public/x','x')\"",
    r'''pwsh -Command "Remove-Item C:\Users\Public\x.txt"''',
    r'cat foo"&calc',
]


@pytest.mark.parametrize("cmd", DENY, ids=[c[:48] for c in DENY])
def test_real_outside_writes_still_denied(proj: Path, cmd: str) -> None:
    hit = _jail(cmd, proj)
    assert hit is not None, f"expected jail for {cmd!r}"


# ------------------------------------------------------ inline code classifier


@pytest.mark.parametrize(
    "code",
    [
        "print(1)",
        "import sys; print(sys.version)",
        "import re; print(re.search(r'y:\\s*(\\d+)', s))",
        "print(open('package.json').read())",
        "print(open(p, 'r', encoding='utf-8').read())",
        "print(open(p, 'rb').read())",
        "from pathlib import Path; print(Path('x').read_text())",
        "import sys; sys.stdout.write('x')",
        "console.log(require('./package.json').version)",
        "console.log(require('fs').readFileSync('x','utf8'))",
        "Get-ChildItem $env:USERPROFILE\\Downloads",
        "Write-Host hi 2>$null",
    ],
)
def test_inline_code_readonly(code: str) -> None:
    assert inline_code_has_write(code) is False, code


@pytest.mark.parametrize(
    "code",
    [
        "open('x','w').write('y')",
        "open('x', mode='w')",
        "open(p, 'a+', encoding='utf-8')",
        "open(os.path.join(a, b), 'w')",
        "Path('x').write_text('y')",
        "Path('x').mkdir()",
        "os.remove('x')",
        "os.makedirs('x')",
        "shutil.copy('a', 'b')",
        "shutil.rmtree('x')",
        "subprocess.run(['del', 'x'])",
        "exec(compile(src, 'x', 'exec'))",
        "import base64; base64.b64decode(s)",
        "require('fs').writeFileSync('x','y')",
        "fs.mkdirSync('x')",
        "require('child_process').execSync('del x')",
        "file_put_contents('x', 'y')",
        "File.write('x', 'y')",
        "Set-Content x.txt hi",
        "echo hi > out.txt",
        "Start-Process notepad",
    ],
)
def test_inline_code_write(code: str) -> None:
    assert inline_code_has_write(code) is True, code


def test_oneshot_readonly_classification() -> None:
    assert oneshot_is_readonly('python -c "print(1)"')
    assert oneshot_is_readonly("node -e \"console.log(1)\"")
    assert oneshot_is_readonly('python -u -c "import sys; print(sys.path)"')
    assert not oneshot_is_readonly("python -c \"open('x','w')\"")
    assert not oneshot_is_readonly('python -c "print(1)"; copy a b')
    assert not oneshot_is_readonly("echo open('x','w') | python -")
    assert not oneshot_is_readonly("copy a b")  # not a one-shot at all


def test_readonly_oneshot_is_not_a_mutation() -> None:
    assert looks_like_mutation('python -c "print(1)"') is False
    assert looks_like_mutation('cmd /c "dir /b C:\\Users"') is False
    assert looks_like_mutation('cmd /c "netstat -ano | findstr :5173"') is False
    # chained / redirected forms stay mutations
    assert looks_like_mutation('python -c "print(1)" > out.txt') is True
    assert looks_like_mutation('copy a b ; python -c "print(1)"') is True
    assert looks_like_mutation("cmd /c drop.bat") is True
    assert looks_like_mutation('cmd /c "python drop.py"') is True


def test_quoted_metachar_only_for_mutations_or_broken_quotes(proj: Path) -> None:
    # read with a pipe inside balanced quotes: allowed
    assert _jail('cmd /c "netstat -ano | findstr :5173"', proj) is None
    # broken quote + metachar: still denied
    assert _jail(r'cat foo"&calc', proj) is not None
    # mutation with a pipe inside quotes: still denied (cannot see the dest)
    hit = _jail('pwsh -Command "Get-Content a | Set-Content b"', proj)
    assert hit is not None


def test_curl_relative_output_lands_in_cwd(proj: Path) -> None:
    assert _jail("curl -o music.mp3 https://example.com/a.mp3", proj) is None
    assert _jail("curl --output out.bin https://example.com/a", proj) is None
    assert _jail("wget -O out.bin https://example.com/a", proj) is None
    # outside cwd → still opaque / denied
    outside = proj.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    assert (
        check_shell_write_jail(
            "curl -o music.mp3 https://example.com/a.mp3",
            write_roots=[proj.resolve()],
            cwd=outside,
        )
        is not None
    )
    assert _jail("curl -o $env:TEMP\\x.bin https://example.com/a", proj) is not None
    assert _jail("curl -o ../x.bin https://example.com/a", proj) is not None
