"""Generate the third-party notices file that ships with Remedy Desktop.

Remedy redistributes other people's code: Go modules linked into the runtime
(native/go), Rust crates linked into the desktop app, the phone app and the
voice and transport helpers, and the npm packages of the web UI that the
Windows installer ships. MIT / BSD / ISC / Apache-2.0 / OFL-1.1 all require
their notice to travel with the binary. Remedy's own crates (path or git
sources such as remedy-ui) are first-party and are listed, not attributed.
The Zig core has no third-party packages (no build.zig.zon).

Output, identical text in both places:
    desktop/public/THIRD_PARTY_NOTICES.txt           web UI (Vite copies public/)
    desktop/remedyui/assets/THIRD_PARTY_NOTICES.txt  compiled into the desktop app
LICENSE.txt is copied from the repository LICENSE to the same two folders.
The product version comes from desktop/remedyui/Cargo.toml.

Usage:
    python scripts/gen_third_party_notices.py            # write the files
    python scripts/gen_third_party_notices.py --check    # fail if stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTS = (
    ROOT / "desktop" / "public" / "THIRD_PARTY_NOTICES.txt",
    ROOT / "desktop" / "remedyui" / "assets" / "THIRD_PARTY_NOTICES.txt",
)
LICENSE_OUTS = (
    ROOT / "desktop" / "public" / "LICENSE.txt",
    ROOT / "desktop" / "remedyui" / "assets" / "LICENSE.txt",
)
VERSION_FILE = ROOT / "desktop" / "remedyui" / "Cargo.toml"
SPDX_DIR = Path(__file__).resolve().parent / "spdx_licenses"
SELF = "python scripts/gen_third_party_notices.py"

# SPDX ids we can fill when a package ships an identifier but no licence file.
# Templates that start with ``{copyright}`` get the package's own notice.
_SPDX_ALIAS = {
    "MIT": "MIT",
    "MIT LICENSE": "MIT",
    "APACHE-2.0": "Apache-2.0",
    "APACHE 2.0": "Apache-2.0",
    "APACHE LICENSE 2.0": "Apache-2.0",
    "BSD-3-CLAUSE": "BSD-3-Clause",
    "BSD-2-CLAUSE": "BSD-2-Clause",
    "MPL-2.0": "MPL-2.0",
    "ISC": "ISC",
    "UNLICENSE": "Unlicense",
    "OFL-1.1": "OFL-1.1",
}
_COPYRIGHT_TEMPLATES = frozenset({"MIT", "BSD-3-Clause", "BSD-2-Clause", "ISC"})

# Licences that would conflict with an Apache-2.0 product if they shipped.
COPYLEFT_RE = re.compile(r"\b(?:A?GPL|LGPL|SSPL|EUPL|CC-BY-NC|CC-BY-SA)\b", re.I)

LICENSE_FILE_RE = re.compile(
    r"^(LICEN[SC]E|COPYING|COPYRIGHT|NOTICE|UNLICENSE|OFL)(?:[-._].*)?$", re.I
)
COPYRIGHT_RE = re.compile(r"^.{0,120}?copyright[^\n]{0,160}", re.I | re.M)
MAX_TEXT = 40_000


class Component:
    __slots__ = ("ecosystem", "name", "version", "license", "copyright", "texts", "note")

    def __init__(self, ecosystem, name, version, license_, copyright_, texts, note=""):
        self.ecosystem = ecosystem
        self.name = name
        self.version = version
        self.license = license_ or "see project"
        self.copyright = copyright_
        self.texts = texts
        self.note = note

    @property
    def key(self):
        return (self.ecosystem, self.name.lower(), self.version)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT]
    except OSError:
        return ""


def _read_full(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _license_texts(directory: Path) -> list[str]:
    """Every licence-ish file in *directory* (plus a dist-info ``licenses/`` dir)."""
    out: list[str] = []
    if not directory.is_dir():
        return out
    candidates = sorted(
        p for p in directory.iterdir() if p.is_file() and LICENSE_FILE_RE.match(p.name)
    )
    for nested_name in ("licenses", "license", "LICENSES"):
        nested = directory / nested_name
        if nested.is_dir():
            candidates += sorted(
                p for p in nested.rglob("*") if p.is_file() and LICENSE_FILE_RE.match(p.name)
            )
    for p in candidates:
        text = _read(p).strip()
        if text:
            out.append(text)
    return out


def spdx_ids(expr: str) -> list[str]:
    """Split a licence expression into SPDX ids we have a fallback text for."""
    if not expr:
        return []
    parts = re.split(r"\s+(?:OR|AND)\s+|[/,]", expr)
    ids: list[str] = []
    for raw in parts:
        key = re.sub(r"\s+", " ", raw.strip().strip("()")).upper()
        mapped = _SPDX_ALIAS.get(key)
        if mapped and mapped not in ids:
            ids.append(mapped)
    return ids


def _spdx_text(spdx_id: str) -> str:
    return _read(SPDX_DIR / f"{spdx_id}.txt").strip()


def _copyright_from_manifest(directory: Path) -> str:
    """Best-effort copyright line from Cargo.toml / package.json when no file exists."""
    cargo = _read(directory / "Cargo.toml")
    if cargo:
        m = re.search(r'(?m)^\s*authors\s*=\s*\[([^\]]*)\]', cargo)
        if m:
            names = re.findall(r'"([^"]+)"', m.group(1))
            if names:
                # Strip emails: "Name <email>" → "Name"
                cleaned = [re.sub(r"\s*<[^>]+>\s*", "", n).strip() for n in names if n.strip()]
                if cleaned:
                    return "Copyright (c) " + ", ".join(cleaned)
    pj = _read(directory / "package.json")
    if pj:
        try:
            meta = json.loads(pj)
        except json.JSONDecodeError:
            meta = {}
        author = meta.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        if isinstance(author, str) and author.strip():
            return "Copyright (c) " + re.sub(r"\s*<[^>]+>\s*", "", author).strip()
    return ""


def apply_spdx_fallback(component: Component, directory: Path | None = None) -> None:
    """Attach the SPDX standard form when a package names a licence but ships no file.

    MIT / BSD / ISC templates get the package's own copyright line (from a
    licence file, Cargo.toml authors, or package.json) so we never reuse
    another project's notice. Apache-2.0 / MPL-2.0 are identical texts.
    """
    if component.texts:
        return
    ids = spdx_ids(component.license)
    if not ids:
        return
    if not component.copyright and directory:
        component.copyright = _copyright_from_manifest(directory)
    texts: list[str] = []
    for spdx_id in ids:
        body = _spdx_text(spdx_id)
        if not body:
            continue
        if spdx_id in _COPYRIGHT_TEMPLATES:
            copy = component.copyright or f"Copyright (c) the {component.name} authors"
            if not component.copyright:
                component.copyright = copy
            body = body.replace("{copyright}", copy)
        texts.append(body)
    if not texts:
        return
    component.texts = texts
    extra = "licence text is the SPDX standard form — the package did not ship a copy"
    component.note = f"{component.note}; {extra}" if component.note else extra


def _copyright_from(texts: list[str]) -> str:
    for t in texts:
        m = COPYRIGHT_RE.search(t)
        if m:
            return " ".join(m.group(0).split())[:200]
    return ""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


# --------------------------------------------------------------------------
# Rust — crates linked into the shipped binaries (normal deps only, so
# build-time and dev-only crates are not attributed as if they shipped).
# --------------------------------------------------------------------------


# Rust manifests that ship: desktop app, phone app (with connect/core), voice
# helper and transport helper.
RUST_SHIPPED = (
    ROOT / "desktop" / "remedyui",
    ROOT / "connect" / "app",
    ROOT / "native" / "voice",
    ROOT / "native" / "iroh",
)
# "name version" of crates from path or git sources; filled by rust_components().
FIRST_PARTY: set[str] = set()


def rust_components() -> list[Component]:
    wanted: dict[tuple[str, str], None] = {}
    for src in RUST_SHIPPED:
        try:
            proc = subprocess.run(
                ["cargo", "tree", "--offline", "-e", "normal", "--prefix", "none", "--no-dedupe"],
                cwd=src, capture_output=True, text=True, timeout=600, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"WARNING: cargo tree failed in {src.name} ({exc}) — its crates not attributed", file=sys.stderr)
            continue
        if proc.returncode != 0:
            print(f"WARNING: cargo tree exited {proc.returncode} in {src.name} — its crates not attributed", file=sys.stderr)
            print(proc.stderr[:400], file=sys.stderr)
            continue
        for line in proc.stdout.splitlines():
            parts = line.replace(" (*)", "").split()
            if len(parts) >= 2 and parts[1].startswith("v"):
                name, version = parts[0], parts[1][1:]
                # A source in parentheses (path or git) means first-party code;
                # "(proc-macro)" is only a kind marker on a registry crate.
                if len(parts) > 2 and parts[2] != "(proc-macro)":
                    FIRST_PARTY.add(f"{name} {version}")
                    continue
                wanted[(name, version)] = None
    if not wanted:
        return []

    registries = sorted((Path.home() / ".cargo" / "registry" / "src").glob("*"))
    out: list[Component] = []
    for name, version in wanted:
        crate_dir = None
        for reg in registries:
            cand = reg / f"{name}-{version}"
            if cand.is_dir():
                crate_dir = cand
                break
        license_ = ""
        texts: list[str] = []
        note = ""
        if crate_dir:
            manifest = _read(crate_dir / "Cargo.toml")
            m = re.search(r'^\s*license\s*=\s*"([^"]+)"', manifest, re.M)
            if m:
                license_ = m.group(1)
            else:
                mf = re.search(r'^\s*license-file\s*=\s*"([^"]+)"', manifest, re.M)
                if mf:
                    license_ = "see bundled licence file"
                    extra = _read(crate_dir / mf.group(1)).strip()
                    if extra:
                        texts.append(extra)
            texts += _license_texts(crate_dir)
        else:
            note = "source not in the local registry cache when this file was generated"
        component = Component(
            "Rust crate", name, version, license_, _copyright_from(texts), texts, note
        )
        apply_spdx_fallback(component, crate_dir)
        out.append(component)
    return out


# --------------------------------------------------------------------------
# npm — production dependencies only (dev tooling never reaches the bundle).
# --------------------------------------------------------------------------


def npm_components() -> list[Component]:
    lock_path = ROOT / "desktop" / "package-lock.json"
    if not lock_path.exists():
        print("WARNING: no package-lock.json — npm packages not attributed", file=sys.stderr)
        return []
    lock = json.loads(_read_full(lock_path) or "{}")
    out: list[Component] = []
    for rel, meta in (lock.get("packages") or {}).items():
        if not rel.startswith("node_modules/"):
            continue
        if meta.get("dev") or meta.get("devOptional"):
            continue
        pkg_dir = ROOT / "desktop" / Path(rel)
        name = rel.split("node_modules/")[-1]
        version = str(meta.get("version") or "")
        license_ = meta.get("license")
        if isinstance(license_, dict):
            license_ = license_.get("type")
        if not license_ and (pkg_dir / "package.json").exists():
            try:
                pj = json.loads(_read_full(pkg_dir / "package.json") or "{}")
                lic = pj.get("license")
                license_ = lic.get("type") if isinstance(lic, dict) else lic
            except json.JSONDecodeError:
                license_ = None
        texts = _license_texts(pkg_dir)
        note = "" if pkg_dir.is_dir() else "not installed when this file was generated"
        component = Component(
            "npm package", name, version, license_ or "", _copyright_from(texts), texts, note
        )
        apply_spdx_fallback(component, pkg_dir if pkg_dir.is_dir() else None)
        out.append(component)
    return out


# --------------------------------------------------------------------------
# Go — modules actually linked into remedy-runtime (go list -deps, so modules
# that are only named in go.mod are not attributed). Run offline.
# --------------------------------------------------------------------------

GO_DIR = ROOT / "native" / "go"
GO_TARGETS = ("windows", "linux", "darwin")  # platforms the runtime ships on
GO_MISSING: list[str] = []  # modules whose licence text was not found


def detect_license_ids(texts: list[str]) -> str:
    """Classify licence texts into an SPDX expression ("" when unknown)."""
    ids: list[str] = []
    for t in texts:
        head = t[:6000]
        low = " ".join(head.split()).lower()
        if "gnu lesser general public" in low:
            found = "LGPL"
        elif "gnu general public" in low or "gnu affero" in low:
            found = "GPL"
        elif "apache license" in low and "version 2.0" in low:
            found = "Apache-2.0"
        elif "mozilla public license" in low and "2.0" in low:
            found = "MPL-2.0"
        elif "permission is hereby granted, free of charge" in low:
            found = "MIT"
        elif "permission to use, copy, modify, and/or distribute" in low:
            found = "ISC"
        elif "redistribution and use in source and binary forms" in low:
            found = "BSD-3-Clause" if "neither the name" in low else "BSD-2-Clause"
        else:
            continue
        if found not in ids:
            ids.append(found)
    return " AND ".join(ids)


def go_components() -> list[Component]:
    env = {**os.environ, "GOFLAGS": "-mod=mod", "GOPROXY": "off"}
    mods: dict[tuple[str, str], Path] = {}
    for goos in GO_TARGETS:
        try:
            proc = subprocess.run(
                ["go", "list", "-deps", "-f",
                 "{{with .Module}}{{.Path}} {{.Version}} {{.Dir}}{{end}}", "./cmd/..."],
                cwd=GO_DIR, capture_output=True, text=True, timeout=600, check=False,
                env={**env, "GOOS": goos},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"WARNING: go list failed for {goos} ({exc}) — its modules not attributed", file=sys.stderr)
            continue
        if proc.returncode != 0:
            print(f"WARNING: go list exited {proc.returncode} for {goos} — its modules not attributed", file=sys.stderr)
            print(proc.stderr[:400], file=sys.stderr)
            continue
        for line in proc.stdout.splitlines():
            parts = line.strip().split(maxsplit=2)
            # No version means the main module (or the standard library).
            if len(parts) == 3 and parts[1].startswith("v"):
                mods[(parts[0], parts[1])] = Path(parts[2])

    out: list[Component] = []
    for (name, version), directory in sorted(mods.items()):
        texts = _license_texts(directory)
        note = ""
        if not texts:
            note = "licence text not found"
            GO_MISSING.append(f"{name} {version}")
            print(f"WARNING: no licence file for Go module {name} {version}", file=sys.stderr)
        component = Component(
            "Go module", name, version, detect_license_ids(texts), _copyright_from(texts), texts, note
        )
        out.append(component)
    return out


# --------------------------------------------------------------------------
# Vendored binaries and assets that are on no package manager's list.
# --------------------------------------------------------------------------


def vendored_components() -> list[Component]:
    out: list[Component] = []
    return out


# --------------------------------------------------------------------------


def stamp_public_license() -> bool:
    """Copy the binding LICENSE next to each notices file (web UI and desktop app)."""
    src = ROOT / "LICENSE"
    if not src.is_file():
        return False
    text = _read_full(src)
    if not text.strip():
        return False
    changed = False
    for dest in LICENSE_OUTS:
        if _read_full(dest) == text:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="\n")
        changed = True
    return changed


def restamp_version(version: str) -> bool:
    """Rewrite only the version line in the existing notices files.

    A version bump does not change who is attributed, so it must not fail the
    freshness gate — but the shipped file should still name the right build.
    """
    changed = False
    for out in OUTS:
        current = _read_full(out)
        if not current.strip():
            continue
        updated = re.sub(
            r"^Remedy Desktop .+$", f"Remedy Desktop {version}", current, count=1, flags=re.M
        )
        if updated != current:
            out.write_text(updated, encoding="utf-8", newline="\n")
            changed = True
    return changed


def _version() -> str:
    """One source of truth: the desktop app's Cargo.toml."""
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', _read_full(VERSION_FILE))
    return m.group(1) if m else "0.0.0"


# Section headings that need to say where the code ships.
SECTION_TITLE = {
    "npm package": "NPM PACKAGES (WEB UI, WINDOWS INSTALLER)",
    "Go module": "GO MODULES (RUNTIME)",
    "Rust crate": "RUST CRATES (DESKTOP APP, PHONE APP, VOICE AND TRANSPORT HELPERS)",
}


def render(components: list[Component], first_party: set[str] | None = None) -> str:
    texts: dict[str, tuple[str, list[str]]] = {}  # digest -> (text, [component labels])
    order: list[str] = []
    for c in components:
        for t in c.texts:
            d = _digest(t)
            if d not in texts:
                texts[d] = (t, [])
                order.append(d)
            label = f"{c.name} {c.version}".strip()
            if label not in texts[d][1]:
                texts[d][1].append(label)

    ref = {d: f"L{i + 1:03d}" for i, d in enumerate(order)}
    version = _version()

    lines: list[str] = []
    add = lines.append
    add("=" * 78)
    add("REMEDY DESKTOP — THIRD-PARTY NOTICES")
    add("=" * 78)
    add("")
    add(f"Remedy Desktop {version}")
    add("")
    add("Remedy Desktop includes software from the projects listed below. Each is")
    add("used under its own licence; the full text of every licence follows the")
    add("component list. Remedy's own licence (Apache-2.0) is separate — see")
    add("LICENSE and NOTICE in the repository.")
    add("")
    add("Go modules are those linked into the runtime, Rust crates are those linked")
    add("into the desktop app, the phone app and the voice and transport helpers,")
    add("and npm packages are those of the web UI that the Windows installer ships.")
    add("")
    add("Models, voices, and optional components that are downloaded at first use")
    add("rather than bundled are documented in docs/THIRD_PARTY.md.")
    add("")
    if any((c.license or "").upper().find("MPL") >= 0 for c in components):
        add("Source availability: the MPL-2.0 components below are used unmodified.")
        add("Their source is published by their own projects at the versions named")
        add("here; a copy can also be requested by email (see the repository README).")
        add("")
    if first_party:
        add("First-party components: these Rust crates are Remedy's own work (the")
        add("Remedy UI engine and the apps built on it) and are covered by Remedy's")
        add("own licence, so they are not attributed below:")
        add(textwrap.fill(", ".join(sorted(first_party)), 76, initial_indent="  ", subsequent_indent="  ", break_on_hyphens=False))
        add("")
    add("This file is generated by scripts/gen_third_party_notices.py. Do not edit")
    add("it by hand — regenerate it instead.")
    add("")

    by_eco: dict[str, list[Component]] = {}
    for c in components:
        by_eco.setdefault(c.ecosystem, []).append(c)

    add("-" * 78)
    add("SUMMARY")
    add("-" * 78)
    add("")
    for eco in sorted(by_eco):
        add(f"  {len(by_eco[eco]):>4}  {eco}s")
    add("")
    seen_lic: dict[str, int] = {}
    for c in components:
        seen_lic[c.license] = seen_lic.get(c.license, 0) + 1
    add("  Licences in use:")
    for lic, n in sorted(seen_lic.items(), key=lambda kv: (-kv[1], kv[0])):
        add(f"    {n:>4}  {lic}")
    add("")

    for eco in sorted(by_eco):
        add("=" * 78)
        add(SECTION_TITLE.get(eco, eco.upper() + "S"))
        add("=" * 78)
        add("")
        for c in sorted(by_eco[eco], key=lambda c: c.name.lower()):
            add(f"{c.name} {c.version}")
            add(f"    Licence: {c.license}")
            if c.copyright:
                add(f"    {c.copyright}")
            if c.note:
                add(f"    Note: {c.note}")
            if c.texts:
                refs = ", ".join(sorted({ref[_digest(t)] for t in c.texts}))
                add(f"    Licence text: [{refs}]")
            else:
                add("    Licence text: not shipped with the package — see the project's repository")
            add("")

    add("=" * 78)
    add("LICENCE TEXTS")
    add("=" * 78)
    add("")
    for d in order:
        text, users = texts[d]
        add("-" * 78)
        add(f"[{ref[d]}]  applies to: {', '.join(users[:12])}" + (" …" if len(users) > 12 else ""))
        add("-" * 78)
        add("")
        add(text)
        add("")

    return "\n".join(lines).rstrip() + "\n"


def collect() -> list[Component]:
    components: list[Component] = []
    components += npm_components()
    components += rust_components()
    components += go_components()
    components += vendored_components()
    # Stable order and no duplicates, so --check only fails on real drift.
    unique: dict[tuple, Component] = {}
    for c in components:
        unique.setdefault(c.key, c)
    return sorted(unique.values(), key=lambda c: (c.ecosystem, c.name.lower(), c.version))


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate THIRD_PARTY_NOTICES.txt")
    ap.add_argument(
        "--check",
        action="store_true",
        help="fail if a committed file is stale; run it on the release machine, "
        "since the content depends on the local cargo registry and Go module caches",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="fail if a copyleft licence is attributed or a Go module has no licence text",
    )
    args = ap.parse_args()

    components = collect()
    if not components:
        print("ERROR: collected nothing — refusing to write an empty notices file", file=sys.stderr)
        return 2

    copyleft = [c for c in components if COPYLEFT_RE.search(c.license or "")]
    for c in copyleft:
        print(f"COPYLEFT: {c.ecosystem} {c.name} {c.version} — {c.license}", file=sys.stderr)

    rendered = render(components, FIRST_PARTY)

    if args.check:
        # Coverage, not byte-equality: a CI job with a cold cargo or Go cache
        # sees only part of the tree, and a byte compare there would fail on
        # everything it simply cannot look at. Every component this run *can*
        # see must already be attributed, in both files.
        for out in OUTS:
            rel = out.relative_to(ROOT)
            current = _read_full(out)
            if not current.strip():
                print(f"MISSING: {rel} — run {SELF}", file=sys.stderr)
                return 1
            missing = [c for c in components if f"{c.name} {c.version}".strip() not in current]
            if missing:
                print(f"STALE: {rel} is missing {len(missing)} component(s):", file=sys.stderr)
                for c in missing[:10]:
                    print(f"  - {c.ecosystem}: {c.name} {c.version}", file=sys.stderr)
                if len(missing) > 10:
                    print(f"  … and {len(missing) - 10} more", file=sys.stderr)
                print(f"Run {SELF}", file=sys.stderr)
                return 1
        license_src = _read_full(ROOT / "LICENSE")
        for dest in LICENSE_OUTS:
            if license_src.strip() and _read_full(dest) != license_src:
                print(
                    f"STALE: {dest.relative_to(ROOT)} does not match LICENSE — run {SELF}",
                    file=sys.stderr,
                )
                return 1
        print(f"OK: {len(OUTS)} notices files cover all {len(components)} visible components")
    else:
        stamp_public_license()
        for out in OUTS:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered, encoding="utf-8", newline="\n")
            print(f"Wrote {out.relative_to(ROOT)} — {len(components)} components, {len(rendered):,} bytes")
        missing_text = [c for c in components if not c.texts]
        if missing_text:
            print(
                f"NOTE: {len(missing_text)} component(s) still have no licence text:",
                file=sys.stderr,
            )
            for c in missing_text[:12]:
                print(f"  - {c.ecosystem}: {c.name} {c.version} ({c.license})", file=sys.stderr)

    if args.strict and (copyleft or GO_MISSING):
        if copyleft:
            print(f"ERROR: {len(copyleft)} copyleft component(s) attributed as shipping", file=sys.stderr)
        if GO_MISSING:
            print(f"ERROR: {len(GO_MISSING)} Go module(s) without licence text", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
