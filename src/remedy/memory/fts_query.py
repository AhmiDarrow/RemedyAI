"""Build a safe SQLite FTS5 ``MATCH`` expression from free text.

FTS5 has its own query grammar: ``.`` ``;`` ``:`` ``-`` ``(`` and bare
``AND``/``OR``/``NOT`` all mean something, so passing a user's sentence
straight into ``MATCH`` raises ``fts5: syntax error`` on anything that is not
plain words. ``MemoryStore.search`` then falls back to a ``LIKE '%q%'`` scan
over every row — correct, but a full-table scan that grows with memory and was
part of a 99 s event-loop stall.

:func:`fts5_match_query` turns the text into a sequence of double-quoted
phrase tokens (``"foo" "bar"``): implicit AND, no operators, no syntax errors.
"""

from __future__ import annotations

import re

# Word characters across scripts; FTS5's unicode61 tokenizer splits on
# roughly the same boundaries, so each token maps to one indexed term.
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)

# FTS5 keywords — only operators when bare; quoting makes them plain terms,
# but dropping them matches user intent better ("cats and dogs" ≠ AND query).
_STOP_OPERATORS = {"and", "or", "not", "near"}

MAX_TOKENS = 32


def fts5_tokens(query: str, *, max_tokens: int = MAX_TOKENS) -> list[str]:
    """Plain word tokens from *query*, operators and punctuation removed."""
    out: list[str] = []
    for tok in _TOKEN_RE.findall(str(query or "")):
        t = tok.strip("_")
        if not t or t.lower() in _STOP_OPERATORS:
            continue
        out.append(t)
        if len(out) >= max_tokens:
            break
    return out


def fts5_match_query(query: str, *, prefix_last: bool = False) -> str:
    """Return an FTS5 ``MATCH`` expression that cannot raise a syntax error.

    Each token is wrapped in double quotes (the only escape FTS5 needs is
    doubling a quote, which the tokenizer already strips). Returns ``""`` when
    nothing searchable remains — callers should return no rows rather than
    run ``MATCH ''``.

    *prefix_last* appends ``*`` to the final token so a half-typed word still
    matches (``"mem" "sto"*``).
    """
    toks = fts5_tokens(query)
    if not toks:
        return ""
    quoted = [f'"{t}"' for t in toks]
    if prefix_last:
        quoted[-1] = quoted[-1] + "*"
    return " ".join(quoted)
