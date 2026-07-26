# How Remedy thinks about work (continuity)

Remedy is not “a chat window glued to a model API.” It is a **local continuity system** that makes whatever model you choose feel like the same partner: **you + your machine + a durable working memory**.

## The loop (not a bot farm)

```text
You (goals, taste, judgment)
        │
        ▼
   Remedy continuity          ← silent, local, always-on
   · Session Brief
   · Memory (facts that last)
   · Skills (procedures that improve)
   · Context budget & quality
        │
        ▼
   Provider model (Grok / Claude / GPT / Ollama / …)
        │
        ▼
   Tools on this PC (files, shell, skills)
        │
        └──────── learn / compress / remember ─────┘
```

You should feel **Remedy**, not a network of agents. Internal workers (sometimes called the continuity layer or nanoswarm in code) run in the background. They measure, prune, rank, and distill — they do not compete for the microphone.

## What you get

| Feeling | What is actually happening |
|---------|----------------------------|
| **Fast** | Hot path stays cheap: no mini-model debate on every keystroke |
| **Cheaper** | Less re-sending of tool sludge; compress when context is full |
| **Accurate over long work** | Session Brief keeps intent, files, decisions, next steps |
| **Same partner on any model** | Continuity lives on your PC, not in the vendor’s chat product |

## Budgets (metabolism)

Remedy tracks more than “tokens left”:

- **Context budget** — how full the model window is  
- **Cost budget** — provider usage when reported  
- **Continuity quality** — did compression keep the right paths and decisions?  
- **Stuck / re-explain rates** — silent signals that recovery guidance should kick in  

Normal use never requires internal continuity diagnostics (`/harness` for operators).

## Compression is fidelity under budget

Compressing is not “summarize until vague.” It is:

1. Keep **intent**, **decisions**, **open tasks**, **key files**  
2. Drop **duplicate tool output** and completed spans that no longer teach  
3. Score whether important paths/decisions survived (`/harness`)  

If something was dropped, Remedy should re-read a file rather than invent it.

## Skills as shared procedures

Skills are **how you and Remedy agree to do a class of work**. They graduate from one-off success to multi-session proof. Hard-won multi-attempt solutions are protected — that is deliberate, not accidental.

## Local vision

Screenshots and OCR use an optional **local** model on this PC (first-run download). That is infrastructure for seeing images when the chat model cannot — not a second personality.

## Work alone

If you say you are stepping away or ask Remedy to **handle this on its own**,
continuity injects an autonomous focus: finish the work with tools, tests, and
docs; only pause for hard blockers (secrets, paid APIs, irreversible destroy).
You should return to progress, not a queue of clarifying questions.

## What to do as a human partner

- Natural preferences (“I prefer TypeScript”) are learned; `/remember` / `/pin` for hard pins  
- `/whoami` / `/forget` to inspect and correct  
- Use `/compact` when switching big tasks  
- Prefer project folders so workspace continuity compounds  
- Leave Tool process on **Off** unless debugging  
- Say “work alone” / “handle this on your own” when you need unattended progress 

## Related

- [Memory & Memory Harness](06-memory-and-harness)  
- [Skills](07-skills)  
- [Local vision](14-visual-decoder)  
- [Continuity workers (nano swarm)](17-nanoswarm) — operator detail  
- [Security & data](04-security-and-data)  
