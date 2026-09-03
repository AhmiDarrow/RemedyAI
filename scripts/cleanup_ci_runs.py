"""Remove failed and cancelled workflow runs from the GitHub Actions history.

The public Actions tab should show the verification that shipped, not every
red run that was fixed before a push. Successful, queued and in-progress runs
are never touched.

    uv run python scripts/cleanup_ci_runs.py            # list what would go
    uv run python scripts/cleanup_ci_runs.py --apply    # delete them

Uses the authenticated gh CLI; the repository comes from the current checkout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

REMOVABLE = frozenset({"failure", "cancelled", "timed_out", "startup_failure", "action_required"})


def _gh(*args: str) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def list_runs() -> list[dict[str, object]]:
    out = _gh(
        "api",
        "--paginate",
        "repos/{owner}/{repo}/actions/runs?per_page=100",
        "--jq",
        ".workflow_runs[] | {id, name, status, conclusion, head_branch, created_at}",
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def removable(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        run
        for run in runs
        if run.get("status") == "completed" and str(run.get("conclusion")) in REMOVABLE
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="delete instead of listing")
    args = parser.parse_args(argv)

    runs = list_runs()
    doomed = removable(runs)
    print(f"{len(runs)} runs; {len(doomed)} failed/cancelled")
    for run in doomed:
        print(
            f"  {str(run['created_at'])[:10]}  {run['conclusion']:<15} "
            f"{run['name']:<16} {run['head_branch']}  #{run['id']}"
        )
    if not args.apply:
        print("dry run; pass --apply to delete")
        return 0

    deleted = 0
    for run in doomed:
        proc = subprocess.run(
            ["gh", "api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/actions/runs/{run['id']}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode == 0:
            deleted += 1
        else:
            print(f"  could not delete #{run['id']}: {proc.stderr.strip()}", file=sys.stderr)
    print(f"deleted {deleted} of {len(doomed)}")
    return 0 if deleted == len(doomed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
