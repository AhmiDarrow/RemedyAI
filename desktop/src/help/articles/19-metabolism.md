# Partner Metabolism (Advanced)

**Shipped in 0.20.0.** Remedy’s **metabolism** is the silent local system that makes any
chat model feel like the same durable partner — faster, leaner, and safer — without
multi-agent theater.

Normal chat still talks to **one Remedy**. These organs never appear as named bots.

## What it does

| Organ | Job |
|-------|-----|
| **Turn cost compiler** | L0 instant local answers · L1 lean chat · L2 agency · L3 deep / work-alone |
| **Evidence ledger** | Tool facts get IDs (EU); send deltas instead of re-pasting sludge |
| **Decision currency** | Counts decisions (DU) and waste batches — not token worship alone |
| **Machine map** | Live browser URL, work roots, windows, file touches |
| **Shadow rehearsal** | Dry-run high-blast tools before commit (on top of write jail) |
| **Action IR** | Redacted replay traces under `~/.remedy/action_ir/` |
| **Spread muscle** | Partitionable work prefers `spread_run` (one merged answer) |
| **Time Crystal** | turn → session → project week → life; secrets never promote |
| **Skill genome** | Local success ranks for skills (protected after multi-win) |
| **CUA macros** | Successful computer-use chains become reusable hints |
| **Quality governor** | Stuck / waste / re-explain → silent remedies next turn |
| **Critical verify** | Catches false “tests green” and secret-risk claims |
| **Portable identity** | Encrypted export/import — never keys or OAuth |

## Operator surfaces

- Chat: `/harness` (includes **Metabolism** line when counters exist)
- API: `GET /api/partner/metabolism`
- Partner status: `metabolism` field
- Export: `POST /api/partner/identity/export` `{ "passphrase": "…" }`
- Import: `POST /api/partner/identity/import` `{ "passphrase": "…", "source": "…" }`

## L0 examples (no provider tokens)

- “What model am I using?”
- “List my skills”
- “Who am I” / `/whoami` style
- “What is your version”

## Trust rules

- One voice only  
- Local-first under `~/.remedy`  
- Secrets redacted at ledger / IR / export / logs / UI tool previews  
- Shadow never replaces write jail or approvals (opaque shell payloads hard-block)  
- Shell write jail fails closed on encoded/download-drop mutations when a project is bound  
- Auth paths under `~/.remedy/auth` never writable via tools/shell (even under home scope)  
- URL userinfo and query tokens stripped from machine map, CUA macros, and Action IR  
- Identity export/import rate-limited; packages require passphrase + HMAC  
- Agency re-arms if the model only *promises* tools/skills without function calls  
- Hot path never blocks on local model inference  
- Plan mode stays research-only (no `computer_act` / click / type mutations)

## Related

- [Continuity philosophy](16-continuity-philosophy)  
- [Memory & harness](06-memory-and-harness)  
- [Nanoswarm](17-nanoswarm)  
- [Computer use soak](computer-use-soak)  
