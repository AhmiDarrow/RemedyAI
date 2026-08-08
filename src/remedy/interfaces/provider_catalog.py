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
            "Rate-limited guest chat only (third-party). Curated stable models — "
            "not the full gateway catalog. Add a real provider for agents / vision."
        ),
        "privacy_note": "Chat is sent to a free third-party API (not Remedy cloud). Prefer Ollama for private local use.",
        "key_docs_url": "https://llm7.io/",
        # Curated guest-chat allowlist only. Never merge live /models (that dumps
        # image/video ids, promo names, and paid-key models like deepseek-*).
        # live_models: False → catalog.py skips endpoint discovery for demo.
        "live_models": False,
        "models": [
            {
                "id": "codestral-latest",
                "name": "Codestral demo",
                "vision": False,
            },
            {
                "id": "gemini-3.1-flash-lite",
                "name": "Gemini Flash Lite demo",
                "vision": False,
            },
            {
                "id": "gpt-oss:20b",
                "name": "GPT-OSS 20B demo",
                "vision": False,
            },
        ],
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "auth": ["api_key"],
        "env_keys": ["OPENAI_API_KEY", "REMEDY_LLM_API_KEY"],
        "show_base_url": False,
        "free_tier": "none",
        "key_docs_url": "https://platform.openai.com/api-keys",
        "models": [
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "vision": True},
            {"id": "gpt-4o", "name": "GPT-4o", "vision": True},
            {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini", "vision": True},
            {"id": "o4-mini", "name": "o4-mini", "vision": False},
        ],
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "auth": ["api_key"],
        "env_keys": ["ANTHROPIC_API_KEY"],
        "show_base_url": False,
        "free_tier": "none",
        "key_docs_url": "https://console.anthropic.com/settings/keys",
        "models": [
            {"id": "claude-3-5-sonnet-latest", "name": "Claude 3.5 Sonnet", "vision": True},
            {"id": "claude-3-5-haiku-latest", "name": "Claude 3.5 Haiku", "vision": True},
            {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku", "vision": True},
            {"id": "claude-3.5-sonnet", "name": "Claude 3.5 Sonnet (alias)", "vision": True},
            {"id": "claude-3-haiku", "name": "Claude 3 Haiku (alias)", "vision": True},
        ],
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
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "vision": True},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "vision": True},
            {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "vision": True},
        ],
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
        "models": [
            {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "vision": False},
            {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "vision": False},
        ],
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
        "models": [
            {"id": "grok-4.5", "name": "Grok 4.5", "vision": True},
            {"id": "grok-4.3", "name": "Grok 4.3", "vision": True},
            {"id": "grok-4", "name": "Grok 4", "vision": True},
        ],
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
        "models": [
            {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "vision": False},
            {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B Instant", "vision": False},
            {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B", "vision": False},
        ],
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
        "models": [
            {"id": "mistral-small-latest", "name": "Mistral Small", "vision": False},
            {"id": "mistral-large-latest", "name": "Mistral Large", "vision": False},
            {"id": "codestral-latest", "name": "Codestral", "vision": False},
        ],
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
        "models": [
            # Stable free chat fallbacks only (not promo/image). Live discovery adds more.
            {"id": "openrouter/auto", "name": "OpenRouter Auto"},
            {"id": "openai/gpt-oss-20b:free", "name": "GPT-OSS 20B (free)"},
            {
                "id": "meta-llama/llama-3.3-70b-instruct:free",
                "name": "Llama 3.3 70B (free)",
            },
        ],
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
        "models": [
            {"id": "Claude-Sonnet-4.6", "name": "Claude Sonnet 4.6", "vision": True},
            {"id": "Claude-Opus-4.7", "name": "Claude Opus 4.7", "vision": True},
            {"id": "GPT-5.4", "name": "GPT-5.4", "vision": True},
            {"id": "Gemini-3.1-Pro", "name": "Gemini 3.1 Pro", "vision": True},
            {"id": "Grok-4", "name": "Grok 4", "vision": True},
        ],
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
        "models": [
            {"id": "llama3.2", "name": "Llama 3.2"},
            {"id": "qwen2.5", "name": "Qwen 2.5"},
            {"id": "codellama", "name": "Code Llama"},
        ],
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
        "models": [
            {"id": "Qwen2.5-Coder-7B-Instruct-Q4_K_M", "name": "Qwen2.5 Coder 7B"},
            {"id": "Qwen2.5-Coder-14B-Instruct-Q4_K_M", "name": "Qwen2.5 Coder 14B"},
            {"id": "default", "name": "Default (loaded model)"},
        ],
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
        "models": [
            {"id": "default", "name": "Default"},
        ],
    },
    "custom": {
        # Display name is user-settable via `custom_llm_name` (shown in Settings +
        # status bar). Falls back to this label when unset.
        "label": "Custom / OpenAI-compatible",
        "base_url": "http://127.0.0.1:5001/api/v1",
        "auth": ["api_key"],
        "env_keys": [],
        "show_base_url": True,
        "free_tier": "none",
        "models": [
            {"id": "default", "name": "Default (custom endpoint)"},
        ],
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

