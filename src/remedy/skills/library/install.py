"""Install skills from the library catalog into quarantine."""

from __future__ import annotations

import hashlib
import io
import logging
import tempfile
from pathlib import Path
from typing import Any

from remedy.skills.library.catalog import SkillCatalogEntry, SkillsCatalog, get_skills_catalog
from remedy.skills.library.security import is_allowed_download_url, is_safe_skill_name

logger = logging.getLogger(__name__)

MAX_SKILL_ZIP_BYTES = 50 * 1024 * 1024  # 50 MiB
MAX_CATALOG_BYTES = 8 * 1024 * 1024  # 8 MiB


def _repo_skills_root() -> Path | None:
    try:
        root = Path(__file__).resolve().parents[4]
    except IndexError:
        return None
    p = root / "community" / "remedy-skills" / "skills"
    return p if p.is_dir() else None


def _bundled_skill_names() -> set[str]:
    try:
        from remedy.bundled_skills import bundled_skills_dir

        b = bundled_skills_dir()
        if b and b.is_dir():
            return {d.name for d in b.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()}
    except Exception:
        pass
    return set()


def _verify_checksum(data: bytes, checksum: str) -> None:
    if not checksum or ":" not in checksum:
        raise ValueError("Missing checksum")
    algo, expected = checksum.split(":", 1)
    if algo.lower() != "sha256":
        raise ValueError(f"Unsupported checksum algorithm: {algo}")
    actual = hashlib.sha256(data).hexdigest()
    if actual.lower() != expected.lower():
        raise ValueError("Checksum mismatch — download corrupted or tampered")


async def _download_bytes(
    url: str,
    *,
    timeout_s: float = 60.0,
    max_bytes: int = MAX_SKILL_ZIP_BYTES,
) -> bytes:
    import aiohttp

    async with aiohttp.ClientSession() as session, session.get(
        url, timeout=aiohttp.ClientTimeout(total=timeout_s), allow_redirects=True
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Download failed HTTP {resp.status}")
        # Always require the *final* URL to be allowlisted (no redirect off-list).
        final = str(resp.url)
        if not is_allowed_download_url(final):
            raise ValueError(f"Download final URL not allowed: {final}")
        # Stream with hard size cap
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.content.iter_chunked(65536):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Download exceeds size limit ({max_bytes} bytes)")
            chunks.append(chunk)
        return b"".join(chunks)


def _zip_from_local_skill(skill_id: str) -> bytes:
    if not is_safe_skill_name(skill_id):
        raise ValueError(f"Invalid local skill id: {skill_id}")
    root = _repo_skills_root()
    if root is None:
        raise FileNotFoundError("Local community skills root not found")
    skill_dir = root / skill_id
    if not (skill_dir / "SKILL.md").is_file():
        raise FileNotFoundError(f"Local skill not found: {skill_id}")
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in skill_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(root).as_posix())
    data = buf.getvalue()
    if len(data) > MAX_SKILL_ZIP_BYTES:
        raise ValueError("Local skill pack exceeds size limit")
    return data


async def resolve_skill_zip_bytes(entry: SkillCatalogEntry) -> bytes:
    url = entry.download_url.strip()
    if not is_allowed_download_url(url):
        raise ValueError(f"Download URL not allowed: {url}")
    if url.startswith("local:"):
        data = _zip_from_local_skill(url[6:].strip())
    else:
        # Cap by catalog size when present (with slack), else global max
        cap = MAX_SKILL_ZIP_BYTES
        if entry.size_bytes and entry.size_bytes > 0:
            cap = min(MAX_SKILL_ZIP_BYTES, max(entry.size_bytes * 2, entry.size_bytes + 1024))
        data = await _download_bytes(url, max_bytes=cap)
    _verify_checksum(data, entry.checksum)
    return data


async def install_skill_from_catalog(
    skill_id: str,
    *,
    runtime: Any,
    version: str | None = None,
    force: bool = False,
    home: Path | None = None,
    catalog: SkillsCatalog | None = None,
) -> dict[str, Any]:
    """Download catalog skill and import quarantined into user skills dir."""
    if catalog is None:
        catalog = await get_skills_catalog(home=home)
    entry = next((s for s in catalog.skills if s.id == skill_id or s.name == skill_id), None)
    if entry is None:
        raise LookupError(f"Skill not found in catalog: {skill_id}")
    if version and version != entry.version:
        raise ValueError(f"Version {version} not available (catalog has {entry.version})")

    if not is_safe_skill_name(entry.name):
        raise ValueError(f"Catalog skill has unsafe name: {entry.name!r}")

    # Never shadow bundled skills — even with force
    if entry.name in _bundled_skill_names():
        raise ValueError(
            f"Skill name '{entry.name}' conflicts with a bundled skill and cannot be "
            "installed from the library."
        )

    if home is None:
        home = Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or Path.home() / ".remedy"
        ).expanduser()
    dest_root = Path(home).expanduser() / "skills"
    dest_root.mkdir(parents=True, exist_ok=True)

    existing = dest_root / entry.name
    if existing.exists() and not force:
        raise FileExistsError(
            f"Skill '{entry.name}' already installed. Use force/update to replace."
        )

    zip_data = await resolve_skill_zip_bytes(entry)

    from remedy.skills.exporter import SkillExporter
    from remedy.skills.shared import invalidate_shared_registry

    # Drop old registry entry when replacing
    if force and runtime is not None and hasattr(runtime, "skills") and hasattr(
        runtime.skills, "remove"
    ):
        try:
            runtime.skills.remove(entry.name)
        except Exception:
            pass

    tmp = Path(tempfile.mkdtemp(prefix="remedy-lib-install-"))
    try:
        zip_path = tmp / "skill.zip"
        zip_path.write_bytes(zip_data)
        exp = SkillExporter(tmp)
        imported = exp.import_pack_quarantine(
            zip_path, dest_root, replace_existing=bool(force)
        )
        if not imported:
            raise RuntimeError("Import produced no skills (invalid pack?)")

        # Enrich metadata for library provenance
        names: list[str] = []
        for skill in imported:
            meta = dict(skill.manifest.metadata or {})
            meta["source"] = "library"
            meta["library_id"] = entry.id
            meta["library_version"] = entry.version
            meta["security_flags"] = list(entry.security_flags or [])
            meta["quarantine"] = True
            # Force library updates back to quarantine (re-Trust after update)
            meta["trust"] = "library-update" if force else "library-install"
            skill.manifest.metadata = meta
            # Persist enriched frontmatter if path known
            try:
                from remedy.skills.loader import load_skill_from_dir

                p = Path(skill.source_skill_dir or skill.manifest.path or "")
                if p.is_dir():
                    md = p / "SKILL.md"
                    if md.is_file():
                        import re

                        import yaml

                        raw = md.read_text(encoding="utf-8")
                        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", raw, re.DOTALL)
                        body = raw[m.end() :] if m else raw
                        fm = yaml.safe_load(m.group(1)) if m else {}
                        if not isinstance(fm, dict):
                            fm = {}
                        fm_meta = dict(fm.get("metadata") or {})
                        fm_meta.update(meta)
                        fm["metadata"] = fm_meta
                        content = (
                            "---\n"
                            + yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
                            + "\n---\n\n"
                            + body.lstrip("\n")
                        )
                        md.write_text(content, encoding="utf-8")
                        skill = load_skill_from_dir(p)
            except Exception as e:
                logger.debug("library metadata enrich failed: %s", e)

            if runtime is not None and hasattr(runtime, "skills"):
                runtime.skills.register(skill)
            names.append(skill.manifest.name)

        invalidate_shared_registry()
        return {
            "status": "installed",
            "skill_id": entry.id,
            "names": names,
            "version": entry.version,
            "quarantine": True,
            "security_flags": entry.security_flags,
            "replaced": bool(force),
            "message": (
                "Installed in quarantine. Trust the skill in Skills → Installed to activate."
                if not force
                else "Updated and re-quarantined. Trust again after reviewing changes."
            ),
        }
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def list_library_updates(
    catalog: SkillsCatalog,
    *,
    skills_dir: Path,
) -> list[dict[str, str]]:
    """Compare installed library skills vs catalog versions."""
    from packaging.version import InvalidVersion, Version

    updates: list[dict[str, str]] = []
    skills_dir = Path(skills_dir).expanduser()
    if not skills_dir.is_dir():
        return updates
    by_id = {s.id: s for s in catalog.skills}
    for d in skills_dir.iterdir():
        if not d.is_dir() or not (d / "SKILL.md").is_file():
            continue
        try:
            from remedy.skills.loader import load_skill_from_dir

            skill = load_skill_from_dir(d)
        except Exception:
            continue
        meta = skill.manifest.metadata or {}
        if meta.get("source") != "library" and not meta.get("library_id"):
            continue
        lid = str(meta.get("library_id") or skill.manifest.name)
        remote = by_id.get(lid) or by_id.get(skill.manifest.name)
        if not remote:
            continue
        cur = skill.manifest.version or "0"
        try:
            if Version(remote.version) > Version(cur):
                updates.append(
                    {
                        "skill_id": remote.id,
                        "name": skill.manifest.name,
                        "current_version": cur,
                        "available_version": remote.version,
                    }
                )
        except InvalidVersion:
            if remote.version != cur:
                updates.append(
                    {
                        "skill_id": remote.id,
                        "name": skill.manifest.name,
                        "current_version": cur,
                        "available_version": remote.version,
                    }
                )
    return updates
