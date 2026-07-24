"""Version synchronization script for Remedy AI.

Updates version numbers consistently across all package manifests:
- pyproject.toml          (Python package)
- desktop/package.json    (Node frontend)
- desktop/src-tauri/tauri.conf.json  (Tauri app)

Usage:
    python scripts/sync_version.py          # check current version
    python scripts/sync_version.py 0.9.1    # bump to specific version
    python scripts/sync_version.py patch    # bump patch version
    python scripts/sync_version.py minor    # bump minor version
    python scripts/sync_version.py major    # bump major version
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATHS = {
    "pyproject": ROOT / "pyproject.toml",
    "package": ROOT / "desktop" / "package.json",
    "package_lock": ROOT / "desktop" / "package-lock.json",
    "tauri": ROOT / "desktop" / "src-tauri" / "tauri.conf.json",
    "cargo": ROOT / "desktop" / "src-tauri" / "Cargo.toml",
    "cargo_lock": ROOT / "desktop" / "src-tauri" / "Cargo.lock",
    "latest_json": ROOT / "scripts" / "latest.json",
}


def _pyproject_version() -> str:
    text = PATHS["pyproject"].read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def _bump_pyproject(ver: str) -> None:
    text = PATHS["pyproject"].read_text(encoding="utf-8")
    text = re.sub(r'^(version\s*=\s*)"[^"]*"', rf'\1"{ver}"', text, flags=re.MULTILINE)
    PATHS["pyproject"].write_text(text, encoding="utf-8")


def _bump_package_json(ver: str) -> None:
    data = json.loads(PATHS["package"].read_text(encoding="utf-8"))
    data["version"] = ver
    PATHS["package"].write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bump_package_lock(ver: str) -> None:
    """Keep package-lock.json root version aligned with package.json."""
    path = PATHS["package_lock"]
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = ver
    packages = data.get("packages")
    if isinstance(packages, dict) and "" in packages and isinstance(packages[""], dict):
        packages[""]["version"] = ver
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bump_cargo_lock(ver: str) -> None:
    """Bump the local app package version in Cargo.lock (name = \"app\")."""
    path = PATHS["cargo_lock"]
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    # Prefer package name "app" (Tauri crate name in this repo).
    for name in ("app", "remedy-desktop"):
        marker = f'name = "{name}"\nversion = "'
        idx = text.find(marker)
        if idx < 0:
            marker = f'name = "{name}"\r\nversion = "'
            idx = text.find(marker)
        if idx < 0:
            continue
        start = idx + len(marker)
        end = text.find('"', start)
        if end < 0:
            continue
        text = text[:start] + ver + text[end:]
        path.write_text(text, encoding="utf-8")
        return


def _bump_tauri_conf(ver: str) -> None:
    text = PATHS["tauri"].read_text(encoding="utf-8")
    text = re.sub(r'"version":\s*"[^"]*"', f'"version": "{ver}"', text, count=1)
    PATHS["tauri"].write_text(text, encoding="utf-8")


def _bump_cargo_toml(ver: str) -> None:
    if not PATHS["cargo"].exists():
        return
    text = PATHS["cargo"].read_text(encoding="utf-8")
    # Only the package version line under [package]
    text = re.sub(
        r'(?m)^(version\s*=\s*)"[^"]*"',
        rf'\1"{ver}"',
        text,
        count=1,
    )
    PATHS["cargo"].write_text(text, encoding="utf-8")


def _bump_latest_json(ver: str) -> None:
    if not PATHS["latest_json"].exists():
        return
    from datetime import datetime

    data = json.loads(PATHS["latest_json"].read_text(encoding="utf-8"))
    old_raw = str(data.get("version", "")).lstrip("v")
    data["version"] = f"v{ver}"
    data["notes"] = f"Remedy Desktop v{ver} — Windows installer"
    data["pub_date"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Prefer rewriting known GitHub release URL shape so notes/URL/version stay aligned.
    # Real NSIS assets use dots for spaces: "Remedy Desktop" → "Remedy.Desktop_…".
    installer_name = f"Remedy.Desktop_{ver}_x64-setup.exe"
    default_url = (
        f"https://github.com/AhmiDarrow/RemedyAI/releases/download/v{ver}/{installer_name}"
    )

    version_changed = bool(old_raw and old_raw != ver)
    for plat in data.get("platforms", {}).values():
        url = str(plat.get("url") or "")
        if version_changed and url:
            # Replace tag (vX.Y.Z) first, then bare version in filenames.
            url = url.replace(f"v{old_raw}", f"v{ver}")
            url = re.sub(
                rf"(?<![0-9]){re.escape(old_raw)}(?![0-9])",
                ver,
                url,
            )
            # Normalize legacy underscore installer names.
            url = url.replace("Remedy_Desktop_", "Remedy.Desktop_")
            plat["url"] = url
        else:
            plat["url"] = default_url
        # Always normalize URL to the canonical installer name for this version.
        plat["url"] = default_url
        # Signature is per-installer file. Clear when version changes OR when the
        # trusted comment / URL still mentions a different version (stale).
        sig = str(plat.get("signature") or "")
        stale_sig = bool(sig) and (
            version_changed
            or (old_raw and old_raw in sig and old_raw != ver)
            or (f"_{ver}_" not in sig and ver not in sig and bool(sig))
        )
        # Heuristic: if signature blob mentions another setup version, drop it.
        if sig:
            m = re.search(r"(\d+\.\d+\.\d+)_x64-setup", sig)
            if m and m.group(1) != ver:
                stale_sig = True
        if stale_sig or version_changed:
            plat["signature"] = ""
        else:
            plat.setdefault("signature", "")

    PATHS["latest_json"].write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bump_version(current: str, target: str) -> str:
    if target in ("patch", "minor", "major"):
        parts = [int(x) for x in current.split(".")]
        if target == "major":
            parts = [parts[0] + 1, 0, 0]
        elif target == "minor":
            parts = [parts[0], parts[1] + 1, 0]
        else:
            parts = [parts[0], parts[1], parts[2] + 1]
        return ".".join(str(p) for p in parts)
    return target


def _runtime_version() -> str:
    """Import remedy.__version__ from the source tree (not a stale install)."""
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        # Force re-read if already imported with a stale value
        for name in list(sys.modules):
            if name == "remedy" or name.startswith("remedy."):
                del sys.modules[name]
        from remedy import __version__ as v

        return str(v)
    except Exception as exc:
        return f"? ({exc})"


def _check_aligned(expected: str) -> int:
    """Print all version surfaces; return 0 if aligned, 1 if mismatch."""
    rows: list[tuple[str, str]] = []
    rows.append(("pyproject.toml", expected))

    pkg = json.loads(PATHS["package"].read_text(encoding="utf-8"))
    rows.append(("package.json", str(pkg.get("version", "?"))))

    if PATHS["package_lock"].exists():
        lock = json.loads(PATHS["package_lock"].read_text(encoding="utf-8"))
        rows.append(("package-lock.json", str(lock.get("version", "?"))))

    taur = PATHS["tauri"].read_text(encoding="utf-8")
    m = re.search(r'"version":\s*"([^"]*)"', taur)
    rows.append(("tauri.conf.json", m.group(1) if m else "?"))

    if PATHS["cargo"].exists():
        cargo = PATHS["cargo"].read_text(encoding="utf-8")
        cm = re.search(r'(?m)^version\s*=\s*"([^"]*)"', cargo)
        rows.append(("Cargo.toml", cm.group(1) if cm else "?"))

    if PATHS["cargo_lock"].exists():
        clock = PATHS["cargo_lock"].read_text(encoding="utf-8")
        # Prefer the local app package entry.
        am = re.search(
            r'(?ms)name = "app"\s*\nversion = "([^"]*)"',
            clock,
        )
        rows.append(("Cargo.lock (app)", am.group(1) if am else "?"))

    if PATHS["latest_json"].exists():
        latest = json.loads(PATHS["latest_json"].read_text(encoding="utf-8"))
        lv = str(latest.get("version", "?")).lstrip("v")
        rows.append(("scripts/latest.json", lv))
        # Installer asset naming: Remedy.Desktop_* (not Remedy_Desktop_*)
        for plat in (latest.get("platforms") or {}).values():
            url = str(plat.get("url") or "")
            if url and "Remedy_Desktop_" in url and "Remedy.Desktop_" not in url:
                rows.append(("latest.json URL shape", "BAD_UNDERSCORE_NAME"))
                break

    rows.append(("remedy.__version__", _runtime_version()))

    print(f"Canonical version: {expected}")
    bad = 0
    for label, ver in rows:
        ok = ver == expected
        if not ok:
            bad += 1
        mark = "OK " if ok else "BAD"
        print(f"  [{mark}] {label:22} = {ver}")
    if bad:
        print(f"\n{bad} mismatch(es). Run: python scripts/sync_version.py {expected}")
        print("Then reinstall editable:  uv pip install -e .")
        return 1
    print("\nAll version surfaces aligned.")
    return 0


def main():
    current = _pyproject_version()

    if len(sys.argv) < 2 or sys.argv[1] in ("check", "--check", "status"):
        raise SystemExit(_check_aligned(current))

    new_ver = _bump_version(current, sys.argv[1])
    print(f"Bumping to: {new_ver}")

    _bump_pyproject(new_ver)
    print("  Updated pyproject.toml")

    _bump_package_json(new_ver)
    print("  Updated package.json")

    _bump_package_lock(new_ver)
    print("  Updated package-lock.json")

    _bump_tauri_conf(new_ver)
    print("  Updated tauri.conf.json")

    _bump_cargo_toml(new_ver)
    print("  Updated Cargo.toml")

    _bump_cargo_lock(new_ver)
    print("  Updated Cargo.lock")

    _bump_latest_json(new_ver)
    print("  Updated scripts/latest.json")

    print(f"\nDone! Version bumped from {current} -> {new_ver}")
    print("Reinstall editable so dist-info matches:")
    print("  uv pip install -e .")
    print("  # or:  python -m pip install -e .")
    # Re-check from source
    raise SystemExit(_check_aligned(new_ver))


if __name__ == "__main__":
    main()
