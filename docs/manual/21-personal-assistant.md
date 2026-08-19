# Personal assistant — reminders, mail, calendar, money, paperwork

Everything in this chapter runs on your PC against accounts you own. Remedy has
no cloud of its own, holds no copy of your mail, and never needs a Google Cloud
project to reach your mailbox.

> Budgeting and debt tracking help you organize numbers you enter — not
> personalized financial, tax, or legal advice.

## Reminders — a clock that actually fires

Ask in plain language; there is no form.

- **`remind_me`** — `when` accepts `in 30m`, `tomorrow 9am`, `friday 3pm`,
  `2026-09-01`, or a bare `5pm`. `importance` decides whether it may interrupt.
  `recurrence` makes it repeat.
- **`reminder_list`**, **`reminder_done`**, **`reminder_snooze`**,
  **`reminder_cancel`** — the rest of the loop. A repeating reminder marked done
  rolls to its next date rather than disappearing.
- **`reminder_sync_bills`** — turns every stored bill with a due date into a
  reminder. Safe to re-run; it will not duplicate.

**When it may interrupt.** Quiet hours default to 22:00–07:00, and only
`high` importance breaks them — anything lower is held, not dropped. An
identical message inside five minutes is suppressed. Delivery goes to a durable
outbox first and to your messengers second, so a messenger being down never
loses the reminder.

## Mail — an app password, not a cloud project

Two routes. Prefer the first.

| | App password (IMAP/SMTP) | Google OAuth |
|---|---|---|
| Setup | 2-step verification + a 16-character app password | A Google Cloud project |
| Works with | Gmail, Outlook/Hotmail/Live, Yahoo, Fastmail, iCloud | Gmail only |
| Where the password lives | Remedy's encrypted local secret store | OAuth tokens, same store |

```
mail_connect      address + app_password   — verifies IMAP *and* SMTP before saving
mail_status       which mailbox, and by which route
mail_disconnect   forget the password and unlink the account
mail_list         query like in:inbox, from:someone
mail_get          read one message
mail_reply        reply in thread — prefer this over a fresh send
mail_create_draft compose without sending
mail_send         send now; only when you explicitly ask
mail_archive      out of the inbox, still searchable
mail_mark_read    read / unread
```

Remedy asks for the **app password**, never your account password, and tells you
where to generate one for your provider. A typo is caught at connect time: the
credential is only stored after a live IMAP and SMTP check, so a mailbox never
shows "connected" when it is not.

## Calendar — the same credential

Where your provider offers CalDAV — Gmail, iCloud, Fastmail — connecting the
mailbox connects the calendar too, with no second login. Outlook and Yahoo
supply mail only; use Google OAuth if you need their calendar.

```
calendar_list_events    days, or an explicit time_min / time_max
calendar_create_event   title, start, end, description
calendar_update_event   only the fields you pass change — moves a time, does not duplicate
calendar_cancel_event   not reversible from here, so it asks first
```

## Money — organization, never advice

Numbers you enter, arithmetic you can check. No credit pulls, no bank links, no
recommendations.

```
budget_set / budget_get / budget_status   a month label, categories, spent vs planned
budget_tx_add                             one expense or income
bill_upsert / bill_list                   name, amount, cadence, next due
debt_upsert / debt_list                   balances and APRs you report
debt_scenario                             illustrative payoff months at min + extra
money_disclaimer                          the full text, any time
```

`debt_scenario` is arithmetic, not a plan. It says so every time it runs.

## Paperwork — a photo of a letter becomes things to do

- **`document_read`** — pulls the text out of a photo, scan, or `.txt`/`.md`.
  Images go through the local vision decoder, on your machine.
- **`document_intake`** — classifies it as **bill, appointment, prescription,
  notice, receipt, statement** or other, then *proposes* actions: a reminder for
  a due date, a bill entry, a calendar event. Proposes. You confirm.

## The brief

**`assistant_brief`** pulls budget, bills, debts and open goals into one short
answer, adding calendar and mail when an account is linked. **`assistant_accounts`**
shows what is connected and what each provider still needs.

## What leaves your machine

> Connected mail/calendar is handled on your PC. When chat uses a cloud AI
> provider, only tool results you trigger may be sent to that provider — not
> your OAuth tokens.

Concretely: app passwords and OAuth tokens are never sent to a model. Mail you
ask Remedy to *read or answer* is sent to whichever AI provider you chose, for
that turn only — because answering it is the thing you asked for. Pick a local
provider (Ollama, RMB, llama.cpp) and even that stays on the machine.

Connecting a mailbox with explicit credentials **is** the consent for account
access; Remedy records it rather than sending you to a second toggle, and states
plainly what it means when it does.

## Setting it up from the CLI

```bash
remedy auth apikey anthropic     # a model to think with
remedy chat                      # then: "connect my mailbox"
```

There is no setup wizard. Remedy asks for one thing at a time, in conversation,
and tells you where to get it.

## See also

- [Providers & auth](03-providers-and-auth.md) — model keys and app passwords
- [Security & data](04-security-and-data.md) — the secret store, jails, SSRF
- [Local vision & SmolVLM2](14-visual-decoder.md) — what reads your documents
