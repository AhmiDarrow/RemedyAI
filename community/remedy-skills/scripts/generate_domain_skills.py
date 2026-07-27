#!/usr/bin/env python3
"""Generate official library skills: gaming, design, content, personal assistant.

No third-party product or program brand names in titles, descriptions, or bodies.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "skills"

# Names already claimed by bundled Remedy skills or prior library packs
RESERVED = {
    "code-review",
    "comfyui",
    "commit-message",
    "debug-error",
    "decision-journal",
    "design-critique",
    "explain-code",
    "git-status",
    "github",
    "memory-backup",
    "personal-briefing",
    "project-etiquette",
    "project-overview",
    "refactor-safe",
    "remember-me",
    "session-handoff",
    "web-research",
    "write-tests",
    "write-with-user",
    "hello-library",
}


def skill(name: str, desc: str, tags: list[str], tools: list[str], body: str) -> tuple:
    return (name, desc.strip(), tags, tools, body.strip())


def write_skill(name: str, desc: str, tags: list[str], tools: list[str], body: str) -> None:
    if name in RESERVED:
        return
    d = ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    tags_y = "\n".join(f"  - {t}" for t in tags)
    tools_y = "\n".join(f"  - {t}" for t in tools)
    title = name.replace("-", " ").title()
    content = f"""---
name: {name}
version: 1.0.0
description: >
  {desc}
author: Remedy Official
license: LicenseRef-Proprietary
tags:
{tags_y}
kind: native
status: discovered
tools:
{tools_y}
metadata:
  source: library
  library_id: {name}
  official: true
  domain: {tags[0] if tags else "general"}
  security_flags: []
---

# {title}

{body}

## Operating rules
- Prefer read-only exploration before changing files.
- Report commands and outcomes; do not invent results.
- Ask before destructive, paid, or irreversible actions.
- Never print or commit secrets, tokens, or private personal data.
- Use generic tool names only (shell, file read/write, image tools already in Remedy).

## Done when
The user goal is met, or you list concrete blockers and the next safe step.
"""
    (d / "SKILL.md").write_text(content, encoding="utf-8")


SKILLS: list[tuple] = []

# =============================================================================
# GAMING (game design, production, systems — engine-agnostic)
# =============================================================================
GAMING = [
    skill(
        "game-design-document",
        "Draft or update a game design document: pillars, loop, progression, risk, and vertical slice scope.",
        ["gaming", "design", "docs"],
        ["file_read", "file_write"],
        """
## When to use
New game concept, pitch, or reconciling a messy design.

## Steps
1. Capture one-sentence pitch, audience, and platform constraints.
2. Define 3 design pillars (must guide every feature).
3. Core loop: minute-to-minute → session → meta progression.
4. Systems list (combat, economy, narrative, multiplayer) with owners/status.
5. Vertical slice: what is playable for the next milestone.
6. Open questions and risks.
7. Write or update `docs/gdd.md` (or path the user chooses).

## Done when
A teammate can build against the slice without guessing pillars.
""",
    ),
    skill(
        "game-loop-design",
        "Design or tighten a core gameplay loop with hooks, rewards, and failure states.",
        ["gaming", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. State the player goal in one line.
2. Map: engage → challenge → reward → return.
3. List inputs/verbs available each step.
4. Failure: what happens, how recovery works, frustration budget.
5. Session length targets (5 / 20 / 60 minutes).
6. Document edge cases (AFK, first-time, expert).

## Avoid
Feature lists without a loop diagram or verb table.
""",
    ),
    skill(
        "game-economy-balance",
        "Balance a game economy: sinks/faucets, inflation risks, and progression pacing.",
        ["gaming", "design", "systems"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Inventory currencies and items (sources and sinks).
2. Sketch earn rates per play minute for new/mid/endgame.
3. Identify inflation or soft-lock risks.
4. Propose tables or formulas (even spreadsheet-ready CSV).
5. Recommend telemetry to watch (earn, spend, time-to-goal).
6. Change only with version notes so designers can roll back.

## Deliverable
Economy notes + patch suggestions for numbers files or configs.
""",
    ),
    skill(
        "quest-design",
        "Design quests/missions with objectives, gates, rewards, and failure paths.",
        ["gaming", "narrative", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. Quest goal and why the player cares.
2. Objectives (primary + optional).
3. Prerequisites and world state flags.
4. Dialogue/brief beats (short).
5. Rewards and XP/loot alignment with economy.
6. Fail / abandon / retry rules.
7. Write quest sheet in project docs format.
""",
    ),
    skill(
        "level-design-brief",
        "Produce a level design brief: layout goals, encounters, pacing, and greybox checklist.",
        ["gaming", "level-design"],
        ["file_read", "file_write"],
        """
## Steps
1. Level fantasy and teaching goal (what skill is taught).
2. Beats on a timeline (quiet / peak / rest).
3. Spatial diagram description (entrances, landmarks, choke points).
4. Encounter list with difficulty intent.
5. Collectibles/secrets budget.
6. Greybox acceptance criteria before art pass.
""",
    ),
    skill(
        "boss-fight-design",
        "Design a boss encounter with phases, tells, accessibility options, and rewards.",
        ["gaming", "combat", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. Boss fantasy and role in progression.
2. Phases with HP or trigger conditions.
3. Attack telegraphs (readable tells).
4. Player counterplay options.
5. Accessibility: reduced flash, assist options if the game supports them.
6. Rewards and story payoff.
7. Playtest checklist (first try, third try, veteran).
""",
    ),
    skill(
        "combat-feel-tuning",
        "Tune combat feel: input buffer, hitstop, feedback, and readability without engine-specific jargon lock-in.",
        ["gaming", "combat"],
        ["file_read", "file_write"],
        """
## Steps
1. List player attacks and recovery frames (or timing numbers in data).
2. Check feedback: camera, sound hooks, VFX hooks, haptics if any.
3. Input forgiveness: buffer and coyote-style timing where relevant.
4. Enemy telegraph clarity.
5. Propose numeric tweaks with before/after rationale.
6. Define a 5-minute playtest script to validate feel.
""",
    ),
    skill(
        "game-ai-behavior",
        "Design enemy or NPC AI behaviors: states, perception, difficulty layers.",
        ["gaming", "ai", "systems"],
        ["file_read", "file_write"],
        """
## Steps
1. Roles (rusher, support, sniper, civilian).
2. State machine or utility goals (idle, investigate, combat, flee).
3. Perception (sight/hearing ranges, memory).
4. Difficulty modifiers without cheap unfairness.
5. Debug visualization needs for designers.
6. Document edge cases (nav stuck, line-of-sight abuse).
""",
    ),
    skill(
        "progression-curve",
        "Design player progression curves: XP, unlocks, soft gates, and catch-up.",
        ["gaming", "systems"],
        ["file_read", "file_write"],
        """
## Steps
1. Time-to-first-reward and time-to-mastery targets.
2. Unlock schedule vs content available.
3. Soft gates vs hard gates.
4. Prestige/endgame loop if any.
5. Spreadsheet-friendly formulas.
6. Risks: grind wall, pay asymmetry if applicable.
""",
    ),
    skill(
        "loot-table-design",
        "Design loot tables with drop rates, pity systems, and economy safety.",
        ["gaming", "systems"],
        ["file_read", "file_write"],
        """
## Steps
1. Rarity tiers and intended drop feel.
2. Conditional tables (biome, boss, chest).
3. Pity / bad-luck protection if used.
4. Duplicate handling.
5. Audit expected value vs craft costs.
6. Publish rates clearly if the product requires disclosure.
""",
    ),
    skill(
        "multiplayer-session-design",
        "Design multiplayer session flow: matchmaking intent, disconnects, host migration, fairness.",
        ["gaming", "multiplayer"],
        ["file_read", "file_write"],
        """
## Steps
1. Session model (lobby, drop-in, async).
2. Party and invite flow.
3. Disconnect / reconnect rules.
4. Authority model (who decides outcomes) at high level.
5. Anti-grief basics (kick, report hooks).
6. Latency-friendly design notes (prediction needs).
""",
    ),
    skill(
        "game-save-system",
        "Design save/load: slots, versioning, cloud caveats, corruption recovery.",
        ["gaming", "systems"],
        ["file_read", "file_write"],
        """
## Steps
1. What state must persist.
2. Slot UX and autosave policy.
3. Schema version + migration.
4. Atomic write / backup copy to limit corruption.
5. Cheating surface if online ranks matter.
6. Test plan: kill process mid-save, upgrade old save.
""",
    ),
    skill(
        "game-telemetry-events",
        "Define gameplay telemetry events for funnels, balance, and crash context (privacy-aware).",
        ["gaming", "ops", "privacy"],
        ["file_read", "file_write"],
        """
## Steps
1. Questions design needs answered (drop-off, weapon usage, crash level).
2. Event list with properties and enums.
3. Avoid PII; hash ids if needed.
4. Sampling for high-frequency events.
5. Dashboard sketches (funnel, heat).
6. Retention/deletion notes.
""",
    ),
    skill(
        "game-accessibility",
        "Apply game accessibility: colorblind, subtitle, input remapping, difficulty assists.",
        ["gaming", "a11y"],
        ["file_read", "file_write"],
        """
## Checklist
1. Color is not the only signal.
2. Subtitle size/background options if dialogue-heavy.
3. Remappable controls; hold vs toggle options.
4. Difficulty assists without shaming.
5. Flash/motion intensity options where relevant.
6. Screen reader/UI text if platform requires.
""",
    ),
    skill(
        "game-localization-prep",
        "Prepare game strings for localization: keys, variables, length expansion, voice notes.",
        ["gaming", "i18n"],
        ["file_read", "file_write"],
        """
## Steps
1. Extract player-facing strings to keys.
2. No string concatenation for sentences; use placeholders.
3. Allow ~30% length expansion in UI.
4. Gender/plural rules notes for translators.
5. Voice-over scripts separated from UI text.
6. Pseudo-loc pass to catch overflow.
""",
    ),
    skill(
        "game-audio-direction",
        "Write audio direction: music intensity layers, SFX categories, mix priorities, implementation checklist.",
        ["gaming", "audio", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. Emotional targets per area/combat state.
2. SFX categories (UI, footsteps, weapons, FOLEY).
3. Ducking/priority rules (dialogue > combat > ambience).
4. Implementation hooks (events, RTPC-style parameters described generically).
5. Loudness targets for platforms.
6. Bug checklist (missing one-shots, looping leaks).
""",
    ),
    skill(
        "game-vfx-checklist",
        "Define VFX readability and performance budgets for abilities and environments.",
        ["gaming", "art", "perf"],
        ["file_read", "file_write"],
        """
## Steps
1. Gameplay readability first (silhouettes, contrast).
2. Particle budgets per platform tier.
3. Overdraw and fullscreen effect limits.
4. Colorblind-safe ability colors.
5. LOD / culling notes.
6. Validation scene checklist.
""",
    ),
    skill(
        "game-ui-hud",
        "Design HUD/information architecture: diegetic vs non-diegetic, clutter budget, combat readability.",
        ["gaming", "ui", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. Critical info always visible vs on demand.
2. Combat readability test (can player see threats).
3. Safe margins and scale options.
4. Console focus navigation if needed.
5. Empty/error states for menus.
6. Wireframe notes before art.
""",
    ),
    skill(
        "playtest-protocol",
        "Run a structured playtest: goals, tasks, observation notes, and debrief actions.",
        ["gaming", "research"],
        ["file_read", "file_write"],
        """
## Steps
1. Hypotheses (what you fear is broken).
2. Task list for players (no leading).
3. Observation template (quotes, friction timestamps).
4. Severity ratings.
5. Debrief actions with owners.
6. Store notes under `docs/playtests/` or user path.
""",
    ),
    skill(
        "game-build-checklist",
        "Pre-ship game build checklist: content locks, known issues, platform cert hygiene (generic).",
        ["gaming", "release"],
        ["file_read", "bash_exec"],
        """
## Checklist
1. Version/build number stamped.
2. Debug cheats off in release config.
3. Content cook/build succeeds cleanly.
4. Crash reporter configured.
5. Known issues list with severities.
6. First-boot path tested cold.
7. Save compatibility notes.
8. Store listing assets readiness (icons, trailers) if shipping.
""",
    ),
    skill(
        "game-input-mapping",
        "Design input mapping for keyboard/mouse/gamepad with conflicts and rebinding.",
        ["gaming", "input"],
        ["file_read", "file_write"],
        """
## Steps
1. Action list (not raw keys).
2. Default maps per device.
3. Conflict detection rules.
4. Hold vs tap vs double-tap.
5. Accessibility rebinding export/import.
6. Test matrix for menus vs gameplay contexts.
""",
    ),
    skill(
        "game-difficulty-design",
        "Design difficulty modes and dynamic assists without breaking the fantasy.",
        ["gaming", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. What “skill” means in this game.
2. Parameters that scale (HP, telegraphs, resources).
3. Avoid bullet-sponge-only modes.
4. Optional assists separate from narrative difficulty if needed.
5. Playtest script per mode.
""",
    ),
    skill(
        "game-tutorial-flow",
        "Design an onboarding/tutorial that teaches verbs in context with skip options.",
        ["gaming", "ux", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. Core verbs to teach (max ~5 early).
2. Contextual prompts vs long text dumps.
3. Safe practice space.
4. Skip/remind later options.
5. Measure drop-off events.
6. Script of first 10 minutes.
""",
    ),
    skill(
        "game-narrative-bible",
        "Create a narrative bible: world rules, characters, tone, continuity constraints.",
        ["gaming", "narrative"],
        ["file_read", "file_write"],
        """
## Steps
1. World premise and hard rules (what cannot break).
2. Character sheets (want, need, voice).
3. Timeline and factions.
4. Tone references (text, not other brand names).
5. Continuity checklist for content creators.
6. Spoiler handling for team docs.
""",
    ),
    skill(
        "game-dialogue-pass",
        "Write or edit game dialogue for voice, subtext, length budgets, and VO notes.",
        ["gaming", "narrative", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Character voice samples.
2. Line length budgets for UI/VO.
3. Subtext over exposition.
4. Branching consequences noted.
5. Localization-friendly wording.
6. VO direction comments separated from spoken text.
""",
    ),
    skill(
        "game-bug-triage",
        "Triage gameplay bugs by repro, severity, blocker status, and regression risk.",
        ["gaming", "qa"],
        ["file_read", "file_write", "bash_exec"],
        """
## Steps
1. Repro steps and build number.
2. Severity (blocker/major/minor/polish).
3. Platform/config matrix.
4. Regression likelihood.
5. Assign owner and milestone.
6. Link logs/saves without private data.
""",
    ),
    skill(
        "game-perf-budget",
        "Set performance budgets: frame time, memory, streaming, and content limits.",
        ["gaming", "perf"],
        ["file_read", "file_write"],
        """
## Steps
1. Target platforms and frame rates.
2. CPU/GPU/memory budgets.
3. Streaming and hitch limits.
4. Content budgets (polys, textures, draw calls) as ranges.
5. How to measure (profiling workflow in-repo tools).
6. Fail criteria for CI or nightly if exists.
""",
    ),
    skill(
        "game-netcode-notes",
        "Document netcode approach at a design level: prediction, reconciliation, lag compensation caveats.",
        ["gaming", "multiplayer"],
        ["file_read", "file_write"],
        """
## Steps
1. Genre constraints (twitchy vs eventual).
2. Authority and ownership of objects.
3. Prediction/reconciliation needs.
4. Cheating surfaces to watch.
5. Test plan (latency, packet loss simulation if tools exist).
6. Keep notes engine-agnostic.
""",
    ),
    skill(
        "game-liveops-calendar",
        "Plan live-ops events: cadence, rewards, economy impact, and rollback.",
        ["gaming", "liveops"],
        ["file_read", "file_write"],
        """
## Steps
1. Cadence (weekly/seasonal).
2. Event goals (retention, economy sink).
3. Content checklist and feature flags.
4. Reward math vs inflation.
5. Kill switch / rollback plan.
6. Post-event metrics review.
""",
    ),
]

# =============================================================================
# DESIGN (visual / product / UX — brand-free)
# =============================================================================
DESIGN = [
    skill(
        "design-brief",
        "Write a design brief: problem, audience, constraints, success metrics, deliverables.",
        ["design", "product"],
        ["file_read", "file_write"],
        """
## Steps
1. Problem statement and non-goals.
2. Audience and contexts of use.
3. Constraints (time, brand, tech, a11y).
4. Success metrics.
5. Deliverables and milestones.
6. Open questions.
""",
    ),
    skill(
        "visual-hierarchy-pass",
        "Improve visual hierarchy on a screen: type scale, weight, spacing, focal point.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. Identify primary action and primary content.
2. Reduce competing focals.
3. Type scale and weight consistent with system.
4. Spacing rhythm (8-point or project grid).
5. Before/after notes for review.
""",
    ),
    skill(
        "color-system",
        "Define or refine a color system: roles (bg, text, accent, danger), contrast, dark mode.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. Roles not raw swatches only.
2. Contrast targets for text/icons.
3. Semantic colors (success/warn/error/info).
4. Dark/light pairs if both exist.
5. Document usage do/don't.
6. Map to CSS variables or theme tokens in code if present.
""",
    ),
    skill(
        "typography-system",
        "Set type scale, line height, and pairing rules for UI or editorial layouts.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. Base size and scale ratio.
2. Roles: display, title, body, caption, code.
3. Line length guidance (~45–75 ch for reading).
4. Font loading strategy if web.
5. Implement tokens in styles if codebase ready.
""",
    ),
    skill(
        "spacing-layout-grid",
        "Define spacing scale and layout grid for consistent composition.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. Base unit (4/8).
2. Spacing scale.
3. Columns/breakpoints for responsive.
4. Container max widths.
5. Apply to key screens as examples.
""",
    ),
    skill(
        "iconography-guide",
        "Create icon rules: optical size, stroke, metaphor consistency, accessibility.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. Style (outline/filled) and stroke weight.
2. Keyline grid for 16/24px.
3. Metaphor list to avoid collisions.
4. Contrast and labels for ambiguous icons.
5. Export checklist (SVG clean, viewBox).
""",
    ),
    skill(
        "design-system-audit",
        "Audit UI against an existing design system: drift, one-offs, missing components.",
        ["design", "ui"],
        ["file_read"],
        """
## Steps
1. Inventory components in code vs design docs.
2. Find one-off colors/spacing/type.
3. Duplicate components with different names.
4. Accessibility gaps.
5. Prioritized cleanup list.
""",
    ),
    skill(
        "wireframe-flow",
        "Produce low-fidelity wireframe flows for a user task (text or simple structure).",
        ["design", "ux"],
        ["file_read", "file_write"],
        """
## Steps
1. User goal and entry points.
2. Screen list with purpose.
3. Primary path + error path.
4. Notes for empty states.
5. Handoff questions for visual design.
""",
    ),
    skill(
        "ux-copy-microcopy",
        "Write UI microcopy: buttons, errors, empty states, confirmations—clear and human.",
        ["design", "writing", "ux"],
        ["file_read", "file_write"],
        """
## Steps
1. Inventory strings on the flow.
2. Action-oriented buttons (verb + object).
3. Errors: what happened + how to fix.
4. Empty states with next step.
5. Confirmations for irreversible actions.
6. Consistent terminology glossary.
""",
    ),
    skill(
        "user-journey-map",
        "Map a user journey: stages, emotions, pain points, opportunities.",
        ["design", "research"],
        ["file_read", "file_write"],
        """
## Steps
1. Persona and scenario.
2. Stages from trigger to outcome.
3. Actions, thoughts, emotions per stage.
4. Pain points and moments of delight.
5. Opportunity backlog linked to stages.
""",
    ),
    skill(
        "persona-profile",
        "Build research-backed personas (or proto-personas) with goals, frustrations, contexts.",
        ["design", "research"],
        ["file_read", "file_write"],
        """
## Steps
1. Evidence sources (interviews, tickets, analytics)—label assumptions.
2. Goals, jobs-to-be-done, frustrations.
3. Environment and constraints.
4. Quote bank (anonymized).
5. How product decisions should change.
""",
    ),
    skill(
        "usability-test-plan",
        "Plan a usability test: tasks, metrics, script, and synthesis template.",
        ["design", "research"],
        ["file_read", "file_write"],
        """
## Steps
1. Research questions.
2. Participant criteria.
3. Task scenarios (no leading).
4. Success metrics (completion, time, errors).
5. Moderator script.
6. Synthesis: findings → severity → actions.
""",
    ),
    skill(
        "information-architecture",
        "Organize information architecture: nav, labels, findability, card sorting notes.",
        ["design", "ux"],
        ["file_read", "file_write"],
        """
## Steps
1. Inventory content/features.
2. Group by user mental model.
3. Nav labels (user words).
4. Cross-links and search entry points.
5. Validate with simple tree test if possible.
""",
    ),
    skill(
        "interaction-states",
        "Specify full interaction states for components: default, hover, focus, active, disabled, error, loading.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. Component list.
2. State matrix.
3. Motion/timing if any (subtle).
4. Keyboard focus visible.
5. Map to CSS/classes in code when implementing.
""",
    ),
    skill(
        "motion-design-spec",
        "Specify motion: purpose, duration, easing, reduced-motion fallback.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. Motion purpose (orientation, feedback—not decoration only).
2. Duration scale.
3. Easing guidance.
4. Prefer reduced-motion alternatives.
5. Performance notes (avoid layout thrash).
""",
    ),
    skill(
        "brand-voice-guide",
        "Write a brand voice guide: principles, tone spectrum, examples do/don't.",
        ["design", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Brand personality adjectives.
2. Tone by context (error vs marketing vs support).
3. Words to use/avoid.
4. Example rewrites.
5. Inclusive language notes.
""",
    ),
    skill(
        "logo-usage-rules",
        "Document logo usage: clear space, min size, on dark/light, misuse examples.",
        ["design", "brand"],
        ["file_read", "file_write"],
        """
## Steps
1. Primary/secondary marks.
2. Clear space and minimum size.
3. Color versions.
4. Misuse gallery (stretch, recolor, effects).
5. File format guidance for export.
""",
    ),
    skill(
        "design-critique-session",
        "Facilitate a design critique: goals, evidence, actionable feedback, decisions.",
        ["design", "process"],
        ["file_read", "file_write"],
        """
## Steps
1. Presenter states goal and constraints (2 min).
2. Silent review.
3. Feedback: observation → impact → suggestion.
4. Separate taste from usability evidence.
5. Capture decisions and follow-ups.
""",
    ),
    skill(
        "moodboard-direction",
        "Assemble a written moodboard direction: themes, keywords, references categories (no brand copying).",
        ["design", "creative"],
        ["file_read", "file_write"],
        """
## Steps
1. Project emotion and audience.
2. Keywords (texture, light, era, density).
3. Reference categories (architecture, nature, print)—describe, do not infringe.
4. Color/type direction summary.
5. What to avoid.
""",
    ),
    skill(
        "illustration-brief",
        "Write an illustration brief: story, style constraints, sizes, deliverables.",
        ["design", "art"],
        ["file_read", "file_write"],
        """
## Steps
1. Narrative purpose of the image.
2. Style constraints (geometry, palette).
3. Composition notes.
4. Sizes/export list.
5. Licensing and attribution needs.
""",
    ),
    skill(
        "photo-art-direction",
        "Art-direct photoshoots or stock selection: subject, lighting, crop, usage rights checklist.",
        ["design", "content"],
        ["file_read", "file_write"],
        """
## Steps
1. Message and emotion.
2. Subject/casting/setting notes.
3. Lighting and color treatment.
4. Crop for channels (hero, social, thumb).
5. Rights/model release checklist (generic).
""",
    ),
    skill(
        "print-layout-basics",
        "Lay out print-ready pages: margins, bleed, hierarchy, export checklist.",
        ["design", "print"],
        ["file_read", "file_write"],
        """
## Steps
1. Page size and bleed/safe margins.
2. Grid and hierarchy.
3. Image resolution guidance.
4. Color mode notes (RGB vs print CMYK handoff).
5. Preflight checklist before send-to-print.
""",
    ),
    skill(
        "presentation-deck-structure",
        "Structure a presentation deck: story arc, slide budget, speaker notes.",
        ["design", "content", "speaking"],
        ["file_read", "file_write"],
        """
## Steps
1. Audience and decision asked.
2. Arc: context → insight → plan → ask.
3. Slide budget (one idea per slide).
4. Visual vs text balance.
5. Speaker notes and timing.
6. Appendix for backups.
""",
    ),
    skill(
        "dashboard-ui-design",
        "Design dashboards: metrics hierarchy, density, empty/loading/error, drill-down.",
        ["design", "ui", "data"],
        ["file_read", "file_write"],
        """
## Steps
1. Primary questions the dashboard answers.
2. Metric hierarchy (KPI vs supporting).
3. Defaults and time range.
4. Density and progressive disclosure.
5. Empty/loading/error states.
6. Access roles if multi-tenant.
""",
    ),
    skill(
        "onboarding-ui-flow",
        "Design product onboarding UI: progressive disclosure, skip, value moments.",
        ["design", "ux", "product"],
        ["file_read", "file_write"],
        """
## Steps
1. Activation moment definition.
2. Step count budget.
3. Skip and resume.
4. Permission requests with rationale.
5. Success celebration without noise.
6. Metrics to instrument.
""",
    ),
    skill(
        "error-state-design",
        "Design error and recovery UI that is calm, specific, and actionable.",
        ["design", "ux"],
        ["file_read", "file_write"],
        """
## Steps
1. Error categories (user, network, server, permission).
2. Message pattern: what / why / next.
3. Illustration optional; clarity required.
4. Retry patterns and support links.
5. Log correlation ids for support (not scary codes alone).
""",
    ),
    skill(
        "design-handoff",
        "Prepare design-to-engineering handoff: specs, assets, behavior notes, open questions.",
        ["design", "process"],
        ["file_read", "file_write"],
        """
## Steps
1. Final flows linked.
2. Component mapping to existing system.
3. Redlines: spacing, type, states.
4. Assets exported and named.
5. Motion notes.
6. Open questions list.
""",
    ),
    skill(
        "a11y-design-review",
        "Design-side accessibility review: contrast, focus order, targets, motion, content structure.",
        ["design", "a11y"],
        ["file_read"],
        """
## Checklist
1. Text/icon contrast.
2. Focus order matches reading order.
3. Target sizes.
4. Motion sensitivity alternatives.
5. Headings and labels planned.
6. Error identification without color alone.
""",
    ),
    skill(
        "responsive-design-spec",
        "Specify responsive behavior across breakpoints: reflow, collapse, priority content.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. Breakpoint definitions used by the project.
2. What stacks vs hides vs moves.
3. Navigation patterns per width.
4. Touch vs pointer differences.
5. Examples for key screens.
""",
    ),
    skill(
        "data-viz-design",
        "Design charts/graphs for honesty: scales, color, annotations, accessibility.",
        ["design", "data"],
        ["file_read", "file_write"],
        """
## Steps
1. Question the chart answers.
2. Chart type fit.
3. Zero-baselines where needed; avoid misleading cuts.
4. Colorblind-safe encodings.
5. Text alternatives (table).
6. Annotation of anomalies.
""",
    ),
]

# =============================================================================
# CONTENT CREATION
# =============================================================================
CONTENT = [
    skill(
        "content-strategy",
        "Draft a content strategy: pillars, channels, cadence, voice, measurement.",
        ["content", "marketing"],
        ["file_read", "file_write"],
        """
## Steps
1. Business goal and audience.
2. Content pillars (3–5).
3. Channel roles (site, email, social, docs).
4. Cadence realistic for team size.
5. Voice principles.
6. KPIs and review loop.
""",
    ),
    skill(
        "editorial-calendar",
        "Build an editorial calendar with themes, owners, statuses, and deadlines.",
        ["content", "planning"],
        ["file_read", "file_write"],
        """
## Steps
1. Time horizon (month/quarter).
2. Themes mapped to launches.
3. Formats and channels.
4. Owners and due dates.
5. Status workflow (idea → draft → review → published).
6. Buffer for reactive content.
""",
    ),
    skill(
        "blog-post-outline",
        "Outline a blog post: angle, outline, sources, CTA, SEO basics without keyword stuffing.",
        ["content", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Reader problem and promise.
2. Working title options.
3. H2 outline.
4. Evidence/sources to gather.
5. CTA.
6. Internal links plan.
""",
    ),
    skill(
        "blog-post-draft",
        "Write a full blog draft from an outline with scannable structure and clear CTA.",
        ["content", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Hook in first screen.
2. Short paragraphs; descriptive subheads.
3. Concrete examples.
4. Honest limitations.
5. CTA and next step.
6. Edit pass for clarity and jargon.
""",
    ),
    skill(
        "social-post-pack",
        "Create a pack of social posts for one announcement across lengths and CTAs.",
        ["content", "social"],
        ["file_read", "file_write"],
        """
## Steps
1. Core message (one sentence).
2. Short / medium / long variants.
3. Thread or carousel outline if needed.
4. Hashtag policy (minimal, relevant).
5. Image/alt text notes.
6. Scheduling suggestions.
""",
    ),
    skill(
        "newsletter-issue",
        "Plan and draft a newsletter issue: sections, links, subject lines, preview text.",
        ["content", "email"],
        ["file_read", "file_write"],
        """
## Steps
1. Goal of this send.
2. Subject lines (A/B options) + preview text.
3. Sections with scannable bullets.
4. Single primary CTA.
5. Footer legal/unsub placeholders as required by product.
6. Plain-text version notes.
""",
    ),
    skill(
        "video-script",
        "Write a video script with visual column, VO/dialogue, timing, and B-roll notes.",
        ["content", "video"],
        ["file_read", "file_write"],
        """
## Steps
1. Length target and platform constraints (generic).
2. Hook in first 5–10 seconds.
3. Two-column script: visual | audio.
4. Timing estimates.
5. B-roll and on-screen text.
6. End screen CTA.
""",
    ),
    skill(
        "video-edit-checklist",
        "Checklist for editing a video cut: pacing, audio, captions, exports.",
        ["content", "video"],
        ["file_read", "file_write"],
        """
## Checklist
1. Story cut before polish.
2. Audio levels and noise.
3. Captions accuracy.
4. Pacing: remove dead air.
5. Brand lower-thirds if any.
6. Export presets per destination.
7. Thumbnail still selection.
""",
    ),
    skill(
        "podcast-episode-plan",
        "Plan a podcast episode: cold open, segments, guests prep, show notes.",
        ["content", "audio"],
        ["file_read", "file_write"],
        """
## Steps
1. Episode thesis.
2. Segment rundown with times.
3. Guest brief questions.
4. Recording checklist (mics, backup).
5. Show notes and timestamps.
6. Promo clips list.
""",
    ),
    skill(
        "script-to-storyboard",
        "Turn a script into a shot list / storyboard frames description.",
        ["content", "video", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. Break script into shots.
2. Shot type (wide/medium/close), angle, motion.
3. Continuity notes.
4. Graphics/overlay callouts.
5. Estimated duration per shot.
""",
    ),
    skill(
        "press-release",
        "Draft a press release: headline, lede, body, boilerplate, quotes, links.",
        ["content", "comms"],
        ["file_read", "file_write"],
        """
## Steps
1. Newsworthy angle (what changed for whom).
2. Headline + subhead.
3. Lede with 5 Ws.
4. Supporting facts and optional quote.
5. Boilerplate about the org.
6. Contact and links.
7. Fact-check all claims.
""",
    ),
    skill(
        "case-study-write",
        "Write a case study: problem, approach, results, proof, lessons.",
        ["content", "marketing"],
        ["file_read", "file_write"],
        """
## Steps
1. Customer context (permissions!).
2. Problem and stakes.
3. Approach without confidential details.
4. Results with real metrics if allowed.
5. Quote if approved.
6. Lessons and CTA.
""",
    ),
    skill(
        "documentation-tutorial",
        "Write a task-oriented tutorial with prerequisites, steps, verification, troubleshooting.",
        ["content", "docs"],
        ["file_read", "file_write"],
        """
## Steps
1. Goal statement (“By the end you will…” ).
2. Prerequisites and time estimate.
3. Numbered steps with expected outputs.
4. Verification section.
5. Troubleshooting common failures.
6. Next steps links.
""",
    ),
    skill(
        "release-announcement",
        "Write a product release announcement for blog/email/in-app.",
        ["content", "product"],
        ["file_read", "file_write"],
        """
## Steps
1. User benefits first (not feature dump).
2. What’s new / improved / fixed.
3. Who is affected.
4. How to start / migrate.
5. Links to docs.
6. Tone match brand voice.
""",
    ),
    skill(
        "seo-content-brief",
        "Create an SEO content brief: intent, outline, questions to answer, internal links (no stuffing).",
        ["content", "seo"],
        ["file_read", "file_write"],
        """
## Steps
1. Search intent (learn/compare/buy).
2. Primary topic and related questions.
3. Outline that satisfies intent.
4. Internal link targets.
5. Title/meta drafts that are human-first.
6. Success metrics (rank is lagging; engagement too).
""",
    ),
    skill(
        "content-repurpose",
        "Repurpose one long asset into multiple channel formats without sounding duplicate.",
        ["content", "marketing"],
        ["file_read", "file_write"],
        """
## Steps
1. Source asset and core ideas.
2. Derive: short posts, email blurb, thread, outline for video.
3. Adjust tone per channel.
4. Track canonical URL.
5. Calendar placement.
""",
    ),
    skill(
        "interview-questions",
        "Prepare interview questions for customers, candidates, or experts with follow-ups.",
        ["content", "research"],
        ["file_read", "file_write"],
        """
## Steps
1. Interview goal.
2. Open-ended questions (avoid leading).
3. Follow-up probes.
4. Timeboxed agenda.
5. Consent/recording notes if applicable.
""",
    ),
    skill(
        "transcript-cleanup",
        "Clean a transcript: speakers, paragraphs, filler removal, summary bullets.",
        ["content", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Identify speakers.
2. Paragraph by topic.
3. Light cleanup (keep meaning).
4. Mark inaudible.
5. Executive summary + action items.
6. Redact sensitive info.
""",
    ),
    skill(
        "style-guide-writing",
        "Create a writing style guide: voice, grammar choices, inclusive language, examples.",
        ["content", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Voice principles.
2. Capitalization/punctuation choices.
3. Product name rules (your product only).
4. Inclusive language.
5. Before/after examples.
6. Where the guide lives and who owns it.
""",
    ),
    skill(
        "fact-check-pass",
        "Fact-check a draft: claims, numbers, links, attribution, uncertainty language.",
        ["content", "quality"],
        ["file_read", "web_search"],
        """
## Steps
1. Extract factual claims.
2. Verify each with sources; prefer primary.
3. Check numbers and dates.
4. Link rot check.
5. Soften unverified claims.
6. Log corrections.
""",
    ),
    skill(
        "content-edit-pass",
        "Edit for clarity, structure, and brevity while preserving author voice.",
        ["content", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Structure pass (order, headings).
2. Clarity pass (shorten, concrete verbs).
3. Consistency (terms, tense).
4. Cut redundancy.
5. Query list for author on ambiguous claims.
""",
    ),
    skill(
        "headline-options",
        "Generate headline options optimized for clarity and curiosity without clickbait.",
        ["content", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Core promise of the piece.
2. 8–12 options across styles (how-to, outcome, question).
3. Flag which are accurate vs hype.
4. Recommend top 2 with rationale.
""",
    ),
    skill(
        "call-to-action-copy",
        "Write CTAs matched to funnel stage with friction-aware wording.",
        ["content", "ux", "marketing"],
        ["file_read", "file_write"],
        """
## Steps
1. Desired action and stage (aware/consider/convert).
2. Button and supporting line.
3. Reduce anxiety (price, time, cancel).
4. Variants for A/B if useful.
5. Align with actual UI behavior.
""",
    ),
    skill(
        "landing-page-copy",
        "Write landing page copy: hero, proof, benefits, objections, CTA.",
        ["content", "marketing"],
        ["file_read", "file_write"],
        """
## Steps
1. Offer and audience.
2. Hero: outcome + clarity.
3. Benefits tied to proof.
4. Objection handling.
5. CTA section.
6. SEO title/meta drafts.
""",
    ),
    skill(
        "faq-generation",
        "Generate FAQs from product behavior, support tickets, and objections.",
        ["content", "support"],
        ["file_read", "file_write"],
        """
## Steps
1. Gather real questions (tickets, sales, docs gaps).
2. Short accurate answers with links.
3. Order by frequency.
4. Keep legal/pricing claims approved.
5. Review cadence.
""",
    ),
    skill(
        "changelog-user-facing",
        "Turn engineering notes into user-facing release notes people understand.",
        ["content", "product"],
        ["file_read", "file_write"],
        """
## Steps
1. Read commits/PRs/changelog drafts.
2. Translate to user outcomes.
3. Group by themes.
4. Call out breaking changes and actions required.
5. Thank contributors if culture fits.
""",
    ),
    skill(
        "community-guidelines",
        "Draft community guidelines: values, allowed/not allowed, enforcement ladder.",
        ["content", "community"],
        ["file_read", "file_write"],
        """
## Steps
1. Values and scope (where rules apply).
2. Allowed vs prohibited behaviors.
3. Reporting path.
4. Enforcement ladder (warn → restrict → ban).
5. Appeals.
6. Keep enforceable and clear.
""",
    ),
    skill(
        "content-localization",
        "Prepare content for localization: freeze strings, context notes, do-not-translate list.",
        ["content", "i18n"],
        ["file_read", "file_write"],
        """
## Steps
1. Identify freezable source language.
2. Context notes for translators.
3. Do-not-translate (product names you own, code).
4. Length constraints.
5. Review cycle for high-visibility pages.
""",
    ),
    skill(
        "thumbnail-concept",
        "Concept thumbnails: focal subject, text overlay limits, contrast, A/B ideas.",
        ["content", "design"],
        ["file_read", "file_write"],
        """
## Steps
1. One clear subject.
2. Readable text at small size (few words).
3. High contrast.
4. Avoid clutter and misleading imagery.
5. 3 concept variants.
""",
    ),
    skill(
        "content-audit",
        "Audit existing content: freshness, accuracy, duplicates, SEO cannibalization, prune plan.",
        ["content", "quality"],
        ["file_read", "file_write"],
        """
## Steps
1. Inventory URLs/docs.
2. Score freshness and traffic if data exists.
3. Find duplicates/overlaps.
4. Update vs redirect vs remove decisions.
5. Prioritized backlog.
""",
    ),
]

# =============================================================================
# PERSONAL ASSISTANT
# =============================================================================
PERSONAL = [
    skill(
        "daily-planning",
        "Build a realistic daily plan from priorities, calendar constraints, and energy.",
        ["personal", "productivity"],
        ["file_read", "file_write"],
        """
## Steps
1. List must-do / should-do / nice-to-do.
2. Timebox with breaks; protect focus blocks.
3. Align with fixed meetings.
4. Define “done for today”.
5. Evening shutdown checklist.
""",
    ),
    skill(
        "weekly-review",
        "Run a weekly review: wins, open loops, priorities, calendar look-ahead.",
        ["personal", "productivity"],
        ["file_read", "file_write"],
        """
## Steps
1. Wins and lessons.
2. Inbox/notes zero or triage.
3. Project list status.
4. Next week top 3 outcomes.
5. Calendar risks.
6. Personal admin (bills, health) if relevant.
""",
    ),
    skill(
        "priority-matrix",
        "Sort tasks with urgency/importance and recommend what to defer or drop.",
        ["personal", "productivity"],
        ["file_read", "file_write"],
        """
## Steps
1. Capture all tasks.
2. Score urgency and importance.
3. Quadrant actions (do, schedule, delegate, drop).
4. Time estimates.
5. Next actions only (no vague projects).
""",
    ),
    skill(
        "meeting-agenda",
        "Create a meeting agenda with purpose, topics, times, and decisions needed.",
        ["personal", "work"],
        ["file_read", "file_write"],
        """
## Steps
1. Meeting purpose (decide/inform/brainstorm).
2. Attendees necessity check.
3. Timed topics.
4. Pre-read links.
5. Decision log template.
""",
    ),
    skill(
        "meeting-notes",
        "Turn discussion into structured notes: decisions, actions, owners, dates.",
        ["personal", "work"],
        ["file_read", "file_write"],
        """
## Steps
1. Context and attendees.
2. Decisions only if actually decided.
3. Actions with owner + due date.
4. Open questions.
5. Share summary draft.
""",
    ),
    skill(
        "email-draft",
        "Draft clear emails: purpose first, short paragraphs, explicit ask, tone control.",
        ["personal", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Goal and audience.
2. Subject line.
3. Opening purpose.
4. Bullets for asks/deadlines.
5. Polite close.
6. Optional shorter version.
""",
    ),
    skill(
        "difficult-conversation",
        "Prepare a difficult conversation: goals, script, boundaries, outcomes.",
        ["personal", "communication"],
        ["file_read", "file_write"],
        """
## Steps
1. Desired outcome and non-negotiables.
2. Facts vs interpretations.
3. Opening script (neutral).
4. Listen prompts.
5. Options and fallback.
6. Follow-up note plan.
""",
    ),
    skill(
        "decision-log-personal",
        "Log a personal or work decision with options, criteria, choice, review date.",
        ["personal", "decisions"],
        ["file_read", "file_write"],
        """
## Steps
1. Decision statement.
2. Options considered.
3. Criteria and weights.
4. Choice and rationale.
5. Review date and kill criteria.
""",
    ),
    skill(
        "habit-design",
        "Design a habit loop: cue, routine, reward, tracking, restart plan.",
        ["personal", "habits"],
        ["file_read", "file_write"],
        """
## Steps
1. Tiny starting version.
2. Cue attachment to existing routine.
3. Reward.
4. Tracking simple as possible.
5. Miss-day restart rule (no all-or-nothing).
""",
    ),
    skill(
        "goal-breakdown",
        "Break a large goal into milestones, weekly outcomes, and first concrete actions.",
        ["personal", "planning"],
        ["file_read", "file_write"],
        """
## Steps
1. Goal with deadline and why.
2. Success metrics.
3. Milestones.
4. Weekly outcomes.
5. Next 3 physical actions.
6. Risks and supports.
""",
    ),
    skill(
        "travel-itinerary",
        "Build a travel itinerary: logistics, buffers, offline notes, packing constraints.",
        ["personal", "travel"],
        ["file_read", "file_write"],
        """
## Steps
1. Dates, travelers, budget band.
2. Transport and lodging blocks.
3. Day plans with buffers.
4. Booking reference placeholders.
5. Offline critical info.
6. Contingency (delays).
""",
    ),
    skill(
        "packing-list",
        "Generate a packing list by trip type, climate, and activities.",
        ["personal", "travel"],
        ["file_read", "file_write"],
        """
## Steps
1. Climate and activities.
2. Clothing by day + layers.
3. Documents/meds/tech.
4. Liquids/security constraints if flying.
5. Checklist format.
""",
    ),
    skill(
        "budget-snapshot",
        "Create a simple budget snapshot: income, fixed costs, variable, goals (no bank logins).",
        ["personal", "finance"],
        ["file_read", "file_write"],
        """
## Steps
1. User-provided numbers only (never scrape banks).
2. Categories: fixed, variable, savings, debt.
3. Surplus/deficit.
4. 1–3 adjustment options.
5. Privacy: store only if user asks.
""",
    ),
    skill(
        "expense-categorize",
        "Categorize a list of expenses and summarize by category with outliers.",
        ["personal", "finance"],
        ["file_read", "file_write"],
        """
## Steps
1. Accept CSV/list from user.
2. Categories consistent.
3. Totals and top merchants.
4. Outliers.
5. Questions for ambiguous items.
""",
    ),
    skill(
        "recipe-plan-meals",
        "Plan meals for N days given constraints (time, diet, servings) with shopping list.",
        ["personal", "food"],
        ["file_read", "file_write"],
        """
## Steps
1. Constraints and preferences.
2. Meal slots.
3. Recipes or simple meal ideas.
4. Consolidated shopping list.
5. Prep-ahead tips.
""",
    ),
    skill(
        "workout-plan-basic",
        "Draft a basic workout plan with warm-up, main work, recovery (not medical advice).",
        ["personal", "fitness"],
        ["file_read", "file_write"],
        """
## Disclaimer
Not medical advice; user should consult professionals for health conditions.

## Steps
1. Goals and available days/equipment.
2. Split (full body / upper-lower).
3. Warm-up.
4. Main sets with progression rule.
5. Deload notes.
6. Stop rules for pain vs effort.
""",
    ),
    skill(
        "sleep-routine",
        "Design a sleep routine: wind-down, environment, schedule consistency (general wellness).",
        ["personal", "wellness"],
        ["file_read", "file_write"],
        """
## Disclaimer
General wellness only—not medical advice.

## Steps
1. Target schedule.
2. Wind-down block.
3. Environment checklist.
4. Caffeine/alcohol timing awareness.
5. Track 1–2 metrics only.
""",
    ),
    skill(
        "reading-notes",
        "Produce structured reading notes: summary, key ideas, quotes, actions.",
        ["personal", "learning"],
        ["file_read", "file_write"],
        """
## Steps
1. Bibliographic basics.
2. 5–10 key ideas.
3. Notable quotes with locations if known.
4. Critiques/questions.
5. Personal action items.
""",
    ),
    skill(
        "learning-curriculum",
        "Build a learning curriculum for a skill: modules, resources types, practice projects.",
        ["personal", "learning"],
        ["file_read", "file_write"],
        """
## Steps
1. Outcome definition.
2. Prerequisites.
3. Modules with time estimates.
4. Practice projects.
5. Checkpoints.
6. Resource types (docs, courses, books)—no need for specific branded funnels.
""",
    ),
    skill(
        "flashcard-set",
        "Create flashcards (Q/A) from notes for spaced practice.",
        ["personal", "learning"],
        ["file_read", "file_write"],
        """
## Steps
1. Source material.
2. Atomic cards (one fact/idea).
3. Avoid overly broad questions.
4. Export as CSV or markdown list.
5. Review schedule suggestion.
""",
    ),
    skill(
        "research-digest",
        "Digest multiple sources into a brief with citations and confidence levels.",
        ["personal", "research"],
        ["file_read", "web_search", "file_write"],
        """
## Steps
1. Question to answer.
2. Gather sources; prefer primary.
3. Synthesize agreements/disagreements.
4. Confidence per claim.
5. Open questions.
6. Citation list.
""",
    ),
    skill(
        "job-application-tailor",
        "Tailor a resume bullet set and cover note to a job description without fabricating experience.",
        ["personal", "career"],
        ["file_read", "file_write"],
        """
## Steps
1. Extract role requirements.
2. Map real experience only—never invent.
3. Rewrite bullets with impact metrics if known.
4. Cover note short and specific.
5. Keyword alignment honestly.
""",
    ),
    skill(
        "interview-prep",
        "Prepare for an interview: stories (STAR), questions to ask, research brief.",
        ["personal", "career"],
        ["file_read", "file_write"],
        """
## Steps
1. Role and company context from public info.
2. 5–8 STAR stories mapped to skills.
3. Questions for them.
4. Logistics checklist.
5. Post-interview note template.
""",
    ),
    skill(
        "negotiation-prep",
        "Prepare a negotiation: BATNA, range, script, concessions.",
        ["personal", "career"],
        ["file_read", "file_write"],
        """
## Steps
1. Goals and BATNA.
2. Market context if available.
3. Ask script.
4. Concession plan.
5. Walk-away line.
""",
    ),
    skill(
        "household-chores-plan",
        "Create a household chore plan with cadence and ownership.",
        ["personal", "home"],
        ["file_read", "file_write"],
        """
## Steps
1. Spaces and tasks inventory.
2. Daily/weekly/monthly cadence.
3. Ownership split if shared.
4. Reset rituals.
5. Supply checklist.
""",
    ),
    skill(
        "home-project-plan",
        "Plan a home project: scope, materials, steps, safety, contingency.",
        ["personal", "home"],
        ["file_read", "file_write"],
        """
## Steps
1. Success definition.
2. Skills/tools required; when to hire pro.
3. Materials list.
4. Step sequence.
5. Safety notes.
6. Budget and time buffer.
""",
    ),
    skill(
        "event-planning",
        "Plan an event: guest list, venue constraints, timeline, budget, day-of run of show.",
        ["personal", "planning"],
        ["file_read", "file_write"],
        """
## Steps
1. Purpose, date, guest count.
2. Budget bands.
3. Venue/logistics.
4. Run of show.
5. Vendors checklist.
6. Contingency weather/tech.
""",
    ),
    skill(
        "gift-ideas",
        "Suggest gift ideas from interests, budget, and constraints (no purchase required).",
        ["personal", "social"],
        ["file_read", "file_write"],
        """
## Steps
1. Recipient interests and constraints.
2. Budget.
3. Experience vs physical options.
4. Personalization angles.
5. Avoid duplicates if user lists owned items.
""",
    ),
    skill(
        "message-draft-personal",
        "Draft personal messages (thanks, apology, invite, check-in) with tone options.",
        ["personal", "communication"],
        ["file_read", "file_write"],
        """
## Steps
1. Intent and relationship.
2. Tone options (warm/formal/brief).
3. Drafts (2–3).
4. What to avoid.
""",
    ),
    skill(
        "boundary-setting",
        "Help articulate personal or work boundaries with scripts and follow-through.",
        ["personal", "communication"],
        ["file_read", "file_write"],
        """
## Steps
1. Situation and limit.
2. Clear request language.
3. Consequences you will actually keep.
4. Soft/firm script variants.
5. Self-check after.
""",
    ),
    skill(
        "focus-block",
        "Design a deep-work focus block: environment, timers, distraction rules, shutdown.",
        ["personal", "productivity"],
        ["file_read", "file_write"],
        """
## Steps
1. Single outcome for the block.
2. Time length realistic.
3. Environment prep.
4. Distraction rules.
5. Break and shutdown ritual.
""",
    ),
    skill(
        "digital-declutter",
        "Plan a digital declutter: files, inbox, photos, subscriptions—with batch rules.",
        ["personal", "productivity"],
        ["file_read", "file_write"],
        """
## Steps
1. Scope areas.
2. Keep/delete/archive rules.
3. Batch sessions.
4. Backup before mass delete.
5. Maintenance cadence.
""",
    ),
    skill(
        "password-hygiene",
        "Personal password hygiene checklist: unique passwords, manager use, 2FA—without handling secrets.",
        ["personal", "security"],
        ["file_read"],
        """
## Steps
1. Unique passwords per site (manager recommended).
2. Enable 2FA where available.
3. Recovery codes stored offline.
4. Breach response steps (rotate).
5. Never ask user to paste passwords into chat.
""",
    ),
    skill(
        "privacy-checkup",
        "Walk through a personal privacy checkup: app permissions, sharing, data downloads.",
        ["personal", "privacy"],
        ["file_read", "file_write"],
        """
## Steps
1. High-risk accounts list.
2. Permission audit on phone/apps.
3. Sharing/privacy settings.
4. Download-your-data if relevant.
5. Tracking reduction tips (general).
""",
    ),
    skill(
        "reminder-system",
        "Design a reminder system: what belongs on calendar vs tasks vs checklists.",
        ["personal", "productivity"],
        ["file_read", "file_write"],
        """
## Steps
1. Types of commitments.
2. Calendar = time-bound; tasks = next actions.
3. Recurring admin.
4. Review triggers.
5. Tool-agnostic workflow.
""",
    ),
    skill(
        "second-brain-notes",
        "Organize notes into a simple personal knowledge system: inbox, projects, areas, archives.",
        ["personal", "learning"],
        ["file_read", "file_write"],
        """
## Steps
1. Capture inbox.
2. Projects vs areas vs resources.
3. Naming conventions.
4. Weekly processing rules.
5. Searchability (tags/links).
""",
    ),
    skill(
        "journal-prompts",
        "Provide journal prompts for reflection, goals, or stress processing (not therapy).",
        ["personal", "wellness"],
        ["file_write"],
        """
## Disclaimer
Not therapy or clinical advice.

## Steps
1. User’s intent (gratitude, stress, goals).
2. 5–10 prompts.
3. Optional structure for a 10-minute entry.
4. Privacy reminder.
""",
    ),
    skill(
        "family-logistics",
        "Coordinate family logistics: shared calendar norms, chores, handoffs, emergency info sheet.",
        ["personal", "home"],
        ["file_read", "file_write"],
        """
## Steps
1. Household roles.
2. Shared calendar rules.
3. Chore ownership.
4. Handoff checklist for caregivers.
5. Emergency contacts sheet (store privately).
""",
    ),
    skill(
        "caregiver-checklist",
        "Build a caregiver checklist for appointments, meds schedules placeholders, and notes (non-clinical).",
        ["personal", "home"],
        ["file_read", "file_write"],
        """
## Disclaimer
Not medical advice; follow clinician instructions.

## Steps
1. Daily routine template.
2. Appointment log.
3. Meds schedule placeholder (user fills).
4. Observation notes.
5. Questions for clinicians.
""",
    ),
    skill(
        "pet-care-routine",
        "Create a pet care routine: feeding, walks, meds placeholders, emergency contacts.",
        ["personal", "home"],
        ["file_read", "file_write"],
        """
## Disclaimer
Not veterinary advice.

## Steps
1. Species/age constraints from user.
2. Daily/weekly care tasks.
3. Sitter instructions sheet.
4. Emergency vet contact placeholders.
""",
    ),
    skill(
        "move-house-plan",
        "Plan a household move: timeline, inventory, vendors, change-of-address checklist.",
        ["personal", "planning"],
        ["file_read", "file_write"],
        """
## Steps
1. Move date and constraints.
2. Room inventory.
3. Timeline T-30/T-7/T-1/T+1.
4. Utilities and address changes.
5. Packing order.
6. First-night box.
""",
    ),
    skill(
        "subscription-audit",
        "Audit subscriptions from a user-provided list: keep, cancel, downgrade recommendations.",
        ["personal", "finance"],
        ["file_read", "file_write"],
        """
## Steps
1. User-provided list only.
2. Monthly/annual cost normalize.
3. Usage fit.
4. Keep/cancel/downgrade.
5. Calendar reminders for renewals.
""",
    ),
    skill(
        "personal-crm",
        "Lightweight personal CRM: people notes, last contact, follow-ups (privacy first).",
        ["personal", "social"],
        ["file_read", "file_write"],
        """
## Steps
1. Fields: name, context, last contact, next follow-up.
2. Cadence rules.
3. Interaction log template.
4. Privacy: local storage preferences.
5. Weekly review of due follow-ups.
""",
    ),
    skill(
        "gratitude-practice",
        "Set up a short gratitude practice with prompts and streak-free consistency tips.",
        ["personal", "wellness"],
        ["file_write"],
        """
## Steps
1. 3-item daily format.
2. Specificity over generic.
3. Time of day cue.
4. Optional weekly reflection.
5. No guilt on misses.
""",
    ),
    skill(
        "time-audit",
        "Guide a time audit: log categories, find leaks, redesign week.",
        ["personal", "productivity"],
        ["file_read", "file_write"],
        """
## Steps
1. Track 2–3 days in categories.
2. Summarize totals.
3. Align with stated priorities.
4. Cut/protect blocks.
5. Experiment for one week.
""",
    ),
    skill(
        "personal-okr",
        "Write personal OKRs: objective, key results, weekly check-ins.",
        ["personal", "planning"],
        ["file_read", "file_write"],
        """
## Steps
1. Objective qualitative.
2. 2–4 measurable KRs.
3. Initiatives under KRs.
4. Weekly score.
5. End-of-cycle retro.
""",
    ),
]

# Extra packs to clear 150+ real workflows
EXTRA = [
    skill(
        "game-cinematic-brief",
        "Brief a game cinematic: emotion, camera beats, length, audio, handoff to animation.",
        ["gaming", "cinematic"],
        ["file_read", "file_write"],
        """
## Steps
1. Story purpose and emotional arc.
2. Shot list with durations.
3. Camera language (generic: wide/push/orbit).
4. Audio cues.
5. Gameplay integration (skippable?).
6. Acceptance criteria for first cut.
""",
    ),
    skill(
        "game-camera-feel",
        "Tune camera feel: follow lag, collision, aim assist notes, comfort options.",
        ["gaming", "feel"],
        ["file_read", "file_write"],
        """
## Steps
1. Camera mode inventory.
2. Follow/look lag targets.
3. Collision and recovery.
4. Combat vs exploration profiles.
5. Comfort options (invert, sensitivity, reduce motion).
6. Playtest script.
""",
    ),
    skill(
        "game-crafting-system",
        "Design crafting: recipes, stations, discovery, economy impact.",
        ["gaming", "systems"],
        ["file_read", "file_write"],
        """
## Steps
1. Player fantasy of crafting.
2. Recipe structure and unlocks.
3. Stations/tools gates.
4. Material sinks vs inflation.
5. UX: discoverability and queues.
6. Balance spreadsheet outline.
""",
    ),
    skill(
        "game-building-tools",
        "Design player building tools: snap, validation, budgets, sharing limits.",
        ["gaming", "systems"],
        ["file_read", "file_write"],
        """
## Steps
1. Placement rules and snap grid.
2. Validation errors (clear messages).
3. Piece budgets and performance caps.
4. Permission in multiplayer.
5. Blueprint save/load.
6. Abuse prevention.
""",
    ),
    skill(
        "game-season-pass-structure",
        "Structure a season pass track: free/premium split ethics, pacing, rewards (no pay-to-win).",
        ["gaming", "liveops"],
        ["file_read", "file_write"],
        """
## Steps
1. Season narrative theme.
2. Free vs paid rewards (cosmetic-first if competitive).
3. XP pacing curve.
4. Catch-up policy.
5. Economy safety.
6. Player communication plan.
""",
    ),
    skill(
        "concept-art-brief",
        "Write a concept art brief: subject, silhouette goals, palette, orthos, deliverables.",
        ["design", "art"],
        ["file_read", "file_write"],
        """
## Steps
1. Subject and role in product/game.
2. Silhouette and read at distance.
3. Palette and materials.
4. Reference mood (descriptive, non-infringing).
5. Deliverables (hero, turnaround, callouts).
""",
    ),
    skill(
        "ui-kit-inventory",
        "Inventory UI components and document missing states for a kit.",
        ["design", "ui"],
        ["file_read", "file_write"],
        """
## Steps
1. List components in use.
2. Required states matrix.
3. Gaps vs design system.
4. Naming consistency.
5. Prioritized build list.
""",
    ),
    skill(
        "poster-layout",
        "Design a poster layout brief: hierarchy, margins, type, print specs.",
        ["design", "print"],
        ["file_read", "file_write"],
        """
## Steps
1. Message hierarchy.
2. Size and bleed.
3. Type scale.
4. Image placement.
5. Print export checklist.
""",
    ),
    skill(
        "portfolio-case-layout",
        "Structure a portfolio case study page: problem, process, outcome, images.",
        ["design", "career"],
        ["file_read", "file_write"],
        """
## Steps
1. Role and contribution honesty.
2. Problem context.
3. Process artifacts.
4. Outcome metrics if allowed.
5. Image sequence captions.
""",
    ),
    skill(
        "content-hooks",
        "Generate non-clickbait content hooks for an article or video topic.",
        ["content", "writing"],
        ["file_read", "file_write"],
        """
## Steps
1. Core promise.
2. 10 hooks across styles.
3. Accuracy check vs content.
4. Recommend top 3.
""",
    ),
    skill(
        "video platforms-chapter-markers",
        "Create chapter markers and titles from a video outline or transcript.",
        ["content", "video"],
        ["file_read", "file_write"],
        """
## Steps
1. Source outline/transcript.
2. Chapters at meaningful shifts.
3. Titles scannable.
4. Timestamps validated.
5. Description block ready to paste.
""",
    ),
    skill(
        "short-form-script",
        "Write a short-form vertical video script under a target duration with on-screen text.",
        ["content", "video"],
        ["file_read", "file_write"],
        """
## Steps
1. Duration target.
2. Hook, body, CTA.
3. On-screen text limited.
4. Shot simplicity.
5. Caption file notes.
""",
    ),
    skill(
        "community-ama-prep",
        "Prepare an AMA: themes, banned topics, moderation, answer bank.",
        ["content", "community"],
        ["file_read", "file_write"],
        """
## Steps
1. Goals and audience.
2. Theme pillars.
3. Off-limits topics.
4. Seed questions.
5. Moderation plan.
6. Follow-up content capture.
""",
    ),
    skill(
        "personal-inventory",
        "Run a personal inventory session: commitments, energy, obligations, free capacity.",
        ["personal", "planning"],
        ["file_read", "file_write"],
        """
## Steps
1. Brain dump commitments.
2. Categorize (work, home, health, social).
3. Energy cost estimate.
4. Drop/defer candidates.
5. Capacity for new yes.
""",
    ),
    skill(
        "morning-shutdown-rituals",
        "Design morning and end-of-day rituals that fit a real schedule.",
        ["personal", "productivity"],
        ["file_write"],
        """
## Steps
1. Available minutes.
2. Morning: plan + one priority.
3. Shutdown: capture open loops.
4. Device boundaries.
5. 2-week experiment.
""",
    ),
    skill(
        "conflict-deescalation",
        "Prepare de-escalation language for personal or workplace conflict (non-clinical).",
        ["personal", "communication"],
        ["file_write"],
        """
## Steps
1. Safety first—exit if unsafe.
2. Neutral observations.
3. Feelings/needs without blame.
4. Clear request.
5. Pause options.
""",
    ),
    skill(
        "celebration-plan",
        "Plan a celebration (birthday, launch, milestone): constraints, program, budget.",
        ["personal", "planning"],
        ["file_write"],
        """
## Steps
1. Occasion and guests.
2. Budget.
3. Program timeline.
4. Food/space/tech needs.
5. Roles.
6. Contingency.
""",
    ),
    skill(
        "personal-values-exercise",
        "Facilitate a values clarification exercise and translate into weekly choices.",
        ["personal", "wellness"],
        ["file_write"],
        """
## Disclaimer
Reflective exercise, not therapy.

## Steps
1. Candidate values list.
2. Top 5 forced rank.
3. Evidence from last month.
4. Gaps.
5. One weekly behavior per value.
""",
    ),
    skill(
        "accountability-partnership",
        "Set up an accountability partnership: cadence, metrics, check-in template.",
        ["personal", "habits"],
        ["file_write"],
        """
## Steps
1. Shared goals.
2. Cadence and medium.
3. Metrics.
4. Check-in questions.
5. Miss protocol.
""",
    ),
    skill(
        "creative-constraint-sprint",
        "Run a creative sprint with constraints: timebox, output, critique, ship.",
        ["design", "content", "creative"],
        ["file_write"],
        """
## Steps
1. Problem and hard constraints.
2. Timebox.
3. Quantity goals (e.g. 10 thumbnails).
4. Select and refine 1–2.
5. Ship or schedule next critique.
""",
    ),
]

# Combine extra domains
for batch in (GAMING, DESIGN, CONTENT, PERSONAL, EXTRA):
    SKILLS.extend(batch)


def main() -> None:
    # load existing names so we don't overwrite older official engineering skills accidentally
    # We still write domain skills; overwrite if same name in this generator only
    written = 0
    for name, desc, tags, tools, body in SKILLS:
        if name in RESERVED:
            print("skip reserved", name)
            continue
        write_skill(name, desc, tags, tools, body)
        written += 1
    print(f"Wrote {written} domain skills under {ROOT}")


if __name__ == "__main__":
    main()
