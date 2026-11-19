"""LLM provider catalog — static defaults and free-tier options.

Split from ``config.py`` so routes/UI can import catalog data without loading
the full config I/O + normalize surface.
"""

from __future__ import annotations

import os
from typing import Any

# -- Provider catalog (defaults + model ownership) ---------------------------
# free_tier: none | free_key | local | instant
#   free_key = permanent free tier with free signup key (no card typical)
#   local    = runs on-device (Ollama)
#   instant  = no signup / no real key (guest demo)

DEMO_DUMMY_API_KEY = "unused"
DEMO_BASE_URL = "https://api.llm7.io/v1"

PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "demo": {
        "label": "Demo (Free)",
        "base_url": DEMO_BASE_URL,
        "auth": ["none"],
        "env_keys": [],
        "show_base_url": False,
        "free_tier": "instant",
        "badge": "No signup",
        "limits_blurb": (
            "Rate-limited guest chat only (third-party). Models come from the "
            "gateway itself. Add a real provider for agents / vision."
        ),
        "privacy_note": "Chat is sent to a free third-party API (not Remedy cloud). Prefer Ollama for private local use.",
        "key_docs_url": "https://llm7.io/",
        # Models come from the gateway's own /models listing, never a list
        # compiled into Remedy.
        # llm7 guest traffic is limited to ~1 request/second (HTTP 429 with
        # retry_after otherwise); consecutive agent rounds are paced to this.
        "min_request_interval_s": 1.1,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "auth": ["api_key"],
        "env_keys": ["OPENAI_API_KEY", "REMEDY_LLM_API_KEY"],
        "show_base_url": False,
        "free_tier": "none",
        "key_docs_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "auth": ["api_key"],
        "env_keys": ["ANTHROPIC_API_KEY"],
        "show_base_url": False,
        "free_tier": "none",
        "key_docs_url": "https://console.anthropic.com/settings/keys",
        "limits_blurb": (
            "Uses prepaid Anthropic API credits (console.anthropic.com). "
            "A Claude Pro / Max / Claude Code weekly limit does not pay for "
            "this. Do not paste sk-ant-oat login tokens here."
        ),
    },
    "google": {
        "label": "Google AI (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "auth": ["api_key"],
        "env_keys": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "show_base_url": False,
        "free_tier": "free_key",
        "badge": "Free key",
        "limits_blurb": "Generous free tier via AI Studio (region limits may apply).",
        "key_docs_url": "https://aistudio.google.com/app/apikey",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "auth": ["api_key"],
        "env_keys": ["DEEPSEEK_API_KEY"],
        "show_base_url": False,
        "free_tier": "none",
        "key_docs_url": "https://platform.deepseek.com/api_keys",
        # Fallback only — live GET /models is preferred when a key is present.
        # deepseek-chat / deepseek-reasoner retired 2026-07-24 → V4 ids.
        # Fallback only — live GET /models is the source of truth with a key.
        # V4 API currently exposes flash + pro (chat/reasoner ids map → flash).
    },
    "xai": {
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "auth": ["oauth", "api_key"],  # Sign in with xAI primary; console API key secondary
        "env_keys": ["XAI_API_KEY", "REMEDY_XAI_API_KEY"],
        "show_base_url": False,
        "free_tier": "none",
        "key_docs_url": "https://console.x.ai/team/default/api-keys",
        # Fallback only — live GET /models is preferred when a key is present.
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "auth": ["api_key"],
        "env_keys": ["GROQ_API_KEY"],
        "show_base_url": False,
        "free_tier": "free_key",
        "badge": "Free key",
        "limits_blurb": "Very fast free tier — great for trying agent tools.",
        "key_docs_url": "https://console.groq.com/keys",
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "auth": ["api_key"],
        "env_keys": ["MISTRAL_API_KEY"],
        "show_base_url": False,
        "free_tier": "free_key",
        "badge": "Free key",
        "limits_blurb": "Free Experiment plan (no card typical).",
        "key_docs_url": "https://console.mistral.ai/api-keys",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "auth": ["api_key"],
        "env_keys": ["OPENROUTER_API_KEY"],
        "show_base_url": False,
        "free_tier": "free_key",
        "badge": "Free key",
        "limits_blurb": (
            "Free-tier models (ids ending in :free) come and go; live /models "
            "lists what your key can use. Curated fallbacks below are long-running free chat."
        ),
        "key_docs_url": "https://openrouter.ai/keys",
    },
    "poe": {
        "label": "Poe",
        "base_url": "https://api.poe.com/v1",
        "auth": ["api_key"],
        "env_keys": ["POE_API_KEY", "REMEDY_POE_API_KEY"],
        "show_base_url": False,
        "free_tier": "none",
        "badge": "Multi-model",
        "limits_blurb": (
            "One Poe API key → many frontier bots (Claude, GPT, Gemini, Grok, …). "
            "Uses your Poe subscription points / add-on balance. "
            "Live GET /models lists bots your account can call."
        ),
        "privacy_note": "Chat is sent to Poe (Quora) and routed to the selected bot/provider.",
        "key_docs_url": "https://poe.com/api_key",
        # Bot names as used by Poe OpenAI-compatible API (case-sensitive).
        # Live discovery expands this when a key is present.
    },
    "ollama": {
        "label": "Ollama (local)",
        "base_url": "http://127.0.0.1:11434/v1",
        "auth": ["none"],
        "env_keys": [],
        "show_base_url": False,
        "free_tier": "local",
        "badge": "Local",
        "limits_blurb": "Fully free on your machine. Install Ollama and pull a model.",
        "key_docs_url": "https://ollama.com/download",
        # No curated model list — Ollama models come ONLY from the live server
        # (GET /api/models on the local Ollama endpoint). Curated guesses were
        # almost never pulled by the user and produced dead chat sessions.
    },
    "rmb": {
        "label": "RMB (local agent)",
        "base_url": "http://127.0.0.1:8787/v1",
        "auth": ["none"],
        "env_keys": [],
        "show_base_url": True,
        "free_tier": "local",
        "badge": "Local · Agent",
        "limits_blurb": (
            "Remedy Muscle Bridge — built-in local chat host (llama.cpp) optimized "
            "for coding and tool use. Manage under Settings → RMB. Fully private on this PC."
        ),
        "privacy_note": "Chat stays on this PC. Powered by llama.cpp under the RMB brand.",
        "key_docs_url": "",
    },
    "llamacpp": {
        "label": "llama.cpp (manual URL)",
        "base_url": "http://127.0.0.1:8080/v1",
        "auth": ["none"],
        "env_keys": [],
        "show_base_url": True,
        "free_tier": "local",
        "badge": "Local",
        "advanced": True,
        "limits_blurb": "Point at any OpenAI-compatible llama-server. Prefer Settings → RMB for managed local agent.",
    },
    "custom": {
        # Display name is user-settable via `custom_llm_name` (shown in Settings +
        # status bar). Falls back to this label when unset.
        "label": "Custom / OpenAI-compatible",
        # KoboldCpp serves the OpenAI API at /v1 (not /api/v1). This is a
        # placeholder default only — the UI keeps whatever the user types and
        # never forces this value back once a custom URL is saved.
        "base_url": "http://127.0.0.1:5001/v1",
        "auth": ["api_key"],
        "env_keys": [],
        "show_base_url": True,
        "free_tier": "none",
    },
}

# Ordered free options for Setup / Settings UI (provider id → display meta).
FREE_PROVIDER_OPTIONS: list[dict[str, Any]] = [
    {
        "id": "demo",
        "tier": "instant",
        "title": "Start instantly (Demo)",
        "blurb": "No account. Limited rate/quality. Chat leaves your PC to a free third-party gateway.",
    },
    {
        "id": "google",
        "tier": "free_key",
        "title": "Google Gemini",
        "blurb": "Free AI Studio key — strong free multimodal models.",
    },
    {
        "id": "groq",
        "tier": "free_key",
        "title": "Groq",
        "blurb": "Free key — very fast open models.",
    },
    {
        "id": "openrouter",
        "tier": "free_key",
        "title": "OpenRouter free models",
        "blurb": "Free key — pick models with :free suffix.",
    },
    {
        "id": "mistral",
        "tier": "free_key",
        "title": "Mistral",
        "blurb": "Free Experiment plan key.",
    },
    {
        "id": "rmb",
        "tier": "local",
        "title": "RMB (local agent)",
        "blurb": "Built-in llama.cpp host for coding + tools — private, no API key.",
    },
    {
        "id": "ollama",
        "tier": "local",
        "title": "Ollama (on this PC)",
        "blurb": "Install locally for private, unlimited free use.",
    },
]


def free_options_public() -> list[dict[str, Any]]:
    """Public free-options list for desktop Setup/Settings.

    Hides the guest demo entry when ``REMEDY_DEMO_DISABLED`` is set so air-gapped
    / enterprise builds never offer a third-party gateway path that cannot run.
    """
    demo_disabled = os.environ.get("REMEDY_DEMO_DISABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    out: list[dict[str, Any]] = []
    for item in FREE_PROVIDER_OPTIONS:
        pid = str(item["id"])
        if pid == "demo" and demo_disabled:
            continue
        meta = PROVIDER_CATALOG.get(pid) or {}
        models = list(meta.get("models") or [])
        out.append(
            {
                "id": pid,
                "tier": item.get("tier"),
                "title": item.get("title") or meta.get("label") or pid,
                "blurb": item.get("blurb") or meta.get("limits_blurb") or "",
                "badge": meta.get("badge"),
                "name": meta.get("label") or pid,
                "base_url": meta.get("base_url"),
                "auth": list(meta.get("auth") or ["api_key"]),
                "key_docs_url": meta.get("key_docs_url"),
                "limits_blurb": meta.get("limits_blurb"),
                "privacy_note": meta.get("privacy_note"),
                "default_model": str(models[0]["id"]) if models else "default",
                "models": models,
                "free_tier": meta.get("free_tier") or item.get("tier"),
            }
        )
    return out

