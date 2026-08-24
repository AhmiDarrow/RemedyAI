"""rig - drive Remedy for testing, and score models on whether they can run it.

    python -m rig doctor
    python -m rig run --provider xai --model grok-4 --suite core
    python -m rig run --gguf C:/models/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
    python -m rig compare out/*.json

Run from the repo root (``scripts`` on sys.path), or as
``python scripts/rig`` - both work.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python scripts/rig`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "rig"

from .llama import RMB_PORT, find_llama_server, has_cuda, launch  # noqa: E402
from .runner import run_suite  # noqa: E402
from .scenarios import SUITES  # noqa: E402
from .score import RunReport, compare, host_info  # noqa: E402


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report what this machine can actually run."""
    info = host_info()
    print("\n  Test bed")
    print(f"    platform : {info.get('platform')}")
    print(f"    cpu      : {info.get('cpu') or 'unknown'}")
    print(f"    gpu      : {info.get('gpu') or 'none detected'}")

    try:
        import psutil  # noqa: F401
        import psutil as _ps

        print(f"    ram      : {_ps.virtual_memory().total / 1024**3:.1f} GB")
    except Exception:
        pass

    server = find_llama_server()
    print("\n  Local runtime")
    if server is None:
        print("    llama-server : NOT FOUND - local models cannot run")
    else:
        cuda = has_cuda(server)
        print(f"    llama-server : {server}")
        print(f"    backend      : {'CUDA (GPU offload available)' if cuda else 'CPU ONLY'}")
        if not cuda:
            print(
                "    WARNING      : a CPU-only build ignores --n-gpu-layers. Your GPU\n"
                "                   will sit idle and every timing here will be wrong.\n"
                "                   Install the CUDA runtime before scoring local models."
            )
    print()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    srv = None
    provider = args.provider
    model = args.model or ""
    base_url = args.base_url or ""
    api_key = args.api_key or ""
    rmb_state: dict | None = None

    if args.use_host_key and not args.gguf:
        from .credentials import describe, host_llm_defaults, host_provider_key

        hp, hm, hb = host_llm_defaults()
        provider = provider if provider != "rmb" else (hp or provider)
        model = model or (hm if provider == hp else "")
        base_url = base_url or (hb if provider == hp else "")
        api_key = host_provider_key(provider)
        print(f"  {describe(provider)}")
        if not api_key:
            print(f"  no key available for {provider} - aborting")
            return 2

    try:
        if args.gguf:
            if not has_cuda() and not args.allow_cpu:
                print(
                    "  refusing to score on a CPU-only llama.cpp build "
                    "(timings would be meaningless).\n"
                    "  install the CUDA runtime, or pass --allow-cpu to proceed anyway."
                )
                return 2
            print(f"  starting llama-server for {Path(args.gguf).name} ...")
            srv = launch(
                args.gguf,
                port=args.llama_port,
                ctx=args.ctx,
                ngl=args.ngl,
                threads=args.threads,
                dry_multiplier=args.dry,
                n_cpu_moe=args.n_cpu_moe,
                cpu_moe_all=args.cpu_moe,
                server=args.llama_server,
            )
            provider = "rmb"
            model = srv.loaded_model_id()
            base_url = srv.base_url
            rmb_state = {
                "port": args.llama_port,
                "ctx_size": args.ctx,
                "model_path": str(Path(args.gguf).expanduser()),
            }
            print(f"  llama-server up: {base_url} (model id: {model})")

        label = args.label or (model or provider).replace("/", "-").replace(":", "-")
        report = run_suite(
            label=label,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            suite=args.suite,
            only=args.only,
            out_dir=Path(args.out) if args.out else None,
            keep=args.keep,
            trace=not args.no_trace,
            approval_mode=args.approval_mode,
            rmb=rmb_state,
        )
        print(report.render())
        return 0 if report.top_tier >= args.require_tier else 1
    finally:
        if srv is not None:
            srv.stop()


def cmd_compare(args: argparse.Namespace) -> int:
    paths: list[str] = []
    for pat in args.reports:
        paths.extend(glob.glob(pat) or [pat])
    reports: list[RunReport] = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        rep = RunReport(
            label=data.get("label", Path(p).stem),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            base_url=data.get("base_url", ""),
            suite=data.get("suite", ""),
        )
        from .score import Outcome

        rep.outcomes = [Outcome(**o) for o in data.get("outcomes", [])]
        reports.append(rep)
    if not reports:
        print("  no scorecards found")
        return 1
    print(compare(reports))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rig", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="Report hardware + local runtime readiness")

    run = sub.add_parser("run", help="Score one model on the scenario ladder")
    run.add_argument("--provider", default="rmb", help="rmb | xai | anthropic | openai | ...")
    run.add_argument("--model", default="", help="Model id for the provider")
    run.add_argument("--base-url", default="", help="OpenAI-compatible base URL")
    run.add_argument("--api-key", default="", help="Key for a cloud provider (teacher runs)")
    run.add_argument(
        "--use-host-key",
        action="store_true",
        help="Borrow the stored key (and provider defaults) from the live install",
    )
    run.add_argument("--gguf", default="", help="GGUF to serve locally and score")
    run.add_argument("--llama-server", default="", help="Path to llama-server binary")
    run.add_argument("--llama-port", type=int, default=RMB_PORT)
    run.add_argument("--ctx", type=int, default=16384, help="Local context window")
    run.add_argument("--ngl", type=int, default=999, help="Layers to offload to GPU")
    run.add_argument("--threads", type=int, default=0)
    run.add_argument(
        "--n-cpu-moe",
        type=int,
        default=0,
        help="Keep MoE experts of the first N layers on CPU (GPU keeps the rest)",
    )
    run.add_argument(
        "--cpu-moe",
        action="store_true",
        help="Keep all MoE experts on CPU",
    )
    run.add_argument(
        "--dry",
        type=float,
        default=0.0,
        help="DRY sampling multiplier (e.g. 0.8) to break repetition loops",
    )
    run.add_argument("--allow-cpu", action="store_true", help="Score even on a CPU-only build")
    run.add_argument("--suite", default="core", choices=sorted(SUITES))
    run.add_argument("--only", nargs="*", help="Run just these scenario ids")
    run.add_argument("--label", default="", help="Name for the scorecard")
    run.add_argument("--out", default="", help="Directory to write the scorecard JSON")
    run.add_argument("--keep", action="store_true", help="Keep the sandbox for inspection")
    run.add_argument("--no-trace", action="store_true", help="Skip LLM request traces")
    run.add_argument("--approval-mode", default="auto", choices=("auto", "ask", "full"))
    run.add_argument(
        "--require-tier",
        type=int,
        default=0,
        help="Exit non-zero unless this clean tier is reached",
    )

    cmp_ = sub.add_parser("compare", help="Table several scorecards side by side")
    cmp_.add_argument("reports", nargs="+", help="Scorecard JSON paths or globs")

    args = ap.parse_args(argv)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "compare":
        return cmd_compare(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
