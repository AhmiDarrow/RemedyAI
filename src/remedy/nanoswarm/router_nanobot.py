"""Router nanobot — short intent labels for the nano swarm.

Deterministic heuristics first. If the shared local SmolVLM2 server is already
running, refine via a few-token completion on the same model as vision.
Never starts the server just to classify. Never grants shell/file power.
"""

from __future__ import annotations

import re
import threading
from typing import Any

_LABELS = ("memory", "skill", "chat", "plan", "tool", "autonomous")

# Identical user text is classified multiple times per turn (snapshot, policy,
# coordinator). Cache last N heuristic results to skip re-regex.
_INTENT_CACHE_MAX = 48


class RouterNanobot:
    """Classify intent; optional local-model refine when llama-server is up."""

    def __init__(self) -> None:
        self.last_label: str | None = None
        self.model_calls = 0
        self.last_method: str = "heuristic"
        self._intent_cache: dict[str, dict[str, Any]] = {}
        self._intent_cache_lock = threading.Lock()
        self.cache_hits = 0

    def classify_intent(self, user_msg: str) -> dict[str, Any]:
        text = (user_msg or "").strip().lower()
        # Cache key: full lowercased text, capped (long msgs still unique head)
        cache_key = text if len(text) <= 512 else text[:512]
        if cache_key:
            with self._intent_cache_lock:
                hit = self._intent_cache.get(cache_key)
                if hit is not None:
                    self.cache_hits += 1
                    self.last_label = str(hit.get("label") or "chat")
                    self.last_method = "heuristic"
                    # Return a shallow copy so callers cannot mutate the cache
                    return dict(hit)

        label = "chat"
        if not text:
            label = "chat"
        elif re.search(
            r"\b("
            r"work alone|on your own|handle this on your own|"
            r"i need to go|step away|don'?t wait for me|do not wait for me|"
            r"unattended|fully autonomous|finish without me|take it from here"
            r")\b",
            text,
        ):
            label = "autonomous"
        elif (
            text.startswith("/memory")
            or "remember that" in text
            or re.search(r"\bremember\b", text)
            or "what do you know" in text
            or "what do you remember" in text
            or re.search(r"\b(recall|forget that|did i tell you)\b", text)
        ):
            label = "memory"
        elif (
            text.startswith("/plan")
            or text.startswith("plan ")
            or "make a plan" in text
            or "step by step" in text
            or re.search(r"\b(roadmap|break (this|it) down|implementation plan)\b", text)
        ):
            label = "plan"
        elif (
            text.startswith("/skills")
            or text.startswith("/skill")
            or re.search(r"\b(use (the )?skill|skill_search|which skill)\b", text)
            or ("skill" in text and re.search(r"\b(run|use|activate|find)\b", text))
        ):
            label = "skill"
        elif re.search(
            r"\b("
            r"run|execute|bash|shell|npm|pip|uv |cargo |git |"
            r"implement|refactor|debug|fix (the |this )?(bug|error|build)|"
            r"create (a )?file|edit (the )?file|write (a )?(test|script)|"
            r"open (the )?pr|commit |push |pull request|"
            # Computer-use / browse — must not stay "chat" (tools stripped on L1)
            r"computer_|navigate to|browse |goto |go to |"
            r"open (https?://|gmail|google|github|youtube|outlook|slack)|"
            r"screenshot|click (the |on )|type into|"
            r"list_dir|repo_search|file_read|file_edit|spread_run|"
            r"search (the )?(codebase|repo|project)"
            r")\b",
            text,
        ):
            label = "tool"
        self.last_label = label
        self.last_method = "heuristic"
        out = {
            "label": label,
            "method": "heuristic",
            "bot": "router",
            "ambiguous": label == "chat" and len(text) > 40,
        }
        if cache_key:
            with self._intent_cache_lock:
                self._intent_cache[cache_key] = out
                if len(self._intent_cache) > _INTENT_CACHE_MAX:
                    # Drop oldest insertion-order keys (dict preserves order 3.7+)
                    drop = len(self._intent_cache) - _INTENT_CACHE_MAX
                    for k in list(self._intent_cache.keys())[:drop]:
                        self._intent_cache.pop(k, None)
        return dict(out)

    def classify_with_local_model(
        self,
        user_msg: str,
        *,
        complete_fn: Any | None = None,
    ) -> dict[str, Any]:
        """If complete_fn provided and works, ask for a one-word label; else heuristic."""
        base = self.classify_intent(user_msg)
        if complete_fn is None:
            return base
        prompt = (
            "Classify the user message into exactly one label: "
            f"{', '.join(_LABELS)}.\n"
            f"Message: {user_msg[:500]}\n"
            "Label:"
        )
        try:
            raw = str(complete_fn(prompt, max_tokens=8) or "").strip().lower()
            for lab in _LABELS:
                if lab in raw:
                    self.model_calls += 1
                    self.last_label = lab
                    self.last_method = "local_model"
                    return {
                        "label": lab,
                        "method": "local_model",
                        "bot": "router",
                        "raw": raw[:40],
                        "heuristic": base["label"],
                    }
        except Exception as e:
            base["model_error"] = str(e)
        return base

    def classify(
        self,
        user_msg: str,
        *,
        use_local: bool = True,
        timeout_s: float = 12.0,
    ) -> dict[str, Any]:
        """Heuristic always; local refine only if server already running."""
        base = self.classify_intent(user_msg)
        if not use_local or not (user_msg or "").strip():
            return base
        try:
            from remedy.interfaces.api_support import load_config
            from remedy.vision.config import load_vision_json, vision_section_from_config
            from remedy.vision.runtime import is_running

            cfg = load_config()
            home = cfg.get("home_dir") if isinstance(cfg, dict) else None
            if not is_running(home):
                base["local_skipped"] = "server_not_running"
                return base
            vcfg = vision_section_from_config(cfg if isinstance(cfg, dict) else {})
            side = load_vision_json(home)
            base_url = str(side.get("base_url") or vcfg.get("base_url") or "")
            if not base_url:
                return base

            from remedy.runtime.jobs import LocalJob, default_queue
            from remedy.runtime.local_infer import ensure_handlers_registered
            from remedy.runtime.roles import LocalRole

            ensure_handlers_registered()
            prompt = (
                "Classify into exactly one word from: "
                f"{', '.join(_LABELS)}.\n"
                f"User: {(user_msg or '')[:400]}\n"
                "Label:"
            )
            job = LocalJob(
                role=LocalRole.NANO,
                kind="nano_classify",
                payload={
                    "prompt": prompt,
                    "base_url": base_url,
                    "max_tokens": 8,
                    "timeout_s": timeout_s,
                },
                priority=5,
            )
            out = default_queue().submit(job, wait=True, timeout=timeout_s + 5)
            if not out.get("ok"):
                base["local_skipped"] = out.get("error") or "job_failed"
                return base
            result = out.get("result") or {}
            if not result.get("ok"):
                base["local_skipped"] = result.get("error") or "empty"
                return base
            raw = str(result.get("text") or "").strip().lower()
            for lab in _LABELS:
                if lab in raw.split() or raw.startswith(lab) or lab in raw:
                    self.model_calls += 1
                    self.last_label = lab
                    self.last_method = "local_model"
                    return {
                        "label": lab,
                        "method": "local_model",
                        "bot": "router",
                        "raw": raw[:40],
                        "heuristic": base["label"],
                    }
            base["local_raw"] = raw[:40]
        except Exception as e:
            base["local_skipped"] = str(e)
        return base

    def status(self) -> dict[str, Any]:
        with self._intent_cache_lock:
            cache_size = len(self._intent_cache)
        return {
            "bot": "router",
            "last_label": self.last_label,
            "last_method": self.last_method,
            "model_calls": self.model_calls,
            "cache_hits": self.cache_hits,
            "cache_size": cache_size,
            "role_model": __import__(
                "remedy.runtime.catalog", fromlist=["DEFAULT_LOCAL_MODEL_ID"]
            ).DEFAULT_LOCAL_MODEL_ID,  # same as vision — never a second model
        }
