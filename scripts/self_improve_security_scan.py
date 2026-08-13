"""CI / local pass 2: independent behavior scan of added lines.

Looks for reverse shells, credential-shaped literals, unverified TLS,
pickle/yaml.load, encoded eval. Separate process from pass 1 on purpose.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from remedy.core.self_inject_guard import (  # noqa: E402
    collect_git_diff,
    scan_added_behavior,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Self-improve PR behavior scan")
    p.add_argument("--base", default="", help="git base ref (e.g. origin/master)")
    p.add_argument("--repo", default=".", help="repo root")
    p.add_argument("--fork", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo).resolve()
    base = args.base.strip() or None
    _files, diff = collect_git_diff(repo, base=base)
    report = scan_added_behavior(diff, from_fork=args.fork)
    if args.json:
        print(json.dumps(report.to_public(), indent=2))
    else:
        print(f"self-improve guard pass2 ok={report.ok} fork={args.fork}")
        for f in report.findings:
            print(f"  [{f.severity}] {f.path or '-'} {f.message}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
