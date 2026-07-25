#!/usr/bin/env python3
"""Run a multi-provider Remedy tool/skill battery, harvest corpus, train BPE v2.

Uses only first-party repo text + live agent transcripts generated in this run.
Scrubs secrets. Does not ship third-party tokenizers.

Usage (repo root, venv):
  python scripts/nanotoken_battery_and_train.py
  python scripts/nanotoken_battery_and_train.py --skip-live --merges 12000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Secret-ish patterns to drop from corpus
_SCRUB = re.compile(
    r"(?i)("
    r"sk-[a-zA-Z0-9]{10,}"
    r"|xai-[a-zA-Z0-9]{10,}"
    r"|eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"  # JWT
    r"|Bearer\s+[a-zA-Z0-9._\-]+"
    r"|api[_-]?key[\"'=\s:]+[^\s\"']+"
    r")"
)


def scrub(text: str) -> str:
    return _SCRUB.sub("[REDACTED]", text or "")


def harvest_repo_text(root: Path, max_files: int = 400, max_chars: int = 2_000_000) -> list[str]:
    """First-party code/docs/skills — licensed as this repo."""
    paths: list[Path] = []
    for pattern in (
        "src/remedy/**/*.py",
        "desktop/src/**/*.ts",
        "desktop/src/**/*.tsx",
        "src/remedy/bundled_skills/**/*.md",
        "docs/**/*.md",
        "tests/**/*.py",
        "scripts/**/*.py",
    ):
        paths.extend(root.glob(pattern))
    for name in ("README.md", "CHANGELOG.md"):
        p = root / name
        if p.is_file():
            paths.append(p)

    skip_parts = {
        "node_modules",
        "__pycache__",
        ".venv",
        "bpe_packs",
        "dist",
        "build",
        "target",
        ".git",
    }
    segs: list[str] = []
    total = 0
    nfiles = 0
    for path in sorted(set(paths), key=lambda p: str(p)):
        if any(part in skip_parts for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".py", ".ts", ".tsx", ".md", ".json", ".toml"}:
            # allow skill md already
            if path.suffix.lower() not in {".py", ".ts", ".tsx", ".md"}:
                continue
        try:
            if path.stat().st_size > 400_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = scrub(text)
        if len(text) < 40:
            continue
        # Chunk large files
        if len(text) > 12_000:
            for i in range(0, len(text), 8_000):
                segs.append(text[i : i + 10_000])
        else:
            segs.append(text)
        nfiles += 1
        total += len(text)
        if nfiles >= max_files or total >= max_chars:
            break
    print(f"Harvested repo: {nfiles} files, {len(segs)} segments, ~{total//1000}k chars")
    return segs


def harvest_memory_messages(max_msgs: int = 500) -> list[str]:
    """Local session text from this machine (owner-requested training battery)."""
    import sqlite3

    db = Path.home() / ".remedy" / "memory.db"
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(str(db))
        rows = con.execute(
            "SELECT role, content, thinking, tool_calls, tool_results "
            "FROM chat_messages ORDER BY created_at DESC LIMIT ?",
            (max_msgs,),
        ).fetchall()
        con.close()
    except Exception as e:
        print(f"memory harvest skip: {e}")
        return []
    out: list[str] = []
    for role, content, thinking, tcs, trs in rows:
        blob = f"role={role}\n{content or ''}\n"
        if thinking:
            blob += f"thinking:\n{thinking}\n"
        if tcs and tcs != "[]":
            blob += f"tool_calls:\n{tcs}\n"
        if trs and trs != "[]":
            blob += f"tool_results:\n{trs}\n"
        s = scrub(blob)
        if len(s) > 30:
            out.append(s[:20_000])
    print(f"Harvested memory messages: {len(out)}")
    return out


def _tool_json_patterns() -> list[str]:
    """Synthetic but realistic agent/tool traffic (first-party authored)."""
    tools = [
        "file_read",
        "file_write",
        "list_dir",
        "bash_exec",
        "skill_activate",
        "skill_search",
        "local_discover",
        "comfyui",
        "memory_search",
    ]
    paths = [
        "src/remedy/core/agent.py",
        "src/remedy/nanoswarm/token_nanobot.py",
        "desktop/src/App.tsx",
        "pyproject.toml",
        "tests/test_bpe_engine.py",
        ".",
        "docs/manual/17-nanoswarm.md",
    ]
    cmds = [
        "git status --porcelain -b",
        "pytest -q tests/test_bpe_engine.py",
        "python -m pytest tests/test_nanoswarm.py -q",
        "dir",
        "Get-ChildItem -Name",
    ]
    out: list[str] = []
    for t in tools:
        for p in paths:
            out.append(
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": t,
                                    "arguments": json.dumps({"path": p, "command": cmds[0]}),
                                },
                            }
                        ],
                    }
                )
            )
            out.append(
                json.dumps(
                    {
                        "role": "tool",
                        "name": t,
                        "content": f"exit_code=0\ncwd=C:\\\\Users\\\\Administrator\\\\RemedyAI\n"
                        f"ok path={p}\n",
                    }
                )
            )
    for c in cmds:
        out.append(
            json.dumps(
                {
                    "tool": "bash_exec",
                    "command": c,
                    "result": f"exit_code=0\n{c} completed\n",
                }
            )
        )
    # Skill activation patterns
    for sk in (
        "code-review",
        "debug-error",
        "git-status",
        "project-overview",
        "write-tests",
        "explain-code",
        "commit-message",
        "session-handoff",
    ):
        out.append(f"skill_activate skill={sk}\nLoading SKILL.md for {sk}…\n")
        out.append(
            f"---\nname: {sk}\ndescription: bundled skill\n---\n\n# {sk}\n\nUse this skill carefully.\n"
        )
    return out


async def _run_provider_battery(
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    project: Path,
    prompts: list[str],
    max_steps: int = 12,
) -> list[str]:
    """Live BasicRuntime turns; capture streamed text + tool process."""
    from remedy.core.agent import BasicRuntime
    from remedy.models import AgentConfig

    cfg = AgentConfig(
        llm_provider=provider,
        llm_model=model,
        llm_base_url=base_url,
        llm_api_key=api_key,
        project_path=str(project),
        access_scope="project",
        harness_mode="auto",
        home_dir=str(Path.home() / ".remedy"),
    )
    rt = BasicRuntime(cfg)
    # Register workspace tools
    try:
        from remedy.core.agent_workspace_tools import register_workspace_tools

        register_workspace_tools(rt)
    except Exception:
        pass
    try:
        from remedy.core.agent_skill_tools import register_skill_tools

        register_skill_tools(rt)
    except Exception:
        pass
    try:
        from remedy.core.agent_memory_tools import register_memory_tools

        register_memory_tools(rt)
    except Exception:
        pass
    try:
        from remedy.core.agent_goals import register_goal_tools

        register_goal_tools(rt)
    except Exception:
        pass

    # Discover skills (bundled + home)
    try:
        n = rt.skills.discover_defaults(home_dir=cfg.home_dir)
        print(f"  skills discovered: {n}")
    except Exception as e:
        print(f"  skill discover: {e}")
    try:
        names = sorted(getattr(rt.skills, "_skills", {}) or rt.skills.list_skills() or [])
        if not isinstance(names, list):
            names = list(names) if names else []
        print(f"  skill names sample: {names[:12]}")
    except Exception:
        pass

    segs: list[str] = []
    for i, prompt in enumerate(prompts):
        print(f"  [{provider}] turn {i+1}/{len(prompts)}: {prompt[:70]}…")
        buf: list[str] = [f"USER: {prompt}\n"]
        t0 = time.perf_counter()
        try:
            # Limit react steps for battery
            rt._max_react_steps = max_steps  # noqa: SLF001
            async for chunk in rt.stream_response(
                prompt,
                session_id=f"nanotoken-battery-{provider}-{i}",
            ):
                if isinstance(chunk, str) and chunk:
                    buf.append(chunk)
        except Exception as e:
            buf.append(f"\n[battery error] {e}\n")
            print(f"    error: {e}")
        ms = (time.perf_counter() - t0) * 1000
        text = scrub("".join(buf))
        segs.append(text[:40_000])
        print(f"    done {ms:.0f}ms, chars={len(text)}")
        # Feed calibrator if we have usage in runtime last estimate
        try:
            from remedy.nanoswarm.token_nanobot import get_token_nanobot

            bot = get_token_nanobot()
            # measure transcript under this provider
            est = bot.measure_messages(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": text[-8000:]},
                ],
                provider=provider,
                model=model,
            )
            segs.append(
                f"nanotoken measure provider={provider} model={model} "
                f"method={bot.last_method} est={est}\n"
            )
        except Exception:
            pass
    return segs


def _battery_prompts() -> list[str]:
    """Tool/skill-heavy turns that mirror real Remedy agent traffic."""
    return [
        # Workspace discovery
        "List the top-level files in this project with list_dir. Then read pyproject.toml with file_read and summarize the package name and version.",
        "Use list_dir on src/remedy/nanoswarm then file_read token_nanobot.py (first ~80 lines is fine). What does NanoToken do in one short paragraph?",
        # Shell / git
        "Run bash_exec with command: git status --porcelain -b  (if git fails, say so). List any modified files briefly.",
        # Skills progressive disclosure
        "Use skill_search or skill tools to list available skills. Then skill_activate (or read) debug-error and code-review. Summarize each in 2 bullets.",
        "Activate the session-handoff skill and project-overview skill if available. Quote one key instruction from each.",
        # BPE / tests inspection
        "list_dir tests/ and file_read tests/test_bpe_engine.py. Name the test functions you see.",
        "file_read src/remedy/nanoswarm/bpe_engine.py focusing on train_bpe and load_pack. Explain byte-level BPE in 3 short sentences.",
        # Planning / multi-tool
        "Create a tiny plan: how would you add a unit test for list_dir pagination? Inspect agent_workspace_tools.py with file_read if needed. Output steps only.",
        # Memory / harness-ish
        "If memory_search or memory tools exist, search for 'nanotoken' or 'bpe'. Otherwise list_dir docs/ and summarize nanoswarm-related docs names.",
        # write-tests / commit style (read skill, don't mutate)
        "skill_activate write-tests and commit-message if available. Then file_read one test under tests/ and propose a one-line commit message for a BPE pack update (do not run git commit).",
        # Multi-file agent pattern
        "list_dir src/remedy/core/ then file_read agent_skill_tools.py. How are skills registered on the runtime?",
        # Local-first product language
        "Using only tools (list_dir + file_read README.md or docs), state Remedy's local-first stance in one sentence.",
    ]


async def run_live(project: Path) -> list[str]:
    from remedy.interfaces.api_support import load_config
    from remedy.interfaces.config import (
        PROVIDER_CATALOG,
        normalize_llm_settings,
        resolve_provider_api_key,
    )

    cfg = load_config()
    segs: list[str] = []
    prompts = _battery_prompts()

    # Use every credentialed cloud provider + local Ollama if up.
    # Prefer catalog defaults (DeepSeek V4 ids; Grok 4.x).
    cur = str(cfg.get("llm_provider") or "").lower()
    candidates: list[tuple[str, str | None]] = [
        ("deepseek", "deepseek-v4-flash"),
        ("deepseek", "deepseek-v4-pro"),  # second DeepSeek model if allowed
        ("xai", "grok-4.5"),
        ("xai", "grok-4.3"),
        ("openai", None),
        ("anthropic", None),
        ("google", None),
        ("groq", None),
        ("mistral", None),
        ("openrouter", None),
        ("ollama", None),
    ]
    if cur and cur not in {c[0] for c in candidates}:
        candidates.insert(0, (cur, cfg.get("llm_model")))

    seen_provider_model: set[tuple[str, str]] = set()
    for provider, model_hint in candidates:
        key = resolve_provider_api_key(cfg, provider)
        if provider == "ollama":
            from remedy.interfaces.config import detect_ollama

            det = detect_ollama()
            if not det.get("available"):
                print(f"skip {provider}: not available")
                continue
            key = key or "local"
            model = str((det.get("models") or ["llama3.2"])[0])
            base = det.get("base_url") or "http://127.0.0.1:11434/v1"
        else:
            if not key:
                print(f"skip {provider}: no credentials")
                continue
            # Always normalize so retired ids (deepseek-chat) remap to V4.
            hint = model_hint
            if hint is None and provider == cur:
                hint = cfg.get("llm_model")
            prov, model, base = normalize_llm_settings(provider, hint, None)
            provider = prov
            # Catalog fallback if normalize left a foreign model id
            models = (PROVIDER_CATALOG.get(provider) or {}).get("models") or []
            known = {m["id"] for m in models if isinstance(m, dict) and m.get("id")}
            if known and str(model) not in known and model_hint and model_hint in known:
                model = model_hint
            if known and str(model) not in known:
                model = models[0]["id"]
            if not base and models:
                base = (PROVIDER_CATALOG.get(provider) or {}).get("base_url") or base

        pm = (provider, str(model))
        if pm in seen_provider_model:
            continue
        seen_provider_model.add(pm)

        # Cap battery size: first model per provider gets full prompts;
        # extra models (e.g. deepseek-v4-pro, grok-4.3) get a short subset.
        first_of_provider = sum(1 for p, _ in seen_provider_model if p == provider) == 1
        use_prompts = prompts if first_of_provider else prompts[:4]
        steps = 10 if first_of_provider else 6

        print(f"\n=== LIVE battery provider={provider} model={model} prompts={len(use_prompts)} ===")
        try:
            segs.extend(
                await _run_provider_battery(
                    provider=provider,
                    model=str(model),
                    base_url=str(base),
                    api_key=str(key),
                    project=project,
                    prompts=use_prompts,
                    max_steps=steps,
                )
            )
        except Exception as e:
            print(f"provider {provider} battery failed: {e}")
            segs.append(f"battery failed provider={provider}: {e}\n")
    return segs


def train_v2(segments: list[str], merges: int, out: Path) -> dict:
    from remedy.nanoswarm.bpe_engine import pack_dict_from_merges, train_bpe

    # Cap total training text for runtime
    corpus: list[str] = []
    total = 0
    for s in segments:
        if total > 3_500_000:
            break
        s = scrub(s)
        if len(s) < 20:
            continue
        corpus.append(s)
        total += len(s)
    print(f"Training v2 on {len(corpus)} segments, ~{total//1000}k chars, merges={merges}")
    t0 = time.perf_counter()
    merges_list = train_bpe(corpus, num_merges=merges, min_pair_count=2)
    print(f"Learned {len(merges_list)} merges in {time.perf_counter()-t0:.1f}s")
    pack = pack_dict_from_merges(
        merges_list,
        pack_id="remedy-bbpe-v2",
        version=2,
        corpus_note=(
            "First-party: RemedyAI repo source/tests/docs/skills + live multi-provider "
            "agent battery transcripts (tool/skill-heavy) generated in-repo. "
            "Secrets scrubbed. No third-party tokenizer merges."
        ),
        msg_overhead=4,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return pack


def load_corpus_dump(path: Path) -> list[str]:
    """Reload segments from a previous battery_corpus.txt dump."""
    if not path.is_file():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    # Split on our marker lines
    parts = re.split(r"\n*===== SEG \d+ =====\n", text)
    segs = [scrub(p) for p in parts if p and len(p.strip()) >= 20]
    print(f"Loaded corpus dump: {path} → {len(segs)} segments, ~{sum(len(s) for s in segs)//1000}k chars")
    return segs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-live", action="store_true", help="Skip live LLM battery")
    ap.add_argument(
        "--from-corpus",
        type=Path,
        default=None,
        help="Train only from an existing battery_corpus.txt (skips harvest + live)",
    )
    ap.add_argument("--merges", type=int, default=12_000)
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "src/remedy/nanoswarm/bpe_packs/remedy-bbpe-v2.json",
    )
    ap.add_argument(
        "--corpus-dir",
        type=Path,
        default=ROOT / "scripts" / "_nanotoken_corpus",
    )
    args = ap.parse_args()

    segments: list[str] = []
    if args.from_corpus is not None:
        segments = load_corpus_dump(args.from_corpus)
    else:
        segments.extend(harvest_repo_text(ROOT))
        segments.extend(harvest_memory_messages())
        segments.extend(_tool_json_patterns())
        # synthetic seed from train script
        try:
            from train_nanotoken_bpe import _synthetic_corpus  # type: ignore

            segments.extend(_synthetic_corpus())
        except Exception:
            # inline minimal if import path fails
            pass
        # Import synthetic via path
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "train_nanotoken_bpe", ROOT / "scripts" / "train_nanotoken_bpe.py"
            )
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(mod)
            segments.extend(mod._synthetic_corpus())
        except Exception as e:
            print(f"synthetic import: {e}")

        if not args.skip_live:
            print("\nRunning live multi-provider battery (may take several minutes)…")
            try:
                live = asyncio.run(run_live(ROOT))
                segments.extend(live)
            except Exception as e:
                print(f"Live battery failed: {e}")

        # Persist corpus for inspection (scrubbed)
        args.corpus_dir.mkdir(parents=True, exist_ok=True)
        corpus_path = args.corpus_dir / "battery_corpus.txt"
        with corpus_path.open("w", encoding="utf-8") as f:
            for i, s in enumerate(segments):
                f.write(f"\n\n===== SEG {i} =====\n")
                f.write(scrub(s)[:30_000])
        print(f"Corpus dump: {corpus_path} ({corpus_path.stat().st_size//1024} KB)")

    if not segments:
        print("No training segments — abort")
        return 1

    train_v2(segments, args.merges, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
