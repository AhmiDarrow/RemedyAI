"""Shared pytest fixtures and env defaults."""

from __future__ import annotations

import os

# Default: existing API tests run without Bearer (local unit suite).
# Explicit auth tests set REMEDY_API_AUTH=1 themselves.
os.environ.setdefault("REMEDY_API_AUTH", "0")
