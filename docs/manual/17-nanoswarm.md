# Continuity workers (nano swarm)

This chapter is for **owners and operators** who want to understand what runs
under the hood. In normal chat you still talk to **one Remedy** — not a cast of
agents. Everyday use does not surface internal diagnostics.

## Why it exists

Provider models are stateless between sessions. Remedy keeps **local continuity**
so work compounds:

| Worker (code name) | Job | Network? |
|--------------------|-----|----------|
| **Token** | Context fill %, compress nudge, usage calibration | No |
| **Router** | Intent label → policy pack (memory / skill / plan / tool / chat) | No (optional local refine if llama-server already up) |
| **Memory** | Session Brief touch (paths, decisions, next steps) | No |
| **Pattern** | Tool sequence window; stuck signals; learn pre-gate | No |
| **Skill** | Feedback + ranking cache for procedures | No |
| **Helper** | Reserved on the same local SmolVLM2 (not shipped as UI yet) | Local only when enabled |

They share one **local SmolVLM2** runtime for vision/nano assist — never a
second product personality.

## How it attaches to a turn

1. User message arrives  
2. **ContextSnapshot** (one pass): token measure, router intent, policy pack,
   brief touch, quality remedies, pattern window  
3. Policy + remedies inject as **silent system notes** (not chat bubbles)  
4. Frontier model runs with tools  
5. Each tool step updates **Pattern** + session quality; speculative prep warms
   brief / memory / skill ranks for the *next* turn  
6. End of multi-tool turns: **pattern pre-gate** can skip noisy auto-learn  

Status API: `GET /api/nanoswarm/status` · slash: `/harness` / `/nanoswarm`  
Partner status may include swarm counters for advanced UIs.

## Intelligent utilization principles

- **Heuristics first** — never block a turn waiting on a local model to classify  
- **Shared bots** — one coordinator instance so pattern/skill history carries  
- **Remedies from quality + pattern** — fail streaks and low tool success rates
  change the next system guidance  
- **Skill ranks off the hot path** — speculative prep warms the catalog; skill
  intent reuses the cache  
- **Learn only good traces** — pattern pre-gate rejects weak multi-tool noise  
- **Spread when partitionable** — ContextSnapshot may add a **[Spread]** hint;
  the frontier then calls `spread_run` for parallel silent jobs (not multi-agent
  chat). Local SmolVLM2 may refine the spread plan only if llama-server is already up.
  Independent work that needs its own memory uses the [hive](28-hive.md), not
  another nano worker.  
- **Library skill check** — Skill nanobot ranks the **cached** Skills Library index
  (never remote on the hot path). At most one soft Install tip; speculative prep
  refreshes the catalog in the background. 

## Local model (optional)

First-run download of pinned SmolVLM2 (see [Local vision](14-visual-decoder)). When
installed and running:

- Vision decode for text-only chat models  
- Optional nano classify refine (only if server is already up)  
- Same weights for all local roles  

## What is *not* disabled for “perf”

Recent releases temporarily limited live **provider** `GET /models` discovery
to ollama/openrouter/custom (0.10.44). That was restored: cloud providers again
query the endpoint so catalog renames (DeepSeek V4, Grok 4.x) stay correct.
Continuity workers themselves were not removed — only some paths stopped
*using* shared instances until Continuity 0.11+ rewired them.

## NanoToken BPE (owned packs)

Token fill estimates use Remedy’s **clean-room byte-level BPE** — not tiktoken or
any vendor merge tables.

| Pack | Role |
|------|------|
| **`remedy-bbpe-v2`** (default) | Trained on first-party repo + multi-provider tool/skill battery transcripts |
| **`remedy-bbpe-v1`** | Earlier synthetic pack; kept as fallback / comparison |

- Swarm **assigns** a pack per provider/model family; `provider_changed` remeasures.
- **Provider API usage** remains billing ground truth. Auto **calibration** scales
  raw pack counts toward real usage (target raw band roughly **0.75–1.25**).
- Retrain: `scripts/nanotoken_battery_and_train.py` (live battery + train) or
  `--from-corpus` on a prior dump; measure with `scripts/nanotoken_ratio_eval.py`.
- `REMEDY_BPE=0` forces heuristic weights only.
- APIs: `/api/nanoswarm/token/assignment`, `/api/nanoswarm/token/packs`.

## Operator knobs

| Knob | Effect |
|------|--------|
| Harness mode **auto** | ContextSnapshot + compress nudges on |
| Harness **off** | Skips snapshot continuity pass |
| `/harness` in chat | Continuity / session quality (operators) |
| `REMEDY_LIVE_MODELS=0` | Disable live provider model listing |
| `REMEDY_BPE=0` | Disable owned BPE; heuristic token estimates |
| Vision enable + auto_start | Local SmolVLM2 with Remedy |

## Related

- [How Remedy works (continuity)](16-continuity-philosophy)  
- [Memory & harness](06-memory-and-harness)  
- [Local vision](14-visual-decoder)  
- [CLI & API](10-cli-and-api)  

