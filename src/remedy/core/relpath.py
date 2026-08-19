"""One correct way to normalise a repo-relative path.

Eight places in the tree wrote ``path.replace("\\\\", "/").lstrip("./")`` to turn
``./src/foo.py`` into ``src/foo.py``. ``str.lstrip`` takes a *set of characters*,
not a prefix, so it also ate the leading dot of every dotfile:

    ".github/workflows/ci.yml"  ->  "github/workflows/ci.yml"
    ".gitignore"                ->  "gitignore"

Wherever one side of a comparison normalised and the other did not, dotfiles
stopped matching — a self-inject rollback could not recognise the ``.github``
file it had just written, and glob/index lookups missed them entirely. This
repo has ``.github/``, ``.claude/`` and root dotfiles, so it is not theoretical.
"""

from __future__ import annotations

__all__ = ["norm_rel"]


def norm_rel(path: str | None) -> str:
    """Posix-slashed, leading ``./`` removed, dotfiles left intact."""
    p = str(path or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/") if p.startswith("//") else p
