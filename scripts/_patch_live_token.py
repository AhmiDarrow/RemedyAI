"""One-shot: wire live soak scripts to lib_local_token."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATCH_SNIP = """
import sys
from pathlib import Path as _PathForToken
_SCRIPTS = _PathForToken(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib_local_token import resolve_local_api_token
"""


def patch(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "resolve_local_api_token" in text:
        print("skip", path.name)
        return
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and (
            line.startswith("from pathlib import Path")
            or line.startswith("from pathlib import Path,")
        ):
            out.append(PATCH_SNIP if PATCH_SNIP.endswith("\n") else PATCH_SNIP + "\n")
            inserted = True
    text2 = "".join(out)

    text2, n1 = re.subn(
        r'TOKEN\s*=\s*\(HOME\s*/\s*"auth"\s*/\s*"local_api_token"\)\.read_text\([^)]*\)\.strip\(\)',
        "TOKEN = resolve_local_api_token(home=HOME, base=BASE)",
        text2,
        count=1,
    )
    text2, n2 = re.subn(
        r"def token\(\) -> str:\n"
        r"(?:[ \t]+.+\n){0,10}?"
        r"[ \t]+return TOKEN_PATH\.read_text\([^)]*\)\.strip\(\)",
        "def token() -> str:\n    return resolve_local_api_token(home=HOME, base=BASE)",
        text2,
        count=1,
    )
    # stress: generic read of local_api_token
    text2, n3 = re.subn(
        r'\(HOME\s*/\s*"auth"\s*/\s*"local_api_token"\)\.read_text\([^)]*\)\.strip\(\)',
        "resolve_local_api_token(home=HOME, base=BASE)",
        text2,
        count=1,
    )
    path.write_text(text2, encoding="utf-8")
    print(f"patched {path.name} insert={inserted} n1={n1} n2={n2} n3={n3}")


def main() -> None:
    for name in (
        "live_full_product_e2e.py",
        "live_soak_security_chat.py",
        "live_agent_break_suite.py",
        "live_project_write_jail_10x.py",
        "stress_desktop_api.py",
    ):
        patch(ROOT / name)


if __name__ == "__main__":
    main()
