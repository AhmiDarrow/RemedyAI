# Skills list

_Auto-generated from `skills/*/SKILL.md` — **281** skills. Last updated: 2026-07-26 23:01 UTC. Do not edit by hand; run `python scripts/generate_skills_list.py` or `python scripts/build_catalog.py`._

## Summary by area

| Area | Count |
|------|------:|
| backend | 22 |
| content | 35 |
| data | 2 |
| design | 45 |
| docs | 5 |
| frontend | 7 |
| gaming | 35 |
| git | 10 |
| llm | 3 |
| ops | 14 |
| other | 2 |
| personal | 54 |
| security | 16 |
| testing | 13 |
| tooling | 18 |

| **Total** | **281** |

## Backend

| Skill | Description |
|-------|-------------|
| [`api-client-sdk`](skills/api-client-sdk/SKILL.md) | Generate or refresh a typed client from OpenAPI for consumers. |
| [`api-contract-review`](skills/api-contract-review/SKILL.md) | Review HTTP APIs for validation, authz, status codes, pagination, and error shape consistency. |
| [`background-job-ui`](skills/background-job-ui/SKILL.md) | Expose long-running job progress/status to users with authz and safe errors. |
| [`backward-compat-api`](skills/backward-compat-api/SKILL.md) | Plan backward-compatible API evolution and deprecation windows. |
| [`cache-invalidation`](skills/cache-invalidation/SKILL.md) | Design cache keys and invalidation to prevent stale reads and stampedes. |
| [`cron-job-design`](skills/cron-job-design/SKILL.md) | Design scheduled jobs with overlap locks, idempotency, and failure alerts. |
| [`datetime-timezone`](skills/datetime-timezone/SKILL.md) | Fix datetime bugs by storing UTC and converting only at the edge. |
| [`db-migration-safe`](skills/db-migration-safe/SKILL.md) | Write or review DB migrations using expand/contract safety and rollback notes. |
| [`docs-api-examples`](skills/docs-api-examples/SKILL.md) | Add runnable request/response examples to API docs for the hardest endpoints. |
| [`env-config-12factor`](skills/env-config-12factor/SKILL.md) | Refactor configuration to env-based 12-factor style with validated startup. |
| [`graceful-shutdown`](skills/graceful-shutdown/SKILL.md) | Implement SIGTERM-aware graceful shutdown and drain for servers/workers. |
| [`graphql-schema-review`](skills/graphql-schema-review/SKILL.md) | Review GraphQL schemas/resolvers for authz, N+1, pagination, and deprecations. |
| [`grpc-api-design`](skills/grpc-api-design/SKILL.md) | Design/review gRPC protos with versioning, deadlines, and idempotency. |
| [`health-endpoints`](skills/health-endpoints/SKILL.md) | Add liveness vs readiness endpoints with appropriate dependency checks. |
| [`idempotent-api`](skills/idempotent-api/SKILL.md) | Make a mutating endpoint safely retryable with idempotency keys. |
| [`json-schema-design`](skills/json-schema-design/SKILL.md) | Design tight JSON Schema / Zod / Pydantic models with bounds and examples. |
| [`migration-data-backfill`](skills/migration-data-backfill/SKILL.md) | Plan batched data backfills that won't lock production tables. |
| [`openapi-sync`](skills/openapi-sync/SKILL.md) | Regenerate or manually sync OpenAPI with implemented routes and flag breaking changes. |
| [`pagination-standard`](skills/pagination-standard/SKILL.md) | Add stable list pagination (cursor preferred) with enforced max limits. |
| [`payment-webhook-flow`](skills/payment-webhook-flow/SKILL.md) | Implement payment webhooks with verification and idempotent entitlement updates. |
| [`queue-consumer-safe`](skills/queue-consumer-safe/SKILL.md) | Build safe queue consumers: ack semantics, retries, DLQ, idempotent handlers. |
| [`search-indexing`](skills/search-indexing/SKILL.md) | Design app search indexing and sync (FTS or search engine) with relevance checks. |

## Content

| Skill | Description |
|-------|-------------|
| [`blog-post-draft`](skills/blog-post-draft/SKILL.md) | Write a full blog draft from an outline with scannable structure and clear CTA. |
| [`blog-post-outline`](skills/blog-post-outline/SKILL.md) | Outline a blog post: angle, outline, sources, CTA, SEO basics without keyword stuffing. |
| [`case-study-write`](skills/case-study-write/SKILL.md) | Write a case study: problem, approach, results, proof, lessons. |
| [`changelog-user-facing`](skills/changelog-user-facing/SKILL.md) | Turn engineering notes into user-facing release notes people understand. |
| [`community-ama-prep`](skills/community-ama-prep/SKILL.md) | Prepare an AMA: themes, banned topics, moderation, answer bank. |
| [`community-guidelines`](skills/community-guidelines/SKILL.md) | Draft community guidelines: values, allowed/not allowed, enforcement ladder. |
| [`content-audit`](skills/content-audit/SKILL.md) | Audit existing content: freshness, accuracy, duplicates, SEO cannibalization, prune plan. |
| [`content-edit-pass`](skills/content-edit-pass/SKILL.md) | Edit for clarity, structure, and brevity while preserving author voice. |
| [`content-hooks`](skills/content-hooks/SKILL.md) | Generate non-clickbait content hooks for an article or video topic. |
| [`content-localization`](skills/content-localization/SKILL.md) | Prepare content for localization: freeze strings, context notes, do-not-translate list. |
| [`content-repurpose`](skills/content-repurpose/SKILL.md) | Repurpose one long asset into multiple channel formats without sounding duplicate. |
| [`content-strategy`](skills/content-strategy/SKILL.md) | Draft a content strategy: pillars, channels, cadence, voice, measurement. |
| [`documentation-tutorial`](skills/documentation-tutorial/SKILL.md) | Write a task-oriented tutorial with prerequisites, steps, verification, troubleshooting. |
| [`editorial-calendar`](skills/editorial-calendar/SKILL.md) | Build an editorial calendar with themes, owners, statuses, and deadlines. |
| [`email-draft`](skills/email-draft/SKILL.md) | Draft clear emails: purpose first, short paragraphs, explicit ask, tone control. |
| [`fact-check-pass`](skills/fact-check-pass/SKILL.md) | Fact-check a draft: claims, numbers, links, attribution, uncertainty language. |
| [`faq-generation`](skills/faq-generation/SKILL.md) | Generate FAQs from product behavior, support tickets, and objections. |
| [`gift-ideas`](skills/gift-ideas/SKILL.md) | Suggest gift ideas from interests, budget, and constraints (no purchase required). |
| [`headline-options`](skills/headline-options/SKILL.md) | Generate headline options optimized for clarity and curiosity without clickbait. |
| [`interview-questions`](skills/interview-questions/SKILL.md) | Prepare interview questions for customers, candidates, or experts with follow-ups. |
| [`landing-page-copy`](skills/landing-page-copy/SKILL.md) | Write landing page copy: hero, proof, benefits, objections, CTA. |
| [`newsletter-issue`](skills/newsletter-issue/SKILL.md) | Plan and draft a newsletter issue: sections, links, subject lines, preview text. |
| [`personal-crm`](skills/personal-crm/SKILL.md) | Lightweight personal CRM: people notes, last contact, follow-ups (privacy first). |
| [`podcast-episode-plan`](skills/podcast-episode-plan/SKILL.md) | Plan a podcast episode: cold open, segments, guests prep, show notes. |
| [`press-release`](skills/press-release/SKILL.md) | Draft a press release: headline, lede, body, boilerplate, quotes, links. |
| [`release-announcement`](skills/release-announcement/SKILL.md) | Write a product release announcement for blog/email/in-app. |
| [`seo-basics`](skills/seo-basics/SKILL.md) | Apply basic technical SEO checks to marketing/docs pages. |
| [`seo-content-brief`](skills/seo-content-brief/SKILL.md) | Create an SEO content brief: intent, outline, questions to answer, internal links (no stuffing). |
| [`short-form-script`](skills/short-form-script/SKILL.md) | Write a short-form vertical video script under a target duration with on-screen text. |
| [`social-post-pack`](skills/social-post-pack/SKILL.md) | Create a pack of social posts for one announcement across lengths and CTAs. |
| [`style-guide-writing`](skills/style-guide-writing/SKILL.md) | Create a writing style guide: voice, grammar choices, inclusive language, examples. |
| [`transcript-cleanup`](skills/transcript-cleanup/SKILL.md) | Clean a transcript: speakers, paragraphs, filler removal, summary bullets. |
| [`video-chapter-markers`](skills/video-chapter-markers/SKILL.md) | Create chapter markers and titles from a video outline or transcript. |
| [`video-edit-checklist`](skills/video-edit-checklist/SKILL.md) | Checklist for editing a video cut: pacing, audio, captions, exports. |
| [`video-script`](skills/video-script/SKILL.md) | Write a video script with visual column, VO/dialogue, timing, and B-roll notes. |

## Data

| Skill | Description |
|-------|-------------|
| [`csv-data-cleanup`](skills/csv-data-cleanup/SKILL.md) | Profile and clean CSV/TSV data: encoding, types, nulls, dedupe, report. |
| [`encoding-fix`](skills/encoding-fix/SKILL.md) | Fix Unicode/encoding issues (UTF-8, BOM, mislabeled files). |

## Design

| Skill | Description |
|-------|-------------|
| [`a11y-design-review`](skills/a11y-design-review/SKILL.md) | Design-side accessibility review: contrast, focus order, targets, motion, content structure. |
| [`brand-voice-guide`](skills/brand-voice-guide/SKILL.md) | Write a brand voice guide: principles, tone spectrum, examples do/don't. |
| [`call-to-action-copy`](skills/call-to-action-copy/SKILL.md) | Write CTAs matched to funnel stage with friction-aware wording. |
| [`cli-ux-polish`](skills/cli-ux-polish/SKILL.md) | Polish CLI help, flags, exit codes, and non-interactive CI mode. |
| [`color-system`](skills/color-system/SKILL.md) | Define or refine a color system: roles (bg, text, accent, danger), contrast, dark mode. |
| [`concept-art-brief`](skills/concept-art-brief/SKILL.md) | Write a concept art brief: subject, silhouette goals, palette, orthos, deliverables. |
| [`creative-constraint-sprint`](skills/creative-constraint-sprint/SKILL.md) | Run a creative sprint with constraints: timebox, output, critique, ship. |
| [`dashboard-ui-design`](skills/dashboard-ui-design/SKILL.md) | Design dashboards: metrics hierarchy, density, empty/loading/error, drill-down. |
| [`data-viz-design`](skills/data-viz-design/SKILL.md) | Design charts/graphs for honesty: scales, color, annotations, accessibility. |
| [`design-brief`](skills/design-brief/SKILL.md) | Write a design brief: problem, audience, constraints, success metrics, deliverables. |
| [`design-critique-session`](skills/design-critique-session/SKILL.md) | Facilitate a design critique: goals, evidence, actionable feedback, decisions. |
| [`design-handoff`](skills/design-handoff/SKILL.md) | Prepare design-to-engineering handoff: specs, assets, behavior notes, open questions. |
| [`design-system-audit`](skills/design-system-audit/SKILL.md) | Audit UI against an existing design system: drift, one-offs, missing components. |
| [`design-token-sync`](skills/design-token-sync/SKILL.md) | Replace one-off colors/spacing with design tokens / CSS variables already in the project. |
| [`empty-state-design`](skills/empty-state-design/SKILL.md) | Add clear empty/error/no-results states with next actions. |
| [`error-state-design`](skills/error-state-design/SKILL.md) | Design error and recovery UI that is calm, specific, and actionable. |
| [`form-validation-ux`](skills/form-validation-ux/SKILL.md) | Improve form validation and error mapping UX (inline errors, double-submit, a11y). |
| [`iconography-guide`](skills/iconography-guide/SKILL.md) | Create icon rules: optical size, stroke, metaphor consistency, accessibility. |
| [`illustration-brief`](skills/illustration-brief/SKILL.md) | Write an illustration brief: story, style constraints, sizes, deliverables. |
| [`information-architecture`](skills/information-architecture/SKILL.md) | Organize information architecture: nav, labels, findability, card sorting notes. |
| [`interaction-states`](skills/interaction-states/SKILL.md) | Specify full interaction states for components: default, hover, focus, active, disabled, error, loading. |
| [`logo-usage-rules`](skills/logo-usage-rules/SKILL.md) | Document logo usage: clear space, min size, on dark/light, misuse examples. |
| [`moodboard-direction`](skills/moodboard-direction/SKILL.md) | Assemble a written moodboard direction: themes, keywords, references categories (no brand copying). |
| [`motion-design-spec`](skills/motion-design-spec/SKILL.md) | Specify motion: purpose, duration, easing, reduced-motion fallback. |
| [`onboarding-checklist`](skills/onboarding-checklist/SKILL.md) | Design a dismissible first-run checklist that drives activation. |
| [`onboarding-ui-flow`](skills/onboarding-ui-flow/SKILL.md) | Design product onboarding UI: progressive disclosure, skip, value moments. |
| [`pagination-ui`](skills/pagination-ui/SKILL.md) | Implement accessible list pagination or load-more with URL state. |
| [`persona-profile`](skills/persona-profile/SKILL.md) | Build research-backed personas (or proto-personas) with goals, frustrations, contexts. |
| [`photo-art-direction`](skills/photo-art-direction/SKILL.md) | Art-direct photoshoots or stock selection: subject, lighting, crop, usage rights checklist. |
| [`portfolio-case-layout`](skills/portfolio-case-layout/SKILL.md) | Structure a portfolio case study page: problem, process, outcome, images. |
| [`poster-layout`](skills/poster-layout/SKILL.md) | Design a poster layout brief: hierarchy, margins, type, print specs. |
| [`presentation-deck-structure`](skills/presentation-deck-structure/SKILL.md) | Structure a presentation deck: story arc, slide budget, speaker notes. |
| [`print-layout-basics`](skills/print-layout-basics/SKILL.md) | Lay out print-ready pages: margins, bleed, hierarchy, export checklist. |
| [`responsive-design-spec`](skills/responsive-design-spec/SKILL.md) | Specify responsive behavior across breakpoints: reflow, collapse, priority content. |
| [`script-to-storyboard`](skills/script-to-storyboard/SKILL.md) | Turn a script into a shot list / storyboard frames description. |
| [`spacing-layout-grid`](skills/spacing-layout-grid/SKILL.md) | Define spacing scale and layout grid for consistent composition. |
| [`threat-model-lite`](skills/threat-model-lite/SKILL.md) | Write a one-page threat model for a feature: assets, actors, entry points, mitigations. |
| [`thumbnail-concept`](skills/thumbnail-concept/SKILL.md) | Concept thumbnails: focal subject, text overlay limits, contrast, A/B ideas. |
| [`typography-system`](skills/typography-system/SKILL.md) | Set type scale, line height, and pairing rules for UI or editorial layouts. |
| [`ui-kit-inventory`](skills/ui-kit-inventory/SKILL.md) | Inventory UI components and document missing states for a kit. |
| [`usability-test-plan`](skills/usability-test-plan/SKILL.md) | Plan a usability test: tasks, metrics, script, and synthesis template. |
| [`user-journey-map`](skills/user-journey-map/SKILL.md) | Map a user journey: stages, emotions, pain points, opportunities. |
| [`ux-copy-microcopy`](skills/ux-copy-microcopy/SKILL.md) | Write UI microcopy: buttons, errors, empty states, confirmations—clear and human. |
| [`visual-hierarchy-pass`](skills/visual-hierarchy-pass/SKILL.md) | Improve visual hierarchy on a screen: type scale, weight, spacing, focal point. |
| [`wireframe-flow`](skills/wireframe-flow/SKILL.md) | Produce low-fidelity wireframe flows for a user task (text or simple structure). |

## Docs

| Skill | Description |
|-------|-------------|
| [`adr-write`](skills/adr-write/SKILL.md) | Write an Architecture Decision Record for a significant technical choice. |
| [`bug-report-template`](skills/bug-report-template/SKILL.md) | Turn a vague bug into a reproducible report: environment, steps, expected/actual. |
| [`feature-toggle-cleanup`](skills/feature-toggle-cleanup/SKILL.md) | Find stale feature flags and remove dead code paths safely. |
| [`markdown-doc-structure`](skills/markdown-doc-structure/SKILL.md) | Restructure Markdown documentation for clear heading hierarchy and working links. |
| [`retro-notes`](skills/retro-notes/SKILL.md) | Run a lightweight blameless retro and produce concrete action items. |

## Frontend

| Skill | Description |
|-------|-------------|
| [`bundle-size-check`](skills/bundle-size-check/SKILL.md) | Find JS bundle size regressions and propose splits or dependency cuts. |
| [`css-specificity-debug`](skills/css-specificity-debug/SKILL.md) | Debug why a CSS rule loses (specificity, order, layers) and fix cleanly. |
| [`email-template-review`](skills/email-template-review/SKILL.md) | Review HTML emails for client safety, plain-text parts, and injection. |
| [`frontend-a11y`](skills/frontend-a11y/SKILL.md) | Audit and fix high-impact accessibility issues in UI code (names, keyboard, semantics). |
| [`i18n-extract`](skills/i18n-extract/SKILL.md) | Extract UI strings into i18n catalogs and find missing locale keys. |
| [`react-performance`](skills/react-performance/SKILL.md) | Fix common React performance issues after identifying hot components. |
| [`responsive-ui-pass`](skills/responsive-ui-pass/SKILL.md) | Fix layout breakage across mobile/tablet/desktop widths. |

## Gaming

| Skill | Description |
|-------|-------------|
| [`boss-fight-design`](skills/boss-fight-design/SKILL.md) | Design a boss encounter with phases, tells, accessibility options, and rewards. |
| [`combat-feel-tuning`](skills/combat-feel-tuning/SKILL.md) | Tune combat feel: input buffer, hitstop, feedback, and readability without engine-specific jargon lock-in. |
| [`game-accessibility`](skills/game-accessibility/SKILL.md) | Apply game accessibility: colorblind, subtitle, input remapping, difficulty assists. |
| [`game-ai-behavior`](skills/game-ai-behavior/SKILL.md) | Design enemy or NPC AI behaviors: states, perception, difficulty layers. |
| [`game-audio-direction`](skills/game-audio-direction/SKILL.md) | Write audio direction: music intensity layers, SFX categories, mix priorities, implementation checklist. |
| [`game-bug-triage`](skills/game-bug-triage/SKILL.md) | Triage gameplay bugs by repro, severity, blocker status, and regression risk. |
| [`game-build-checklist`](skills/game-build-checklist/SKILL.md) | Pre-ship game build checklist: content locks, known issues, platform cert hygiene (generic). |
| [`game-building-tools`](skills/game-building-tools/SKILL.md) | Design player building tools: snap, validation, budgets, sharing limits. |
| [`game-camera-feel`](skills/game-camera-feel/SKILL.md) | Tune camera feel: follow lag, collision, aim assist notes, comfort options. |
| [`game-cinematic-brief`](skills/game-cinematic-brief/SKILL.md) | Brief a game cinematic: emotion, camera beats, length, audio, handoff to animation. |
| [`game-crafting-system`](skills/game-crafting-system/SKILL.md) | Design crafting: recipes, stations, discovery, economy impact. |
| [`game-design-document`](skills/game-design-document/SKILL.md) | Draft or update a game design document: pillars, loop, progression, risk, and vertical slice scope. |
| [`game-dialogue-pass`](skills/game-dialogue-pass/SKILL.md) | Write or edit game dialogue for voice, subtext, length budgets, and VO notes. |
| [`game-difficulty-design`](skills/game-difficulty-design/SKILL.md) | Design difficulty modes and dynamic assists without breaking the fantasy. |
| [`game-economy-balance`](skills/game-economy-balance/SKILL.md) | Balance a game economy: sinks/faucets, inflation risks, and progression pacing. |
| [`game-input-mapping`](skills/game-input-mapping/SKILL.md) | Design input mapping for keyboard/mouse/gamepad with conflicts and rebinding. |
| [`game-liveops-calendar`](skills/game-liveops-calendar/SKILL.md) | Plan live-ops events: cadence, rewards, economy impact, and rollback. |
| [`game-localization-prep`](skills/game-localization-prep/SKILL.md) | Prepare game strings for localization: keys, variables, length expansion, voice notes. |
| [`game-loop-design`](skills/game-loop-design/SKILL.md) | Design or tighten a core gameplay loop with hooks, rewards, and failure states. |
| [`game-narrative-bible`](skills/game-narrative-bible/SKILL.md) | Create a narrative bible: world rules, characters, tone, continuity constraints. |
| [`game-netcode-notes`](skills/game-netcode-notes/SKILL.md) | Document netcode approach at a design level: prediction, reconciliation, lag compensation caveats. |
| [`game-perf-budget`](skills/game-perf-budget/SKILL.md) | Set performance budgets: frame time, memory, streaming, and content limits. |
| [`game-save-system`](skills/game-save-system/SKILL.md) | Design save/load: slots, versioning, cloud caveats, corruption recovery. |
| [`game-season-pass-structure`](skills/game-season-pass-structure/SKILL.md) | Structure a season pass track: free/premium split ethics, pacing, rewards (no pay-to-win). |
| [`game-telemetry-events`](skills/game-telemetry-events/SKILL.md) | Define gameplay telemetry events for funnels, balance, and crash context (privacy-aware). |
| [`game-tutorial-flow`](skills/game-tutorial-flow/SKILL.md) | Design an onboarding/tutorial that teaches verbs in context with skip options. |
| [`game-ui-hud`](skills/game-ui-hud/SKILL.md) | Design HUD/information architecture: diegetic vs non-diegetic, clutter budget, combat readability. |
| [`game-vfx-checklist`](skills/game-vfx-checklist/SKILL.md) | Define VFX readability and performance budgets for abilities and environments. |
| [`godot-game-engine`](skills/godot-game-engine/SKILL.md) | Build and debug games in Godot Engine 4.7.1: project layout, scenes/nodes, typed GDScript, signals, resources, input, physics, export, and common pitfalls. |
| [`level-design-brief`](skills/level-design-brief/SKILL.md) | Produce a level design brief: layout goals, encounters, pacing, and greybox checklist. |
| [`loot-table-design`](skills/loot-table-design/SKILL.md) | Design loot tables with drop rates, pity systems, and economy safety. |
| [`multiplayer-session-design`](skills/multiplayer-session-design/SKILL.md) | Design multiplayer session flow: matchmaking intent, disconnects, host migration, fairness. |
| [`playtest-protocol`](skills/playtest-protocol/SKILL.md) | Run a structured playtest: goals, tasks, observation notes, and debrief actions. |
| [`progression-curve`](skills/progression-curve/SKILL.md) | Design player progression curves: XP, unlocks, soft gates, and catch-up. |
| [`quest-design`](skills/quest-design/SKILL.md) | Design quests/missions with objectives, gates, rewards, and failure paths. |

## Git

| Skill | Description |
|-------|-------------|
| [`branch-hygiene`](skills/branch-hygiene/SKILL.md) | Prune merged local branches, fetch --prune, and name a clean branch for the next task. |
| [`changelog-entry`](skills/changelog-entry/SKILL.md) | Author a Keep-a-Changelog entry from commits/diff for a version bump or release. |
| [`cherry-pick-commit`](skills/cherry-pick-commit/SKILL.md) | Cherry-pick specific commits onto the current branch with careful conflict resolution. |
| [`codeowners-setup`](skills/codeowners-setup/SKILL.md) | Create CODEOWNERS for critical paths and align with review rules. |
| [`conventional-commits`](skills/conventional-commits/SKILL.md) | Propose or write Conventional Commit messages (feat/fix/docs/chore) matching the diff. |
| [`feature-flag-rollout`](skills/feature-flag-rollout/SKILL.md) | Add a feature flag with default-off rollout, metrics, and removal plan. |
| [`git-bisect-helper`](skills/git-bisect-helper/SKILL.md) | Drive git bisect with a clear good/bad test command to find a regression-introducing commit. |
| [`pr-description`](skills/pr-description/SKILL.md) | Draft a precise PR title and body from branch commits and diff. Use before opening or updating a pull request. |
| [`rebase-onto-main`](skills/rebase-onto-main/SKILL.md) | Update the current branch onto latest main/master via rebase or merge with conflict handling. |
| [`semver-bump`](skills/semver-bump/SKILL.md) | Recommend major/minor/patch from the change set and apply a consistent version bump. |

## Llm

| Skill | Description |
|-------|-------------|
| [`llm-cost-guardrails`](skills/llm-cost-guardrails/SKILL.md) | Add token/cost/latency guardrails and sensible model routing. |
| [`rag-chunking`](skills/rag-chunking/SKILL.md) | Design document chunking and metadata for higher-quality RAG retrieval. |
| [`tool-use-spec`](skills/tool-use-spec/SKILL.md) | Specify safe tool/function contracts: schemas, side effects, confirmations, timeouts. |

## Ops

| Skill | Description |
|-------|-------------|
| [`backup-restore-drill`](skills/backup-restore-drill/SKILL.md) | Plan or execute a non-destructive backup restore drill and document RTO/RPO. |
| [`ci-pipeline-review`](skills/ci-pipeline-review/SKILL.md) | Review CI pipelines for caching, secret hygiene, required checks, and runtime. |
| [`container-image-harden`](skills/container-image-harden/SKILL.md) | Write or harden container image recipes: multi-stage, non-root, pin bases, no secrets in layers. |
| [`container-orchestration-review`](skills/container-orchestration-review/SKILL.md) | Review container orchestration manifests for probes, resources, securityContext, and rollout safety. |
| [`incident-postmortem`](skills/incident-postmortem/SKILL.md) | Write a blameless postmortem with timeline, root cause, and owned actions. |
| [`local-container-stack`](skills/local-container-stack/SKILL.md) | Provide local multi-service containers for local dependencies with healthchecks and sane ports. |
| [`log-level-triage`](skills/log-level-triage/SKILL.md) | Triage production issues from logs: timeline, correlation IDs, dependency health. |
| [`metrics-instrumentation`](skills/metrics-instrumentation/SKILL.md) | Add RED/USE-style metrics without high-cardinality label explosions. |
| [`release-checklist`](skills/release-checklist/SKILL.md) | Execute a pre-release gate: dirty tree, version alignment, tests, docs, remaining ship steps. |
| [`runbook-write`](skills/runbook-write/SKILL.md) | Author an on-call runbook: health checks, common failures, deploy/rollback. |
| [`sla-error-budget`](skills/sla-error-budget/SKILL.md) | Define practical SLIs/SLOs and an error-budget policy for a service. |
| [`structured-logging`](skills/structured-logging/SKILL.md) | Introduce structured logging with levels, correlation IDs, and secret redaction. |
| [`terraform-plan-review`](skills/terraform-plan-review/SKILL.md) | Review infrastructure-as-code plans for destroys, public exposure, and IAM blast radius before apply. |
| [`tracing-spans`](skills/tracing-spans/SKILL.md) | Add distributed tracing spans across request and outbound calls. |

## Other

| Skill | Description |
|-------|-------------|
| [`hello-library`](skills/hello-library/SKILL.md) | Demo community skill from the Remedy Skills Library. Prints a short greeting via its script — safe for quarantine/trust smoke tests. |
| [`license-compliance`](skills/license-compliance/SKILL.md) | Summarize third-party licenses and flag strong copyleft risk for distribution. |

## Personal

| Skill | Description |
|-------|-------------|
| [`accountability-partnership`](skills/accountability-partnership/SKILL.md) | Set up an accountability partnership: cadence, metrics, check-in template. |
| [`boundary-setting`](skills/boundary-setting/SKILL.md) | Help articulate personal or work boundaries with scripts and follow-through. |
| [`budget-snapshot`](skills/budget-snapshot/SKILL.md) | Create a simple budget snapshot: income, fixed costs, variable, goals (no bank logins). |
| [`caregiver-checklist`](skills/caregiver-checklist/SKILL.md) | Build a caregiver checklist for appointments, meds schedules placeholders, and notes (non-clinical). |
| [`celebration-plan`](skills/celebration-plan/SKILL.md) | Plan a celebration (birthday, launch, milestone): constraints, program, budget. |
| [`conflict-deescalation`](skills/conflict-deescalation/SKILL.md) | Prepare de-escalation language for personal or workplace conflict (non-clinical). |
| [`daily-planning`](skills/daily-planning/SKILL.md) | Build a realistic daily plan from priorities, calendar constraints, and energy. |
| [`data-deletion-user`](skills/data-deletion-user/SKILL.md) | Implement account deletion with re-auth, cascade/anonymize, and session revoke. |
| [`data-export-user`](skills/data-export-user/SKILL.md) | Implement authenticated user data export with async processing if large. |
| [`decision-log-personal`](skills/decision-log-personal/SKILL.md) | Log a personal or work decision with options, criteria, choice, review date. |
| [`difficult-conversation`](skills/difficult-conversation/SKILL.md) | Prepare a difficult conversation: goals, script, boundaries, outcomes. |
| [`digital-declutter`](skills/digital-declutter/SKILL.md) | Plan a digital declutter: files, inbox, photos, subscriptions—with batch rules. |
| [`event-planning`](skills/event-planning/SKILL.md) | Plan an event: guest list, venue constraints, timeline, budget, day-of run of show. |
| [`expense-categorize`](skills/expense-categorize/SKILL.md) | Categorize a list of expenses and summarize by category with outliers. |
| [`family-logistics`](skills/family-logistics/SKILL.md) | Coordinate family logistics: shared calendar norms, chores, handoffs, emergency info sheet. |
| [`flashcard-set`](skills/flashcard-set/SKILL.md) | Create flashcards (Q/A) from notes for spaced practice. |
| [`focus-block`](skills/focus-block/SKILL.md) | Design a deep-work focus block: environment, timers, distraction rules, shutdown. |
| [`goal-breakdown`](skills/goal-breakdown/SKILL.md) | Break a large goal into milestones, weekly outcomes, and first concrete actions. |
| [`gratitude-practice`](skills/gratitude-practice/SKILL.md) | Set up a short gratitude practice with prompts and streak-free consistency tips. |
| [`habit-design`](skills/habit-design/SKILL.md) | Design a habit loop: cue, routine, reward, tracking, restart plan. |
| [`home-project-plan`](skills/home-project-plan/SKILL.md) | Plan a home project: scope, materials, steps, safety, contingency. |
| [`household-chores-plan`](skills/household-chores-plan/SKILL.md) | Create a household chore plan with cadence and ownership. |
| [`interview-prep`](skills/interview-prep/SKILL.md) | Prepare for an interview: stories (STAR), questions to ask, research brief. |
| [`job-application-tailor`](skills/job-application-tailor/SKILL.md) | Tailor a resume bullet set and cover note to a job description without fabricating experience. |
| [`journal-prompts`](skills/journal-prompts/SKILL.md) | Provide journal prompts for reflection, goals, or stress processing (not therapy). |
| [`learning-curriculum`](skills/learning-curriculum/SKILL.md) | Build a learning curriculum for a skill: modules, resources types, practice projects. |
| [`meeting-agenda`](skills/meeting-agenda/SKILL.md) | Create a meeting agenda with purpose, topics, times, and decisions needed. |
| [`meeting-notes`](skills/meeting-notes/SKILL.md) | Turn discussion into structured notes: decisions, actions, owners, dates. |
| [`message-draft-personal`](skills/message-draft-personal/SKILL.md) | Draft personal messages (thanks, apology, invite, check-in) with tone options. |
| [`money-calculations`](skills/money-calculations/SKILL.md) | Implement money math with integers/decimals, explicit rounding, and currency codes. |
| [`morning-shutdown-rituals`](skills/morning-shutdown-rituals/SKILL.md) | Design morning and end-of-day rituals that fit a real schedule. |
| [`move-house-plan`](skills/move-house-plan/SKILL.md) | Plan a household move: timeline, inventory, vendors, change-of-address checklist. |
| [`negotiation-prep`](skills/negotiation-prep/SKILL.md) | Prepare a negotiation: BATNA, range, script, concessions. |
| [`packing-list`](skills/packing-list/SKILL.md) | Generate a packing list by trip type, climate, and activities. |
| [`password-hygiene`](skills/password-hygiene/SKILL.md) | Personal password hygiene checklist: unique passwords, manager use, 2FA—without handling secrets. |
| [`personal-inventory`](skills/personal-inventory/SKILL.md) | Run a personal inventory session: commitments, energy, obligations, free capacity. |
| [`personal-okr`](skills/personal-okr/SKILL.md) | Write personal OKRs: objective, key results, weekly check-ins. |
| [`personal-values-exercise`](skills/personal-values-exercise/SKILL.md) | Facilitate a values clarification exercise and translate into weekly choices. |
| [`pet-care-routine`](skills/pet-care-routine/SKILL.md) | Create a pet care routine: feeding, walks, meds placeholders, emergency contacts. |
| [`pii-data-handling`](skills/pii-data-handling/SKILL.md) | Minimize and protect PII: access, logs redaction, retention, deletion paths. |
| [`priority-matrix`](skills/priority-matrix/SKILL.md) | Sort tasks with urgency/importance and recommend what to defer or drop. |
| [`privacy-checkup`](skills/privacy-checkup/SKILL.md) | Walk through a personal privacy checkup: app permissions, sharing, data downloads. |
| [`reading-notes`](skills/reading-notes/SKILL.md) | Produce structured reading notes: summary, key ideas, quotes, actions. |
| [`recipe-plan-meals`](skills/recipe-plan-meals/SKILL.md) | Plan meals for N days given constraints (time, diet, servings) with shopping list. |
| [`reminder-system`](skills/reminder-system/SKILL.md) | Design a reminder system: what belongs on calendar vs tasks vs checklists. |
| [`research-digest`](skills/research-digest/SKILL.md) | Digest multiple sources into a brief with citations and confidence levels. |
| [`second-brain-notes`](skills/second-brain-notes/SKILL.md) | Organize notes into a simple personal knowledge system: inbox, projects, areas, archives. |
| [`sleep-routine`](skills/sleep-routine/SKILL.md) | Design a sleep routine: wind-down, environment, schedule consistency (general wellness). |
| [`subscription-audit`](skills/subscription-audit/SKILL.md) | Audit subscriptions from a user-provided list: keep, cancel, downgrade recommendations. |
| [`time-audit`](skills/time-audit/SKILL.md) | Guide a time audit: log categories, find leaks, redesign week. |
| [`travel-itinerary`](skills/travel-itinerary/SKILL.md) | Build a travel itinerary: logistics, buffers, offline notes, packing constraints. |
| [`user-story-split`](skills/user-story-split/SKILL.md) | Split an epic into vertical, testable user stories with acceptance criteria. |
| [`weekly-review`](skills/weekly-review/SKILL.md) | Run a weekly review: wins, open loops, priorities, calendar look-ahead. |
| [`workout-plan-basic`](skills/workout-plan-basic/SKILL.md) | Draft a basic workout plan with warm-up, main work, recovery (not medical advice). |

## Security

| Skill | Description |
|-------|-------------|
| [`audit-log-design`](skills/audit-log-design/SKILL.md) | Design audit logs for sensitive admin/user actions. |
| [`auth-session-review`](skills/auth-session-review/SKILL.md) | Review login, session, JWT, or OAuth handling for common authentication flaws. |
| [`cors-review`](skills/cors-review/SKILL.md) | Review CORS settings for overly broad origins and credentialed cross-origin risks. |
| [`dependency-audit`](skills/dependency-audit/SKILL.md) | Audit project dependencies for known vulnerabilities and outdated high-risk packages. |
| [`enterprise-sso-notes`](skills/enterprise-sso-notes/SKILL.md) | Enterprise SAML SSO integration checklist (metadata, assertions, JIT). |
| [`federated-login-setup`](skills/federated-login-setup/SKILL.md) | Configure OAuth/OIDC clients correctly (PKCE, redirects, scopes, token storage). |
| [`file-upload-secure`](skills/file-upload-secure/SKILL.md) | Harden file uploads: authz, size/type checks, safe storage keys, download posture. |
| [`multi-tenant-isolation`](skills/multi-tenant-isolation/SKILL.md) | Audit multi-tenant isolation for cross-tenant data leaks. |
| [`owasp-web-checklist`](skills/owasp-web-checklist/SKILL.md) | Security-review a web change against practical OWASP-style controls (injection, XSS, authz, CSRF, SSRF). |
| [`permissions-matrix`](skills/permissions-matrix/SKILL.md) | Build a role×action permission matrix and verify server enforcement. |
| [`rate-limit-design`](skills/rate-limit-design/SKILL.md) | Design rate limits for public/auth endpoints with clear 429 behavior. |
| [`regex-safety`](skills/regex-safety/SKILL.md) | Review regexes for ReDoS and correctness on untrusted input. |
| [`sbom-generate`](skills/sbom-generate/SKILL.md) | Generate a Software Bill of Materials (CycloneDX/SPDX) using available tooling. |
| [`secret-scan-guidance`](skills/secret-scan-guidance/SKILL.md) | Find likely leaked secrets in the tree and guide rotation without printing secret values. |
| [`sql-query-review`](skills/sql-query-review/SKILL.md) | Review SQL/ORM usage for N+1, injection, and missing indexes. |
| [`webhook-verify`](skills/webhook-verify/SKILL.md) | Implement or review webhook receivers: signature verification, raw body, replay protection, idempotency. |

## Testing

| Skill | Description |
|-------|-------------|
| [`acceptance-criteria`](skills/acceptance-criteria/SKILL.md) | Write testable acceptance criteria for a feature or bugfix. |
| [`benchmark-micro`](skills/benchmark-micro/SKILL.md) | Create a trustworthy microbenchmark for a profiled hot function. |
| [`browser-automation-safe`](skills/browser-automation-safe/SKILL.md) | Automate browser checks with browser-test best practices (stable selectors, no fixed sleeps). |
| [`contract-test-api`](skills/contract-test-api/SKILL.md) | Add consumer/provider contract tests so API changes don't silently break clients. |
| [`coverage-gap`](skills/coverage-gap/SKILL.md) | Find coverage gaps on changed critical code and add focused tests. |
| [`e2e-smoke`](skills/e2e-smoke/SKILL.md) | Define or run a short end-to-end smoke path for the critical user journey. |
| [`fixture-factory`](skills/fixture-factory/SKILL.md) | Create maintainable test factories/fixtures instead of brittle object literals everywhere. |
| [`flaky-test-triage`](skills/flaky-test-triage/SKILL.md) | Reproduce and fix flaky tests: races, time, order dependence, shared state. |
| [`load-test-plan`](skills/load-test-plan/SKILL.md) | Design and run a minimal load test on critical endpoints with clear stop conditions. |
| [`prompt-eval-harness`](skills/prompt-eval-harness/SKILL.md) | Build a small regression suite for prompts/agent behaviors with deterministic checks. |
| [`snapshot-test-discipline`](skills/snapshot-test-discipline/SKILL.md) | Tame snapshot tests: reduce scope, review diffs, avoid golden files that hide bugs. |
| [`test-selection`](skills/test-selection/SKILL.md) | Select and run the smallest high-value tests for the current change set. |
| [`visual-regression-setup`](skills/visual-regression-setup/SKILL.md) | Add a small visual regression set for critical screens with stable snapshots. |

## Tooling

| Skill | Description |
|-------|-------------|
| [`algorithmic-complexity`](skills/algorithmic-complexity/SKILL.md) | Find accidental quadratic patterns and propose better data structures. |
| [`cross-platform-paths`](skills/cross-platform-paths/SKILL.md) | Fix Windows/macOS/Linux path bugs using pathlib and safe joins. |
| [`deadlock-debug`](skills/deadlock-debug/SKILL.md) | Debug deadlocks via stack dumps and lock-order fixes. |
| [`dev-environment-container`](skills/dev-environment-container/SKILL.md) | Add a dev environment container for reproducible contributor environments. |
| [`editorconfig-setup`](skills/editorconfig-setup/SKILL.md) | Add .editorconfig aligned with project formatters. |
| [`go-module-hygiene`](skills/go-module-hygiene/SKILL.md) | Tidy Go modules and verify reproducible builds/tests. |
| [`http-debugging`](skills/http-debugging/SKILL.md) | Debug HTTP failures with curl -v, status/headers, and TLS basics (redact auth). |
| [`makefile-tasks`](skills/makefile-tasks/SKILL.md) | Add Makefile/task targets wrapping real project commands (setup/test/lint/run). |
| [`memory-leak-hunt`](skills/memory-leak-hunt/SKILL.md) | Find memory growth in long-running services via profiles and retained allocations. |
| [`monorepo-task-runner`](skills/monorepo-task-runner/SKILL.md) | Fix monorepo task graphs (turbo/nx/pnpm) for filtered build/test. |
| [`nodejs-upgrade`](skills/nodejs-upgrade/SKILL.md) | Plan and execute a Node.js runtime upgrade with CI and dependency checks. |
| [`perf-profile-cpu`](skills/perf-profile-cpu/SKILL.md) | Capture and interpret a CPU profile to find hot functions before optimizing. |
| [`pre-commit-hooks`](skills/pre-commit-hooks/SKILL.md) | Configure pre-commit/husky hooks for format, lint, and optional secret scan. |
| [`python-packaging`](skills/python-packaging/SKILL.md) | Package Python projects with pyproject entry points and a clean build/install check. |
| [`python-typing-pass`](skills/python-typing-pass/SKILL.md) | Raise typing quality on selected Python modules until mypy/pyright is clean. |
| [`rust-clippy-fix`](skills/rust-clippy-fix/SKILL.md) | Run Clippy and fix correctness-oriented lints; re-test. |
| [`ts-strict-migration`](skills/ts-strict-migration/SKILL.md) | Incrementally enable TypeScript strictness without a big-bang freeze. |
| [`websocket-debug`](skills/websocket-debug/SKILL.md) | Diagnose WebSocket handshake, auth, ping/pong, and reconnect storms. |

## Alphabetical index

- [`a11y-design-review`](skills/a11y-design-review/SKILL.md) — Design-side accessibility review: contrast, focus order, targets, motion, content structure.
- [`acceptance-criteria`](skills/acceptance-criteria/SKILL.md) — Write testable acceptance criteria for a feature or bugfix.
- [`accountability-partnership`](skills/accountability-partnership/SKILL.md) — Set up an accountability partnership: cadence, metrics, check-in template.
- [`adr-write`](skills/adr-write/SKILL.md) — Write an Architecture Decision Record for a significant technical choice.
- [`algorithmic-complexity`](skills/algorithmic-complexity/SKILL.md) — Find accidental quadratic patterns and propose better data structures.
- [`api-client-sdk`](skills/api-client-sdk/SKILL.md) — Generate or refresh a typed client from OpenAPI for consumers.
- [`api-contract-review`](skills/api-contract-review/SKILL.md) — Review HTTP APIs for validation, authz, status codes, pagination, and error shape consistency.
- [`audit-log-design`](skills/audit-log-design/SKILL.md) — Design audit logs for sensitive admin/user actions.
- [`auth-session-review`](skills/auth-session-review/SKILL.md) — Review login, session, JWT, or OAuth handling for common authentication flaws.
- [`background-job-ui`](skills/background-job-ui/SKILL.md) — Expose long-running job progress/status to users with authz and safe errors.
- [`backup-restore-drill`](skills/backup-restore-drill/SKILL.md) — Plan or execute a non-destructive backup restore drill and document RTO/RPO.
- [`backward-compat-api`](skills/backward-compat-api/SKILL.md) — Plan backward-compatible API evolution and deprecation windows.
- [`benchmark-micro`](skills/benchmark-micro/SKILL.md) — Create a trustworthy microbenchmark for a profiled hot function.
- [`blog-post-draft`](skills/blog-post-draft/SKILL.md) — Write a full blog draft from an outline with scannable structure and clear CTA.
- [`blog-post-outline`](skills/blog-post-outline/SKILL.md) — Outline a blog post: angle, outline, sources, CTA, SEO basics without keyword stuffing.
- [`boss-fight-design`](skills/boss-fight-design/SKILL.md) — Design a boss encounter with phases, tells, accessibility options, and rewards.
- [`boundary-setting`](skills/boundary-setting/SKILL.md) — Help articulate personal or work boundaries with scripts and follow-through.
- [`branch-hygiene`](skills/branch-hygiene/SKILL.md) — Prune merged local branches, fetch --prune, and name a clean branch for the next task.
- [`brand-voice-guide`](skills/brand-voice-guide/SKILL.md) — Write a brand voice guide: principles, tone spectrum, examples do/don't.
- [`browser-automation-safe`](skills/browser-automation-safe/SKILL.md) — Automate browser checks with browser-test best practices (stable selectors, no fixed sleeps).
- [`budget-snapshot`](skills/budget-snapshot/SKILL.md) — Create a simple budget snapshot: income, fixed costs, variable, goals (no bank logins).
- [`bug-report-template`](skills/bug-report-template/SKILL.md) — Turn a vague bug into a reproducible report: environment, steps, expected/actual.
- [`bundle-size-check`](skills/bundle-size-check/SKILL.md) — Find JS bundle size regressions and propose splits or dependency cuts.
- [`cache-invalidation`](skills/cache-invalidation/SKILL.md) — Design cache keys and invalidation to prevent stale reads and stampedes.
- [`call-to-action-copy`](skills/call-to-action-copy/SKILL.md) — Write CTAs matched to funnel stage with friction-aware wording.
- [`caregiver-checklist`](skills/caregiver-checklist/SKILL.md) — Build a caregiver checklist for appointments, meds schedules placeholders, and notes (non-clinical).
- [`case-study-write`](skills/case-study-write/SKILL.md) — Write a case study: problem, approach, results, proof, lessons.
- [`celebration-plan`](skills/celebration-plan/SKILL.md) — Plan a celebration (birthday, launch, milestone): constraints, program, budget.
- [`changelog-entry`](skills/changelog-entry/SKILL.md) — Author a Keep-a-Changelog entry from commits/diff for a version bump or release.
- [`changelog-user-facing`](skills/changelog-user-facing/SKILL.md) — Turn engineering notes into user-facing release notes people understand.
- [`cherry-pick-commit`](skills/cherry-pick-commit/SKILL.md) — Cherry-pick specific commits onto the current branch with careful conflict resolution.
- [`ci-pipeline-review`](skills/ci-pipeline-review/SKILL.md) — Review CI pipelines for caching, secret hygiene, required checks, and runtime.
- [`cli-ux-polish`](skills/cli-ux-polish/SKILL.md) — Polish CLI help, flags, exit codes, and non-interactive CI mode.
- [`codeowners-setup`](skills/codeowners-setup/SKILL.md) — Create CODEOWNERS for critical paths and align with review rules.
- [`color-system`](skills/color-system/SKILL.md) — Define or refine a color system: roles (bg, text, accent, danger), contrast, dark mode.
- [`combat-feel-tuning`](skills/combat-feel-tuning/SKILL.md) — Tune combat feel: input buffer, hitstop, feedback, and readability without engine-specific jargon lock-in.
- [`community-ama-prep`](skills/community-ama-prep/SKILL.md) — Prepare an AMA: themes, banned topics, moderation, answer bank.
- [`community-guidelines`](skills/community-guidelines/SKILL.md) — Draft community guidelines: values, allowed/not allowed, enforcement ladder.
- [`concept-art-brief`](skills/concept-art-brief/SKILL.md) — Write a concept art brief: subject, silhouette goals, palette, orthos, deliverables.
- [`conflict-deescalation`](skills/conflict-deescalation/SKILL.md) — Prepare de-escalation language for personal or workplace conflict (non-clinical).
- [`container-image-harden`](skills/container-image-harden/SKILL.md) — Write or harden container image recipes: multi-stage, non-root, pin bases, no secrets in layers.
- [`container-orchestration-review`](skills/container-orchestration-review/SKILL.md) — Review container orchestration manifests for probes, resources, securityContext, and rollout safety.
- [`content-audit`](skills/content-audit/SKILL.md) — Audit existing content: freshness, accuracy, duplicates, SEO cannibalization, prune plan.
- [`content-edit-pass`](skills/content-edit-pass/SKILL.md) — Edit for clarity, structure, and brevity while preserving author voice.
- [`content-hooks`](skills/content-hooks/SKILL.md) — Generate non-clickbait content hooks for an article or video topic.
- [`content-localization`](skills/content-localization/SKILL.md) — Prepare content for localization: freeze strings, context notes, do-not-translate list.
- [`content-repurpose`](skills/content-repurpose/SKILL.md) — Repurpose one long asset into multiple channel formats without sounding duplicate.
- [`content-strategy`](skills/content-strategy/SKILL.md) — Draft a content strategy: pillars, channels, cadence, voice, measurement.
- [`contract-test-api`](skills/contract-test-api/SKILL.md) — Add consumer/provider contract tests so API changes don't silently break clients.
- [`conventional-commits`](skills/conventional-commits/SKILL.md) — Propose or write Conventional Commit messages (feat/fix/docs/chore) matching the diff.
- [`cors-review`](skills/cors-review/SKILL.md) — Review CORS settings for overly broad origins and credentialed cross-origin risks.
- [`coverage-gap`](skills/coverage-gap/SKILL.md) — Find coverage gaps on changed critical code and add focused tests.
- [`creative-constraint-sprint`](skills/creative-constraint-sprint/SKILL.md) — Run a creative sprint with constraints: timebox, output, critique, ship.
- [`cron-job-design`](skills/cron-job-design/SKILL.md) — Design scheduled jobs with overlap locks, idempotency, and failure alerts.
- [`cross-platform-paths`](skills/cross-platform-paths/SKILL.md) — Fix Windows/macOS/Linux path bugs using pathlib and safe joins.
- [`css-specificity-debug`](skills/css-specificity-debug/SKILL.md) — Debug why a CSS rule loses (specificity, order, layers) and fix cleanly.
- [`csv-data-cleanup`](skills/csv-data-cleanup/SKILL.md) — Profile and clean CSV/TSV data: encoding, types, nulls, dedupe, report.
- [`daily-planning`](skills/daily-planning/SKILL.md) — Build a realistic daily plan from priorities, calendar constraints, and energy.
- [`dashboard-ui-design`](skills/dashboard-ui-design/SKILL.md) — Design dashboards: metrics hierarchy, density, empty/loading/error, drill-down.
- [`data-deletion-user`](skills/data-deletion-user/SKILL.md) — Implement account deletion with re-auth, cascade/anonymize, and session revoke.
- [`data-export-user`](skills/data-export-user/SKILL.md) — Implement authenticated user data export with async processing if large.
- [`data-viz-design`](skills/data-viz-design/SKILL.md) — Design charts/graphs for honesty: scales, color, annotations, accessibility.
- [`datetime-timezone`](skills/datetime-timezone/SKILL.md) — Fix datetime bugs by storing UTC and converting only at the edge.
- [`db-migration-safe`](skills/db-migration-safe/SKILL.md) — Write or review DB migrations using expand/contract safety and rollback notes.
- [`deadlock-debug`](skills/deadlock-debug/SKILL.md) — Debug deadlocks via stack dumps and lock-order fixes.
- [`decision-log-personal`](skills/decision-log-personal/SKILL.md) — Log a personal or work decision with options, criteria, choice, review date.
- [`dependency-audit`](skills/dependency-audit/SKILL.md) — Audit project dependencies for known vulnerabilities and outdated high-risk packages.
- [`design-brief`](skills/design-brief/SKILL.md) — Write a design brief: problem, audience, constraints, success metrics, deliverables.
- [`design-critique-session`](skills/design-critique-session/SKILL.md) — Facilitate a design critique: goals, evidence, actionable feedback, decisions.
- [`design-handoff`](skills/design-handoff/SKILL.md) — Prepare design-to-engineering handoff: specs, assets, behavior notes, open questions.
- [`design-system-audit`](skills/design-system-audit/SKILL.md) — Audit UI against an existing design system: drift, one-offs, missing components.
- [`design-token-sync`](skills/design-token-sync/SKILL.md) — Replace one-off colors/spacing with design tokens / CSS variables already in the project.
- [`dev-environment-container`](skills/dev-environment-container/SKILL.md) — Add a dev environment container for reproducible contributor environments.
- [`difficult-conversation`](skills/difficult-conversation/SKILL.md) — Prepare a difficult conversation: goals, script, boundaries, outcomes.
- [`digital-declutter`](skills/digital-declutter/SKILL.md) — Plan a digital declutter: files, inbox, photos, subscriptions—with batch rules.
- [`docs-api-examples`](skills/docs-api-examples/SKILL.md) — Add runnable request/response examples to API docs for the hardest endpoints.
- [`documentation-tutorial`](skills/documentation-tutorial/SKILL.md) — Write a task-oriented tutorial with prerequisites, steps, verification, troubleshooting.
- [`e2e-smoke`](skills/e2e-smoke/SKILL.md) — Define or run a short end-to-end smoke path for the critical user journey.
- [`editorconfig-setup`](skills/editorconfig-setup/SKILL.md) — Add .editorconfig aligned with project formatters.
- [`editorial-calendar`](skills/editorial-calendar/SKILL.md) — Build an editorial calendar with themes, owners, statuses, and deadlines.
- [`email-draft`](skills/email-draft/SKILL.md) — Draft clear emails: purpose first, short paragraphs, explicit ask, tone control.
- [`email-template-review`](skills/email-template-review/SKILL.md) — Review HTML emails for client safety, plain-text parts, and injection.
- [`empty-state-design`](skills/empty-state-design/SKILL.md) — Add clear empty/error/no-results states with next actions.
- [`encoding-fix`](skills/encoding-fix/SKILL.md) — Fix Unicode/encoding issues (UTF-8, BOM, mislabeled files).
- [`enterprise-sso-notes`](skills/enterprise-sso-notes/SKILL.md) — Enterprise SAML SSO integration checklist (metadata, assertions, JIT).
- [`env-config-12factor`](skills/env-config-12factor/SKILL.md) — Refactor configuration to env-based 12-factor style with validated startup.
- [`error-state-design`](skills/error-state-design/SKILL.md) — Design error and recovery UI that is calm, specific, and actionable.
- [`event-planning`](skills/event-planning/SKILL.md) — Plan an event: guest list, venue constraints, timeline, budget, day-of run of show.
- [`expense-categorize`](skills/expense-categorize/SKILL.md) — Categorize a list of expenses and summarize by category with outliers.
- [`fact-check-pass`](skills/fact-check-pass/SKILL.md) — Fact-check a draft: claims, numbers, links, attribution, uncertainty language.
- [`family-logistics`](skills/family-logistics/SKILL.md) — Coordinate family logistics: shared calendar norms, chores, handoffs, emergency info sheet.
- [`faq-generation`](skills/faq-generation/SKILL.md) — Generate FAQs from product behavior, support tickets, and objections.
- [`feature-flag-rollout`](skills/feature-flag-rollout/SKILL.md) — Add a feature flag with default-off rollout, metrics, and removal plan.
- [`feature-toggle-cleanup`](skills/feature-toggle-cleanup/SKILL.md) — Find stale feature flags and remove dead code paths safely.
- [`federated-login-setup`](skills/federated-login-setup/SKILL.md) — Configure OAuth/OIDC clients correctly (PKCE, redirects, scopes, token storage).
- [`file-upload-secure`](skills/file-upload-secure/SKILL.md) — Harden file uploads: authz, size/type checks, safe storage keys, download posture.
- [`fixture-factory`](skills/fixture-factory/SKILL.md) — Create maintainable test factories/fixtures instead of brittle object literals everywhere.
- [`flaky-test-triage`](skills/flaky-test-triage/SKILL.md) — Reproduce and fix flaky tests: races, time, order dependence, shared state.
- [`flashcard-set`](skills/flashcard-set/SKILL.md) — Create flashcards (Q/A) from notes for spaced practice.
- [`focus-block`](skills/focus-block/SKILL.md) — Design a deep-work focus block: environment, timers, distraction rules, shutdown.
- [`form-validation-ux`](skills/form-validation-ux/SKILL.md) — Improve form validation and error mapping UX (inline errors, double-submit, a11y).
- [`frontend-a11y`](skills/frontend-a11y/SKILL.md) — Audit and fix high-impact accessibility issues in UI code (names, keyboard, semantics).
- [`game-accessibility`](skills/game-accessibility/SKILL.md) — Apply game accessibility: colorblind, subtitle, input remapping, difficulty assists.
- [`game-ai-behavior`](skills/game-ai-behavior/SKILL.md) — Design enemy or NPC AI behaviors: states, perception, difficulty layers.
- [`game-audio-direction`](skills/game-audio-direction/SKILL.md) — Write audio direction: music intensity layers, SFX categories, mix priorities, implementation checklist.
- [`game-bug-triage`](skills/game-bug-triage/SKILL.md) — Triage gameplay bugs by repro, severity, blocker status, and regression risk.
- [`game-build-checklist`](skills/game-build-checklist/SKILL.md) — Pre-ship game build checklist: content locks, known issues, platform cert hygiene (generic).
- [`game-building-tools`](skills/game-building-tools/SKILL.md) — Design player building tools: snap, validation, budgets, sharing limits.
- [`game-camera-feel`](skills/game-camera-feel/SKILL.md) — Tune camera feel: follow lag, collision, aim assist notes, comfort options.
- [`game-cinematic-brief`](skills/game-cinematic-brief/SKILL.md) — Brief a game cinematic: emotion, camera beats, length, audio, handoff to animation.
- [`game-crafting-system`](skills/game-crafting-system/SKILL.md) — Design crafting: recipes, stations, discovery, economy impact.
- [`game-design-document`](skills/game-design-document/SKILL.md) — Draft or update a game design document: pillars, loop, progression, risk, and vertical slice scope.
- [`game-dialogue-pass`](skills/game-dialogue-pass/SKILL.md) — Write or edit game dialogue for voice, subtext, length budgets, and VO notes.
- [`game-difficulty-design`](skills/game-difficulty-design/SKILL.md) — Design difficulty modes and dynamic assists without breaking the fantasy.
- [`game-economy-balance`](skills/game-economy-balance/SKILL.md) — Balance a game economy: sinks/faucets, inflation risks, and progression pacing.
- [`game-input-mapping`](skills/game-input-mapping/SKILL.md) — Design input mapping for keyboard/mouse/gamepad with conflicts and rebinding.
- [`game-liveops-calendar`](skills/game-liveops-calendar/SKILL.md) — Plan live-ops events: cadence, rewards, economy impact, and rollback.
- [`game-localization-prep`](skills/game-localization-prep/SKILL.md) — Prepare game strings for localization: keys, variables, length expansion, voice notes.
- [`game-loop-design`](skills/game-loop-design/SKILL.md) — Design or tighten a core gameplay loop with hooks, rewards, and failure states.
- [`game-narrative-bible`](skills/game-narrative-bible/SKILL.md) — Create a narrative bible: world rules, characters, tone, continuity constraints.
- [`game-netcode-notes`](skills/game-netcode-notes/SKILL.md) — Document netcode approach at a design level: prediction, reconciliation, lag compensation caveats.
- [`game-perf-budget`](skills/game-perf-budget/SKILL.md) — Set performance budgets: frame time, memory, streaming, and content limits.
- [`game-save-system`](skills/game-save-system/SKILL.md) — Design save/load: slots, versioning, cloud caveats, corruption recovery.
- [`game-season-pass-structure`](skills/game-season-pass-structure/SKILL.md) — Structure a season pass track: free/premium split ethics, pacing, rewards (no pay-to-win).
- [`game-telemetry-events`](skills/game-telemetry-events/SKILL.md) — Define gameplay telemetry events for funnels, balance, and crash context (privacy-aware).
- [`game-tutorial-flow`](skills/game-tutorial-flow/SKILL.md) — Design an onboarding/tutorial that teaches verbs in context with skip options.
- [`game-ui-hud`](skills/game-ui-hud/SKILL.md) — Design HUD/information architecture: diegetic vs non-diegetic, clutter budget, combat readability.
- [`game-vfx-checklist`](skills/game-vfx-checklist/SKILL.md) — Define VFX readability and performance budgets for abilities and environments.
- [`gift-ideas`](skills/gift-ideas/SKILL.md) — Suggest gift ideas from interests, budget, and constraints (no purchase required).
- [`git-bisect-helper`](skills/git-bisect-helper/SKILL.md) — Drive git bisect with a clear good/bad test command to find a regression-introducing commit.
- [`go-module-hygiene`](skills/go-module-hygiene/SKILL.md) — Tidy Go modules and verify reproducible builds/tests.
- [`goal-breakdown`](skills/goal-breakdown/SKILL.md) — Break a large goal into milestones, weekly outcomes, and first concrete actions.
- [`godot-game-engine`](skills/godot-game-engine/SKILL.md) — Build and debug games in Godot Engine 4.7.1: project layout, scenes/nodes, typed GDScript, signals, resources, input, ph…
- [`graceful-shutdown`](skills/graceful-shutdown/SKILL.md) — Implement SIGTERM-aware graceful shutdown and drain for servers/workers.
- [`graphql-schema-review`](skills/graphql-schema-review/SKILL.md) — Review GraphQL schemas/resolvers for authz, N+1, pagination, and deprecations.
- [`gratitude-practice`](skills/gratitude-practice/SKILL.md) — Set up a short gratitude practice with prompts and streak-free consistency tips.
- [`grpc-api-design`](skills/grpc-api-design/SKILL.md) — Design/review gRPC protos with versioning, deadlines, and idempotency.
- [`habit-design`](skills/habit-design/SKILL.md) — Design a habit loop: cue, routine, reward, tracking, restart plan.
- [`headline-options`](skills/headline-options/SKILL.md) — Generate headline options optimized for clarity and curiosity without clickbait.
- [`health-endpoints`](skills/health-endpoints/SKILL.md) — Add liveness vs readiness endpoints with appropriate dependency checks.
- [`hello-library`](skills/hello-library/SKILL.md) — Demo community skill from the Remedy Skills Library. Prints a short greeting via its script — safe for quarantine/trust …
- [`home-project-plan`](skills/home-project-plan/SKILL.md) — Plan a home project: scope, materials, steps, safety, contingency.
- [`household-chores-plan`](skills/household-chores-plan/SKILL.md) — Create a household chore plan with cadence and ownership.
- [`http-debugging`](skills/http-debugging/SKILL.md) — Debug HTTP failures with curl -v, status/headers, and TLS basics (redact auth).
- [`i18n-extract`](skills/i18n-extract/SKILL.md) — Extract UI strings into i18n catalogs and find missing locale keys.
- [`iconography-guide`](skills/iconography-guide/SKILL.md) — Create icon rules: optical size, stroke, metaphor consistency, accessibility.
- [`idempotent-api`](skills/idempotent-api/SKILL.md) — Make a mutating endpoint safely retryable with idempotency keys.
- [`illustration-brief`](skills/illustration-brief/SKILL.md) — Write an illustration brief: story, style constraints, sizes, deliverables.
- [`incident-postmortem`](skills/incident-postmortem/SKILL.md) — Write a blameless postmortem with timeline, root cause, and owned actions.
- [`information-architecture`](skills/information-architecture/SKILL.md) — Organize information architecture: nav, labels, findability, card sorting notes.
- [`interaction-states`](skills/interaction-states/SKILL.md) — Specify full interaction states for components: default, hover, focus, active, disabled, error, loading.
- [`interview-prep`](skills/interview-prep/SKILL.md) — Prepare for an interview: stories (STAR), questions to ask, research brief.
- [`interview-questions`](skills/interview-questions/SKILL.md) — Prepare interview questions for customers, candidates, or experts with follow-ups.
- [`job-application-tailor`](skills/job-application-tailor/SKILL.md) — Tailor a resume bullet set and cover note to a job description without fabricating experience.
- [`journal-prompts`](skills/journal-prompts/SKILL.md) — Provide journal prompts for reflection, goals, or stress processing (not therapy).
- [`json-schema-design`](skills/json-schema-design/SKILL.md) — Design tight JSON Schema / Zod / Pydantic models with bounds and examples.
- [`landing-page-copy`](skills/landing-page-copy/SKILL.md) — Write landing page copy: hero, proof, benefits, objections, CTA.
- [`learning-curriculum`](skills/learning-curriculum/SKILL.md) — Build a learning curriculum for a skill: modules, resources types, practice projects.
- [`level-design-brief`](skills/level-design-brief/SKILL.md) — Produce a level design brief: layout goals, encounters, pacing, and greybox checklist.
- [`license-compliance`](skills/license-compliance/SKILL.md) — Summarize third-party licenses and flag strong copyleft risk for distribution.
- [`llm-cost-guardrails`](skills/llm-cost-guardrails/SKILL.md) — Add token/cost/latency guardrails and sensible model routing.
- [`load-test-plan`](skills/load-test-plan/SKILL.md) — Design and run a minimal load test on critical endpoints with clear stop conditions.
- [`local-container-stack`](skills/local-container-stack/SKILL.md) — Provide local multi-service containers for local dependencies with healthchecks and sane ports.
- [`log-level-triage`](skills/log-level-triage/SKILL.md) — Triage production issues from logs: timeline, correlation IDs, dependency health.
- [`logo-usage-rules`](skills/logo-usage-rules/SKILL.md) — Document logo usage: clear space, min size, on dark/light, misuse examples.
- [`loot-table-design`](skills/loot-table-design/SKILL.md) — Design loot tables with drop rates, pity systems, and economy safety.
- [`makefile-tasks`](skills/makefile-tasks/SKILL.md) — Add Makefile/task targets wrapping real project commands (setup/test/lint/run).
- [`markdown-doc-structure`](skills/markdown-doc-structure/SKILL.md) — Restructure Markdown documentation for clear heading hierarchy and working links.
- [`meeting-agenda`](skills/meeting-agenda/SKILL.md) — Create a meeting agenda with purpose, topics, times, and decisions needed.
- [`meeting-notes`](skills/meeting-notes/SKILL.md) — Turn discussion into structured notes: decisions, actions, owners, dates.
- [`memory-leak-hunt`](skills/memory-leak-hunt/SKILL.md) — Find memory growth in long-running services via profiles and retained allocations.
- [`message-draft-personal`](skills/message-draft-personal/SKILL.md) — Draft personal messages (thanks, apology, invite, check-in) with tone options.
- [`metrics-instrumentation`](skills/metrics-instrumentation/SKILL.md) — Add RED/USE-style metrics without high-cardinality label explosions.
- [`migration-data-backfill`](skills/migration-data-backfill/SKILL.md) — Plan batched data backfills that won't lock production tables.
- [`money-calculations`](skills/money-calculations/SKILL.md) — Implement money math with integers/decimals, explicit rounding, and currency codes.
- [`monorepo-task-runner`](skills/monorepo-task-runner/SKILL.md) — Fix monorepo task graphs (turbo/nx/pnpm) for filtered build/test.
- [`moodboard-direction`](skills/moodboard-direction/SKILL.md) — Assemble a written moodboard direction: themes, keywords, references categories (no brand copying).
- [`morning-shutdown-rituals`](skills/morning-shutdown-rituals/SKILL.md) — Design morning and end-of-day rituals that fit a real schedule.
- [`motion-design-spec`](skills/motion-design-spec/SKILL.md) — Specify motion: purpose, duration, easing, reduced-motion fallback.
- [`move-house-plan`](skills/move-house-plan/SKILL.md) — Plan a household move: timeline, inventory, vendors, change-of-address checklist.
- [`multi-tenant-isolation`](skills/multi-tenant-isolation/SKILL.md) — Audit multi-tenant isolation for cross-tenant data leaks.
- [`multiplayer-session-design`](skills/multiplayer-session-design/SKILL.md) — Design multiplayer session flow: matchmaking intent, disconnects, host migration, fairness.
- [`negotiation-prep`](skills/negotiation-prep/SKILL.md) — Prepare a negotiation: BATNA, range, script, concessions.
- [`newsletter-issue`](skills/newsletter-issue/SKILL.md) — Plan and draft a newsletter issue: sections, links, subject lines, preview text.
- [`nodejs-upgrade`](skills/nodejs-upgrade/SKILL.md) — Plan and execute a Node.js runtime upgrade with CI and dependency checks.
- [`onboarding-checklist`](skills/onboarding-checklist/SKILL.md) — Design a dismissible first-run checklist that drives activation.
- [`onboarding-ui-flow`](skills/onboarding-ui-flow/SKILL.md) — Design product onboarding UI: progressive disclosure, skip, value moments.
- [`openapi-sync`](skills/openapi-sync/SKILL.md) — Regenerate or manually sync OpenAPI with implemented routes and flag breaking changes.
- [`owasp-web-checklist`](skills/owasp-web-checklist/SKILL.md) — Security-review a web change against practical OWASP-style controls (injection, XSS, authz, CSRF, SSRF).
- [`packing-list`](skills/packing-list/SKILL.md) — Generate a packing list by trip type, climate, and activities.
- [`pagination-standard`](skills/pagination-standard/SKILL.md) — Add stable list pagination (cursor preferred) with enforced max limits.
- [`pagination-ui`](skills/pagination-ui/SKILL.md) — Implement accessible list pagination or load-more with URL state.
- [`password-hygiene`](skills/password-hygiene/SKILL.md) — Personal password hygiene checklist: unique passwords, manager use, 2FA—without handling secrets.
- [`payment-webhook-flow`](skills/payment-webhook-flow/SKILL.md) — Implement payment webhooks with verification and idempotent entitlement updates.
- [`perf-profile-cpu`](skills/perf-profile-cpu/SKILL.md) — Capture and interpret a CPU profile to find hot functions before optimizing.
- [`permissions-matrix`](skills/permissions-matrix/SKILL.md) — Build a role×action permission matrix and verify server enforcement.
- [`persona-profile`](skills/persona-profile/SKILL.md) — Build research-backed personas (or proto-personas) with goals, frustrations, contexts.
- [`personal-crm`](skills/personal-crm/SKILL.md) — Lightweight personal CRM: people notes, last contact, follow-ups (privacy first).
- [`personal-inventory`](skills/personal-inventory/SKILL.md) — Run a personal inventory session: commitments, energy, obligations, free capacity.
- [`personal-okr`](skills/personal-okr/SKILL.md) — Write personal OKRs: objective, key results, weekly check-ins.
- [`personal-values-exercise`](skills/personal-values-exercise/SKILL.md) — Facilitate a values clarification exercise and translate into weekly choices.
- [`pet-care-routine`](skills/pet-care-routine/SKILL.md) — Create a pet care routine: feeding, walks, meds placeholders, emergency contacts.
- [`photo-art-direction`](skills/photo-art-direction/SKILL.md) — Art-direct photoshoots or stock selection: subject, lighting, crop, usage rights checklist.
- [`pii-data-handling`](skills/pii-data-handling/SKILL.md) — Minimize and protect PII: access, logs redaction, retention, deletion paths.
- [`playtest-protocol`](skills/playtest-protocol/SKILL.md) — Run a structured playtest: goals, tasks, observation notes, and debrief actions.
- [`podcast-episode-plan`](skills/podcast-episode-plan/SKILL.md) — Plan a podcast episode: cold open, segments, guests prep, show notes.
- [`portfolio-case-layout`](skills/portfolio-case-layout/SKILL.md) — Structure a portfolio case study page: problem, process, outcome, images.
- [`poster-layout`](skills/poster-layout/SKILL.md) — Design a poster layout brief: hierarchy, margins, type, print specs.
- [`pr-description`](skills/pr-description/SKILL.md) — Draft a precise PR title and body from branch commits and diff. Use before opening or updating a pull request.
- [`pre-commit-hooks`](skills/pre-commit-hooks/SKILL.md) — Configure pre-commit/husky hooks for format, lint, and optional secret scan.
- [`presentation-deck-structure`](skills/presentation-deck-structure/SKILL.md) — Structure a presentation deck: story arc, slide budget, speaker notes.
- [`press-release`](skills/press-release/SKILL.md) — Draft a press release: headline, lede, body, boilerplate, quotes, links.
- [`print-layout-basics`](skills/print-layout-basics/SKILL.md) — Lay out print-ready pages: margins, bleed, hierarchy, export checklist.
- [`priority-matrix`](skills/priority-matrix/SKILL.md) — Sort tasks with urgency/importance and recommend what to defer or drop.
- [`privacy-checkup`](skills/privacy-checkup/SKILL.md) — Walk through a personal privacy checkup: app permissions, sharing, data downloads.
- [`progression-curve`](skills/progression-curve/SKILL.md) — Design player progression curves: XP, unlocks, soft gates, and catch-up.
- [`prompt-eval-harness`](skills/prompt-eval-harness/SKILL.md) — Build a small regression suite for prompts/agent behaviors with deterministic checks.
- [`python-packaging`](skills/python-packaging/SKILL.md) — Package Python projects with pyproject entry points and a clean build/install check.
- [`python-typing-pass`](skills/python-typing-pass/SKILL.md) — Raise typing quality on selected Python modules until mypy/pyright is clean.
- [`quest-design`](skills/quest-design/SKILL.md) — Design quests/missions with objectives, gates, rewards, and failure paths.
- [`queue-consumer-safe`](skills/queue-consumer-safe/SKILL.md) — Build safe queue consumers: ack semantics, retries, DLQ, idempotent handlers.
- [`rag-chunking`](skills/rag-chunking/SKILL.md) — Design document chunking and metadata for higher-quality RAG retrieval.
- [`rate-limit-design`](skills/rate-limit-design/SKILL.md) — Design rate limits for public/auth endpoints with clear 429 behavior.
- [`react-performance`](skills/react-performance/SKILL.md) — Fix common React performance issues after identifying hot components.
- [`reading-notes`](skills/reading-notes/SKILL.md) — Produce structured reading notes: summary, key ideas, quotes, actions.
- [`rebase-onto-main`](skills/rebase-onto-main/SKILL.md) — Update the current branch onto latest main/master via rebase or merge with conflict handling.
- [`recipe-plan-meals`](skills/recipe-plan-meals/SKILL.md) — Plan meals for N days given constraints (time, diet, servings) with shopping list.
- [`regex-safety`](skills/regex-safety/SKILL.md) — Review regexes for ReDoS and correctness on untrusted input.
- [`release-announcement`](skills/release-announcement/SKILL.md) — Write a product release announcement for blog/email/in-app.
- [`release-checklist`](skills/release-checklist/SKILL.md) — Execute a pre-release gate: dirty tree, version alignment, tests, docs, remaining ship steps.
- [`reminder-system`](skills/reminder-system/SKILL.md) — Design a reminder system: what belongs on calendar vs tasks vs checklists.
- [`research-digest`](skills/research-digest/SKILL.md) — Digest multiple sources into a brief with citations and confidence levels.
- [`responsive-design-spec`](skills/responsive-design-spec/SKILL.md) — Specify responsive behavior across breakpoints: reflow, collapse, priority content.
- [`responsive-ui-pass`](skills/responsive-ui-pass/SKILL.md) — Fix layout breakage across mobile/tablet/desktop widths.
- [`retro-notes`](skills/retro-notes/SKILL.md) — Run a lightweight blameless retro and produce concrete action items.
- [`runbook-write`](skills/runbook-write/SKILL.md) — Author an on-call runbook: health checks, common failures, deploy/rollback.
- [`rust-clippy-fix`](skills/rust-clippy-fix/SKILL.md) — Run Clippy and fix correctness-oriented lints; re-test.
- [`sbom-generate`](skills/sbom-generate/SKILL.md) — Generate a Software Bill of Materials (CycloneDX/SPDX) using available tooling.
- [`script-to-storyboard`](skills/script-to-storyboard/SKILL.md) — Turn a script into a shot list / storyboard frames description.
- [`search-indexing`](skills/search-indexing/SKILL.md) — Design app search indexing and sync (FTS or search engine) with relevance checks.
- [`second-brain-notes`](skills/second-brain-notes/SKILL.md) — Organize notes into a simple personal knowledge system: inbox, projects, areas, archives.
- [`secret-scan-guidance`](skills/secret-scan-guidance/SKILL.md) — Find likely leaked secrets in the tree and guide rotation without printing secret values.
- [`semver-bump`](skills/semver-bump/SKILL.md) — Recommend major/minor/patch from the change set and apply a consistent version bump.
- [`seo-basics`](skills/seo-basics/SKILL.md) — Apply basic technical SEO checks to marketing/docs pages.
- [`seo-content-brief`](skills/seo-content-brief/SKILL.md) — Create an SEO content brief: intent, outline, questions to answer, internal links (no stuffing).
- [`short-form-script`](skills/short-form-script/SKILL.md) — Write a short-form vertical video script under a target duration with on-screen text.
- [`sla-error-budget`](skills/sla-error-budget/SKILL.md) — Define practical SLIs/SLOs and an error-budget policy for a service.
- [`sleep-routine`](skills/sleep-routine/SKILL.md) — Design a sleep routine: wind-down, environment, schedule consistency (general wellness).
- [`snapshot-test-discipline`](skills/snapshot-test-discipline/SKILL.md) — Tame snapshot tests: reduce scope, review diffs, avoid golden files that hide bugs.
- [`social-post-pack`](skills/social-post-pack/SKILL.md) — Create a pack of social posts for one announcement across lengths and CTAs.
- [`spacing-layout-grid`](skills/spacing-layout-grid/SKILL.md) — Define spacing scale and layout grid for consistent composition.
- [`sql-query-review`](skills/sql-query-review/SKILL.md) — Review SQL/ORM usage for N+1, injection, and missing indexes.
- [`structured-logging`](skills/structured-logging/SKILL.md) — Introduce structured logging with levels, correlation IDs, and secret redaction.
- [`style-guide-writing`](skills/style-guide-writing/SKILL.md) — Create a writing style guide: voice, grammar choices, inclusive language, examples.
- [`subscription-audit`](skills/subscription-audit/SKILL.md) — Audit subscriptions from a user-provided list: keep, cancel, downgrade recommendations.
- [`terraform-plan-review`](skills/terraform-plan-review/SKILL.md) — Review infrastructure-as-code plans for destroys, public exposure, and IAM blast radius before apply.
- [`test-selection`](skills/test-selection/SKILL.md) — Select and run the smallest high-value tests for the current change set.
- [`threat-model-lite`](skills/threat-model-lite/SKILL.md) — Write a one-page threat model for a feature: assets, actors, entry points, mitigations.
- [`thumbnail-concept`](skills/thumbnail-concept/SKILL.md) — Concept thumbnails: focal subject, text overlay limits, contrast, A/B ideas.
- [`time-audit`](skills/time-audit/SKILL.md) — Guide a time audit: log categories, find leaks, redesign week.
- [`tool-use-spec`](skills/tool-use-spec/SKILL.md) — Specify safe tool/function contracts: schemas, side effects, confirmations, timeouts.
- [`tracing-spans`](skills/tracing-spans/SKILL.md) — Add distributed tracing spans across request and outbound calls.
- [`transcript-cleanup`](skills/transcript-cleanup/SKILL.md) — Clean a transcript: speakers, paragraphs, filler removal, summary bullets.
- [`travel-itinerary`](skills/travel-itinerary/SKILL.md) — Build a travel itinerary: logistics, buffers, offline notes, packing constraints.
- [`ts-strict-migration`](skills/ts-strict-migration/SKILL.md) — Incrementally enable TypeScript strictness without a big-bang freeze.
- [`typography-system`](skills/typography-system/SKILL.md) — Set type scale, line height, and pairing rules for UI or editorial layouts.
- [`ui-kit-inventory`](skills/ui-kit-inventory/SKILL.md) — Inventory UI components and document missing states for a kit.
- [`usability-test-plan`](skills/usability-test-plan/SKILL.md) — Plan a usability test: tasks, metrics, script, and synthesis template.
- [`user-journey-map`](skills/user-journey-map/SKILL.md) — Map a user journey: stages, emotions, pain points, opportunities.
- [`user-story-split`](skills/user-story-split/SKILL.md) — Split an epic into vertical, testable user stories with acceptance criteria.
- [`ux-copy-microcopy`](skills/ux-copy-microcopy/SKILL.md) — Write UI microcopy: buttons, errors, empty states, confirmations—clear and human.
- [`video-chapter-markers`](skills/video-chapter-markers/SKILL.md) — Create chapter markers and titles from a video outline or transcript.
- [`video-edit-checklist`](skills/video-edit-checklist/SKILL.md) — Checklist for editing a video cut: pacing, audio, captions, exports.
- [`video-script`](skills/video-script/SKILL.md) — Write a video script with visual column, VO/dialogue, timing, and B-roll notes.
- [`visual-hierarchy-pass`](skills/visual-hierarchy-pass/SKILL.md) — Improve visual hierarchy on a screen: type scale, weight, spacing, focal point.
- [`visual-regression-setup`](skills/visual-regression-setup/SKILL.md) — Add a small visual regression set for critical screens with stable snapshots.
- [`webhook-verify`](skills/webhook-verify/SKILL.md) — Implement or review webhook receivers: signature verification, raw body, replay protection, idempotency.
- [`websocket-debug`](skills/websocket-debug/SKILL.md) — Diagnose WebSocket handshake, auth, ping/pong, and reconnect storms.
- [`weekly-review`](skills/weekly-review/SKILL.md) — Run a weekly review: wins, open loops, priorities, calendar look-ahead.
- [`wireframe-flow`](skills/wireframe-flow/SKILL.md) — Produce low-fidelity wireframe flows for a user task (text or simple structure).
- [`workout-plan-basic`](skills/workout-plan-basic/SKILL.md) — Draft a basic workout plan with warm-up, main work, recovery (not medical advice).
