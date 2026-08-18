# Remedy — terms of use

**Version 1 · applies to all of Remedy.**
The binding software terms are [LICENSE](../LICENSE); this page states, in plain
language, what you are agreeing to when you run it. Nothing here grants rights
the LICENSE does not, and where the two differ, the LICENSE wins.

You are asked to agree to this once, on first run, in conversation. Some
features — anything that reaches outside this machine — ask again for their own
terms on top of this one.

---

## 1. Remedy acts. That is the point, and the risk

Remedy is not a chat window. It runs commands on this PC, edits and deletes
files, installs things, drives a browser, uses your accounts, and — where you
enable it — speaks to other people. It does this on your instruction and under
your authority.

**Every action Remedy takes is your action.** You are responsible for what it
does with the access you give it, exactly as you would be if you had typed the
commands yourself. That includes actions taken while you were not watching,
under away mode, on a schedule, or from an approval you granted earlier.

## 2. Use at your own risk

Remedy is provided **as is and as available**, with no warranty of any kind:
no guarantee that it works, that it is correct, that it is available, or that it
is fit for anything in particular.

**To the maximum extent the law allows, the author accepts no liability** for
any loss or harm arising from its use. That includes, without limit: lost,
corrupted, or deleted data; money spent, moved, or committed; work destroyed or
never done; accounts locked, suspended, or compromised; messages, calls, or
publications you did not intend; decisions you or anyone else made on its
output; downtime; and any indirect or consequential loss.

**If it is not covered by the LICENSE, it is not covered.**

## 3. It will be wrong sometimes

Remedy is built on language models. It will occasionally be confidently
mistaken, misread a page, misunderstand an instruction, or invent a detail. It
can be manipulated by content it reads — a web page, an email, a document — into
doing something you did not ask for.

Keep backups. Review anything that matters before relying on it. Do not give it
access you would not be willing to lose.

## 4. Your keys, your accounts, your bills

You bring your own model providers and accounts. Their terms, their prices,
their rate limits, and their bills are between **you and them**. Remedy will
spend money through accounts you connect to it — tokens, phone minutes, anything
else you authorise — and those charges are yours.

Credentials you give Remedy are stored on this machine (DPAPI-encrypted on
Windows). Anyone with your user account has, in practice, what Remedy has.

## 5. The law where you are is your responsibility

You are responsible for using Remedy lawfully: what you automate, what you
scrape, what you record, what you send, who you contact, and what you do with
other people's data. Rules differ by country and by state. If you are unsure
whether something is permitted where you are, assume it is not until you have
checked.

Do not use Remedy to break into systems you do not own, to harass anyone, to
impersonate anyone, or to do anything you would not put your own name to. It is
acting as you, and to everyone on the receiving end, it *is* you.

## 6. Limits that are not configurable

No setting, approval mode, or instruction unlocks these. Remedy will always stop
for you before spending money or completing a purchase, will not exfiltrate your
credentials, and — where it speaks to people — will not deny being an AI when
asked directly.

## 7. What stays here

Memory, session history, skills, approvals, and secrets live under `~/.remedy`
on this machine. Remedy has no account service and phones no home. What leaves
this PC is what you send to the model provider you chose, plus whatever a
feature you enabled reaches out to.

Deleting `~/.remedy` deletes it. There is no copy elsewhere.

## 8. Features with their own terms

Some capabilities carry extra risk and ask separately, on top of this:

- **Telephony, voice, and SMS** — [TELEPHONY_TERMS.md](./TELEPHONY_TERMS.md).
  Notably: **Remedy is not an emergency service and must never be used to call
  one.**

## 9. Changes

If these terms change materially you will be asked again, in conversation, and
told what changed. The version you agreed to is recorded under
`~/.remedy/terms.json`.

---

*This is a plain-language statement of risk and responsibility, written by the
project, not legal advice. Anyone shipping this commercially should have a
lawyer review it.*
