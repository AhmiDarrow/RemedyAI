"""OpenClaw-to-Remedy migration tools.

Migrates skills and configurations from OpenClaw/ClawHub into the Remedy
framework. The core migration logic lives in `remedy.migrate.from_hermes`
as `migrate_from_openclaw` (shared adapter-based pipeline).

Re-exported here for a clean import path:
    from remedy.migrate.from_openclaw import migrate_from_openclaw
"""

from remedy.migrate.from_hermes import migrate_from_openclaw

__all__ = ["migrate_from_openclaw"]
