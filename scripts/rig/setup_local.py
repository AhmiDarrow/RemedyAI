"""Fetch what the harness needs to score local models: a GPU runtime + weights.

Two jobs, both resumable and both hash-checked where the catalog pins a hash:

* the CUDA llama.cpp build (Remedy pins ``b10107``), because the runtime that
  ships with the vision stack is CPU-only and silently ignores GPU offload;
* a GGUF (plus its ``mmproj`` when the model is multimodal).

    python -m rig.setup_local runtime
    python -m rig.setup_local model --repo google/gemma-4-12B-it-qat-q4_0-gguf \\
        --file gemma-4-12b-it-qat-q4_0.gguf --mmproj mmproj-gemma-4-12b-it-qat-q4_0.gguf
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def remedy_home() -> Path:
    return Path(os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()


def rmb_runtime_dir() -> Path:
    return remedy_home() / "rmb" / "runtime"


def rmb_models_dir() -> Path:
    return remedy_home() / "rmb" / "models"


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def download(url: str, dest: Path, *, expect_sha: str | None = None) -> Path:
    """Resumable download with a progress line and optional hash check."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and expect_sha and sha256(dest) == expect_sha:
        print(f"    have {dest.name} (hash ok)")
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.is_file() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "remedy-rig/1.0"})
    if have:
        req.add_header("Range", f"bytes={have}-")

    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except Exception as e:
        raise SystemExit(f"download failed for {url}: {e}") from e

    resumed = resp.status == 206
    if have and not resumed:
        have = 0  # server ignored Range - start over
    total = int(resp.headers.get("Content-Length") or 0) + have

    mode = "ab" if resumed and have else "wb"
    start = time.time()
    done = have
    last = 0.0
    with resp, part.open(mode) as fh:
        while True:
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            now = time.time()
            if now - last > 1.0:
                last = now
                rate = done / max(now - start, 0.1)
                pct = f"{100 * done / total:.0f}%" if total else "?"
                print(
                    f"      {dest.name}: {_fmt(done)}/{_fmt(total)} ({pct}) "
                    f"{_fmt(rate)}/s",
                    end="\r",
                    flush=True,
                )
    print()
    part.replace(dest)

    if expect_sha:
        got = sha256(dest)
        if got != expect_sha:
            dest.unlink(missing_ok=True)
            raise SystemExit(
                f"hash mismatch for {dest.name}\n  expected {expect_sha}\n  got      {got}"
            )
        print(f"    {dest.name} hash verified")
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------


def install_runtime(dest: Path | None = None, *, cache: Path | None = None) -> Path:
    """Install the pinned CUDA llama.cpp build (engine + CUDA redistributables)."""
    from remedy.runtime.catalog import LLAMA_CPP_TAG, LLAMA_RUNTIMES

    spec = LLAMA_RUNTIMES.get("win-cuda-12.4-x64")
    if spec is None:
        raise SystemExit("catalog has no win-cuda-12.4-x64 runtime")

    out = dest or rmb_runtime_dir()
    out.mkdir(parents=True, exist_ok=True)
    cache = cache or (remedy_home() / "cache" / "runtime")
    cache.mkdir(parents=True, exist_ok=True)

    print(f"  installing llama.cpp {LLAMA_CPP_TAG} (CUDA 12.4) -> {out}")
    zips: list[Path] = []
    zips.append(
        download(spec.url, cache / spec.zip_name, expect_sha=spec.sha256)
    )
    for extra in spec.extra_zips or ():
        zips.append(download(extra.url, cache / extra.name, expect_sha=extra.sha256))

    for z in zips:
        print(f"    extracting {z.name}")
        with zipfile.ZipFile(z) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                # Release zips nest under build/bin/ - flatten to one dir.
                target = out / Path(member).name
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    server = out / "llama-server.exe"
    if not server.is_file():
        raise SystemExit(f"llama-server.exe missing after extract in {out}")
    cuda = list(out.glob("*cuda*"))
    print(f"    installed: {server}")
    print(f"    cuda backends: {len(cuda)} file(s)")
    if not cuda:
        raise SystemExit("no CUDA backend files extracted - wrong archive?")
    return server


# ---------------------------------------------------------------------------
# weights
# ---------------------------------------------------------------------------


def hf_url(repo: str, filename: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{filename}"


def install_model(
    repo: str, filename: str, *, mmproj: str = "", dest: Path | None = None
) -> Path:
    out = dest or rmb_models_dir()
    out.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {repo}")
    model = download(hf_url(repo, filename), out / filename)
    if mmproj:
        download(hf_url(repo, mmproj), out / mmproj)
    print(f"    model: {model}  ({_fmt(model.stat().st_size)})")
    return model


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rig.setup_local", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rt = sub.add_parser("runtime", help="Install the pinned CUDA llama.cpp build")
    rt.add_argument("--dest", default="", help="Target dir (default ~/.remedy/rmb/runtime)")

    md = sub.add_parser("model", help="Download a GGUF (and mmproj) from Hugging Face")
    md.add_argument("--repo", required=True)
    md.add_argument("--file", required=True)
    md.add_argument("--mmproj", default="")
    md.add_argument("--dest", default="", help="Target dir (default ~/.remedy/rmb/models)")

    args = ap.parse_args(argv)
    if args.cmd == "runtime":
        install_runtime(Path(args.dest) if args.dest else None)
    else:
        install_model(
            args.repo,
            args.file,
            mmproj=args.mmproj,
            dest=Path(args.dest) if args.dest else None,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
