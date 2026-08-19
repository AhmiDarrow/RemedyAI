"""Shared pytest fixtures and env defaults."""

from __future__ import annotations

import os

# Default: existing API tests run without Bearer (local unit suite).
# Explicit auth tests set REMEDY_API_AUTH=1 themselves.
os.environ.setdefault("REMEDY_API_AUTH", "0")

# The suite must never write to — or drive — the owner's real installation.
# Several subsystems (the computer host bridge above all) resolve their home
# from REMEDY_HOME and fall back to ~/.remedy. A test that builds one without
# an explicit tmp home then enqueues jobs into the LIVE queue, which a running
# Desktop app claims and executes: real clicks, real keystrokes, real browser
# navigation on whoever is running the tests. Point the whole suite at a
# throwaway home before any test module is imported.
#
# A test that genuinely needs the real home can still monkeypatch REMEDY_HOME
# back; this is a floor, not a lock.
if not os.environ.get("REMEDY_HOME"):
    import tempfile

    os.environ["REMEDY_HOME"] = tempfile.mkdtemp(prefix="remedy-test-home-")
