# Web etiquette — how Remedy reads the public web

Remedy's web tools are off until an owner turns them on
(`web_tools_enabled = true`). This is what they do once they are on, and what
is left for the owner to decide.

## What every fetch does

- **Names itself.** The User-Agent is
  `RemedyAI-WebFetch/<version> (+https://github.com/AhmiDarrow/RemedyAI)`.
  A site owner who wants to rate-limit or block Remedy specifically can, and
  the version tracks the package rather than a frozen literal.
- **Reads robots.txt first and obeys it.** `Disallow` for `*` or for
  `RemedyAI-WebFetch` means the page is skipped and the model is told why.
  Answers are cached per origin for an hour. An unreachable or unparseable
  robots.txt is treated as no stated rule, which is how browsers and most
  crawlers treat it — a missing file is not a refusal.
- **Leaves a gap.** At least a second between two hits on the same host, and
  longer when robots.txt states a `Crawl-delay`. A crawl delay longer than ten
  seconds is refused with an explanation rather than slept through, so one
  hostile value cannot hold a turn open.
- **Re-checks after a redirect.** A hop onto a different host consults that
  host's robots.txt before the body is used.
- **Refuses private targets.** Loopback, RFC1918, link-local, and cloud
  metadata addresses are blocked, DNS is pinned on resolve, and every redirect
  hop is revalidated (see `tests/test_web_fetch_ssrf.py`).

`web_respect_robots = false` turns the robots gate off. It exists because an
owner sometimes needs a page on their own site that their own rule covers.
Pacing and the self-identifying User-Agent stay on either way.

## Human-check walls are never solved

CAPTCHA, hCaptcha, reCAPTCHA, Turnstile, and press-and-hold walls are owner
handoffs. Remedy stops and asks; she does not click through them, and there is
no fingerprint spoofing, proxy rotation, or stealth-driver machinery anywhere
in the tree. The in-app browser is a real Edge/WebView2 view; its mobile mode
is the same device emulation a phone browser's "Request desktop site" toggle
performs, and the owner can flip it.

## Search

There is no keyless, terms-clean, general web-search API to point at. The
services with their own index (Brave, Mojeek, Marginalia) want a key; the
keyless routes aggregate by scraping somebody else. So search is layered:

1. **A search instance the owner runs.** Set `web_search_url` to a SearXNG
   base URL and Remedy queries its JSON API. Nobody else is in the loop, there
   is no quota, and no third party's terms apply between Remedy and the owner.
   The instance must have `json` in `search.formats` — a stock install answers
   403 and Remedy reports that rather than guessing.

   A self-hosted instance usually listens on loopback or the LAN, which the
   SSRF guard is there to refuse. That hole is opened by hand in `config.toml`
   with `web_search_url_allow_private = true`, and deliberately **cannot** be
   set through `update_settings` — otherwise anything able to write settings
   could point Remedy at an internal address.

2. **DuckDuckGo's no-JavaScript results page**, once the owner accepts it.
   Their robots.txt allows every agent on that host (`Allow: /`), Remedy names
   itself, and requests are paced. It is still automated use of a service
   someone else pays for: they throttle it, they may block it, and the shape
   of the page can change without notice. Until `web_search_scraping_ack` is
   set, `web_search` returns a question instead of results, and Remedy asks
   the owner rather than deciding for them. Background passes that have nobody
   to ask simply return nothing.

Research tools are separate and unaffected: arXiv, Crossref, OpenAlex, PubMed,
and Semantic Scholar are queried through their own documented APIs, with the
polite-pool mailto convention (`research_mailto`).

## What is left to the owner

Remedy makes the requests politely and identifies herself. She does not decide
whether a given use is allowed:

- **Site terms.** A site's terms of service may restrict automated access even
  where robots.txt permits it, and even for a logged-in account holder. That
  includes the search fallback above.
- **Logged-in sessions.** The browser rail can act inside sessions the owner
  is signed into. Many services prohibit automated use of an account, whoever
  drives it.
- **What happens to the results.** Copyright, database rights, and licence
  terms follow the content out of the page and into whatever is built with it.
- **Jurisdiction.** Text- and data-mining rules, machine-readable
  reservations, and personal-data law vary by where the owner and the site sit.

These are the owner's calls. Remedy is a tool operated by its owner; the
project makes no warranty that a particular use is permitted, and
responsibility for use of the software rests with the person running it.
The binding language is in `LICENSE` (sections 5–8 and 12–14): your use is
your action, third-party terms still apply, enabling a tool is not permission
from the site, and there is no warranty or liability beyond that license.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `web_tools_enabled` | `false` | Master switch for `web_fetch` / `web_search` |
| `web_respect_robots` | `true` | Obey robots.txt on fetched pages |
| `web_search_url` | unset | Base URL of a SearXNG instance the owner runs |
| `web_search_url_allow_private` | `false` | Allow a loopback/LAN search instance (config file only) |
| `web_search_scraping_ack` | `false` | Owner accepts the DuckDuckGo HTML fallback |

Implementation: `src/remedy/core/agent_web_tools.py`. Tests:
`tests/test_web_robots.py`, `tests/test_web_fetch_ssrf.py`.
