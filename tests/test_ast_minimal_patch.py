"""Replacing one definition instead of rewriting a whole file.

The point is blast radius: when the model only needs to fix one function, the
rest of the module should not be at risk of being regenerated slightly
differently. That makes the guarantees mostly about what is *left alone* —
everything above and below the target survives byte for byte, and a patch that
does not parse leaves the file exactly as it was.

The last one is the important one. A half-applied surgical edit is worse than a
refused one, because the file still compiles and nobody notices.
"""

from __future__ import annotations

import pytest

from remedy.core.build_ast_patch import (
    extract_def_source,
    replace_top_level_def,
)

MODULE = '''"""A module docstring."""

import os

CONSTANT = 42


def before():
    return "untouched above"


def target(a, b):
    """The one being replaced."""
    return a + b


class After:
    def method(self):
        return "untouched below"
'''


# --- reading a definition out ------------------------------------------------


def test_a_function_is_found_by_name():
    src = extract_def_source(MODULE, "target")
    assert src.startswith("def target(a, b):")
    assert "return a + b" in src


def test_a_class_is_found_by_name():
    assert extract_def_source(MODULE, "After").startswith("class After:")


def test_an_async_function_is_found():
    src = extract_def_source("async def fetch():\n    return 1\n", "fetch")
    assert src.startswith("async def fetch():")


def test_a_name_that_is_not_there_returns_nothing():
    assert extract_def_source(MODULE, "nonexistent") is None


def test_a_nested_function_is_not_a_top_level_definition():
    """Only top-level defs are surgical targets; a nested one has no span here."""
    src = "def outer():\n    def inner():\n        return 1\n    return inner\n"
    assert extract_def_source(src, "inner") is None


def test_a_module_that_does_not_parse_returns_nothing():
    assert extract_def_source("def broken(:\n", "broken") is None


def test_an_empty_module_returns_nothing():
    assert extract_def_source("", "anything") is None


# --- replacing ----------------------------------------------------------------


def test_a_replacement_takes_effect():
    r = replace_top_level_def(MODULE, "target", "def target(a, b):\n    return a * b\n")
    assert r.ok
    assert "return a * b" in r.source
    assert "return a + b" not in r.source


def test_everything_above_and_below_survives():
    """The whole point — the rest of the file is not at risk."""
    r = replace_top_level_def(MODULE, "target", "def target(a, b):\n    return a * b\n")
    assert '"""A module docstring."""' in r.source
    assert "import os" in r.source
    assert "CONSTANT = 42" in r.source
    assert "untouched above" in r.source
    assert "untouched below" in r.source


def test_the_result_still_compiles():
    r = replace_top_level_def(MODULE, "target", "def target(a, b):\n    return a * b\n")
    compile(r.source, "<test>", "exec")


def test_the_method_is_reported():
    r = replace_top_level_def(MODULE, "target", "def target():\n    return 1\n")
    assert r.method == "ast_replace"
    assert r.symbol == "target"


def test_a_definition_that_is_not_there_is_appended():
    r = replace_top_level_def(MODULE, "brand_new", "def brand_new():\n    return 1\n")
    assert r.ok
    assert "def brand_new():" in r.source
    assert "untouched below" in r.source
    compile(r.source, "<test>", "exec")


def test_a_class_can_be_replaced():
    r = replace_top_level_def(MODULE, "After", "class After:\n    pass\n")
    assert r.ok
    assert "untouched below" not in r.source
    assert "untouched above" in r.source


def test_a_replacement_with_more_lines_than_the_original_fits():
    body = "def target(a, b):\n" + "".join(f"    x{i} = {i}\n" for i in range(20)) + "    return a\n"
    r = replace_top_level_def(MODULE, "target", body)
    assert r.ok
    assert "x19 = 19" in r.source
    compile(r.source, "<test>", "exec")


def test_a_replacement_missing_its_trailing_newline_still_works():
    r = replace_top_level_def(MODULE, "target", "def target():\n    return 1")
    assert r.ok
    compile(r.source, "<test>", "exec")


# --- refusals: the file must be left exactly as it was ------------------------


def test_an_empty_patch_is_refused_and_changes_nothing():
    r = replace_top_level_def(MODULE, "target", "")
    assert r.ok is False
    assert r.source == MODULE
    assert "empty patch" in r.error


def test_a_patch_that_does_not_parse_is_refused_and_changes_nothing():
    """A half-applied surgical edit still compiles and nobody notices."""
    r = replace_top_level_def(MODULE, "target", "def target(:\n  broken\n")
    assert r.ok is False
    assert r.source == MODULE
    assert "SyntaxError" in r.error


def test_a_base_file_that_does_not_parse_is_refused():
    broken = "def oops(:\n"
    r = replace_top_level_def(broken, "oops", "def oops():\n    return 1\n")
    assert r.ok is False
    assert r.source == broken
    assert "base SyntaxError" in r.error


def test_a_patch_of_only_whitespace_is_refused():
    r = replace_top_level_def(MODULE, "target", "   \n  \n")
    assert r.ok is False
    assert r.source == MODULE


def test_a_patch_that_breaks_the_merged_file_is_refused():
    """Each half parses; together they do not. Only the merge can catch it."""
    r = replace_top_level_def(
        "def target():\n    return 1\n", "target", "return 2\n"
    )
    assert r.ok is False
    assert "return 1" in r.source


# --- what the model actually sends -------------------------------------------


def test_a_markdown_fence_around_the_patch_is_stripped():
    """Models wrap code in fences constantly; refusing that costs a whole turn."""
    r = replace_top_level_def(
        MODULE, "target", "```python\ndef target():\n    return 99\n```"
    )
    assert r.ok
    assert "return 99" in r.source
    assert "```" not in r.source


def test_an_unlabelled_fence_is_stripped_too():
    r = replace_top_level_def(MODULE, "target", "```\ndef target():\n    return 99\n```")
    assert r.ok
    assert "```" not in r.source


def test_a_decorated_function_does_not_end_up_with_two_decorators():
    """A decorator sits above the def line, which is where the span starts."""
    src = "import functools\n\n\n@functools.cache\ndef target():\n    return 1\n"
    r = replace_top_level_def(src, "target", "@functools.cache\ndef target():\n    return 2\n")
    assert r.ok
    assert r.source.count("@functools.cache") == 1, r.source
    compile(r.source, "<test>", "exec")


def test_a_decorated_function_keeps_its_decorator_when_the_patch_omits_it():
    src = "import functools\n\n\n@functools.cache\ndef target():\n    return 1\n"
    r = replace_top_level_def(src, "target", "def target():\n    return 2\n")
    assert r.ok
    assert "@functools.cache" in r.source
    compile(r.source, "<test>", "exec")


def test_a_stacked_set_of_decorators_is_replaced_wholesale():
    src = (
        "import functools\n\n\n@functools.cache\n@staticmethod\n"
        "def target():\n    return 1\n"
    )
    r = replace_top_level_def(
        src, "target", "@functools.cache\ndef target():\n    return 2\n"
    )
    assert r.ok
    assert r.source.count("@functools.cache") == 1
    assert "@staticmethod" not in r.source, "the patch replaced the decorators"
    compile(r.source, "<test>", "exec")


def test_an_undecorated_function_is_unaffected_by_the_decorator_handling():
    r = replace_top_level_def(MODULE, "target", "@property\ndef target(self):\n    return 1\n")
    assert r.ok
    assert r.source.count("@property") == 1
    assert "CONSTANT = 42" in r.source


@pytest.mark.parametrize("symbol", ["target", "After", "before"])
def test_replacing_each_definition_leaves_a_compiling_module(symbol):
    kind = "class" if symbol == "After" else "def"
    body = f"{kind} {symbol}:\n    pass\n" if kind == "class" else f"def {symbol}():\n    pass\n"
    r = replace_top_level_def(MODULE, symbol, body)
    assert r.ok
    compile(r.source, "<test>", "exec")
