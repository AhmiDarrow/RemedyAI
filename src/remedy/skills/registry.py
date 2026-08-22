"""Skill registry for discovery, validation, activation, ranking, and versioning.

Maintains the canonical set of skills available to the agent runtime.
Implements agentskills.io progressive disclosure: catalog (name+description)
in context; full bodies load only on activation.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from remedy.models import Skill, SkillStatus
from remedy.skills.loader import discover_skills, load_skill_from_dir

# Status preference for ranking / catalog (higher = more trusted)
_STATUS_SCORE = {
    SkillStatus.ACTIVE: 1.0,
    SkillStatus.VALIDATED: 0.7,
    SkillStatus.DISCOVERED: 0.4,
    SkillStatus.DISABLED: 0.05,
    SkillStatus.DEPRECATED: 0.0,
}


def _skill_frontmatter(path: Path) -> dict[str, Any]:
    import yaml

    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", raw, re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _version_tuple(ver: Any) -> tuple[int, ...]:
    """Parse ``1.2.3`` / ``1.1`` into comparable ints; unknown → (0,)."""
    s = str(ver or "0").strip()
    parts: list[int] = []
    for p in re.split(r"[^\d]+", s):
        if p.isdigit():
            parts.append(int(p))
    return tuple(parts) if parts else (0,)


def _bundled_version_newer(bundled_skill_md: Path, user_skill_md: Path) -> bool:
    b = _skill_frontmatter(bundled_skill_md)
    u = _skill_frontmatter(user_skill_md)
    return _version_tuple(b.get("version")) > _version_tuple(u.get("version"))


def _refresh_seeded_skill_from_bundled(bundled_dir: Path, user_dir: Path) -> bool:
    """Copy SKILL.md + scripts from bundled when its version is newer.

    Skips if the user placed a ``.user_locked`` file in the skill dir (opt-out).
    Does not delete extra user files under the skill folder.
    """
    import shutil

    if (user_dir / ".user_locked").is_file():
        return False
    b_md = bundled_dir / "SKILL.md"
    u_md = user_dir / "SKILL.md"
    if not b_md.is_file() or not u_md.is_file():
        return False
    if not _bundled_version_newer(b_md, u_md):
        return False
    shutil.copy2(b_md, u_md)
    b_scripts = bundled_dir / "scripts"
    if b_scripts.is_dir():
        dest_scripts = user_dir / "scripts"
        dest_scripts.mkdir(parents=True, exist_ok=True)
        for src in b_scripts.rglob("*"):
            if src.is_file():
                rel = src.relative_to(b_scripts)
                target = dest_scripts / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
    return True


def _merge_bundled_local_frontmatter(
    *,
    bundled_skill_md: Path,
    user_skill_md: Path,
) -> None:
    """If user SKILL.md has no ``local:`` block but bundled does, inject it.

    Preserves the rest of the user's skill (instructions/tools) so upgrades add
    portable discovery without resetting personalizations.
    """
    import yaml

    if not bundled_skill_md.is_file() or not user_skill_md.is_file():
        return
    b_raw = bundled_skill_md.read_text(encoding="utf-8")
    u_raw = user_skill_md.read_text(encoding="utf-8")
    b_m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", b_raw, re.DOTALL)
    u_m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", u_raw, re.DOTALL)
    if not b_m or not u_m:
        return
    try:
        b_fm = yaml.safe_load(b_m.group(1)) or {}
        u_fm = yaml.safe_load(u_m.group(1)) or {}
    except yaml.YAMLError:
        return
    if not isinstance(b_fm, dict) or not isinstance(u_fm, dict):
        return
    if "local" not in b_fm or "local" in u_fm:
        return
    u_fm["local"] = b_fm["local"]
    # Prefer listing local_discover alongside skill tools when bundled does.
    b_tools = list(b_fm.get("tools") or [])
    u_tools = list(u_fm.get("tools") or [])
    for t in b_tools:
        if t not in u_tools:
            u_tools.append(t)
    if u_tools:
        u_fm["tools"] = u_tools
    body = u_raw[u_m.end() :]
    new_fm = yaml.safe_dump(u_fm, sort_keys=False, allow_unicode=True).strip()
    user_skill_md.write_text(f"---\n{new_fm}\n---\n{body}", encoding="utf-8")


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) >= 2}


# Segments that appear in auto-learned tool-chain skill names (file_read-list_dir-…).
# Used to demote catalog spam so curated procedures win on coding/long tasks.
_TOOL_CHAIN_SEGMENTS = frozenset(
    {
        "bash_exec",
        "file_read",
        "file_write",
        "file_edit",
        "file_edit_batch",
        "list_dir",
        "file_glob",
        "todo_write",
        "build_drive",
        "repo_search",
        "spread_run",
        "job_run",
        "mission_start",
        "mission_update",
        "mission_status",
        "plan_show",
        "plan_save",
        "plan_step_status",
        "goal_list",
        "goal_add",
        "goal_complete",
        "checkpoint_show",
        "get_settings",
        "update_settings",
        "memory_search",
        "memory_save",
        "web_fetch",
        "web_search",
        "vision_decode",
        "computer_navigate",
        "computer_act",
        "computer_snapshot",
        "computer_screenshot",
        "computer_windows",
        "computer_app",
        "computer_key",
        "computer_wait",
        "computer_find",
        "computer_page_text",
        "comfyui",
        "skill_activate",
        "skill_search",
        "local_discover",
        "mail_list",
        "mail_send",
        "calendar_list_events",
        "calendar_create_event",
        "budget_set",
        "budget_status",
        "budget_tx_add",
        "bill_list",
        "bill_upsert",
        "debt_upsert",
        "assistant_accounts",
        "compress_context",
        "read_dir",
    }
)


def looks_like_tool_chain_skill_name(name: str) -> bool:
    """True when a skill name is mostly auto-learned tool ids joined by hyphens.

    Names are often slugified (``file_read`` → ``file-read``), so matching is
    greedy on hyphen-normalized tool ids rather than raw underscore segments.
    """
    raw = (name or "").strip().lower().replace("_", "-").strip("-")
    if not raw or "-" not in raw:
        return False
    tools = sorted(
        {t.replace("_", "-") for t in _TOOL_CHAIN_SEGMENTS},
        key=len,
        reverse=True,
    )
    remaining = raw
    found = 0
    while remaining:
        remaining = remaining.lstrip("-")
        if not remaining:
            break
        matched = False
        for t in tools:
            if remaining == t or remaining.startswith(t + "-"):
                found += 1
                remaining = remaining[len(t) :]
                matched = True
                break
        if not matched:
            # Skip one unknown segment and continue
            parts = remaining.split("-", 1)
            remaining = parts[1] if len(parts) > 1 else ""
    # Need ≥2 tool segments covering most of a multi-segment name
    segs = [p for p in raw.split("-") if p]
    return found >= 2 and found >= max(2, len(segs) // 2)


class SkillRegistry:
    """Registry of loadable skills with ranking and progressive disclosure.

    Handles discovery, deduplication (by name), activation, version tracking,
    and ranked matching for context injection.
    """

    def __init__(self, auto_discover: bool | Path | str = False) -> None:
        self._skills: dict[UUID, Skill] = {}
        self._by_name: dict[str, UUID] = {}
        # Session-local activation telemetry (name -> counts)
        self._activation_counts: dict[str, int] = {}
        self._last_activated: str | None = None
        if auto_discover is True:
            self.discover_defaults()
        elif auto_discover:
            self.discover(auto_discover)

    def discover_defaults(self, home_dir: str | Path | None = None) -> int:
        """Discover skills from bundled + user locations.

        Order (later names win on collision):
        1. Package bundled defaults (``remedy/bundled_skills``)
        2. Repo ``./skills`` if present (dev tree)
        3. ``$REMEDY_HOME/skills`` or ``~/.remedy/skills`` (user overrides)

        Lifecycle: curated (non auto-generated) skills without an explicit
        disabled/deprecated status become ACTIVE. Auto-generated skills keep
        their frontmatter status (probation) — never force-ACTIVE lucky one-offs.
        """
        import os
        import shutil

        from remedy.bundled_skills import bundled_skills_dir

        # Collect curated source roots (later names win on collision until user home).
        # Package install alone may lag git; REMEDY_DEV_ROOT / monorepo fills the gap
        # so dogfood skills ship into ~/.remedy/skills without waiting for a PyPI bump.
        seed_roots: list[Path] = []
        bundled = bundled_skills_dir()
        if bundled.is_dir():
            seed_roots.append(bundled)

        def _maybe_add(p: Path) -> None:
            if p.is_dir() and p not in seed_roots:
                seed_roots.append(p)

        # Editable / monorepo: package dir may already be source; also scan DEV_ROOT.
        dev_root = (os.environ.get("REMEDY_DEV_ROOT") or "").strip()
        if dev_root:
            root = Path(dev_root).expanduser()
            _maybe_add(root / "src" / "remedy" / "bundled_skills")
            _maybe_add(root / "skills")
        # cwd when agent works inside the repo
        _maybe_add(Path.cwd() / "skills")
        _maybe_add(Path.cwd() / "src" / "remedy" / "bundled_skills")
        # Walk up a few levels for "serve from desktop/" etc.
        here = Path.cwd().resolve()
        for parent in [here, *list(here.parents)[:4]]:
            _maybe_add(parent / "skills")
            _maybe_add(parent / "src" / "remedy" / "bundled_skills")
            if (parent / "pyproject.toml").is_file() and (parent / "src" / "remedy").is_dir():
                break

        for root in seed_roots:
            self.discover(root, recurse=True)

        # User home skills (custom + seeded)
        if home_dir is not None:
            user_skills = Path(home_dir).expanduser() / "skills"
        else:
            env_home = os.environ.get("REMEDY_HOME")
            user_skills = (
                Path(env_home).expanduser() / "skills"
                if env_home
                else Path("~/.remedy/skills").expanduser()
            )
        user_skills.mkdir(parents=True, exist_ok=True)
        # Seed missing curated skills into user dir from every seed root.
        # Upgrades: if source frontmatter ``version`` is newer than the seeded
        # copy, refresh SKILL.md + scripts (skip when ``.user_locked`` exists).
        for seed in seed_roots:
            for child in seed.iterdir():
                if not child.is_dir() or not (child / "SKILL.md").is_file():
                    continue
                # Skip junk / learned-looking dirs under monorepo skills noise
                if child.name.startswith("."):
                    continue
                dest = user_skills / child.name
                if not dest.exists():
                    with contextlib.suppress(OSError):
                        shutil.copytree(child, dest)
                else:
                    with contextlib.suppress(OSError):
                        _refresh_seeded_skill_from_bundled(child, dest)
                    with contextlib.suppress(OSError):
                        _merge_bundled_local_frontmatter(
                            bundled_skill_md=child / "SKILL.md",
                            user_skill_md=dest / "SKILL.md",
                        )
        if user_skills.is_dir():
            self.discover(user_skills, recurse=True)

        # Curated ready; auto-generated / quarantine keep probation status
        for skill in self.skills:
            self._apply_default_status(skill)
        return self.count

    @staticmethod
    def _apply_default_status(skill: Skill) -> None:
        """Promote curated skills to ACTIVE; leave learned/quarantine alone."""
        m = skill.manifest
        meta = m.metadata or {}
        if m.status in (SkillStatus.DISABLED, SkillStatus.DEPRECATED):
            return
        if meta.get("auto_generated") or meta.get("quarantine"):
            # Probation / quarantine — respect explicit status from frontmatter
            return
        # Explicit status in frontmatter (validated/discovered) for non-auto: still
        # treat shipped curated packs as ready unless user disabled them.
        if m.status in (
            SkillStatus.DISCOVERED,
            SkillStatus.VALIDATED,
            SkillStatus.ACTIVE,
        ):
            m.status = SkillStatus.ACTIVE

    def active_budget(self) -> int:
        """Soft cap for skills eligible for hot-catalog injection (power-user scale)."""
        try:
            from remedy.interfaces.api_support import load_config

            cfg = load_config()
            return max(10, min(500, int(cfg.get("skills_active_budget") or 80)))
        except Exception:
            return 80

    def summary_lines(
        self,
        *,
        limit: int = 40,
        query: str = "",
        include_disabled: bool = False,
    ) -> list[str]:
        """Catalog lines for prompts (progressive disclosure stage 1).

        Ranked when ``query`` is set; otherwise prefer ACTIVE, then effort, name.
        Includes status so the model knows probation vs trusted.
        Respects skills_active_budget so libraries of 100+ stay lean in-prompt.
        """
        budget = self.active_budget()
        effective_limit = min(limit, budget)
        ranked = self.match_skills(
            query or "",
            limit=max(effective_limit * 2, effective_limit),
            include_disabled=include_disabled,
        )
        # Optional pack filter (enabled skill packs)
        try:
            from remedy.skills.shared import skills_in_enabled_packs

            pack_names = skills_in_enabled_packs()
            if pack_names is not None:
                ranked = [(s, sc) for s, sc in ranked if s.manifest.name in pack_names]
        except Exception:
            pass
        ranked = ranked[:effective_limit]
        lines: list[str] = []
        for skill, _score in ranked:
            m = skill.manifest
            meta = m.metadata or {}
            st = m.status.value if hasattr(m.status, "value") else str(m.status)
            effort = float(meta.get("effort_weight") or 0.0)
            badges: list[str] = [st]
            if effort >= 0.62 or "hard-won" in (m.tags or []):
                badges.append("hard-won")
            if meta.get("auto_generated"):
                badges.append("learned")
            if meta.get("quarantine"):
                badges.append("quarantine")
            badge = ",".join(badges)
            extra = ""
            raw = m.raw_frontmatter or {}
            local = raw.get("local") if isinstance(raw, dict) else None
            if isinstance(local, dict):
                svc_ids = []
                for s in local.get("services") or []:
                    if isinstance(s, dict) and s.get("id"):
                        svc_ids.append(str(s["id"]))
                if svc_ids:
                    extra = (
                        f" [local: {', '.join(svc_ids)} — use local_discover/"
                        "comfyui tools, not disk hunts]"
                    )
            # Keep description short for catalog token budget
            desc = (m.description or "").strip()
            if len(desc) > 160:
                desc = desc[:157] + "…"
            lines.append(f"- **{m.name}** [{badge}]: {desc}{extra}")
        if lines:
            lines.append(
                "_Activate a skill with tool `skill_activate` (name=…) before "
                "following its full procedure. Do not invent skill bodies._"
            )
        return lines

    def match_skills(
        self,
        query: str = "",
        *,
        limit: int = 20,
        include_disabled: bool = False,
        workspace_hint: str = "",
        project_path: str | None = None,
    ) -> list[tuple[Skill, float]]:
        """Rank skills for retrieval (status + description + tags + effort).

        Returns (skill, score) sorted descending. Empty query ranks by trust/effort.
        Prefer skills tagged/bound to project_path when provided.
        """
        q_tokens = _tokenize(query)
        w_tokens = _tokenize(workspace_hint)
        proj = (project_path or "").strip().lower()
        scored: list[tuple[Skill, float]] = []
        for skill in self._skills.values():
            m = skill.manifest
            if not include_disabled and m.status in (
                SkillStatus.DISABLED,
                SkillStatus.DEPRECATED,
                SkillStatus.ARCHIVED,
            ):
                continue
            meta = m.metadata or {}
            if meta.get("quarantine") and not include_disabled:
                # Quarantine skills only match explicit name queries
                if not q_tokens or m.name.lower() not in (query or "").lower():
                    continue

            status_s = _STATUS_SCORE.get(m.status, 0.3)
            effort = float(meta.get("effort_weight") or 0.0)
            # Soft success rate if present
            rate = float(meta.get("success_rate") or meta.get("estimated_success_rate") or 0.5)
            name_tokens = _tokenize(m.name.replace("-", " "))
            desc_tokens = _tokenize(m.description)
            tag_tokens = _tokenize(" ".join(m.tags or []))
            all_skill = name_tokens | desc_tokens | tag_tokens

            if q_tokens:
                overlap = len(q_tokens & all_skill)
                name_hit = len(q_tokens & name_tokens)
                tag_hit = len(q_tokens & tag_tokens)
                # substring bonus
                ql = (query or "").lower()
                substr = 0.0
                if ql and ql in m.name.lower():
                    substr += 0.5
                if ql and ql in (m.description or "").lower():
                    substr += 0.25
                text_score = (
                    0.45 * min(1.0, overlap / max(1, len(q_tokens)))
                    + 0.30 * min(1.0, name_hit / max(1, len(q_tokens)))
                    + 0.15 * min(1.0, tag_hit / max(1, len(q_tokens)))
                    + 0.10 * min(1.0, substr)
                )
            else:
                text_score = 0.35  # neutral when browsing

            ws_boost = 0.0
            if w_tokens:
                ws_boost = 0.1 * min(1.0, len(w_tokens & all_skill) / max(1, len(w_tokens)))

            act_boost = 0.05 * min(1.0, self._activation_counts.get(m.name, 0) / 5.0)

            # Cost signal: prefer skills that finish faster when quality is similar
            # (avg_duration_ms or last_duration_ms in metadata; lower is better)
            cost_boost = 0.0
            try:
                dur = float(
                    meta.get("avg_duration_ms")
                    or meta.get("last_duration_ms")
                    or meta.get("duration_ms")
                    or 0.0
                )
                if dur > 0:
                    # 0–30s → small positive; multi-minute → tiny penalty
                    cost_boost = max(-0.06, 0.05 - (dur / 60_000.0) * 0.08)
            except (TypeError, ValueError):
                cost_boost = 0.0

            score = (
                0.30 * status_s
                + 0.36 * text_score
                + 0.14 * min(1.0, effort)
                + 0.10 * min(1.0, rate)
                + ws_boost
                + act_boost
                + cost_boost
            )
            # Hard-won slight preference when equally relevant
            if effort >= 0.62:
                score += 0.03
            # Auto-learned probation / tool-chain spam must not bury curated
            # procedures on coding queries ("implement…", "write tests…").
            # Meaningful hard-won skills with non-tool names stay competitive;
            # tool-id chains (file-read-list-dir-…) always demote unless named.
            if meta.get("auto_generated") and m.status != SkillStatus.ACTIVE:
                name_l = (m.name or "").lower()
                ql = (query or "").lower()
                name_exact = bool(name_l and ql and (name_l in ql or ql in name_l))
                desc_l = (m.description or "").lower()
                auto_template = desc_l.startswith(
                    "use when the user needs help with:"
                )
                chain = looks_like_tool_chain_skill_name(m.name) or auto_template
                if not name_exact:
                    if chain:
                        # Tool-chain / template skills are catalog noise for task rank
                        score -= 0.22
                    elif effort < 0.62:
                        score -= 0.14
            # Domain packs (godot-4, bioinformatics, clinical-research …) declare
            # triggers so they surface exactly when the ask is theirs. On an empty
            # or unrelated query they must not take stage-1 catalog slots from the
            # general procedures — with dozens of field packs installed, otherwise
            # every "hi" turn renders a directory of fields nobody asked about.
            if m.triggers:
                score += 0.25 if self._trigger_hit(m.triggers, query) is not None else -0.30
            # Project-scoped skills: metadata.project_path or tag matching folder name
            if proj:
                bound = str(meta.get("project_path") or meta.get("project") or "").lower()
                if bound and (bound in proj or proj in bound):
                    score += 0.12
                else:
                    # tag like project:foo or folder name in tags
                    folder = proj.replace("\\", "/").rstrip("/").split("/")[-1]
                    if folder and folder in tag_tokens:
                        score += 0.06
            scored.append((skill, score))

        scored.sort(key=lambda x: (-x[1], x[0].manifest.name.lower()))
        return scored[:limit]

    @staticmethod
    def _trigger_hit(triggers: list[str], text: str) -> int | None:
        """Earliest match position of any trigger regex in *text*, else None."""
        q = (text or "").strip()
        if not q or not triggers:
            return None
        best: int | None = None
        for pat in triggers:
            try:
                mt = re.search(pat, q, re.IGNORECASE)
            except re.error:
                continue
            if mt and (best is None or mt.start() < best):
                best = mt.start()
        return best

    def triggered_skills(self, text: str, *, limit: int = 3) -> list[str]:
        """Names of usable skills whose frontmatter ``triggers`` match *text*.

        Ordered by how early the match sits in the message (an engine named
        up front beats a generic word at the end), then by name. Disabled,
        deprecated and quarantined skills never trigger.
        """
        q = (text or "").strip()
        if not q:
            return []
        hits: list[tuple[int, str]] = []
        for skill in self._skills.values():
            m = skill.manifest
            if not m.triggers:
                continue
            if m.status in (SkillStatus.DISABLED, SkillStatus.DEPRECATED):
                continue
            if (m.metadata or {}).get("quarantine"):
                continue
            best = self._trigger_hit(m.triggers, q)
            if best is not None:
                hits.append((best, m.name))
        hits.sort(key=lambda t: (t[0], t[1].lower()))
        return [n for _, n in hits[:limit]]

    def skill_body(self, name: str, *, include_references: bool = False) -> str | None:
        """Full SKILL.md body for progressive disclosure activation."""
        skill = self.get(name)
        if skill is None:
            return None
        m = skill.manifest
        meta = m.metadata or {}
        header = [
            f"# Skill: {m.name}",
            f"**Status:** {m.status.value}",
            f"**Version:** {m.version}",
            f"**Description:** {m.description}",
        ]
        if skill.source_skill_dir:
            # So the model can file_read any reference the inline budget skips.
            header.append(f"**Path:** {skill.source_skill_dir}")
        if m.tags:
            header.append(f"**Tags:** {', '.join(m.tags)}")
        if m.tools:
            header.append(f"**Tools:** {', '.join(m.tools)}")
        effort = meta.get("effort_weight")
        if effort is not None:
            header.append(f"**Effort weight:** {effort} ({meta.get('effort_band', '')})")
        if meta.get("parent_skill"):
            header.append(f"**Parent / merged from:** {meta['parent_skill']}")
        if meta.get("requires_chain") or meta.get("composes_with"):
            chain = meta.get("requires_chain") or meta.get("composes_with")
            header.append(f"**Composes with:** {chain}")
        header.append("")
        body = skill.instructions or ""
        # Cap inject size so one skill cannot dominate context
        try:
            from remedy.core.react_policy import SKILL_BODY_CHAR_CAP

            cap = int(SKILL_BODY_CHAR_CAP)
        except Exception:
            cap = 24_000
        if cap > 0 and len(body) > cap:
            body = (
                body[:cap]
                + f"\n\n…[skill body truncated at {cap} chars — open SKILL.md on disk for full text]"
            )
        header.append(body)
        if include_references and skill.source_skill_dir and skill.references:
            header.append(self._references_block(skill))
        return "\n".join(header)

    @staticmethod
    def _references_block(skill: Skill) -> str:
        """Every reference listed with its size; INDEX.md inlined first, then
        the next few — the rest are a ``file_read`` away via **Path**."""
        try:
            from remedy.core.react_policy import SKILL_REF_CHAR_CAP, SKILL_REF_INLINE_MAX

            ref_cap, ref_max = int(SKILL_REF_CHAR_CAP), int(SKILL_REF_INLINE_MAX)
        except Exception:
            ref_cap, ref_max = 4_000, 5
        base = Path(skill.source_skill_dir or "")
        refs = list(skill.references)
        refs.sort(key=lambda r: (0 if Path(r).name.upper() == "INDEX.MD" else 1, r.lower()))
        lines = ["\n\n## References (on disk)"]
        for rel in refs:
            p = base / rel
            try:
                size = p.stat().st_size if p.is_file() else 0
            except OSError:
                size = 0
            lines.append(f"- {Path(rel).as_posix()} ({size} bytes)")
        inlined = 0
        for rel in refs:
            if inlined >= ref_max:
                break
            p = base / rel
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if len(text) > ref_cap:
                text = (
                    text[:ref_cap]
                    + f"\n…[truncated at {ref_cap} chars — file_read {rel} for the rest]"
                )
            lines.append(f"\n\n## Reference: {Path(rel).as_posix()}\n\n{text}")
            inlined += 1
        if inlined < len(refs):
            lines.append(
                f"\n\n_{len(refs) - inlined} more reference(s) not inlined — "
                f"file_read them from the skill path above._"
            )
        return "\n".join(lines)

    def mark_activated(self, name: str) -> None:
        self._activation_counts[name] = self._activation_counts.get(name, 0) + 1
        self._last_activated = name

    @property
    def last_activated(self) -> str | None:
        return self._last_activated

    def related_skills(self, name: str, *, limit: int = 5) -> list[str]:
        """Composition hints: skills sharing tags/tools (soft graph)."""
        skill = self.get(name)
        if skill is None:
            return []
        m = skill.manifest
        tags = set(m.tags or [])
        tools = set(m.tools or [])
        scored: list[tuple[str, int]] = []
        for other in self._skills.values():
            if other.manifest.name == name:
                continue
            if other.manifest.status in (SkillStatus.DISABLED, SkillStatus.DEPRECATED):
                continue
            ot = set(other.manifest.tags or [])
            oo = set(other.manifest.tools or [])
            score = len(tags & ot) * 2 + len(tools & oo)
            if score > 0:
                scored.append((other.manifest.name, score))
        scored.sort(key=lambda x: -x[1])
        return [n for n, _ in scored[:limit]]

    def health_snapshot(self, name: str) -> dict[str, Any]:
        skill = self.get(name)
        if skill is None:
            return {}
        m = skill.manifest
        meta = dict(m.metadata or {})
        return {
            "name": m.name,
            "status": m.status.value,
            "version": m.version,
            "description": m.description,
            "tags": list(m.tags or []),
            "kind": m.kind.value if hasattr(m.kind, "value") else str(m.kind),
            "effort_weight": float(meta.get("effort_weight") or 0.0),
            "effort_band": meta.get("effort_band"),
            "auto_generated": bool(meta.get("auto_generated")),
            "quarantine": bool(meta.get("quarantine")),
            "lifecycle": meta.get("lifecycle"),
            "success_rate": meta.get("success_rate"),
            "path": m.path or skill.source_skill_dir,
            "activations_session": self._activation_counts.get(m.name, 0),
            "related": self.related_skills(m.name),
            "scripts": list(skill.scripts or []),
            "references": list(skill.references or []),
        }

    # -- properties ----------------------------------------------------------

    @property
    def skills(self) -> list[Skill]:
        return list(self._skills.values())

    @property
    def active(self) -> list[Skill]:
        return [s for s in self._skills.values() if s.manifest.status == SkillStatus.ACTIVE]

    @property
    def count(self) -> int:
        return len(self._skills)

    # -- registration --------------------------------------------------------

    def register(self, skill: Skill) -> Skill:
        """Register or update a skill. If a skill with the same name exists,
        the newer one replaces the old (by UUID)."""
        existing_id = self._by_name.get(skill.manifest.name)
        if existing_id is not None:
            del self._skills[existing_id]

        self._skills[skill.id] = skill
        self._by_name[skill.manifest.name] = skill.id
        return skill

    def discover(self, *paths: str | Path, recurse: bool = True) -> int:
        """Scan directories for SKILL.md files and register them.

        Returns the number of newly discovered skills.
        """
        discovered = 0
        for raw_path in paths:
            root = Path(raw_path).expanduser().resolve()
            if not root.exists():
                continue
            for skill in discover_skills(root, recurse=recurse):
                self.register(skill)
                discovered += 1
        return discovered

    def load_single(self, path: str | Path) -> Skill:
        """Load and register a single skill from its directory."""
        skill = load_skill_from_dir(Path(path))
        return self.register(skill)

    # -- activation ----------------------------------------------------------

    def activate(self, name: str) -> bool:
        skill_id = self._by_name.get(name)
        if skill_id is None:
            return False
        self._skills[skill_id].manifest.status = SkillStatus.ACTIVE
        return True

    def deactivate(self, name: str) -> bool:
        skill_id = self._by_name.get(name)
        if skill_id is None:
            return False
        self._skills[skill_id].manifest.status = SkillStatus.DISABLED
        return True

    def set_status(self, name: str, status: SkillStatus | str) -> bool:
        skill = self.get(name)
        if skill is None:
            return False
        if isinstance(status, str):
            try:
                status = SkillStatus(status.strip().lower())
            except ValueError:
                return False
        skill.manifest.status = status
        return True

    def validate_all(self) -> tuple[int, int]:
        """Mark all currently ACTIVE skills as VALIDATED. Returns (total, validated)."""
        count = 0
        for skill in self._skills.values():
            if skill.manifest.status == SkillStatus.ACTIVE:
                skill.manifest.status = SkillStatus.VALIDATED
                count += 1
        return len(self._skills), count

    # -- lookup --------------------------------------------------------------

    def get(self, name_or_id: str | UUID) -> Skill | None:
        if isinstance(name_or_id, UUID):
            return self._skills.get(name_or_id)
        skill_id = self._by_name.get(name_or_id)
        if skill_id is None:
            return None
        return self._skills.get(skill_id)

    def search(self, query: str) -> list[Skill]:
        """Substring / token search across skill names, descriptions, and tags."""
        q = (query or "").strip().lower()
        if not q:
            return list(self._skills.values())
        # Prefer ranked hits that actually touch the query (not zero-relevance)
        ranked = self.match_skills(q, limit=50, include_disabled=True)
        out: list[Skill] = []
        for skill, score in ranked:
            m = skill.manifest
            if (
                q in m.name.lower()
                or q in (m.description or "").lower()
                or any(q in t.lower() for t in (m.tags or []))
                or score >= 0.55
            ):
                out.append(skill)
        # Fallback pure substring if ranking was too strict
        if not out:
            for skill in self._skills.values():
                m = skill.manifest
                if (
                    q in m.name.lower()
                    or q in (m.description or "").lower()
                    or any(q in t.lower() for t in (m.tags or []))
                ):
                    out.append(skill)
        return out

    def remove(self, name: str) -> bool:
        skill_id = self._by_name.pop(name, None)
        if skill_id is None:
            return False
        self._skills.pop(skill_id, None)
        return True

    def clear(self) -> None:
        self._skills.clear()
        self._by_name.clear()
        self._activation_counts.clear()
        self._last_activated = None
