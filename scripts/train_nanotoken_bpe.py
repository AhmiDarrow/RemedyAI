#!/usr/bin/env python3
"""Train a Remedy-owned BPE pack for NanoToken (clean-room).

Corpus is **first-party / synthetic** text only — no scraped proprietary chats,
no third-party merge tables. See corpus_note in the output pack.

Usage (from repo root, with venv):
  python scripts/train_nanotoken_bpe.py
  python scripts/train_nanotoken_bpe.py --merges 3000 --out src/remedy/nanoswarm/bpe_packs/remedy-bbpe-v1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running without install
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remedy.nanoswarm.bpe_engine import (  # noqa: E402
    DEFAULT_PACK_ID,
    pack_dict_from_merges,
    train_bpe,
)


def _synthetic_corpus() -> list[str]:
    """License-clean training text authored for Remedy (synthetic + public phrases)."""
    lines: list[str] = []
    # Prose patterns
    prose = [
        "The quick brown fox jumps over the lazy dog.",
        "Remedy is a local continuity agent for real work on your machine.",
        "Please remember that estimates are not the same as provider billing tokens.",
        "Switching providers remeasures context fill under NanoToken.",
        "Session briefs capture decisions, next steps, and project paths.",
        "Approvals protect shell and file writes in ask mode by default.",
        "Continuity workers stay silent; you still talk to one Remedy.",
    ]
    # Code patterns (generic)
    code = [
        "def hello_world(name: str) -> str:\n    return f'Hello, {name}!'\n",
        "async def list_dir(path: str = '.', limit: int = 200) -> str:\n    ...\n",
        "const x = { provider: 'xai', model: 'grok-4.5', tokens: 128000 };\n",
        "import json\nfrom pathlib import Path\n\ndef main():\n    print(Path.cwd())\n",
        "if __name__ == '__main__':\n    main()\n",
        "export async function apiFetch(path: string): Promise<unknown> {\n  return fetch(path)\n}\n",
        "SELECT id, title, content FROM memory_entries WHERE session_id = ?;\n",
        "git status --porcelain -b\n",
        "class TokenNanobot:\n    def measure_messages(self, messages):\n        return 0\n",
        "for i in range(100):\n    total += i * i\n",
    ]
    # Tool / JSON-like agent sludge
    tools = [
        '{"role":"user","content":"fix the build error"}\n',
        '{"tool":"file_read","path":"src/remedy/core/agent.py"}\n',
        '{"role":"assistant","tool_calls":[{"name":"bash_exec","arguments":{"command":"pytest -q"}}]}\n',
        "Error: ModuleNotFoundError: No module named 'example'\n",
        "HTTP 429 rate limit exceeded; retry after 30 seconds.\n",
        "path not found: ./missing/file.txt — try list_dir on parent\n",
    ]
    # Multilingual light (common words; short)
    multi = [
        "你好世界 こんにちは 안녕하세요 Привет мир\n",
        "café naïve résumé 東京 北京\n",
    ]
    for block in (prose, code, tools, multi):
        lines.extend(block)
    # Repeat with light variation to grow pair stats without huge files
    out: list[str] = []
    for i in range(40):
        for s in lines:
            out.append(s if i % 3 else s.replace(" ", "  ") if " " in s else s)
            out.append(s.upper() if i % 7 == 0 and s.isascii() else s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Train Remedy NanoToken BPE pack")
    ap.add_argument("--merges", type=int, default=2500, help="Number of BPE merges")
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "src" / "remedy" / "nanoswarm" / "bpe_packs" / f"{DEFAULT_PACK_ID}.json",
    )
    ap.add_argument("--min-pair-count", type=int, default=2)
    args = ap.parse_args()

    corpus = _synthetic_corpus()
    print(f"Training on {len(corpus)} segments, target merges={args.merges}…")
    merges = train_bpe(
        corpus,
        num_merges=args.merges,
        min_pair_count=args.min_pair_count,
    )
    print(f"Learned {len(merges)} merges")
    pack = pack_dict_from_merges(
        merges,
        pack_id=DEFAULT_PACK_ID,
        version=1,
        corpus_note=(
            "Synthetic first-party English/code/JSON-like text authored for Remedy "
            "training only. No third-party tokenizer merges or scraped chat logs."
        ),
        msg_overhead=4,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
