# Remedy Vault — card details and credentials

The Vault is where payment information and passwords live so that Remedy can
fill them into a page **without ever seeing them**.

That last part is the whole design. The model never receives a card number. It
receives a handle.

## How it works

You store a secret once, under a name you choose:

```
vault_add  kind=card  handle=main-card  domain=amazon.com   …
```

Kinds: **card**, **password**, **note**, **address**, **identity**.

From then on, everything the model touches — its context, the transcript, the
logs, the job files on disk — contains only the token:

```
{{vault:main-card}}
```

When Remedy types into a field, the token is expanded **machine-side**, on the
way to the input. The plaintext exists for that moment and nowhere else. Nothing
that is written down, sent to a provider, or kept in a session ever holds the
value.

This is why the protection survives a model that has been talked into
misbehaving: there is nothing in its context to leak.

## Site binding

A secret can be bound to a domain, and binding is enforced at fill time.

- A card bound to `amazon.com` **refuses to fill anywhere else**.
- A destination that cannot be verified — a desktop window rather than a page
  with an address — **refuses bound items by design**. Not a warning; a refusal.

So a page that pretends to be your bank does not get the card, and neither does
a native app that Remedy cannot prove the identity of.

## Every fill is yours to approve

Filling a vault item is an **owner checkpoint**. It cannot be waived by an
approval mode, because payment is the category where "she was probably right"
is not good enough. You see what is about to be filled and where.

Each fill writes an audit line recording the **handle** — never the value.

## Where it lives

| | |
|---|---|
| Items | `~/.remedy/vault.json` |
| Master key | `~/.remedy/vault.key` |
| Cipher | libsodium SecretBox (XSalsa20-Poly1305) |
| Key sealed by | DPAPI on Windows, or an owner passphrase (Argon2id) |

Established crypto only — no invented cipher anywhere in this path.

If the key is sealed with a passphrase, the Vault is **locked** until you supply
it; a locked vault refuses to expand tokens rather than failing open.

## What the model can ask for

One tool: **`vault_list`**, and it returns **metadata only** — handles, kinds,
bindings. Never values. Secrets go in through the owner-side API (`vault_add`),
not through the model.

## If you are not using it

Nothing about the Vault is required. Without it, Remedy simply asks you to type
payment details yourself, which is also a reasonable way to live.

## See also

- [Security & data](04-security-and-data.md) — the secret store, jails, scope
- [Coding agency](18-agency.md) — approvals and checkpoints
- [Grove](22-grove.md) — where approval cards surface
