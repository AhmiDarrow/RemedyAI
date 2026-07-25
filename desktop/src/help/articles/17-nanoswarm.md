# Continuity workers (nano swarm)

This chapter is for **owners and operators** who want to understand what runs
under the hood. In normal chat you still talk to **one Remedy** — not a cast of
agents. Full+ tool process can surface diagnostics; everyday use does not.

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
| **Helper** | Reserved on the same local Qwen (not shipped as UI yet) | Local only when enabled |

They share one optional **local Qwen** runtime for vision/nano assist — never a
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

## Local model (optional)

First-run download of pinned Qwen (see [Local vision](14-visual-decoder)). When
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

## Operator knobs

| Knob | Effect |
|------|--------|
| Harness mode **auto** | ContextSnapshot + compress nudges on |
| Harness **off** | Skips snapshot continuity pass |
| Tool process **Full+** | Continuity activity visible in UI |
| `REMEDY_LIVE_MODELS=0` | Disable live provider model listing |
| Vision enable + auto_start | Local Qwen with Remedy |

## Related

- [How Remedy works (continuity)](16-continuity-philosophy)  
- [Memory & harness](06-memory-and-harness)  
- [Local vision](14-visual-decoder)  
- [CLI & API](10-cli-and-api)  
