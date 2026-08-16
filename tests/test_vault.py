"""Remedy Vault: handle-based secret store — encryption, binding, no-leak.

Established crypto only (libsodium SecretBox / Argon2id via PyNaCl) — the
"non-human/AI-readable" property is architectural: plaintext is never
representable in model context, tool results, or job logs; only handles are.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.core.vault import (
    VaultDomainError,
    VaultError,
    VaultLockedError,
    contains_vault_token,
    expand_text,
    reveal_for_use,
    token_handles,
    vault_add,
    vault_delete,
    vault_list,
)

CARD = "4242424242424242"


def _add_card(tmp_path: Path, **kw):
    args = {
        "value": CARD,
        "handle": "card-visa",
        "kind": "card",
        "label": "Personal Visa",
        "domains": ["amazon.com"],
        "preview": "Visa …4242",
        "home": tmp_path,
    }
    args.update(kw)
    return vault_add(**args)


def test_add_list_never_exposes_value(tmp_path: Path):
    pub = _add_card(tmp_path)
    assert CARD not in json.dumps(pub)
    items = vault_list(tmp_path)
    assert len(items) == 1
    blob = json.dumps(items)
    assert CARD not in blob
    assert items[0]["handle"] == "card-visa"
    assert items[0]["token"] == "{{vault:card-visa}}"
    assert items[0]["preview"] == "Visa …4242"


def test_value_encrypted_at_rest(tmp_path: Path):
    """The card number must not appear anywhere on disk in plaintext."""
    _add_card(tmp_path)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert CARD.encode() not in p.read_bytes(), p


def test_reveal_enforces_domain_binding(tmp_path: Path):
    _add_card(tmp_path)
    # Bound site (with scheme, www, subdomain) → allowed
    assert reveal_for_use("card-visa", destination_url="https://www.amazon.com/checkout", home=tmp_path) == CARD
    assert reveal_for_use("card-visa", destination_url="smile.amazon.com", home=tmp_path) == CARD
    # Any other site → refused
    with pytest.raises(VaultDomainError):
        reveal_for_use("card-visa", destination_url="https://evil.example.com", home=tmp_path)
    # Lookalike suffix must not pass ("notamazon.com")
    with pytest.raises(VaultDomainError):
        reveal_for_use("card-visa", destination_url="https://notamazon.com", home=tmp_path)
    # Unknown destination (e.g. desktop typing) → refused for bound items
    with pytest.raises(VaultDomainError):
        reveal_for_use("card-visa", destination_url="", home=tmp_path)


def test_unbound_item_reveals_anywhere(tmp_path: Path):
    vault_add(value="s3cret", handle="wifi", kind="note", home=tmp_path)
    assert reveal_for_use("wifi", destination_url="", home=tmp_path) == "s3cret"


def test_expand_text_substitutes_tokens(tmp_path: Path):
    _add_card(tmp_path)
    out, handles = expand_text(
        "{{vault:card-visa}}",
        destination_url="https://www.amazon.com/pay",
        home=tmp_path,
    )
    assert out == CARD
    assert handles == ["card-visa"]
    # Domain mismatch surfaces as an exception, never a partial fill
    with pytest.raises(VaultDomainError):
        expand_text("{{vault:card-visa}}", destination_url="https://bad.site", home=tmp_path)


def test_token_helpers():
    assert contains_vault_token("pay with {{vault:card-visa}} now")
    assert not contains_vault_token("pay with card now")
    assert token_handles("{{vault:a}} and {{ vault:b-2 }}") == ["a", "b-2"]


def test_unknown_handle_and_empty_value(tmp_path: Path):
    with pytest.raises(VaultError):
        reveal_for_use("nope", home=tmp_path)
    with pytest.raises(VaultError):
        vault_add(value="", handle="x", home=tmp_path)


def test_delete(tmp_path: Path):
    _add_card(tmp_path)
    assert vault_delete("card-visa", tmp_path) is True
    assert vault_list(tmp_path) == []
    assert vault_delete("card-visa", tmp_path) is False


def test_passphrase_seal_roundtrip_and_wrong_passphrase(tmp_path: Path):
    vault_add(
        value="p@ss",
        handle="bank-login",
        kind="password",
        home=tmp_path,
        passphrase="correct horse",
    )
    assert (
        reveal_for_use("bank-login", home=tmp_path, passphrase="correct horse")
        == "p@ss"
    )
    with pytest.raises(VaultLockedError):
        reveal_for_use("bank-login", home=tmp_path, passphrase="wrong")
    with pytest.raises(VaultLockedError):
        reveal_for_use("bank-login", home=tmp_path, passphrase=None)


def test_reveal_is_audited_without_value(tmp_path: Path, monkeypatch):
    calls: list[dict] = []

    def fake_log(**kw):
        calls.append(kw)

    monkeypatch.setattr(
        "remedy.core.computer.audit.log_computer_action", fake_log
    )
    _add_card(tmp_path)
    reveal_for_use("card-visa", destination_url="amazon.com", home=tmp_path)
    assert calls and calls[0]["action"] == "vault_reveal"
    assert CARD not in json.dumps(calls, default=str)


# ---------------------------------------------------------------------------
# Executor integration: machine-side substitution, model never sees values
# ---------------------------------------------------------------------------


def test_executor_binds_to_live_page_not_last_navigate(tmp_path: Path, monkeypatch):
    """Domain binding must use the LIVE probed URL, not the last explicit
    navigate — a click/redirect could have changed the origin (reviewer P0)."""
    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor

    monkeypatch.setattr(hb, "_bridge", None)
    _add_card(tmp_path)
    ex = ComputerExecutor(home_dir=tmp_path)

    # last_navigate says amazon, but the live page is actually the attacker's.
    ex.bridge.mark_navigated("https://www.amazon.com/checkout")
    monkeypatch.setattr(
        ex, "_page_probe", lambda **_k: {"ok": True, "url": "https://evil.example.com/pay"}
    )
    expanded, err = ex._expand_vault_text("{{vault:card-visa}}", target="browser")
    assert err is not None and err["ok"] is False
    assert CARD not in json.dumps(err)
    assert expanded == "{{vault:card-visa}}"

    # Live page really is the bound site → fills.
    monkeypatch.setattr(
        ex, "_page_probe", lambda **_k: {"ok": True, "url": "https://www.amazon.com/pay"}
    )
    expanded2, err2 = ex._expand_vault_text("{{vault:card-visa}}", target="browser")
    assert err2 is None
    assert expanded2 == CARD


def test_executor_fails_closed_when_page_unknown(tmp_path: Path, monkeypatch):
    """Bound item + unconfirmable page → refuse (fail closed)."""
    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor

    monkeypatch.setattr(hb, "_bridge", None)
    _add_card(tmp_path)
    ex = ComputerExecutor(home_dir=tmp_path)
    monkeypatch.setattr(ex, "_page_probe", lambda **_k: {"ok": False})
    _expanded, err = ex._expand_vault_text("{{vault:card-visa}}", target="browser")
    assert err is not None and err["ok"] is False


def test_executor_unbound_item_fills_without_page(tmp_path: Path, monkeypatch):
    """Unbound secrets fill anywhere, even when the page can't be probed."""
    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.vault import vault_add

    monkeypatch.setattr(hb, "_bridge", None)
    vault_add(value="s3cret", handle="wifi", kind="note", home=tmp_path)
    ex = ComputerExecutor(home_dir=tmp_path)
    monkeypatch.setattr(ex, "_page_probe", lambda **_k: {"ok": False})
    expanded, err = ex._expand_vault_text("{{vault:wifi}}", target="browser")
    assert err is None
    assert expanded == "s3cret"


def test_executor_desktop_type_refuses_bound_items(tmp_path: Path, monkeypatch):
    """Desktop destination is unverifiable → bound items refuse by design."""
    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor

    monkeypatch.setattr(hb, "_bridge", None)
    _add_card(tmp_path)
    ex = ComputerExecutor(home_dir=tmp_path)
    _expanded, err = ex._expand_vault_text(
        "{{vault:card-visa}}", destination_url="", action="type", target="desktop"
    )
    assert err is not None and err["ok"] is False


def test_vault_type_is_owner_checkpoint_in_every_mode(monkeypatch):
    """Filling a stored secret always checkpoints — auto/full included."""
    from remedy.core.approvals import SENSITIVE_PREFIX, ApprovalQueue

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    for mode in ("ask", "auto", "full"):
        q = ApprovalQueue()
        q.set_mode(mode)
        reason = q.needs_ask(
            "type chars=20 target=auto vault=card-visa",
            tool_name="computer_type",
        )
        assert reason is not None and reason.startswith(SENSITIVE_PREFIX), mode
    # Plain-language card names the handle, never a value
    q = ApprovalQueue()
    item = q.create(
        tool_name="computer_type",
        command="type chars=20 target=auto vault=card-visa",
        reason=reason,
        session_id="s1",
    )
    pub = q.to_public(item)
    assert "card-visa" in pub["summary"]
    assert pub["sensitive"] is True
