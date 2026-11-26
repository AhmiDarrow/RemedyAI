# Security model and safeguards

This page says what Remedy protects, which settings control it, and what each protection does not cover. It is written so you can decide for yourself; nothing here is hidden behind a default.

Remedy runs on your PC with your account's rights. The main risk is not someone breaking in from outside. It is a **hidden instruction**: text in a web page, a file, an email or a message that tells the model to do something you did not ask for. The protections below are built around one rule: an instruction that did not come from you must not be able to act for you without you seeing it.

## Who counts as you

| Where a turn comes from | Treated as |
|---|---|
| The desktop app, the web UI on this PC, the command line | You |
| Your paired phone (Remedy Connect) | You. It reaches the PC through an encrypted, authenticated tunnel. |
| A messenger chat (Telegram, Signal and so on) | Outside, even when the sender is on that channel's allow list |
| Another agent (A2A and other interop protocols) | Outside |
| A routine or background job **you** had Remedy create | You |
| A routine or background job created because someone **outside** asked | Outside, for as long as it exists |

A turn from outside asks before every change, whatever the approval mode, never uses your standing approvals, cannot change settings or house rules, does not teach Remedy new skills, and stops after 200 model rounds.

## Approval modes

- **Ask**: changes wait for your yes.
- **Auto**: routine work inside your project runs without asking. The safeguards below still ask.
- **Full**: everything runs without asking. Use it knowingly.

In every mode these always need your approval on a card, for that exact action: sending mail or a message, payments, entering credentials, a sensitive settings change (model provider and address, keys, approval mode, access scope, project folder, privacy, phone access, the safeguards on this page).

Approving **Just this once** covers the one call you were shown. An approval is tied to the whole request, not to the part that fits on the card.

## Safeguards and their settings

Each of these only ever adds a question. None of them lets through what the approval mode would have stopped. You change them in Settings → Security & power → Safeguards (desktop) or Settings → Advanced → Safeguards (web UI), or in `config.toml`. Remedy cannot change them by herself, and a paired phone cannot change them.

### Shell commands in Auto: `shell_in_auto`

| Value | What happens in Auto mode |
|---|---|
| `guard` (default) | A plain command inside your project runs. A command waits for you when it names a place keys are kept, reaches outside the folders the conversation may write, contacts a network address, or is built while it runs (variables, `$(...)`, encoded PowerShell, `python -c`, piping into a shell). |
| `ask` | Every shell command waits for you. |
| `allow` | No shell command waits. This is how Remedy behaved before 1.1.0. |

Why it exists: the write jail checks where a program is started and with which arguments, but a shell command is one piece of text, and text can be written so the jail cannot read it.

What it does not cover:

- It reads the text of the command. A script or program the command starts can do things its name does not say. This is a filter, not a sandbox.
- **Full** mode and `allow` skip it.
- Expect extra questions in Auto for commands that use shell variables or fetch from the network. Approve one **for this session** and the same command stops asking.

### Web pages that could carry data out: `web_fetch_guard`

| Value | What happens |
|---|---|
| `payload` (default) | Fetching an ordinary page needs no approval. A fetch waits for you when its address has room to carry data: more than 256 characters after the `?` or `#`, or one path step longer than 96 characters. |
| `ask` | Every fetch waits for you. |
| `off` | No fetch waits. |

Why it exists: reading a page is harmless, but an address can also be used to send something. A hidden instruction could have Remedy read a file and then "fetch" an address with the file's contents in it.

What it does not cover:

- Small amounts of data can still leave in several short addresses.
- **Full** approval mode does not ask (a turn from outside still does).
- Web search queries are sent to the search provider you configured.
- Fetches to this PC, your home network and cloud metadata addresses are always refused; that is separate and has no off switch.

### What people on your messenger allow lists may read: `messenger_tools`

| Value | Without asking you, they can have Remedy use |
|---|---|
| `safe` (default) | Only tools that show nothing of yours: web search and fetch, text helpers, her skills. |
| `all` | Every read-only tool: your files, memory, mail list, calendar, other conversations, clipboard, screen. |
| `none` | Nothing. Every tool call waits for you. |

It starts at `safe` so that nothing of yours is readable from a chat until you decide it should be. With `safe`, asking her from a messenger about your calendar, files or memory raises an approval on the PC. If you use a messenger to reach your own assistant and only you are on that channel's allow list, `all` is the convenient choice. Leave it at `safe` if anyone else is on an allow list.

### What other agents may read: `peer_tools`

Same three values. The default is `safe`. It also applies to routines and background jobs that were created because someone outside asked.

What these two do not cover: whatever is allowed comes back in the reply to the sender. `all` means the sender can ask for anything Remedy can read.

### Largest request to the local API: `api_max_body_mb`

Default `128`. A request that says it is larger is refused with HTTP 413; one that does not say is cut off at the limit. `0` removes the limit.

## Protections with no off switch

- **Remedy's own secrets** (`.remedy/auth`): file tools never open them and searches never walk into them, including when your project folder is your home folder, in full access scope, and through Windows short names and alternate spellings.
- **The local API** listens on `127.0.0.1` only, needs a token, refuses token requests from other web sites, and the desktop checks it is talking to Remedy's own runtime before sending the token.
- **Phone**: a new phone can be paired only from this PC or the home network. Removing a phone cuts it off on every path. A phone cannot change the model address, approval mode, access scope, project folder or the safeguards.
- **Downloads Remedy makes herself** (voice and listening models) are checked against pinned checksums.

## Known limits

We would rather you read these here than find them out.

- **No operating-system sandbox.** Remedy's tools run as your user. The write jail and the shell safeguard decide by reading paths and command text. A program that Remedy starts is not confined once it runs.
- **Full mode trusts the model completely.** A hidden instruction that gets past the model's own judgement can do anything your account can, except the always-approve actions listed above.
- **Reading is not asked about for you.** In your own turns, read-only tools run without approval in every mode. If Remedy reads a page with a hidden instruction and then reads a private file, the remaining barrier to that file leaving the PC is the fetch safeguard and the approval on sending mail and messages.
- **Other programs running as you** can read Remedy's files, including her memory database. Memory encryption at rest is a separate setting (`memory_encrypt`).
- **A messenger sender, once you set `messenger_tools` to `all`,** can read whatever Remedy can.

## Reporting a problem

Open an issue on the repository. For something that should not be public yet, say only that you have a security report and ask the maintainer for a private way to send it.
