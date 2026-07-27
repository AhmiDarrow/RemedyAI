#!/usr/bin/env python3
"""Remove third-party product/program brand names from library skill text."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RENAMES = {
    "stripe-webhook-flow": "payment-webhook-flow",
    "dockerfile-harden": "container-image-harden",
    "k8s-manifest-review": "container-orchestration-review",
    "compose-dev-env": "local-container-stack",
}

REPLS: list[tuple[str, str]] = [
    (r"browser-test", "browser-test"),
    (r"browser screenshots", "browser screenshots"),
    (r"the browser test runner", "the browser test runner"),
    (r"browser tests", "browser tests"),
    (r"visual review tools", "visual review tools"),
    (r"visual review tools", "visual review tools"),
    (r"container image recipes", "container image recipes"),
    (r"container image recipe", "container image recipe"),
    (r"local multi-service containers", "local multi-service containers"),
    (r"local multi-service containers", "local multi-service containers"),
    (r"\bdocker\b", "container tooling"),
    (r"\bDocker\b", "container tooling"),
    (r"container orchestration", "container orchestration"),
    (r"\bK8s\b", "orchestration"),
    (r"infrastructure-as-code", "infrastructure-as-code"),
    (r"infrastructure-as-code", "infrastructure-as-code"),
    (r"infrastructure-as-code", "infrastructure-as-code"),
    (r"payment-provider", "payment-provider"),
    (r"payment provider", "payment provider"),
    (r"Ed25519", "Ed25519"),
    (r"coding agents", "coding agents"),
    (r"coding agents", "coding agents"),
    (r"coding agents", "coding agents"),
    (r"model providers", "model providers"),
    (r"model providers", "model providers"),
    (r"chat assistants", "chat assistants"),
    (r"coding assistants", "coding assistants"),
    (r"CI pipelines", "CI pipelines"),
    (r"CI pipelines", "CI pipelines"),
    (r"hosted flag services", "hosted flag services"),
    (r"flag services", "flag services"),
    (r"search engines", "search engines"),
    (r"search engines", "search engines"),
    (r"search engines", "search engines"),
    (r"trace backends", "trace backends"),
    (r"trace backends", "trace backends"),
    (r"metrics backends", "metrics backends"),
    (r"distributed tracing", "distributed tracing"),
    (r"bundle analyzers", "bundle analyzers"),
    (r"bundle analyzers", "bundle analyzers"),
    (r"bundle analyzers", "bundle analyzers"),
    (r"factory helpers", "factory helpers"),
    (r"factory helpers", "factory helpers"),
    (r"contract-test tools", "contract-test tools"),
    (r"\bPact\b", "contract tests"),
    (r"email preview services", "email preview services"),
    (r"migration frameworks", "migration frameworks"),
    (r"\bnpm audit\b", "Node audit tools"),
    (r"\bpnpm audit\b", "Node audit tools"),
    (r"Python audit tools", "Python audit tools"),
    (r"Rust audit tools", "Rust audit tools"),
    (r"Go vulnerability scanners", "Go vulnerability scanners"),
    (r"secret scanners", "secret scanners"),
    (r"secret scanners", "secret scanners"),
    (r"secret scanners", "secret scanners"),
    (r"\bsyft\b", "SBOM tools"),
    (r"SBOM tools", "SBOM tools"),
    (r"load generators", "load generators"),
    (r"\bk6\b", "load generators"),
    (r"editors", "editors"),
    (r"dev environment container", "dev environment container"),
]


def rename_skills() -> None:
    skills = ROOT / "skills"
    for old, new in RENAMES.items():
        op, np = skills / old, skills / new
        if not op.exists():
            continue
        if np.exists():
            shutil.rmtree(np)
        op.rename(np)
        md = np / "SKILL.md"
        if md.is_file():
            t = md.read_text(encoding="utf-8")
            t = t.replace(f"name: {old}", f"name: {new}")
            t = t.replace(f"library_id: {old}", f"library_id: {new}")
            t = re.sub(
                r"^# .+$",
                f"# {new.replace('-', ' ').title()}",
                t,
                count=1,
                flags=re.M,
            )
            t = t.replace("payment provider Webhook Flow", "Payment Webhook Flow")
            md.write_text(t, encoding="utf-8")
        print(f"renamed {old} -> {new}")


def scrub_file(path: Path) -> bool:
    t = path.read_text(encoding="utf-8")
    orig = t
    for pat, rep in REPLS:
        t = re.sub(pat, rep, t)
    if t != orig:
        path.write_text(t, encoding="utf-8")
        return True
    return False


def main() -> None:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description=(
            "ONE-SHOT brand scrub. Destructive (renames + rewrites). "
            "Most REPLS are no-ops now; do not re-run casually. Requires --force."
        )
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Required to actually mutate the tree.",
    )
    args = p.parse_args()
    if not args.force:
        print(
            "Refusing to run: scrub_brands is a frozen one-shot.\n"
            "Re-run with --force only if you know you need it.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    rename_skills()
    n = 0
    for p in ROOT.rglob("*"):
        if p.suffix.lower() in {".md", ".py", ".yml", ".yaml"} and p.is_file():
            if scrub_file(p):
                n += 1
                print("scrubbed", p.relative_to(ROOT))
    print(f"scrubbed {n} files")


if __name__ == "__main__":
    main()
