"""Dev sidecar for Tauri — always runs live source via uv/python.

Tauri spawns: remedy-desktop.exe --home <path> serve --host 127.0.0.1 --port 7400
We re-exec into the repo checkout so secret-store / OAuth / version fixes apply
without a full PyInstaller rebuild of the whole agent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    env = os.environ.get("REMEDY_DEV_ROOT", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "src" / "remedy").is_dir():
            return p

    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent  # …/desktop/bin
        for candidate in (here.parent.parent, here.parent, here):
            if (candidate / "src" / "remedy").is_dir():
                return candidate
        return here.parent.parent

    return Path(__file__).resolve().parents[1]


def _python_cmd(root: Path) -> list[str]:
    """Prefer project venv, then uv run, then bare python."""
    venv_py = root / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        return [str(venv_py)]

    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--project", str(root), "python"]

    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            return [found]
    return [sys.executable]


def main() -> int:
    root = _repo_root()
    os.environ["REMEDY_DEV_ROOT"] = str(root)
    # Ensure imports resolve to checkout even without editable install.
    src = str(root / "src")
    sep = os.pathsep
    existing = os.environ.get("PYTHONPATH", "")
    if src not in existing.split(sep):
        os.environ["PYTHONPATH"] = src + (sep + existing if existing else "")

    py = _python_cmd(root)
    # Inline runner: import CLI from live tree and exec main()
    # Using -c keeps a single process (Tauri tracks this PID for kill).
    code = (
        "import os,sys;"
        f"sys.path.insert(0, {src!r});"
        "from remedy.interfaces.cli import main;"
        f"sys.argv={['remedy', *sys.argv[1:]]!r};"
        "main()"
    )
    cmd = [*py, "-c", code]
    # Replace process image on POSIX; on Windows spawn and wait so signals map.
    env = os.environ.copy()
    env["REMEDY_DEV_ROOT"] = str(root)
    try:
        return subprocess.call(cmd, cwd=str(root), env=env)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
