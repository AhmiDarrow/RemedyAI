"""Host Command IR — the contract models should speak instead of raw shell.

A command string is a compatibility wrapper. Structured ops (run / mkdir /
which / script) never need quoting. ``raw`` is last resort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

HostKind = Literal["cmd", "pwsh", "posix"]
OpKind = Literal["run", "mkdir", "which", "env", "script", "raw", "chain"]
ScriptLang = Literal["pwsh", "cmd", "python"]


@dataclass
class HostOp:
    """One host operation. ``chain`` holds sequential child ops."""

    kind: OpKind
    argv: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    name: str = ""
    lang: str = ""
    body: str = ""
    host: str = ""
    text: str = ""
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    ops: list[HostOp] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.argv:
            d["argv"] = list(self.argv)
        if self.paths:
            d["paths"] = list(self.paths)
        if self.name:
            d["name"] = self.name
        if self.lang:
            d["lang"] = self.lang
        if self.body:
            d["body"] = self.body
        if self.host:
            d["host"] = self.host
        if self.text:
            d["text"] = self.text
        if self.cwd:
            d["cwd"] = self.cwd
        if self.env:
            d["env"] = dict(self.env)
        if self.ops:
            d["ops"] = [o.to_dict() for o in self.ops]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HostOp:
        if not isinstance(data, dict):
            return cls(kind="raw", text="")
        kind = str(data.get("kind") or "raw")
        if kind not in ("run", "mkdir", "which", "env", "script", "raw", "chain"):
            kind = "raw"
        children = data.get("ops") or []
        ops = [
            cls.from_dict(c)
            for c in children
            if isinstance(c, dict)
        ]
        env_raw = data.get("env") or {}
        env = (
            {str(k): str(v) for k, v in env_raw.items()}
            if isinstance(env_raw, dict)
            else {}
        )
        return cls(
            kind=kind,  # type: ignore[arg-type]
            argv=[str(a) for a in (data.get("argv") or [])],
            paths=[str(p) for p in (data.get("paths") or [])],
            name=str(data.get("name") or ""),
            lang=str(data.get("lang") or ""),
            body=str(data.get("body") or ""),
            host=str(data.get("host") or ""),
            text=str(data.get("text") or ""),
            cwd=str(data.get("cwd") or ""),
            env=env,
            ops=ops,
        )


def run_op(argv: list[str], *, cwd: str = "") -> HostOp:
    return HostOp(kind="run", argv=[str(a) for a in argv if str(a)], cwd=cwd)


def mkdir_op(paths: list[str], *, cwd: str = "") -> HostOp:
    return HostOp(kind="mkdir", paths=[str(p) for p in paths if str(p)], cwd=cwd)


def which_op(name: str) -> HostOp:
    return HostOp(kind="which", name=str(name or "").strip())


def script_op(lang: str, body: str, *, cwd: str = "") -> HostOp:
    return HostOp(kind="script", lang=str(lang or "pwsh"), body=str(body or ""), cwd=cwd)


def raw_op(text: str, *, host: str = "") -> HostOp:
    return HostOp(kind="raw", text=str(text or ""), host=host)
