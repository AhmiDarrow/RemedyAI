"""CI / local pass 1: path jail + secrets + size for inbound PRs.

Exit 0 only if the diff is inside the allowed self-improve surface.
Never merges. Does not use repo secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python scripts/self_improve_pr_guard.py` from repo root.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from remedy.core.self_inject_guard import (  # noqa: E402
    collect_git_diff,
    scan_diff_secrets_and_size,
    scan_paths,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Self-improve PR path/secret guard")
    p.add_argument("--base", default="", help="git base ref (e.g. origin/master)")
    p.add_argument("--repo", default=".", help="repo root")
    p.add_argument("--fork", action="store_true", help="stricter fork limits")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo).resolve()
    base = args.base.strip() or None
    files, diff = collect_git_diff(repo, base=base)
    path_r = scan_paths(files, from_fork=args.fork)
    size_r = scan_diff_secrets_and_size(diff, from_fork=args.fork)
    # Pass 1 only — behavior is the sibling script (double-check).
    ok = path_r.ok and size_r.ok
    report = {
        "ok": ok,
        "pass": "path_jail+secrets_size",
        "from_fork": args.fork,
        "files": path_r.files,
        "path_jail": path_r.to_public(),
        "secrets_size": size_r.to_public(),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"self-improve guard pass1 ok={ok} files={len(path_r.files)} fork={args.fork}")
        for f in path_r.findings + size_r.findings:
            print(f"  [{f.severity}] {f.path or '-'} {f.message}")
        if not files:
            print("  (empty diff — nothing to review)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
