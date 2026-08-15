"""Skills Library HTTP routes: catalog, search, install, updates, submit."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel


class LibraryInstallBody(BaseModel):
    skill_id: str
    version: str | None = None
    force: bool = False


class LibraryDismissBody(BaseModel):
    skill_id: str
    session_id: str = ""


async def _read_upload_capped(
    upload: UploadFile,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Stream an upload with a hard cap (same as /api/skills/import)."""
    from remedy.skills.library.install import MAX_SKILL_ZIP_BYTES

    cap = int(max_bytes or MAX_SKILL_ZIP_BYTES)
    size_hint = getattr(upload, "size", None)
    if size_hint is not None:
        try:
            if int(size_hint) > cap:
                raise HTTPException(413, f"Skill zip exceeds {cap} bytes")
        except (TypeError, ValueError):
            pass
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await upload.read(65536)
        if not block:
            break
        total += len(block)
        if total > cap:
            raise HTTPException(413, f"Skill zip exceeds {cap} bytes")
        chunks.append(block)
    return b"".join(chunks)


def register_skills_library_routes(
    app: FastAPI, *, runtime=None, gateway=None, memory=None
) -> None:
    def _home() -> Path:
        return Path(
            getattr(getattr(runtime, "config", None), "home_dir", None)
            or Path.home() / ".remedy"
        ).expanduser()

    @app.get("/api/skills/library/catalog")
    async def library_catalog(refresh: bool = Query(default=False)) -> dict[str, Any]:
        from remedy.skills.library.catalog import catalog_to_public_dict, get_skills_catalog

        try:
            cat = await get_skills_catalog(refresh=refresh, home=_home())
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return catalog_to_public_dict(cat)

    @app.get("/api/skills/library/search")
    async def library_search(
        q: str = Query(default=""),
        tags: str = Query(default=""),
        author: str | None = Query(default=None),
        sort_by: str = Query(default="name"),
    ) -> dict[str, Any]:
        from remedy.skills.library.catalog import get_skills_catalog, search_catalog

        try:
            cat = await get_skills_catalog(home=_home())
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None
        results = search_catalog(
            cat, q=q, tags=tag_list, author=author, sort_by=sort_by
        )
        return {
            "query": q,
            "total": len(results),
            "results": [r.model_dump() for r in results],
            "source": cat.source,
        }

    @app.get("/api/skills/library/suggest")
    async def library_suggest(
        q: str = Query(default=""),
        session_id: str = Query(default=""),
        mark: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Cache-only soft suggestion for a task (never installs)."""
        from remedy.skills.library.suggest import (
            build_library_index,
            rank_library_skills,
            suggest_library_skill,
        )
        from remedy.skills.shared import get_shared_registry

        installed: set[str] = set()
        top_score = None
        try:
            reg = get_shared_registry()
            if hasattr(reg, "_by_name"):
                installed = {str(n) for n in (reg._by_name or {})}
            if q and hasattr(reg, "match_skills"):
                ranked = reg.match_skills(q, limit=3) or []
                if ranked:
                    top_score = float(ranked[0][1])
        except Exception:
            pass
        idx = build_library_index(_home())
        if mark:
            hit = suggest_library_skill(
                q,
                intent="tool",
                home=_home(),
                installed_names=installed,
                installed_top_score=top_score,
                session_id=session_id or None,
                mark_suggested=True,
            )
            return {
                "query": q,
                "suggestion": hit.to_public() if hit else None,
                "source": idx.source,
                "index_size": len(idx),
                "needs_refresh": idx.needs_refresh,
            }
        hits = rank_library_skills(
            q,
            home=_home(),
            installed_names=installed,
            limit=5,
            session_id=session_id or None,
            mark_suppressed=False,
        )
        return {
            "query": q,
            "suggestion": hits[0].to_public() if hits else None,
            "results": [h.to_public() for h in hits],
            "source": idx.source,
            "index_size": len(idx),
            "needs_refresh": idx.needs_refresh,
        }

    @app.post("/api/skills/library/suggest/dismiss")
    async def library_suggest_dismiss(body: LibraryDismissBody) -> dict[str, Any]:
        from remedy.skills.library.suggest import suppress_suggest

        suppress_suggest(body.session_id or "_default", body.skill_id)
        return {"ok": True, "skill_id": body.skill_id}

    @app.post("/api/skills/library/install")
    async def library_install(body: LibraryInstallBody) -> dict[str, Any]:
        from remedy.skills.library.install import install_skill_from_catalog

        try:
            return await install_skill_from_catalog(
                body.skill_id,
                runtime=runtime,
                version=body.version,
                force=body.force,
                home=_home(),
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except FileExistsError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @app.get("/api/skills/library/updates")
    async def library_updates() -> dict[str, Any]:
        from remedy.skills.library.catalog import get_skills_catalog
        from remedy.skills.library.install import list_library_updates

        try:
            cat = await get_skills_catalog(home=_home())
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        updates = list_library_updates(cat, skills_dir=_home() / "skills")
        return {"updates": updates}

    @app.post("/api/skills/library/update/{skill_id}")
    async def library_update(skill_id: str) -> dict[str, Any]:
        from remedy.skills.library.install import install_skill_from_catalog

        try:
            return await install_skill_from_catalog(
                skill_id,
                runtime=runtime,
                force=True,
                home=_home(),
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @app.post("/api/skills/library/submit")
    async def library_submit(
        skill_zip: Annotated[UploadFile, File()],
        metadata: str | None = None,
    ) -> dict[str, Any]:
        """Validate a user skill zip for contribution (no auto-PR)."""
        import re
        import shutil
        import zipfile

        from remedy.skills.exporter import _safe_extract_zip
        from remedy.skills.loader import load_skill_from_dir
        from remedy.skills.validator import SkillValidator

        raw_meta: dict[str, Any] = {}
        if metadata:
            try:
                raw_meta = json.loads(metadata)
            except Exception as e:
                raise HTTPException(400, f"Invalid metadata JSON: {e}") from e

        data = await _read_upload_capped(skill_zip)
        if len(data) < 32:
            raise HTTPException(400, "Empty or invalid zip")

        tmp = Path(tempfile.mkdtemp(prefix="remedy-lib-submit-"))
        try:
            zpath = tmp / "in.zip"
            zpath.write_bytes(data)
            extract = tmp / "extract"
            extract.mkdir()
            with zipfile.ZipFile(zpath, "r") as zf:
                _safe_extract_zip(zf, extract)

            skill_mds = list(extract.rglob("SKILL.md"))
            if not skill_mds:
                raise HTTPException(400, "No SKILL.md in zip")
            skill = load_skill_from_dir(skill_mds[0].parent)
            v = SkillValidator()
            meta_res = v.validate_metadata(skill)
            if not meta_res.is_valid:
                raise HTTPException(
                    400,
                    detail={"errors": meta_res.errors, "warnings": meta_res.warnings},
                )

            # Security pattern scan
            banned = ("eval(", "exec(", "pickle.", "shell=True")
            hits: list[str] = []
            for f in skill_mds[0].parent.rglob("*"):
                if not f.is_file() or f.suffix.lower() not in {
                    ".py",
                    ".md",
                    ".js",
                    ".ts",
                    ".sh",
                    "",
                }:
                    continue
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for b in banned:
                    if b in text:
                        hits.append(f"{f.name}: {b}")
            if hits:
                raise HTTPException(400, detail={"errors": hits})

            name = skill.manifest.name
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", name or ""):
                raise HTTPException(400, "Invalid skill name")

            return {
                "status": "validated",
                "skill_name": name,
                "version": skill.manifest.version,
                "author": raw_meta.get("author") or skill.manifest.author,
                "message": "Skill passed local validation. Open a PR on the skills library repo.",
                "repository": "https://github.com/AhmiDarrow/remedy-skills",
                "instructions": (
                    f"1. Fork AhmiDarrow/remedy-skills\n"
                    f"2. Branch feat/{name}-v{skill.manifest.version}\n"
                    f"3. Add skills/{name}/ with SKILL.md (+ scripts)\n"
                    f"4. Open a pull request"
                ),
                "warnings": meta_res.warnings,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, detail=str(e)) from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
