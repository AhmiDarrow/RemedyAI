"""Hugging Face GGUF search / parse / pull for RMB."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.runtime.rmb.hf import (
    HfError,
    dest_path,
    download_gguf,
    list_gguf_files,
    parse_hf_hint,
    reset_progress,
    resolve_query,
    resolve_url,
    sanitize_filename,
    sanitize_repo,
    search_gguf_repos,
)


def test_parse_name_is_search():
    hint = parse_hf_hint("qwen2.5-coder-7b")
    assert hint.kind == "search"
    assert hint.repo is None
    assert hint.filename is None


def test_parse_owner_repo():
    hint = parse_hf_hint("Qwen/Qwen2.5-Coder-7B-Instruct-GGUF")
    assert hint.kind == "repo"
    assert hint.repo == "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
    assert hint.filename is None


def test_parse_owner_repo_file():
    hint = parse_hf_hint(
        "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    )
    assert hint.kind == "url"
    assert hint.repo == "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
    assert hint.filename.endswith("Q4_K_M.gguf")
    assert hint.url and "/resolve/main/" in hint.url


def test_parse_resolve_and_blob_urls():
    blob = (
        "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/"
        "blob/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf?download=true"
    )
    hint = parse_hf_hint(blob)
    assert hint.kind == "url"
    assert hint.repo == "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
    assert hint.url == (
        "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/"
        "resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    )
    hf_co = parse_hf_hint(
        "https://hf.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/foo.gguf"
    )
    assert hf_co.repo == "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
    assert hf_co.url.endswith("/foo.gguf")


def test_parse_repo_page_url():
    hint = parse_hf_hint("https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF")
    assert hint.kind == "repo"
    assert hint.repo == "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
    assert hint.filename is None


def test_reject_non_hf_and_ssrf():
    with pytest.raises(HfError, match="huggingface"):
        parse_hf_hint("https://example.com/evil.gguf")
    with pytest.raises(HfError, match="http"):
        parse_hf_hint("file:///etc/passwd")
    with pytest.raises(HfError, match="huggingface"):
        parse_hf_hint("https://127.0.0.1/x.gguf")
    with pytest.raises(HfError, match="huggingface"):
        parse_hf_hint("https://169.254.169.254/latest/meta-data")


def test_reject_datasets_and_path_escape():
    with pytest.raises(HfError):
        sanitize_repo("datasets/someone/foo")
    with pytest.raises(HfError):
        sanitize_repo("../etc/passwd")
    with pytest.raises(HfError):
        sanitize_filename("../evil.gguf")
    with pytest.raises(HfError):
        sanitize_filename("weights.bin")


def test_resolve_url_rebuilds_safe_path():
    url = resolve_url(
        "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "sub/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        "main",
    )
    assert url.startswith("https://huggingface.co/Qwen/")
    assert "/resolve/main/" in url
    assert url.endswith(".gguf")


def test_search_returns_multiple_hosts_without_picking(monkeypatch):
    rows = [
        {
            "id": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
            "downloads": 9000,
            "likes": 10,
            "tags": ["gguf"],
        },
        {
            "id": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
            "downloads": 50000,
            "likes": 80,
            "tags": ["gguf"],
        },
        {
            "id": "unsloth/Qwen2.5-Coder-7B-Instruct-GGUF",
            "downloads": 20000,
            "likes": 20,
            "tags": ["gguf"],
        },
    ]

    def fake_json(url: str, timeout: float = 30.0):
        assert "huggingface.co/api/models" in url
        return rows, {}

    monkeypatch.setattr("remedy.runtime.rmb.hf._hf_json", fake_json)
    found = search_gguf_repos("qwen2.5-coder-7b")
    ids = [r["id"] for r in found]
    assert len(ids) == 3
    # Highest downloads first — still a list, never a single implicit pick
    assert ids[0] == "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF"
    assert "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF" in ids


def test_list_files_skips_extra_shards_and_marks_recommended(monkeypatch):
    tree = [
        {"type": "file", "path": "README.md", "size": 12},
        {
            "type": "file",
            "path": "model-Q4_K_M.gguf",
            "size": 4_000_000_000,
        },
        {
            "type": "file",
            "path": "model-Q8_0.gguf",
            "size": 8_000_000_000,
        },
        {
            "type": "file",
            "path": "model-00001-of-00002.gguf",
            "size": 2_000_000_000,
        },
        {
            "type": "file",
            "path": "model-00002-of-00002.gguf",
            "size": 2_000_000_000,
        },
        {
            "type": "file",
            "path": "mmproj-model-f16.gguf",
            "size": 600_000_000,
        },
    ]
    monkeypatch.setattr(
        "remedy.runtime.rmb.hf._hf_json",
        lambda url, timeout=30.0: (tree, {}),
    )
    files = list_gguf_files("Qwen/Demo-GGUF")
    names = [f["name"] for f in files]
    assert "model-00002-of-00002.gguf" not in names
    assert "model-00001-of-00002.gguf" in names
    assert "model-Q4_K_M.gguf" in names
    rec = [f for f in files if f.get("recommended")]
    assert len(rec) == 1
    assert rec[0]["name"] == "model-Q4_K_M.gguf"
    assert rec[0]["role"] == "weights"


def test_resolve_query_name_needs_repo_choice(monkeypatch):
    monkeypatch.setattr(
        "remedy.runtime.rmb.hf.search_gguf_repos",
        lambda q, limit=12: [
            {"id": "OwnerA/Model-GGUF", "downloads": 1},
            {"id": "OwnerB/Model-GGUF", "downloads": 2},
        ],
    )
    out = resolve_query("some-model")
    assert out["ok"] is True
    assert out["need"] == "repo"
    assert len(out["repos"]) == 2
    assert out["files"] == []


def test_download_resumes_partial(tmp_path: Path, monkeypatch):
    reset_progress()
    payload = b"ABCDEFGHIJ" * 100
    dest_dir = tmp_path / "rmb" / "models"
    dest_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "remedy.runtime.rmb.hf.models_dir",
        lambda home_dir=None: dest_dir,
    )

    class _Resp:
        def __init__(self, data: bytes, status: int = 206):
            self._data = data
            self.status = status
            self.headers = {"Content-Length": str(len(data))}
            self._i = 0

        def getcode(self):
            return self.status

        def read(self, n: int = -1):
            if self._i >= len(self._data):
                return b""
            if n < 0:
                chunk = self._data[self._i :]
                self._i = len(self._data)
                return chunk
            chunk = self._data[self._i : self._i + n]
            self._i += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    dest = dest_path("tiny-Q4_K_M.gguf", home_dir=tmp_path, repo="Owner/Tiny-GGUF")
    partial = dest.with_suffix(".gguf.partial")
    partial.write_bytes(payload[:500])

    def fake_open(req, timeout=120.0):
        rng = req.headers.get("Range") or req.headers.get("range")
        assert rng == "bytes=500-"
        return _Resp(payload[500:], status=206)

    monkeypatch.setattr("remedy.runtime.rmb.hf._urlopen", fake_open)
    out = download_gguf(
        repo="Owner/Tiny-GGUF",
        filename="tiny-Q4_K_M.gguf",
        home_dir=tmp_path,
        expected_size=len(payload),
    )
    assert out["ok"] is True
    assert Path(out["path"]).read_bytes() == payload
    assert not partial.exists()


def test_download_rejects_rebuild_to_non_hf():
    with pytest.raises(HfError):
        resolve_url("https://evil.example/x", "x.gguf")


def test_dest_is_flat_basename(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "remedy.runtime.rmb.hf.models_dir",
        lambda home_dir=None: tmp_path,
    )
    p = dest_path("sub/dir/Model-Q4_K_M.gguf", home_dir=tmp_path)
    assert p.parent == tmp_path
    assert p.name == "Model-Q4_K_M.gguf"


def test_dest_namespaces_when_size_mismatches(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "remedy.runtime.rmb.hf.models_dir",
        lambda home_dir=None: tmp_path,
    )
    existing = tmp_path / "Model-Q4_K_M.gguf"
    existing.write_bytes(b"x" * 100)
    p = dest_path(
        "Model-Q4_K_M.gguf",
        home_dir=tmp_path,
        repo="Other/Model-GGUF",
        expected_size=4_000_000_000,
    )
    assert p.name == "Other--Model-GGUF--Model-Q4_K_M.gguf"


def test_dest_always_namespaces_by_repo(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "remedy.runtime.rmb.hf.models_dir",
        lambda home_dir=None: tmp_path,
    )
    existing = tmp_path / "Model-Q4_K_M.gguf"
    existing.write_bytes(b"x" * 100)
    p = dest_path(
        "Model-Q4_K_M.gguf",
        home_dir=tmp_path,
        repo="Owner/Tiny-GGUF",
        expected_size=100,
    )
    assert p.name == "Owner--Tiny-GGUF--Model-Q4_K_M.gguf"


def test_download_one_refuses_skip_without_matching_size(tmp_path: Path, monkeypatch):
    from remedy.runtime.rmb.hf import _download_one

    dest = tmp_path / "Owner--Tiny--tiny.gguf"
    dest.write_bytes(b"stale-file-bytes-" + b"x" * 80)
    payload = b"fresh-payload-" + b"y" * 80

    class _Resp:
        def __init__(self, data: bytes):
            self.headers = {"Content-Length": str(len(data))}
            self.status = 200
            self._data = data
            self._i = 0

        def read(self, n: int = -1):
            if self._i >= len(self._data):
                return b""
            if n < 0:
                chunk = self._data[self._i :]
                self._i = len(self._data)
                return chunk
            chunk = self._data[self._i : self._i + n]
            self._i += len(chunk)
            return chunk

        def getcode(self):
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "remedy.runtime.rmb.hf._urlopen",
        lambda req, timeout=120.0: _Resp(payload),
    )
    out = _download_one("https://huggingface.co/x", dest, expected_size=0)
    assert out.read_bytes() == payload


def test_sibling_part_uses_listed_size(tmp_path: Path, monkeypatch):
    from remedy.runtime.rmb import hf as hf_mod

    seen: list[int] = []

    def _fake_download(url, dest, *, expected_size=0, on_chunk=None):
        seen.append(int(expected_size or 0))
        dest.write_bytes(b"x" * 80)
        return dest

    monkeypatch.setattr(hf_mod, "_download_one", _fake_download)
    monkeypatch.setattr(
        hf_mod,
        "_listed_file_size",
        lambda repo, filename, rev: 111 if "00002" in filename else 222,
    )
    monkeypatch.setattr(
        hf_mod,
        "_sibling_parts",
        lambda filename: [
            "model-00001-of-00002.gguf",
            "model-00002-of-00002.gguf",
        ],
    )
    monkeypatch.setattr(hf_mod, "models_dir", lambda home_dir=None: tmp_path)
    monkeypatch.setattr(hf_mod, "resolve_url", lambda *a, **k: "https://huggingface.co/x")
    out = hf_mod.download_gguf(
        repo="Owner/Tiny",
        filename="model-00001-of-00002.gguf",
        home_dir=tmp_path,
        expected_size=222,
    )
    assert out["ok"] is True
    assert seen == [222, 111]


def test_empty_query_errors():
    with pytest.raises(HfError):
        parse_hf_hint("   ")
    with pytest.raises(HfError):
        parse_hf_hint("")
