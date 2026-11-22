# Inner language research (2025–November 2026)

A short pass on how models compress thought and talk to each other, and
what that means for Remedy's inner language. Real URLs only. This is not
a survey; it records what we adopted, what we deferred, and why.

## What we adopted for level 1 (now)

**A versioned, auditable text envelope — not a latent channel.** Hosted
and local models both have to speak it, so the channel has to be text.
Agent-to-agent specs (A2A) treat capability negotiation and a clear
fallback as the baseline for inter-operable agents. We took the shape:
a versioned envelope, a capability flag for later levels, and a
natural-language fallback when a worker or model cannot read the dense
form.

- https://a2a-protocol.org/latest/specification/

**Tokenizer-aware shorthand, not exotic symbols.** Earlier token-budget
work (see `docs/TOKEN_OPTIMIZATION.md`) already warned that unusual
symbols and base64 often cost *more* tokens. We measured candidates
with Remedy's own token estimator (`estimate_text_tokens`, BPE pack
when present, else the family heuristic) across the families she
actually routes: OpenAI-compat (`cl100k`), Anthropic, and local. The
seed grammar uses short ASCII keys, a fixed field order (so keys can
be dropped at low verbosity), and common-token English fragments.

**Audit the decoded form, always.** 2026 causal audits of latent
multi-agent channels show that an accuracy bump is not proof the
receiver used the intended message. Mismatched or zeroed payloads can
keep most of the gain. If a compressed channel cannot be expanded to
readable text, Joe cannot audit it. So every inner and worker message
is written to an audit log in both dense and decoded form, and the
status view shows the lexicon (with glosses) and that log. There is no
privacy layer on this channel.

- https://arxiv.org/abs/2607.26773
- https://arxiv.org/abs/2608.04893
- https://arxiv.org/abs/2607.14103

**A self-evolving vocabulary, not a frozen codebook.** We ship only a
minimal seed (the envelope, kinds, and core field tags). Remedy coins
compact terms during dreams and reflection, with a mandatory
plain-language gloss, and prunes terms that do not save tokens or that
child workers fail to decode. That is her language. The papers on
gist tokens and latent working memory motivate *learned* compression;
we apply the idea at the text-lexicon layer, which stays decodable on
every model she runs.

## What we deferred, and why

**Level 2 — gist / memory tokens.** UniGist, sentence-anchored gist
compression, and follow-ups show that learned compression tokens can
shrink long context, but also that naive gisting can fail on long or
detailed recall, and that simple pooling sometimes wins. These methods
need model-side fine-tuning or special tokens the hosted APIs do not
let us inject. The envelope reserves a gist slot and a capability flag.
We do not mint gist tokens yet.

- https://arxiv.org/abs/2509.15763
- https://arxiv.org/abs/2511.08128
- https://arxiv.org/abs/2504.08934

**Level 3 — latent / hidden-state passing, local models only.** Latent
Thought Flow and adaptive-horizon latent CoT, plus LatentMAS,
StateBridge, and the Agent Vector Protocol, show real token and latency
wins when agents share hidden states or KV caches instead of text. They
also require engine access, usually the same (or aligned) local model,
and they are easy to mis-credit: audits find “gains” that survive a
mismatched cache. Joe requires every inner and worker message to be
decodable and visible. We reserve a latent slot and a `local` capability
flag, and stub the interfaces. We do not pass hidden state.

- https://arxiv.org/abs/2606.16222
- https://arxiv.org/abs/2611.24001
- https://proceedings.mlr.press/v306/zou26k.html
- https://arxiv.org/abs/2608.13317
- https://github.com/VectorArc/avp-spec

## Capability flags

The envelope carries `L=1` today. `gist` and `latent` default off.
`latent` is allowed to turn on later only when the parent and child
are both local. Hosted models stay on level 1. Any future level-2/3
payload must still write a decoded gloss into the audit log.
