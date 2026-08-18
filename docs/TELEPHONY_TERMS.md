# Telephony — terms of use

**Version 1 · applies to Remedy's phone, voice, and SMS features only.**
The binding software terms remain [LICENSE](../LICENSE); this page adds the
terms specific to putting an AI on a telephone line. Nothing here grants rights
the LICENSE does not.

You are asked to agree to this once, in conversation, before any phone feature
is set up. Remedy will not place or answer a call until you have.

---

## 1. Emergency calls — do not rely on this

**Remedy is not an emergency service and must never be used to contact one.**

Do not use it to call 911, 112, 999, or any emergency number, and do not rely on
it to summon help. It can fail silently for reasons entirely outside your
control — the network drops, the provider suspends the account, the PC sleeps,
a model stalls, the phone is unplugged. Emergency calling also depends on the
location data carriers require, which a software line generally cannot supply.

If you may need emergency help, keep a working phone that does not depend on
this software.

## 2. Use at your own risk

The phone features are provided **as is and as available**, with no warranty of
any kind — no guarantee that a call connects, is understood, is completed
correctly, or is completed at all, and no guarantee of accuracy in anything said
or heard.

**To the maximum extent the law allows, the author accepts no liability** for
any loss or harm arising from these features, including missed, wrong, or
failed calls, decisions made on their basis, money spent, appointments made or
missed, information disclosed, or any indirect or consequential loss. If the
LICENSE does not cover it, it is not covered.

Remedy speaks on your behalf and can be wrong. Check anything that matters.

## 3. The law where you live is your responsibility

You are responsible for using these features lawfully. Rules differ sharply by
country and, in the United States, by state. The ones that catch people out:

- **Recording consent.** Many places require all parties to consent before a
  call is recorded. Some require only one. Recording defaults to announcing
  itself; if you turn that off, the consequences are yours.
- **Disclosure that it is an AI.** A growing number of jurisdictions require an
  automated caller to say so. Disclosure is **on by default** for this reason.
  You can turn it off per contact; if you do, that is your decision and your
  exposure.
- **Automated and unsolicited calling.** Rules on auto-dialing, robocalls, and
  do-not-call lists (in the US, the TCPA among others) carry real penalties,
  including per-call statutory damages. Remedy places one call at a time, at
  your direction — it is not a dialer, and must not be used as one.
- **Impersonation and caller ID.** Do not use these features to impersonate
  another person, or to present a number you are not entitled to use.
- **Voice cloning.** Your own voice may be cloned only for a task you name, and
  it expires. Do not supply anyone else's voice.

If you are unsure whether something is lawful where you are, assume it is not
until you have checked.

## 4. Other people's services and software

- **Phone service** — trunks, VoIP accounts, carriers, numbers — is a contract
  between **you and that provider**, under their terms, at their prices. Their
  fees, per-minute charges, and taxes are yours. Some providers restrict
  automated or AI use; check before you rely on it.
- **Cloud calling apps** (the Android VM option) route your calls through that
  company. Your audio and metadata reach them under **their** privacy terms, not
  Remedy's. Choose a different option if that is not acceptable.
- **Downloaded components.** Nothing telephony-related ships with Remedy. Each
  piece — the SIP engine, speech models, Android images — is fetched **only when
  you ask for it**, from its own publisher, under its own licence, recorded in
  [THIRD_PARTY.md](./THIRD_PARTY.md). Their licences govern their use.

## 5. What stays on your machine

With the SIP, wired-phone, and Bluetooth options, call audio and transcripts
stay on this PC. The Android VM option is the exception: the call itself travels
through a third-party service by design.

Transcripts and call notes are written under `~/.remedy` and are yours. Deleting
them is deleting them; there is no copy elsewhere.

## 6. Limits that are not configurable

Regardless of settings or approval mode, Remedy will not:

- claim to be a human when asked directly;
- read out card numbers, security codes, passwords, or one-time codes;
- agree to a payment, contract, or cancellation without you;
- answer identity-verification questions on your behalf without you present;
- dial lists, run campaigns, or call strangers unprompted.

## 7. Changes

If these terms change materially, you will be asked again, in conversation, and
told what changed. The version you agreed to is recorded under
`~/.remedy/telephony/consent.json`.

---

*This is a plain-language statement of risk and responsibility, written by the
project, not legal advice. Telephony, recording, and automated-calling law is
genuinely complicated and carries real penalties; anyone shipping this
commercially should have a lawyer review it.*
