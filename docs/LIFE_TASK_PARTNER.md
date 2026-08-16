# Remedy as a life-task partner — design

*Design chapter for the pillar that makes Remedy more than a coding agent: a partner that drives this computer to complete the goals its owner sets — local software, local UI, online tasks, forms, shopping, everyday life.*

Status: **design** · Owner: Ahmi · Companion: local audit `docs/AUDIT_LIFE_TASK_2026-08-16.md` (fix anchors) · North star: `AGENTS.md`

---

## 1. North star

> Anyone — regardless of ability level or technical skill — can tell Remedy a goal in their own words, and Remedy drives this computer to finish it: seeing the screen, using local apps, browsing, filling forms, buying things — with trust checkpoints a non-technical person understands, and a record they can review afterwards.

Remedy already treats the PC as a workbench (Browser rail, Computer use, Host Bridge, UIA snapshots, SmolVLM2 vision). This pillar turns those capabilities into **finished life tasks**. The test is never "did the tool call succeed" — it is **"is the user's goal done, and do they know it."**

### Who this is for

| Owner | What they need from Remedy |
|-------|---------------------------|
| **Non-technical** | Plain words in, plain words out. No tool names, no JSON, no jargon in prompts or errors |
| **Low-vision / blind** | Remedy speakable end-to-end: approvals, progress, results. Screen-reader-true UI |
| **Limited motor control** | One utterance → many actions. Yes/No/Explain decisions. Never "click here to continue" |
| **Cognitively loaded / elderly** | One question at a time. Small consistent vocabulary. Nothing silent, nothing sudden |
| **Power user** | All of the above never *removes* capability — Advanced keeps full control (product feel bar) |

Accessibility is not a mode bolted on at the end; it is the quality bar. **An interface a blind user can drive by voice is also the best interface for a sighted user who is cooking dinner.**

---

## 2. The shape of a life task

Every life task moves through the same five stages. Each stage has a design contract:

```text
GOAL  →  PLAN  →  DRIVE  →  HANDOFF (sometimes)  →  DONE
"order      "here's what     act · verify ·        login / 2FA /       "done — here's
 my usual    I'll do —        retry · narrate       CAPTCHA / payment    what I did"
 groceries"  okay?"                                 → owner moment
```

### 2.1 GOAL — heard, not routed

- Commerce and life verbs (**buy, order, book, schedule, apply, renew, pay, register, fill out**) are first-class task intents — never "open the site and stop."
- Ambiguity resolves by asking **one** plain question, not by guessing: "Walmart or Kroger?"
- "My usual" is a real concept, backed by the routine store (§6).

### 2.2 PLAN — the trust contract

One approval **per goal**, up front, in plain language:

> **Remedy will:** open kroger.com → sign in as you → add your 12 usual items → stop at checkout and show you the total before paying.
> **Okay?** Yes · No · Change something

- The plan names the **checkpoints** (submit, pay, send, delete) where Remedy will stop again no matter what.
- Approving the plan replaces per-click prompting. Prompt fatigue is a safety failure: every unnecessary "Approve?" trains the owner to flip to auto-approve everything.
- Sensitive checkpoints (money, credentials, personal data, irreversible sends) are **non-waivable** — no mode, setting, or optimization may skip them. This is the same non-waivable tier that already protects owner-lock tools.

### 2.3 DRIVE — reliable hands

The primitive contract changes from *fire-and-forget* to **act → verify → retry → escalate**:

| Step | Meaning |
|------|---------|
| **Act** | Click / type / select — targeted at a named element (ref), not "whatever is focused" |
| **Verify** | Observe that it landed: URL changed, field contains the text, dialog closed, focus is where expected |
| **Retry** | Re-resolve the target once (fresh snapshot) and try again |
| **Escalate** | Tell the owner in plain words what's stuck and what Remedy suggests — never silently claim success |

Supporting requirements:

- **Forms are a first-class vocabulary**: fill-many-fields-at-once with per-field verification, dropdowns, checkboxes/radios, file upload, field-targeted typing. A government form is the benchmark, not a stretch goal.
- **Native apps deserve the same**: structural control (accessibility patterns — set value, invoke, expand, scroll-into-view, stable element identity) first; pixels + vision as fallback, with real OCR and marked screenshots — never coordinate guessing as the primary plan.
- **Secrets only type into verified fields**: before any credential-shaped text is sent, Remedy confirms the focused element is the intended field in the intended window/site. No verification, no keystrokes.
- **Progress is narrated calmly**: "Step 3 of 5 — adding items to cart." (Calm wording is existing Remedy doctrine; it extends to computer driving.)

### 2.4 HANDOFF — owner moments, by design

Some steps are the owner's on purpose: passwords Remedy doesn't hold, 2FA codes, CAPTCHAs, biometrics, final payment confirmation. These are **designed moments**, not failures:

- Remedy detects the wall, pauses, and says what's needed: "Kroger wants your password. Type it in the browser panel — I'll wait and continue after."
- The rail stays visible and interactive so the owner acts directly; Remedy resumes automatically once the wall clears.
- Handoffs must be accessible: announced audibly/visibly, no time pressure, "I can't do this step" always offers an alternative path or a graceful stop with state saved.

### 2.5 DONE — evidence, not vibes

- Completion is **observed** (order confirmation on screen, file exists, form shows "submitted"), never inferred from "my last action didn't error."
- Every drive session produces a reviewable record: **What Remedy did** — plain-language steps with before/after snapshots, kept for the owner (not purged in minutes), with undo hints where undo exists.
- Failure ends in a plain narrative with state saved: "I finished 3 of 5 steps. Nothing was submitted. Say *continue* to pick up at step 4."

---

## 3. Trust UX for non-technical owners

Approval cards are rewritten from developer-speak to owner-speak:

| | Today | Target |
|--|-------|--------|
| Voice | "Computer control requires approval (computer_click)" + raw command | "**Remedy wants to press 'Submit application' on irs.gov** with these 6 values: …" |
| Unit | Per tool call | Per goal, plus non-waivable sensitive checkpoints |
| Detail | Raw command string | Plain summary + values preview; raw detail under an expander (Advanced) |
| Expiry | Silent | Visible: "That approval timed out — nothing was done." |
| Form | Text in chat | Structured, **speakable** card: answerable by voice, switch, or one key (Yes / No / Explain) |

Rules:

- **No silent trust changes.** Nothing flips approval posture programmatically without prior owner opt-in; every bypass is visible.
- **Money always stops.** Payment/checkout actions checkpoint in every mode, with the amount shown. Spend limits are an owner setting.
- **Untrusted content never gains capability.** Web page text, downloaded files, and other untrusted inputs can trigger *questions* to the owner, never approvals.

---

## 4. Accessibility of Remedy itself (headline track)

The machinery Remedy uses to read *other* apps' accessibility trees must be matched by Remedy *being* accessible:

1. **Structured, speakable surfaces first** — approval cards, progress, and results emitted as structured payloads (not prose blobs) so the desktop can render, enlarge, or speak them. This is the foundation everything else builds on.
2. **Voice in / voice out** — state a goal, hear the plan, answer checkpoints by voice.
3. **Screen-reader truth** — semantic roles/labels/live-regions across chat, rails, settings; full keyboard operability.
4. **Low-vision** — large-text and high-contrast that survive every surface, including the Browser rail chrome.
5. **Single-switch / reduced-motor** — every decision reachable as Yes / No / Explain; no drag-only or hover-only interactions in Remedy's own UI.
6. **Cognitive load** — one question at a time, consistent vocabulary, progress always visible, nothing modal that traps.

Simple/Advanced chrome already encodes "power without clutter"; this track extends the same philosophy to "power without prerequisites."

---

## 5. Local software, both desktops

- **Windows** is the reference: structural-first driving (identity-stable elements, value/invoke/expand patterns, focus verification, per-monitor DPI correctness), vision as fallback with real OCR and marked screenshots.
- **Linux parity is a committed track, not a footnote**: the same see/act API backed by AT-SPI2 (tree), XTest (input) on X11, and desktop-portal remote-desktop/screencast on Wayland. Until it ships, Linux owners get honest capability reporting — "I can't drive apps on this desktop yet; I can still do it in the browser rail" — never a raise from deep inside a tool call.
- **Honest capability census**: `/stretch` already maps this PC; the life-task pillar extends it to "what Remedy can drive here" so plans never promise actions the host can't perform.

---

## 6. Routines — "my usual"

Life tasks repeat. The (currently dormant) macro store becomes the **routine store**:

- Successful multi-step drives are observed and keyed by **site + goal**, with failures recorded as failures (only wins become hints).
- Recurring wins are promoted into named routines the owner can invoke ("my grocery order"), inspect, and edit — same probation → promote lifecycle as skills, same calm transparency.
- Routines store *structure* (site, steps, field names), never secrets; values are re-resolved or re-asked at run time.
- Hints from the routine store are injected into drive guidance so repeated tasks get faster and more reliable, visibly: "I've done this before — last time these 4 steps worked."

---

## 7. What we will not do

- **No dark-pattern autonomy.** Remedy never completes a payment, sends a message, or submits a legal/government form without its checkpoint, even mid-"full" mode.
- **No CAPTCHA defeat, no impersonation.** Walls designed to verify a human are owner moments, full stop.
- **No surveillance residue.** Evidence records exist for the owner's review, stored locally under `~/.remedy`, purgeable by the owner; screenshots never leave the machine except to the owner's chosen model per existing vision rules.
- **No capability stripping in the name of simplicity.** Accessibility and simplicity constrain the *default surface*, never the ceiling (product feel bar).

---

## 8. Roadmap (summary)

| Phase | Theme | Ships |
|-------|-------|-------|
| **1 — Finishes the task** | Life-task verbs heard; act→verify→retry; full form vocabulary; input-primitive correctness; secret-field targeting | Form-fill + mock-checkout soak battery becomes a release gate |
| **2 — You can trust it** | Plan-level trust contract; plain-language speakable approval cards; non-waivable money/credential checkpoints; handoff moments; evidence ledger + recovery narratives | "What Remedy did" review surface |
| **3 — It knows this PC and this person** | Structural desktop driving (patterns + identity); deterministic routing; routine store live; focus/DPI/capture hardening | "My usual" works end-to-end |
| **4 — Everyone, everywhere** | Accessibility of Remedy itself (voice, screen-reader, switch, low-vision); Linux desktop driving | Two headline tracks, run in parallel |

Engineering anchors for every phase live in the local audit (`docs/AUDIT_LIFE_TASK_2026-08-16.md`). All work follows the ship-gate chain and change-safety protocol in `AGENTS.md`.

---

*The measure of this pillar: an owner who cannot use a mouse, or has never opened a settings panel, gets their errand done — and can tell you exactly what Remedy did on their behalf.*
