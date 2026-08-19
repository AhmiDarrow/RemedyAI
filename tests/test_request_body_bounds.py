"""No route may buffer an unbounded request body.

``request.body()`` reads everything before anyone looks at it — so a route that
keeps only the first kilobyte still pays for the whole megabyte first. The
webhook routes already stream-cap; the generic one under /api/memory did not.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

ROUTES = Path("src/remedy/interfaces/routes")


@pytest.mark.asyncio
async def test_an_oversized_content_length_is_refused_before_reading():
    from remedy.interfaces.routes.webhooks import read_body_capped

    class _Req:
        headers = {"content-length": str(50 * 1024 * 1024)}

        async def stream(self):  # pragma: no cover — must not be reached
            raise AssertionError("body was read despite an oversized header")
            yield b""

    with pytest.raises(HTTPException) as exc:
        await read_body_capped(_Req())
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_a_body_that_lies_about_its_length_is_still_capped():
    from remedy.interfaces.routes.webhooks import read_body_capped

    class _Req:
        headers = {"content-length": "10"}

        async def stream(self):
            for _ in range(100):
                yield b"x" * 65536

    with pytest.raises(HTTPException) as exc:
        await read_body_capped(_Req(), max_bytes=1024)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_an_ordinary_body_comes_through_whole():
    from remedy.interfaces.routes.webhooks import read_body_capped

    class _Req:
        headers: dict[str, str] = {}

        async def stream(self):
            yield b'{"a":'
            yield b"1}"

    assert await read_body_capped(_Req()) == b'{"a":1}'


def test_the_old_private_name_still_works():
    """Anything already importing it must not break."""
    from remedy.interfaces.routes import webhooks

    assert webhooks._read_body_capped is webhooks.read_body_capped


def test_no_route_reads_a_body_without_a_cap():
    """Whole-class guard across every route module."""
    offenders: list[str] = []
    for path in sorted(ROUTES.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        text = path.read_text(encoding="utf-8")
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call)
                    and getattr(n.func, "attr", "") == "body"
                    and getattr(getattr(n.func, "value", None), "id", "") == "request"):
                # voice.py checks len(body) right after and answers 413
                if "_MAX_AUDIO_BYTES" in text:
                    continue
                offenders.append(f"{path.relative_to(ROUTES)}:{n.lineno} request.body()")
    assert not offenders, (
        "unbounded request bodies:\n  " + "\n  ".join(offenders)
        + "\n(use routes.webhooks.read_body_capped)"
    )
