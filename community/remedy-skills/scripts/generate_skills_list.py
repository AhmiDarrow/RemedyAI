#!/usr/bin/env python3
"""Generate SKILLS.md and refresh the auto skills list section in README.md.

Always derives from skills/*/SKILL.md (source of truth). Run after any skill add.
Also invoked from build_catalog.py so catalog rebuilds keep docs in sync.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BEGIN = "<!-- BEGIN AUTO-SKILLS-LIST -->"
END = "<!-- END AUTO-SKILLS-LIST -->"


def _parse_skill_md(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}
    data = yaml.safe_load(parts[1]) or {}
    return data if isinstance(data, dict) else {}


def _primary_domain(tags: list[str]) -> str:
    """Bucket skills for readable grouping."""
    tags_l = [t.lower() for t in tags]
    order = [
        ("gaming", ("gaming", "combat", "level-design", "liveops", "narrative", "multiplayer", "godot", "gdscript", "engine", "pixel-art", "pixellab", "assets")),
        ("design", ("design", "ui", "ux", "art", "brand", "print", "creative")),
        ("content", ("content", "writing", "marketing", "video", "audio", "social", "comms", "community", "seo")),
        ("personal", ("personal", "productivity", "habits", "career", "wellness", "home", "travel", "food", "fitness", "learning", "social", "finance", "planning", "privacy")),
        ("security", ("security", "secrets", "auth", "privacy", "supply-chain")),
        ("testing", ("testing", "e2e", "qa", "browser")),
        ("frontend", ("frontend", "react", "css", "a11y", "i18n")),
        ("backend", ("backend", "api", "database", "graphql", "grpc")),
        ("ops", ("ops", "devops", "ci", "docker", "k8s", "reliability", "observability", "sre", "iac")),
        ("git", ("git", "pr", "release", "versioning")),
        ("llm", ("llm", "rag", "tools", "cost")),
        ("data", ("data", "csv", "search")),
        ("docs", ("docs", "architecture", "process", "product")),
        ("tooling", ("tooling", "python", "node", "typescript", "go", "rust", "packaging", "quality", "monorepo", "cli", "windows", "upgrade", "config", "network", "perf", "concurrency", "payments", "enterprise")),
    ]
    for label, keys in order:
        if any(k in tags_l for k in keys):
            return label
    return "other"


def collect_skills(skills_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        fm = _parse_skill_md(skill_md)
        name = str(fm.get("name") or skill_md.parent.name).strip()
        desc = str(fm.get("description") or "").strip().replace("\n", " ")
        # collapse whitespace from folded YAML
        desc = re.sub(r"\s+", " ", desc).strip()
        tags = [str(t) for t in (fm.get("tags") or [])]
        version = str(fm.get("version") or "1.0.0")
        rows.append(
            {
                "name": name,
                "description": desc,
                "tags": tags,
                "version": version,
                "path": f"skills/{skill_md.parent.name}/SKILL.md",
                "domain": _primary_domain(tags),
            }
        )
    rows.sort(key=lambda r: (r["domain"], r["name"].lower()))
    return rows


def render_skills_md(rows: list[dict]) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_domain[r["domain"]].append(r)

    lines = [
        "# Skills list",
        "",
        f"_Auto-generated from `skills/*/SKILL.md` — **{len(rows)}** skills. "
        f"Last updated: {now}. Do not edit by hand; run "
        "`python scripts/generate_skills_list.py` or `python scripts/build_catalog.py`._",
        "",
        "## Summary by area",
        "",
        "| Area | Count |",
        "|------|------:|",
    ]
    for domain in sorted(by_domain.keys()):
        lines.append(f"| {domain} | {len(by_domain[domain])} |")
    lines += ["", f"| **Total** | **{len(rows)}** |", ""]

    for domain in sorted(by_domain.keys()):
        lines.append(f"## {domain.title()}")
        lines.append("")
        lines.append("| Skill | Description |")
        lines.append("|-------|-------------|")
        for r in by_domain[domain]:
            desc = r["description"].replace("|", "\\|")
            if len(desc) > 160:
                desc = desc[:157] + "…"
            link = f"[`{r['name']}`]({r['path']})"
            lines.append(f"| {link} | {desc} |")
        lines.append("")

    lines.append("## Alphabetical index")
    lines.append("")
    for r in sorted(rows, key=lambda x: x["name"].lower()):
        lines.append(f"- [`{r['name']}`]({r['path']}) — {r['description'][:120]}{'…' if len(r['description']) > 120 else ''}")
    lines.append("")
    return "\n".join(lines)


def render_readme_section(rows: list[dict]) -> str:
    """Compact section embedded in README between AUTO markers."""
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_domain[r["domain"]].append(r)

    # Friendlier area labels for the README table
    labels = {
        "backend": "Backend & APIs",
        "content": "Content & writing",
        "data": "Data",
        "design": "Design & UX",
        "docs": "Docs & process",
        "frontend": "Frontend",
        "gaming": "Gaming",
        "git": "Git & release",
        "llm": "LLM & agents",
        "ops": "Ops & reliability",
        "other": "Other",
        "personal": "Personal assistant",
        "security": "Security",
        "testing": "Testing",
        "tooling": "Tooling & languages",
    }

    lines = [
        BEGIN,
        "",
        f"## What’s in the library ({len(rows)} skills)",
        "",
        f"Snapshot of packs on this branch as of {now}. "
        f"Descriptions and the full alphabetical list live in **[SKILLS.md](./SKILLS.md)**.",
        "",
        "| Area | # | A few examples |",
        "|------|--:|----------------|",
    ]
    for domain in sorted(by_domain.keys()):
        items = by_domain[domain]
        sample = ", ".join(f"`{r['name']}`" for r in items[:4])
        if len(items) > 4:
            sample += f" · +{len(items) - 4} more"
        label = labels.get(domain, domain.title())
        lines.append(f"| {label} | {len(items)} | {sample} |")
    lines += [
        "",
        f"**{len(rows)} total** — skim the [full list](./SKILLS.md), or open "
        f"**Skills → Library** in Remedy Desktop and install only what you need.",
        "",
        END,
    ]
    return "\n".join(lines)


def update_readme(readme: Path, section: str) -> None:
    text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    block = section.strip() + "\n"
    if BEGIN in text and END in text:
        pattern = re.compile(
            re.escape(BEGIN) + r".*?" + re.escape(END),
            re.DOTALL,
        )
        text = pattern.sub(block.strip(), text)
    else:
        # Insert after first heading block
        if text.startswith("#"):
            parts = text.split("\n\n", 1)
            if len(parts) == 2:
                text = parts[0] + "\n\n" + block + "\n" + parts[1]
            else:
                text = text + "\n\n" + block
        else:
            text = block + "\n" + text
    readme.write_text(text.rstrip() + "\n", encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--skills-dir", type=Path, default=ROOT / "skills")
    p.add_argument("--skills-md", type=Path, default=ROOT / "SKILLS.md")
    p.add_argument("--readme", type=Path, default=ROOT / "README.md")
    p.add_argument("--no-readme", action="store_true")
    args = p.parse_args()

    rows = collect_skills(args.skills_dir)
    args.skills_md.write_text(render_skills_md(rows), encoding="utf-8")
    print(f"Wrote {args.skills_md} ({len(rows)} skills)")
    if not args.no_readme:
        update_readme(args.readme, render_readme_section(rows))
        print(f"Updated {args.readme} auto skills section")


if __name__ == "__main__":
    main()
