#!/usr/bin/env python3
"""Build catalog.json from skills/ folders (local or CI)."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _parse_skill_md(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}
    data = yaml.safe_load(parts[1]) or {}
    return data if isinstance(data, dict) else {}


def _zip_skill(skill_dir: Path) -> bytes:
    buf = io.BytesIO()
    root_parent = skill_dir.parent
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in skill_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(root_parent).as_posix())
    return buf.getvalue()


def build_catalog(
    skills_dir: Path,
    *,
    repo: str = "AhmiDarrow/remedy-skills",
    use_local: bool = True,
    release_tag: str = "v1.0.0",
    write_dist: bool = True,
) -> dict:
    skills_dir = skills_dir.resolve()
    entries: list[dict] = []
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    dist = skills_dir.parent / "dist"
    if write_dist:
        dist.mkdir(parents=True, exist_ok=True)

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        fm = _parse_skill_md(skill_md)
        name = str(fm.get("name") or skill_md.parent.name).strip()
        version = str(fm.get("version") or "1.0.0").strip()
        desc = str(fm.get("description") or "").strip()
        author = str(fm.get("author") or "community").strip()
        tags = list(fm.get("tags") or [])
        tools = list(fm.get("tools") or [])
        requires = list(fm.get("requires") or [])
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        flags = list(meta.get("security_flags") or fm.get("security_flags") or [])

        zbytes = _zip_skill(skill_md.parent)
        sha = hashlib.sha256(zbytes).hexdigest()
        zip_name = f"{name}-{version}.zip"
        if write_dist:
            (dist / zip_name).write_bytes(zbytes)
        if use_local:
            download_url = f"local:{name}"
        else:
            # Release tag is library release (v1.0.0), not each skill version.
            download_url = (
                f"https://github.com/{repo}/releases/download/"
                f"{release_tag}/{zip_name}"
            )

        entries.append(
            {
                "id": name,
                "name": name,
                "description": desc,
                "version": version,
                "author": author,
                "tags": tags,
                "download_url": download_url,
                "size_bytes": len(zbytes),
                "checksum": f"sha256:{sha}",
                "requires": requires,
                "tools": tools,
                "rating": 0.0,
                "installs": 0,
                "reviews_count": 0,
                "updated_at": now,
                "published_at": now,
                "compatible_remedy": [">=0.15.0"],
                "security_flags": flags,
                "status": "published",
            }
        )

    return {
        "version": "1",
        "generated_at": now,
        "repository": repo,
        "skills": entries,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--skills-dir", type=Path, default=ROOT / "skills")
    p.add_argument("--output", type=Path, default=ROOT / "catalog.json")
    p.add_argument("--repo", default="AhmiDarrow/remedy-skills")
    p.add_argument("--github-urls", action="store_true")
    p.add_argument("--release-tag", default="v1.0.0")
    p.add_argument("--no-dist", action="store_true")
    p.add_argument("--skip-docs", action="store_true", help="Skip SKILLS.md / README list regen")
    args = p.parse_args()
    cat = build_catalog(
        args.skills_dir,
        repo=args.repo,
        use_local=not args.github_urls,
        release_tag=args.release_tag,
        write_dist=not args.no_dist,
    )
    args.output.write_text(json.dumps(cat, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({len(cat['skills'])} skills)")
    if not args.skip_docs:
        import subprocess
        import sys

        list_script = Path(__file__).resolve().parent / "generate_skills_list.py"
        try:
            subprocess.check_call(
                [
                    sys.executable,
                    str(list_script),
                    "--skills-dir",
                    str(args.skills_dir),
                ]
            )
        except Exception as e:
            print(f"Warning: could not regenerate skills list docs: {e}")


if __name__ == "__main__":
    main()
