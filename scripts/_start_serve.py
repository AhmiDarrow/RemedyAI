"""Thin launcher so Windows process spawn does not mangle -c quoting."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remedy.interfaces.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.argv = [
        "remedy",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "7400",
        "--skip-setup",
    ]
    main()
