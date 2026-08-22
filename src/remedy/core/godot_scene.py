"""Cheap, offline checks for Godot 4 text resources and GDScript.

Godot's own ``--check-only`` is the real oracle for ``.gd``; these helpers
exist so a broken scene or script is caught when no engine binary is
around, and so a scene's resource references are validated without
launching anything. They never false-red on things they cannot judge.

- :func:`parse_scene` — tokenises ``.tscn`` / ``.tres`` into sections.
- :func:`check_scene` — missing ``res://`` targets, duplicate resource ids,
  unknown ``ExtResource("id")`` references, a root node that never appears.
- :func:`check_gdscript_text` — indentation consistency, bracket balance,
  block headers that miss their ``:``, unterminated strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SECTION_RE = re.compile(r'^\[(?P<kind>[A-Za-z_]+)(?P<attrs>(?:\s+[^\]]*)?)\]\s*$')
_ATTR_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|[^\s\]]+)')
_EXT_REF_RE = re.compile(r'ExtResource\(\s*"([^"]+)"\s*\)')
_SUB_REF_RE = re.compile(r'SubResource\(\s*"([^"]+)"\s*\)')
_BLOCK_HEAD_RE = re.compile(
    r"^\s*(?:(?:static\s+)?func\b|if\b|elif\b|else\b|for\b|while\b|match\b|class\b|"
    r"class_name\b.*:\s*$)"
)
_FUNC_LIKE_RE = re.compile(r"^\s*(?:static\s+)?(?:func|if|elif|else|for|while|match|class)\b")


@dataclass
class SceneSection:
    kind: str
    attrs: dict[str, str] = field(default_factory=dict)
    lineno: int = 0
    body: list[str] = field(default_factory=list)


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"')
    return v


def parse_scene(text: str) -> list[SceneSection]:
    """Split a ``.tscn``/``.tres`` into header sections with attributes."""
    sections: list[SceneSection] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        m = _SECTION_RE.match(line)
        if m:
            attrs = {k: _unquote(v) for k, v in _ATTR_RE.findall(m.group("attrs") or "")}
            sections.append(SceneSection(kind=m.group("kind"), attrs=attrs, lineno=i))
        elif sections and line:
            sections[-1].body.append(line)
    return sections


def project_root_for(path: Path) -> Path | None:
    """Nearest ancestor holding ``project.godot`` (``res://`` base)."""
    p = path.resolve()
    for anc in [p, *p.parents]:
        if (anc / "project.godot").is_file():
            return anc
    return None


def check_scene(path: str | Path, text: str | None = None) -> dict:
    """{ok, path, error, engine, warnings} for a text scene/resource."""
    p = Path(path)
    if text is None:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"ok": False, "path": str(p), "error": str(exc), "engine": "io", "warnings": []}
    errors: list[str] = []
    warnings: list[str] = []
    sections = parse_scene(text)
    if not sections:
        return {
            "ok": False,
            "path": str(p),
            "error": "no [gd_scene]/[gd_resource] header",
            "engine": "tscn-parse",
            "warnings": [],
        }
    head = sections[0]
    if head.kind not in ("gd_scene", "gd_resource"):
        errors.append(f"line {head.lineno}: first section is [{head.kind}], expected gd_scene/gd_resource")
    root = project_root_for(p)
    ext_ids: set[str] = set()
    sub_ids: set[str] = set()
    node_names: list[str] = []
    has_root_node = False
    for s in sections:
        if s.kind == "ext_resource":
            rid = s.attrs.get("id", "")
            if rid in ext_ids:
                errors.append(f"line {s.lineno}: duplicate ext_resource id {rid!r}")
            ext_ids.add(rid)
            target = s.attrs.get("path", "")
            if not target:
                errors.append(f"line {s.lineno}: ext_resource without path")
            elif target.startswith("res://"):
                if root is not None and not (root / target[len("res://"):]).exists():
                    errors.append(f"line {s.lineno}: missing resource {target}")
            elif target.startswith("uid://"):
                pass
            else:
                warnings.append(f"line {s.lineno}: non-res:// path {target!r}")
            if "uid" not in s.attrs and head.kind == "gd_scene":
                warnings.append(f"line {s.lineno}: ext_resource {rid!r} has no uid (Godot 4.1+ adds one)")
        elif s.kind == "sub_resource":
            rid = s.attrs.get("id", "")
            if rid in sub_ids:
                errors.append(f"line {s.lineno}: duplicate sub_resource id {rid!r}")
            sub_ids.add(rid)
        elif s.kind == "node":
            name = s.attrs.get("name", "")
            node_names.append(name)
            if "parent" not in s.attrs:
                has_root_node = True
            elif s.attrs["parent"] not in (".",) and s.attrs["parent"] not in node_names:
                # Parent paths can be nested ("Player/Sprite"); only flag a
                # bare name that never appeared.
                if "/" not in s.attrs["parent"]:
                    warnings.append(
                        f"line {s.lineno}: node {name!r} parent {s.attrs['parent']!r} not seen yet"
                    )
    for s in sections:
        for line in s.body:
            for ref in _EXT_REF_RE.findall(line):
                if ref not in ext_ids:
                    errors.append(f"[{s.kind}] line ~{s.lineno}: ExtResource({ref!r}) is not declared")
            for ref in _SUB_REF_RE.findall(line):
                if ref not in sub_ids:
                    errors.append(f"[{s.kind}] line ~{s.lineno}: SubResource({ref!r}) is not declared")
    if head.kind == "gd_scene" and not has_root_node:
        errors.append("scene has no root node (a [node name=...] without parent)")
    return {
        "ok": not errors,
        "path": str(p),
        "error": "; ".join(errors[:8]),
        "engine": "tscn-parse",
        "warnings": warnings[:12],
    }


def check_gdscript_text(text: str) -> tuple[bool, str]:
    """Fallback GDScript sanity when no Godot binary is available.

    Catches what a typo usually produces — mixed indentation, unbalanced
    brackets, a ``func``/``if`` line missing its colon, an unterminated
    string — and stays quiet about everything else.
    """
    problems: list[str] = []
    indent_style: str | None = None
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[tuple[str, int]] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lead = line[: len(line) - len(line.lstrip(" \t"))]
        if lead:
            style = "tab" if lead[0] == "\t" else "space"
            if " " in lead and "\t" in lead:
                problems.append(f"line {i}: mixed tabs and spaces in indentation")
            elif indent_style is None:
                indent_style = style
            elif style != indent_style:
                problems.append(f"line {i}: {style} indentation in a {indent_style}-indented file")
        # Strings / comments — scan for bracket balance outside them.
        in_str: str | None = None
        j = 0
        code = ""
        while j < len(line):
            ch = line[j]
            if in_str:
                if ch == "\\":
                    j += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch in ('"', "'"):
                in_str = ch
            elif ch == "#":
                break
            else:
                code += ch
                if ch in pairs:
                    stack.append((ch, i))
                elif ch in pairs.values():
                    if not stack or pairs[stack[-1][0]] != ch:
                        problems.append(f"line {i}: unexpected {ch!r}")
                    else:
                        stack.pop()
            j += 1
        if in_str and not (line.count('"""') % 2):
            # Triple-quoted / multi-line strings are rare in GDScript; only
            # flag a lone unterminated single-line string.
            if line.count(in_str) % 2 == 1 and '"""' not in line:
                problems.append(f"line {i}: unterminated string")
        code_s = code.strip()
        if _FUNC_LIKE_RE.match(code_s) and not code_s.endswith(":") and depth == 0 and not stack:
            # `match x:` / `func f():` / `if a and \` — continuation lines
            # (open bracket) are exempt via the stack check above.
            if not code_s.endswith("\\"):
                problems.append(f"line {i}: block header without ':' — {code_s[:40]!r}")
        depth = len(stack)
    for ch, ln in stack[:3]:
        problems.append(f"line {ln}: unclosed {ch!r}")
    return (not problems), "; ".join(problems[:6])
