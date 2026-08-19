"""No regex in the tree may blow up on hostile input.

Many of these run against text Remedy does not control — model output, shell
commands the model composed, file contents, web pages. A pattern with nested
quantifiers can take exponential time on a crafted string, and the symptom is
not an error: the turn simply stops responding.

Twelve patterns in the tree have nested-quantifier *shapes*. All of them turn
out to be linear, because the alternatives do not overlap. This test is what
keeps that true for the next one added.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import time

import pytest

import remedy

#: Inputs shaped to make a backtracking engine explore every split.
ADVERSARIAL = [
    lambda n: "a" * n,
    lambda n: "a" * n + "!",
    lambda n: "a." * n + "!",
    lambda n: "a/" * n + "!",
    lambda n: "a " * n + "!",
    lambda n: "/" + "a" * n,
    lambda n: '"' + "\\" * n,
    lambda n: "-" * n + "x",
    lambda n: "0" * n + ".",
]

#: Small enough that the suite stays quick, large enough that anything
#: superlinear is unmistakable — exponential blowup shows up long before here.
_N = 600
_BUDGET_MS = 250.0


def _all_patterns() -> list[tuple[str, str, re.Pattern]]:
    found: list[tuple[str, str, re.Pattern]] = []
    for mod_info in pkgutil.walk_packages(remedy.__path__, prefix="remedy."):
        if ".bundled_skills." in mod_info.name:
            continue
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception:  # pragma: no cover — import health has its own test
            continue
        for name, value in vars(mod).items():
            if isinstance(value, re.Pattern):
                found.append((mod_info.name, name, value))
    return found


def test_there_are_patterns_to_check():
    assert len(_all_patterns()) > 200


def test_no_pattern_backtracks_catastrophically():
    slow: list[str] = []
    for mod, name, rx in _all_patterns():
        for seed in ADVERSARIAL:
            text = seed(_N)
            start = time.perf_counter()
            try:
                rx.search(text)
            except Exception:  # pragma: no cover — a pattern that rejects input
                continue
            elapsed = (time.perf_counter() - start) * 1000
            if elapsed > _BUDGET_MS:
                slow.append(f"{mod}.{name}: {elapsed:.0f} ms on {len(text)} chars")
                break
    assert not slow, "regexes that backtrack on hostile input:\n  " + "\n  ".join(slow)


@pytest.mark.parametrize(
    ("module", "attr"),
    [
        ("remedy.core.shell_write_jail", None),
        ("remedy.core.provider_sanitize", None),
        ("remedy.core.computer.router", None),
    ],
)
def test_the_patterns_that_see_model_output_stay_linear(module, attr):
    """Named separately: these read text the model composed, so they are the
    ones an injected prompt could aim at."""
    mod = importlib.import_module(module)
    patterns = [v for v in vars(mod).values() if isinstance(v, re.Pattern)]
    assert patterns, f"{module} has no compiled patterns any more"
    for rx in patterns:
        for seed in ADVERSARIAL:
            text = seed(_N)
            start = time.perf_counter()
            with __import__("contextlib").suppress(Exception):
                rx.search(text)
            assert (time.perf_counter() - start) * 1000 <= _BUDGET_MS, rx.pattern[:80]
