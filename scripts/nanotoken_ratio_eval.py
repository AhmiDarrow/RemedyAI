#!/usr/bin/env python3
"""Measure raw BPE pack estimates vs live provider usage (actual/est slope).

Target band for v2 acceptance: slope ≈ 0.75–1.25 (prefer near 1.0).
Uses only first-party pack + provider-reported usage. No foreign tokenizers.

  python scripts/nanotoken_ratio_eval.py
  python scripts/nanotoken_ratio_eval.py --pack remedy-bbpe-v2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


SAMPLES = [
    "List three files in the RemedyAI project root.",
    "Explain NanoToken BPE in two short sentences for a developer.",
    "def train_bpe(texts, num_merges=4000):\n    # clean-room byte BPE\n    return merges\n",
    json.dumps(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "src/remedy/nanoswarm/bpe_engine.py"}),
                    },
                }
            ],
        }
    ),
    "skill_activate skill=code-review\n" + ("# code review notes\n" * 20),
    "USER: run git status\nASSISTANT: exit_code=0\n## main...origin/main\n M scripts/foo.py\n",
]


def _chat_usage(base_url: str, api_key: str, model: str, text: str) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 32,
        "temperature": 0,
    }
    with httpx.Client(timeout=90.0) as client:
        r = client.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    usage = data.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(
            usage.get("total_tokens")
            or (
                int(usage.get("prompt_tokens") or 0)
                + int(usage.get("completion_tokens") or 0)
            )
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="remedy-bbpe-v2")
    args = ap.parse_args()

    from remedy.interfaces.api_support import load_config
    from remedy.interfaces.config import (
        PROVIDER_CATALOG,
        normalize_llm_settings,
        resolve_provider_api_key,
    )
    from remedy.nanoswarm.bpe_engine import count_tokens, get_pack

    # Force load target pack as default for this process
    pack = get_pack(args.pack)
    if pack is None:
        print(f"Pack not found: {args.pack}")
        return 1
    print(f"Pack {pack.id} v{pack.version} merges={len(pack.ranks)} method={pack.method_label}")

    # Also load v1 for comparison if present
    pack_v1 = get_pack("remedy-bbpe-v1")

    cfg = load_config()
    targets = [
        ("deepseek", "deepseek-v4-flash"),
        ("xai", "grok-4.5"),
    ]

    rows: list[dict] = []
    for provider, model_hint in targets:
        key = resolve_provider_api_key(cfg, provider)
        if not key:
            print(f"skip {provider}: no credentials")
            continue
        prov, model, base = normalize_llm_settings(provider, model_hint, None)
        models = (PROVIDER_CATALOG.get(prov) or {}).get("models") or []
        known = {m["id"] for m in models if isinstance(m, dict) and m.get("id")}
        if known and str(model) not in known:
            model = model_hint if model_hint in known else models[0]["id"]
        base = base or (PROVIDER_CATALOG.get(prov) or {}).get("base_url")
        print(f"\n=== {prov} / {model} ===")
        for i, sample in enumerate(SAMPLES):
            est = count_tokens(sample, pack)
            est_v1 = count_tokens(sample, pack_v1) if pack_v1 else None
            try:
                u = _chat_usage(str(base), str(key), str(model), sample)
            except Exception as e:
                print(f"  sample {i}: API error {e}")
                continue
            # Compare pack count of the *prompt* to provider prompt_tokens
            actual = u["prompt_tokens"]
            slope = (actual / est) if est else None
            row = {
                "provider": prov,
                "model": model,
                "sample": i,
                "chars": len(sample),
                "est_v2": est,
                "est_v1": est_v1,
                "prompt_tokens": actual,
                "slope_v2": round(slope, 3) if slope else None,
                "slope_v1": round(actual / est_v1, 3) if est_v1 else None,
            }
            rows.append(row)
            print(
                f"  s{i}: chars={len(sample)} est_v2={est} "
                f"est_v1={est_v1} actual_pt={actual} "
                f"slope_v2={row['slope_v2']} slope_v1={row['slope_v1']}"
            )
            time.sleep(0.3)

    if not rows:
        print("No measurements")
        return 1

    # Aggregate slopes
    print("\n=== SUMMARY (actual/est; target band 0.75–1.25) ===")
    for key_fn, label in (
        (lambda r: r["provider"], "by provider"),
        (lambda r: "all", "overall"),
    ):
        groups: dict[str, list[float]] = {}
        for r in rows:
            if r.get("slope_v2") is None:
                continue
            k = key_fn(r) if label != "overall" else "all"
            groups.setdefault(k, []).append(float(r["slope_v2"]))
        for k, vals in groups.items():
            avg = sum(vals) / len(vals)
            mn, mx = min(vals), max(vals)
            in_band = sum(1 for v in vals if 0.75 <= v <= 1.25)
            print(
                f"  {label} {k}: n={len(vals)} avg_slope={avg:.3f} "
                f"min={mn:.3f} max={mx:.3f} in_band={in_band}/{len(vals)}"
            )

    out = ROOT / "scripts" / "_nanotoken_corpus" / "ratio_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"pack": args.pack, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
