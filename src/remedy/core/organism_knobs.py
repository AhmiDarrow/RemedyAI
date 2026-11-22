"""Advanced organism and token-budget knobs.

Defaults match the values already in production. Overrides live under
``[advanced]`` in config.toml (Settings → Advanced, hidden in simple mode)
or matching environment variables, which still win when set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

Kind = Literal["int", "float", "bool"]


@dataclass(frozen=True)
class Knob:
    key: str
    default: int | float | bool
    kind: Kind
    minimum: int | float | None
    maximum: int | float | None
    unit: str
    group: str
    help: str
    env: str | None = None
    env_invert: bool = False  # env=1 means this bool is False


KNOBS: tuple[Knob, ...] = (
    Knob("improve_tick_s", 60, "int", 20, 3600, "s", "Improve tick",
         "Default unattended learn interval."),
    Knob("improve_tick_min_s", 20, "int", 5, 600, "s", "Improve tick",
         "Fastest improve tick after heavy use."),
    Knob("improve_tick_max_s", 900, "int", 60, 7200, "s", "Improve tick",
         "Slowest improve tick when nothing changed or the machine is busy."),
    Knob("improve_heavy_window_s", 300, "int", 30, 3600, "s", "Improve tick",
         "How long a turn_end / after_tool_batch counts as heavy use."),
    Knob("turn_end_timeout_s", 30, "int", 5, 120, "s", "Learn timeouts",
         "Cap for background prompt.turn_end after a turn."),
    Knob("after_tool_batch_timeout_s", 10, "int", 2, 60, "s", "Learn timeouts",
         "Cap for mid-turn prompt.after_tool_batch."),
    Knob("pulse_learn_timeout_s", 45, "int", 10, 180, "s", "Learn timeouts",
         "Cap for one hearth partner.improve pulse."),
    Knob("soul_inject_chars", 1400, "int", 200, 8000, "chars", "Token budgets",
         "Max characters of soul injected after the cache-stable marker."),
    Knob("max_result_chars", 24_000, "int", 2_000, 256_000, "chars", "Token budgets",
         "Max characters of each tool result kept for the model.",
         env="REMEDY_REACT_MAX_RESULT_CHARS"),
    Knob("soul_max_bytes", 64 * 1024, "int", 8 * 1024, 1024 * 1024, "bytes", "Caps",
         "Hard cap on the on-disk soul field."),
    Knob("residue_max_bytes", 256 * 1024, "int", 16 * 1024, 4 * 1024 * 1024, "bytes", "Caps",
         "Hard cap on residue dumps during compact."),
    Knob("journal_max_bytes", 64 * 1024, "int", 4 * 1024, 1024 * 1024, "bytes", "Caps",
         "Hard cap on the vigil journal tail."),
    Knob("learn_log_max_bytes", 256 * 1024, "int", 16 * 1024, 4 * 1024 * 1024, "bytes", "Caps",
         "Learn-log file size cap."),
    Knob("learn_log_max_lines", 400, "int", 50, 10_000, "lines", "Caps",
         "Learn-log line cap."),
    Knob("max_episodes", 12, "int", 4, 128, "count", "Caps",
         "Soul episodes kept after salience retention."),
    Knob("max_future_dreams", 8, "int", 1, 64, "count", "Caps",
         "Queued future-dream notes kept."),
    Knob("max_phenotypes", 128, "int", 16, 1024, "count", "Caps",
         "Skill-genome phenotypes kept."),
    Knob("episode_recent_protect", 4, "int", 1, 32, "count", "Caps",
         "Newest soul episodes always kept."),
    Knob("episode_halflife_days", 30.0, "float", 1.0, 365.0, "days", "Caps",
         "Forgetting-curve half-life since last recall."),
    Knob("max_tensions", 16, "int", 2, 128, "count", "Caps",
         "Open relational tensions kept."),
    Knob("max_self_lessons", 24, "int", 2, 128, "count", "Caps",
         "Self-lessons kept on the soul field."),
    Knob("max_voice_markers", 12, "int", 2, 64, "count", "Caps",
         "Voice markers kept on the soul field."),
    Knob("max_open_threads", 10, "int", 2, 64, "count", "Caps",
         "Open threads kept on the soul field."),
    Knob("vigil_offer_turns", 12, "int", 1, 200, "turns", "Cadence",
         "Turns together before the one-time vigil offer."),
    Knob("dream_cooldown_s", 120.0, "float", 10.0, 86_400.0, "s", "Cadence",
         "Minimum seconds between dream passes."),
    Knob("organism_compact_every_s", 86_400.0, "float", 300.0, 30 * 86_400.0, "s", "Cadence",
         "How often the organism cycle may compact residue."),
    Knob("organism_stall_after_s", 7 * 86_400.0, "float", 3600.0, 90 * 86_400.0, "s", "Cadence",
         "Idle time before the organism treats a skill path as stalled."),
    Knob("voice_evolution", True, "bool", None, None, "", "Voice & self-inject",
         "Daily voice drift at worker start. Off via REMEDY_NO_VOICE_EVOLUTION=1.",
         env="REMEDY_NO_VOICE_EVOLUTION", env_invert=True),
    Knob("self_inject_idle_s", 300, "int", 10, 7200, "s", "Voice & self-inject",
         "User-idle window before unattended code self-inject may run."),
    Knob("drives_enabled", True, "bool", None, None, "", "Alive",
         "Internal drives (curiosity, social, competence, rest) drift on the hearth tick."),
    Knob("drive_drift_rate", 0.04, "float", 0.005, 0.2, "", "Alive",
         "How far drives move toward a new value on each unattended tick."),
    Knob("proactive_reach_out", False, "bool", None, None, "", "Alive",
         "Opt-in: when a drive crosses the threshold, arm one gentle check-in. Off by default."),
    Knob("reach_out_threshold", 0.85, "float", 0.5, 1.0, "", "Alive",
         "Drive level that may arm a single reach-out offer."),
    Knob("reach_out_min_interval_s", 7 * 86_400.0, "float", 3600.0, 90 * 86_400.0, "s", "Alive",
         "Minimum time between reach-out offers. Never nagging."),
    Knob("autobiography_chars", 400, "int", 0, 2000, "chars", "Alive",
         "Max characters of the self-maintained autobiography injected after the cache marker."),
    Knob("affect_enabled", True, "bool", None, None, "", "Alive",
         "Affect state, shaped by outcomes and rapport, may lightly color tone."),
    Knob("affect_tone_weight", 0.15, "float", 0.0, 1.0, "", "Alive",
         "How strongly affect may influence tone. 0 turns the tone hint off."),
    Knob("voice_drift_from_affect", True, "bool", None, None, "", "Alive",
         "Daily voice drift follows affect and rapport instead of a random daily step."),
    Knob("outcome_learning", True, "bool", None, None, "", "Alive",
         "Track whether suggestions worked (accepted, fix held, unhappy) and feed the skill genome."),
    Knob("weekly_reflection", True, "bool", None, None, "", "Alive",
         "Weekly self-reflection may write proposed self-changes. They never apply without approval."),
    Knob("weekly_reflection_s", 7 * 86_400.0, "float", 3600.0, 90 * 86_400.0, "s", "Alive",
         "Minimum seconds between weekly reflection proposals."),
)

KNOB_BY_KEY: dict[str, Knob] = {k.key: k for k in KNOBS}


def _load_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(cfg, dict):
        return cfg
    try:
        from remedy.interfaces.config import load_config

        loaded = load_config()
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _coerce(knob: Knob, raw: Any) -> int | float | bool:
    if knob.kind == "bool":
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if knob.kind == "float":
        val: int | float = float(raw)
    else:
        val = int(float(raw))
    if knob.minimum is not None and val < knob.minimum:
        val = knob.minimum
    if knob.maximum is not None and val > knob.maximum:
        val = knob.maximum
    return val


def _from_env(knob: Knob) -> int | float | bool | None:
    if not knob.env:
        return None
    raw = os.environ.get(knob.env)
    if raw is None or str(raw).strip() == "":
        return None
    if knob.env_invert and knob.kind == "bool":
        # REMEDY_NO_VOICE_EVOLUTION=1 means voice_evolution is off.
        return str(raw).strip() not in ("1", "true", "yes", "on")
    try:
        return _coerce(knob, raw)
    except (TypeError, ValueError):
        return None


def _legacy(knob: Knob, cfg: dict[str, Any]) -> Any:
    if knob.key == "self_inject_idle_s":
        si = cfg.get("self_inject")
        if isinstance(si, dict) and "idle_seconds" in si:
            return si.get("idle_seconds")
    return None


def resolve(key: str, cfg: dict[str, Any] | None = None) -> int | float | bool:
    """Effective value: env (if mapped) > ``[advanced]`` > legacy > default."""
    knob = KNOB_BY_KEY[key]
    env_val = _from_env(knob)
    if env_val is not None:
        return env_val
    data = _load_cfg(cfg)
    table = data.get("advanced")
    if isinstance(table, dict) and key in table and table[key] is not None:
        try:
            return _coerce(knob, table[key])
        except (TypeError, ValueError):
            pass
    legacy = _legacy(knob, data)
    if legacy is not None:
        try:
            return _coerce(knob, legacy)
        except (TypeError, ValueError):
            pass
    return knob.default


def resolve_int(key: str, cfg: dict[str, Any] | None = None) -> int:
    return int(resolve(key, cfg))


def resolve_float(key: str, cfg: dict[str, Any] | None = None) -> float:
    return float(resolve(key, cfg))


def resolve_bool(key: str, cfg: dict[str, Any] | None = None) -> bool:
    return bool(resolve(key, cfg))


def effective_table(cfg: dict[str, Any] | None = None) -> dict[str, int | float | bool]:
    data = _load_cfg(cfg)
    return {k.key: resolve(k.key, data) for k in KNOBS}


def schema() -> list[dict[str, Any]]:
    return [
        {
            "key": k.key,
            "default": k.default,
            "kind": k.kind,
            "min": k.minimum,
            "max": k.maximum,
            "unit": k.unit,
            "group": k.group,
            "help": k.help,
            "env": k.env,
        }
        for k in KNOBS
    ]


def normalize_table(raw: Any) -> dict[str, int | float | bool]:
    """Clamp known keys; drop unknown keys and values that match defaults."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int | float | bool] = {}
    for key, value in raw.items():
        knob = KNOB_BY_KEY.get(str(key))
        if knob is None or value is None:
            continue
        try:
            coerced = _coerce(knob, value)
        except (TypeError, ValueError):
            continue
        if coerced != knob.default:
            out[knob.key] = coerced
    return out
