"""Clean-room Remedy BPE engine + NanoToken assignment tests."""

from __future__ import annotations

from pathlib import Path

from remedy.nanoswarm.bpe_engine import (
    DEFAULT_PACK_ID,
    count_tokens,
    get_pack,
    pack_from_dict,
    train_bpe,
)
from remedy.nanoswarm.token_nanobot import (
    estimate_text_tokens,
    get_token_nanobot,
    resolve_bpe_assignment,
)


def test_default_pack_loads():
    pack = get_pack(DEFAULT_PACK_ID)
    assert pack is not None
    assert pack.id == DEFAULT_PACK_ID
    assert pack.ranks  # trained merges present


def test_bpe_count_basic():
    pack = get_pack(DEFAULT_PACK_ID)
    assert pack is not None
    n = count_tokens("Hello world from Remedy NanoToken BPE.", pack)
    assert n >= 1
    assert count_tokens("", pack) == 0
    # Longer text should not be fewer tokens than short (approx)
    n2 = count_tokens("Hello world from Remedy NanoToken BPE. " * 5, pack)
    assert n2 >= n


def test_train_bpe_learns_merges():
    merges = train_bpe(
        ["aaaa bbbb aaaa bbbb", "aaaa aaaa", "bbbb bbbb"] * 20,
        num_merges=50,
        min_pair_count=2,
    )
    assert len(merges) >= 1
    pack = pack_from_dict(
        {
            "id": "test-pack",
            "version": 1,
            "merges": [f"{a} {b}" for a, b in merges],
            "pretoken": "basic",
            "msg_overhead": 4,
        }
    )
    assert count_tokens("aaaa", pack) >= 1


def test_assignment_and_measure_uses_bpe():
    asg = resolve_bpe_assignment("xai", "grok-4.5")
    assert asg["family"] == "openai-compat"
    assert asg.get("available") is True
    assert asg.get("bpe_pack_id") == DEFAULT_PACK_ID
    assert asg.get("method") == "bpe"

    bot = get_token_nanobot()
    n = bot.measure_messages(
        [{"role": "user", "content": "debug the pytest failure please"}],
        provider="xai",
        model="grok-4.5",
        calibrate=False,
    )
    assert n >= 1
    assert "bpe" in (bot.last_method or "")


def test_provider_changed_includes_pack():
    bot = get_token_nanobot()
    out = bot.on_provider_changed(
        "anthropic",
        "claude-sonnet",
        session_id="bpe-test",
        messages=[{"role": "user", "content": "hello " * 20}],
    )
    assert out.get("bpe_pack_id") == DEFAULT_PACK_ID
    assert out.get("encoding_family") == "anthropic-like"
    assert out.get("remeasured") is True


def test_estimate_text_tokens_bpe_path():
    n = estimate_text_tokens(
        "function foo() { return 42; }",
        provider="openai",
        model="gpt-4o",
    )
    assert n >= 1


def test_packaged_pack_file_exists():
    p = Path(__file__).resolve().parents[1] / "src" / "remedy" / "nanoswarm" / "bpe_packs" / f"{DEFAULT_PACK_ID}.json"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "remedy-bbpe-v1" in text
    assert "No third-party" in text or "Original Remedy" in text
