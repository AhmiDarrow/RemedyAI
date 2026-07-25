"""Remedy clean-room byte-level BPE (BBPE) for NanoToken.

Original implementation of the public BPE method (byte base + ranked merges).
No third-party tokenizer code or foreign merge tables.

Used only for token *counts* / estimates. Provider API usage remains ground truth.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Pack id for the default shipped Remedy pack
DEFAULT_PACK_ID = "remedy-bbpe-v1"

_pack_lock = threading.Lock()
_pack_cache: dict[str, "BpePack"] = {}
_pretoken_cache: dict[tuple[str, str], int] = {}
_pretoken_order: list[tuple[str, str]] = []
_PRETOKEN_CACHE_MAX = 4096


def _bpe_enabled() -> bool:
    flag = str(os.environ.get("REMEDY_BPE", "1")).strip().lower()
    return flag not in ("0", "false", "no", "off", "disable", "disabled")


def packaged_packs_dir() -> Path:
    return Path(__file__).resolve().parent / "bpe_packs"


def user_packs_dir(home: Path | str | None = None) -> Path:
    if home is not None:
        base = Path(home).expanduser()
    else:
        try:
            from remedy.core.security import get_home_dir

            base = get_home_dir()
        except Exception:
            base = Path("~/.remedy").expanduser()
    return base / "token_tables" / "bpe"


@dataclass
class BpePack:
    """In-memory Remedy BPE pack (owned merges only)."""

    id: str
    version: int = 1
    msg_overhead: int = 4
    pretoken: str = "basic"
    corpus_note: str = ""
    # pair (a, b) -> rank (lower merges first)
    ranks: dict[tuple[int, int], int] = field(default_factory=dict)
    # token id -> bytes (for optional decode; count path may not need full vocab)
    id_to_bytes: dict[int, bytes] = field(default_factory=dict)
    special_tokens: dict[str, int] = field(default_factory=dict)

    @property
    def method_label(self) -> str:
        return f"bpe:{self.id}@v{self.version}"


def _basic_pretokenize(text: str) -> list[str]:
    """Split into coarse pieces: runs of alnum or single non-alnum (incl. space).

    Original simple splitter — not a copy of any vendor pretokenizer.
    """
    if not text:
        return []
    parts: list[str] = []
    buf: list[str] = []
    mode: str | None = None  # 'a' alnum, 'o' other

    def flush() -> None:
        nonlocal buf, mode
        if buf:
            parts.append("".join(buf))
            buf = []
            mode = None

    for ch in text:
        is_al = ch.isalnum() or ch == "_"
        m = "a" if is_al else "o"
        if mode is None:
            mode = m
            buf.append(ch)
        elif m == mode and m == "a":
            buf.append(ch)
        elif m == mode and m == "o" and ch.isspace() and buf and buf[-1].isspace():
            buf.append(ch)  # keep whitespace runs together
        else:
            flush()
            mode = m
            buf.append(ch)
    flush()
    return parts


def _get_pairs(ids: list[int]) -> set[tuple[int, int]]:
    return {(ids[i], ids[i + 1]) for i in range(len(ids) - 1)}


def _merge_once(ids: list[int], pair: tuple[int, int], new_id: int) -> list[int]:
    if len(ids) < 2:
        return ids
    out: list[int] = []
    i = 0
    a, b = pair
    while i < len(ids):
        if i < len(ids) - 1 and ids[i] == a and ids[i + 1] == b:
            out.append(new_id)
            i += 2
        else:
            out.append(ids[i])
            i += 1
    return out


def encode_piece_to_ids(piece: str, pack: BpePack) -> list[int]:
    """Encode one pretoken piece to token ids using pack ranks."""
    raw = piece.encode("utf-8")
    if not raw:
        return []
    # Start as individual bytes (ids 0..255 reserved for bytes)
    ids = list(raw)
    if not pack.ranks:
        return ids

    while True:
        pairs = _get_pairs(ids)
        if not pairs:
            break
        # Lowest rank among present pairs
        best: tuple[int, int] | None = None
        best_rank = 10**18
        for p in pairs:
            r = pack.ranks.get(p)
            if r is not None and r < best_rank:
                best_rank = r
                best = p
        if best is None:
            break
        # new id = 256 + rank (matches training convention)
        new_id = 256 + best_rank
        ids = _merge_once(ids, best, new_id)
    return ids


def count_tokens(text: str | None, pack: BpePack) -> int:
    """Count tokens for text with this pack (specials as whole pieces if exact match)."""
    if not text:
        return 0
    # Exact special token match (rare in free text)
    if text in pack.special_tokens:
        return 1

    # Pretoken cache
    cache_key = (pack.id, text)
    with _pack_lock:
        if cache_key in _pretoken_cache:
            return _pretoken_cache[cache_key]

    if pack.pretoken == "none":
        pieces = [text]
    else:
        pieces = _basic_pretokenize(text)

    total = 0
    for piece in pieces:
        if piece in pack.special_tokens:
            total += 1
        else:
            total += len(encode_piece_to_ids(piece, pack))

    with _pack_lock:
        _pretoken_cache[cache_key] = total
        _pretoken_order.append(cache_key)
        while len(_pretoken_order) > _PRETOKEN_CACHE_MAX:
            old = _pretoken_order.pop(0)
            _pretoken_cache.pop(old, None)
    return total


def pack_from_dict(data: dict[str, Any]) -> BpePack:
    """Build BpePack from JSON-serializable dict (our schema)."""
    merges = data.get("merges") or []
    ranks: dict[tuple[int, int], int] = {}
    # Training stores merges as "int int" or unicode pair strings of single chars
    for i, m in enumerate(merges):
        if isinstance(m, str):
            parts = m.split()
            if len(parts) != 2:
                continue
            try:
                a, b = int(parts[0]), int(parts[1])
            except ValueError:
                # legacy char form — treat as utf-8 single-byte if possible
                ba = parts[0].encode("utf-8")
                bb = parts[1].encode("utf-8")
                if len(ba) == 1 and len(bb) == 1:
                    a, b = ba[0], bb[0]
                else:
                    continue
            ranks[(a, b)] = i
        elif isinstance(m, (list, tuple)) and len(m) == 2:
            ranks[(int(m[0]), int(m[1]))] = i

    specials = data.get("special_tokens") or {}
    if not isinstance(specials, dict):
        specials = {}

    return BpePack(
        id=str(data.get("id") or DEFAULT_PACK_ID),
        version=int(data.get("version") or 1),
        msg_overhead=int(data.get("msg_overhead") or 4),
        pretoken=str(data.get("pretoken") or "basic"),
        corpus_note=str(data.get("corpus_note") or ""),
        ranks=ranks,
        special_tokens={str(k): int(v) for k, v in specials.items()},
    )


def load_pack_file(path: Path) -> BpePack:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid pack file: {path}")
    return pack_from_dict(data)


def find_pack_path(pack_id: str, *, home: Path | str | None = None) -> Path | None:
    """User override first, then packaged pack."""
    pid = (pack_id or "").strip()
    if not pid:
        return None
    name = f"{pid}.json"
    user = user_packs_dir(home) / name
    if user.is_file():
        return user
    pkg = packaged_packs_dir() / name
    if pkg.is_file():
        return pkg
    return None


def get_pack(pack_id: str | None = None, *, home: Path | str | None = None) -> BpePack | None:
    """Load and cache a Remedy pack by id."""
    if not _bpe_enabled():
        return None
    pid = (pack_id or DEFAULT_PACK_ID).strip() or DEFAULT_PACK_ID
    with _pack_lock:
        if pid in _pack_cache:
            return _pack_cache[pid]
    path = find_pack_path(pid, home=home)
    if path is None:
        return None
    try:
        pack = load_pack_file(path)
    except Exception:
        return None
    with _pack_lock:
        _pack_cache[pid] = pack
    return pack


def list_available_packs(*, home: Path | str | None = None) -> list[dict[str, Any]]:
    """Discover packaged + user BPE packs."""
    seen: dict[str, dict[str, Any]] = {}
    for root, source in (
        (packaged_packs_dir(), "packaged"),
        (user_packs_dir(home), "user"),
    ):
        if not root.is_dir():
            continue
        for p in sorted(root.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                pid = str(data.get("id") or p.stem)
                seen[pid] = {
                    "id": pid,
                    "version": data.get("version"),
                    "source": source,
                    "path": str(p),
                    "corpus_note": data.get("corpus_note"),
                    "merge_count": len(data.get("merges") or []),
                }
            except Exception:
                continue
    return list(seen.values())


def train_bpe(
    texts: list[str],
    *,
    num_merges: int = 2000,
    min_pair_count: int = 2,
) -> list[tuple[int, int]]:
    """Train merge list from corpus (clean-room). Returns merges in rank order.

    Base vocab = bytes 0..255. New symbols are 256, 257, ... in merge order.
    """
    # Represent each training string as list of byte ids
    corpus: list[list[int]] = []
    for t in texts:
        if not t:
            continue
        for piece in _basic_pretokenize(t):
            raw = piece.encode("utf-8")
            if raw:
                corpus.append(list(raw))

    if not corpus:
        return []

    next_id = 256
    merges: list[tuple[int, int]] = []
    # Map from current id space — we merge in place using next_id

    for _ in range(num_merges):
        counts: dict[tuple[int, int], int] = {}
        for ids in corpus:
            for i in range(len(ids) - 1):
                pair = (ids[i], ids[i + 1])
                counts[pair] = counts.get(pair, 0) + 1
        if not counts:
            break
        best_pair, best_c = max(counts.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
        if best_c < min_pair_count:
            break
        merges.append(best_pair)
        # Apply merge across corpus
        new_corpus: list[list[int]] = []
        for ids in corpus:
            new_corpus.append(_merge_once(ids, best_pair, next_id))
        corpus = new_corpus
        next_id += 1
    return merges


def pack_dict_from_merges(
    merges: list[tuple[int, int]],
    *,
    pack_id: str = DEFAULT_PACK_ID,
    version: int = 1,
    corpus_note: str = "",
    msg_overhead: int = 4,
) -> dict[str, Any]:
    return {
        "id": pack_id,
        "version": version,
        "created": __import__("datetime").datetime.now(
            __import__("datetime").UTC
        ).date().isoformat(),
        "corpus_note": corpus_note,
        "vocab_size": 256 + len(merges),
        "byte_fallback": True,
        "msg_overhead": msg_overhead,
        "pretoken": "basic",
        "merges": [f"{a} {b}" for a, b in merges],
        "special_tokens": {},
        "ip_note": (
            "Original Remedy pack trained with remedy.nanoswarm.bpe_engine.train_bpe. "
            "No third-party tokenizer code or foreign merge tables."
        ),
    }
