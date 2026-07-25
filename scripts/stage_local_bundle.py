"""Stage optional offline local-model tree (NOT for production installers).

Production: first-run download into ~/.remedy/vision/ — installer stays small.
This script only fills desktop/resources/local for air-gap / REMEDY_LOCAL_BUNDLE.

Usage:
  python scripts/stage_local_bundle.py --from-vision-home
  python scripts/stage_local_bundle.py --out path/to/local

Does not download from the network. Source files must already exist under
~/.remedy/vision (or you pass paths). Does not modify tauri.conf.json
(production builds must not embed this tree).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from remedy.runtime.catalog import (  # noqa: E402
    BUNDLED_RUNTIME_IDS,
    DEFAULT_LOCAL_MODEL_ID,
    get_model_spec,
    get_runtime_spec,
)


def stage(
    *,
    out: Path,
    vision_home: Path | None,
) -> None:
    out = out.resolve()
    mid = DEFAULT_LOCAL_MODEL_ID
    spec = get_model_spec(mid)
    model_dst = out / "models" / mid
    model_dst.mkdir(parents=True, exist_ok=True)

    sources: list[Path] = []
    if vision_home:
        sources.append(Path(vision_home) / "models" / mid)
    sources.append(Path.home() / ".remedy" / "vision" / "models" / mid)

    model_src = mmproj_src = None
    for d in sources:
        m = d / spec.model_file
        p = d / spec.mmproj_file
        if m.is_file() and p.is_file():
            model_src, mmproj_src = m, p
            break
    if not model_src or not mmproj_src:
        print(
            "ERROR: pinned Qwen GGUF/mmproj not found.\n"
            "  1) Run Remedy Setup once online (download into ~/.remedy/vision), or\n"
            "  2) Place files under ~/.remedy/vision/models/"
            f"{mid}/ then re-run.\n"
            "This script does not download — production uses first-run download."
        )
        sys.exit(1)

    for src, name in ((model_src, spec.model_file), (mmproj_src, spec.mmproj_file)):
        dst = model_dst / name
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            print(f"skip {name} (already staged)")
        else:
            print(f"copy {src} -> {dst}")
            shutil.copy2(src, dst)

    for rid in BUNDLED_RUNTIME_IDS:
        flavor = "cuda" if "cuda" in rid else "cpu"
        rdst = out / "runtime" / flavor
        rdst.mkdir(parents=True, exist_ok=True)
        user_rt = Path.home() / ".remedy" / "vision" / "runtime"
        if user_rt.is_dir() and any(user_rt.glob("llama-server*")):
            # Only copy into matching flavor when possible
            has_cuda = (user_rt / "ggml-cuda.dll").is_file() or any(
                user_rt.glob("*cuda*")
            )
            if flavor == "cpu" or (flavor == "cuda" and has_cuda):
                for item in user_rt.iterdir():
                    if item.is_file():
                        shutil.copy2(item, rdst / item.name)
                print(f"staged runtime -> {rdst}")
            else:
                print(
                    f"NOTE: stage {rid} under {rdst} "
                    f"(extract {get_runtime_spec(rid).zip_name})"
                )
        else:
            print(
                f"NOTE: no extracted runtime; place llama-server under {rdst} "
                f"from {get_runtime_spec(rid).zip_name}"
            )

    print()
    print(f"Done. Offline bundle root: {out}")
    print(f"Model id (all roles): {mid}")
    print("To use without re-download:")
    print(f'  set REMEDY_LOCAL_BUNDLE={out}')
    print()
    print(
        "Production installers do NOT embed this folder "
        "(tauri.conf.json has no resources/local entry)."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "desktop" / "resources" / "local",
        help="Output root (default: desktop/resources/local)",
    )
    ap.add_argument(
        "--from-vision-home",
        action="store_true",
        help="Read models from ~/.remedy/vision",
    )
    ap.add_argument(
        "--vision-home",
        type=Path,
        default=None,
        help="Custom vision home (models + runtime parent)",
    )
    args = ap.parse_args()
    vision_home = args.vision_home
    if args.from_vision_home and vision_home is None:
        vision_home = Path.home() / ".remedy" / "vision"
    stage(out=args.out, vision_home=vision_home)


if __name__ == "__main__":
    main()
