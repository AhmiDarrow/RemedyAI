"""Text completions against the local llama-server tiers (runtime/local_infer.py).

This is the only door Remedy's nano workers use to reach a local model, so it
is also the door a poisoned ``vision.json``, a hostile job payload or a
malicious local listener would use to reach somewhere else. What must hold:

* a base_url that is not loopback is refused before any socket is opened, and
  a loopback server that answers with a redirect is never followed;
* a runaway ranker cannot post a multi-megabyte prompt — it is truncated, not
  refused, so classify/rerank still get an answer;
* every failure llama-server can produce (a 500, a garbage body, a dropped
  socket, a hang, a reply with no text in it) comes back as ``{"ok": False}``
  with a readable error instead of an exception escaping into the job worker;
* the MDL tier resolver falls back rather than raising when tiers are absent,
  and RMB mode wins over tier routing;
* the queue handlers pass the payload's own limits through and default the
  ones the caller left out.

The tests talk to a real loopback socket (tests/harness/fake_http), so the
loopback guard, the headers, the JSON body, the timeout and the no-redirect
opener are all in the path — mocking ``urlopen`` away would skip exactly the
machinery that keeps the owner's prompts on this machine.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from remedy.runtime import local_infer
from remedy.runtime.local_infer import (
    _MAX_PROMPT_CHARS,
    _MAX_SYSTEM_CHARS,
    _ensemble_confidence,
    _resolve_mdl_url,
    ensure_handlers_registered,
    local_text_complete,
)
from tests.harness.fake_http import (  # noqa: F401  (imported for the fixture)
    Canned,
    fake_http_fixture,
)

CHAT = "/v1/chat/completions"


def _reply(text) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _v1(server) -> str:
    return server.url("/v1")


@pytest.fixture()
def chat(fake_http):  # noqa: F811
    """A fake llama-server answering /v1/chat/completions with 'yes'."""
    fake_http.route(CHAT, json=_reply("yes"))
    return fake_http


# --- refusals: nothing leaves loopback ---------------------------------------


@pytest.mark.parametrize("base", [None, "", "/", "///"])
def test_a_missing_base_url_is_answered_not_raised(base):
    assert local_text_complete("hi", base_url=base) == {
        "ok": False,
        "text": "",
        "error": "no base_url",
    }


@pytest.mark.parametrize("base", ["   ", "\t", "\n"])
def test_a_whitespace_base_url_is_refused_by_the_loopback_guard(base):
    """Not stripped before the check, so it lands in the refusal, not the default."""
    assert local_text_complete("hi", base_url=base)["error"] == (
        "local base_url must be loopback"
    )


@pytest.mark.parametrize(
    "base",
    [
        "http://169.254.169.254/v1",  # cloud metadata
        "http://10.0.0.5:8080/v1",
        "http://192.168.1.10:8740/v1",
        "http://example.com/v1",
        "http://user:pw@127.0.0.1:8740/v1",  # userinfo smuggles a different host
        "file:///etc/passwd",
        "ftp://127.0.0.1/v1",
        "http://2130706433/v1",  # decimal-encoded 127.0.0.1
    ],
)
def test_an_off_loopback_base_url_is_refused_without_opening_a_socket(base, monkeypatch):
    import remedy.core.security as security

    def _boom(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError(f"opened a connection to {base!r}")

    monkeypatch.setattr(security, "urlopen_no_redirect", _boom)

    out = local_text_complete("hi", base_url=base)

    assert out["ok"] is False
    assert "loopback" in out["error"]
    assert out["text"] == ""


def test_a_redirect_off_the_local_server_is_not_followed(fake_http):  # noqa: F811
    """A loopback 302 to metadata is the whole reason for urlopen_no_redirect."""
    fake_http.route(CHAT, status=302, headers={"Location": "/v1/elsewhere"}, body="")
    fake_http.route("/v1/elsewhere", json=_reply("leaked"))

    out = local_text_complete("hi", base_url=_v1(fake_http))

    assert out["ok"] is False
    assert out["error"].startswith("HTTP 302")
    assert fake_http.requests_for("/v1/elsewhere") == []


# --- the request that goes on the wire ---------------------------------------


def test_the_request_is_a_post_of_json_with_the_nanoswarm_user_agent(chat):
    assert local_text_complete("hi", base_url=_v1(chat))["ok"] is True

    req = chat.last_request
    assert req.method == "POST"
    assert req.path == CHAT
    assert req.header("content-type") == "application/json"
    assert req.header("user-agent") == "RemedyAI-nanoswarm/1.0"


@pytest.mark.parametrize(
    "base",
    [
        "http://127.0.0.1:{port}/v1",
        "http://127.0.0.1:{port}/v1/",
        "http://127.0.0.1:{port}/v1///",
        "http://127.0.0.1:{port}/v1/chat/completions",
    ],
)
def test_the_chat_completions_path_is_appended_exactly_once(base, chat):
    out = local_text_complete("hi", base_url=base.format(port=chat.port))

    assert out["ok"] is True
    assert [r.path for r in chat.requests] == [CHAT]


def test_the_body_names_the_local_model_and_carries_the_prompt(chat):
    local_text_complete("classify this", base_url=_v1(chat))

    body = chat.last_request.json_body()
    assert body["model"] == "local-qwen"
    assert body["messages"] == [{"role": "user", "content": "classify this"}]


@pytest.mark.parametrize(
    ("asked", "sent"),
    [
        (16, 16),
        (0, 16),  # falsy -> the documented default, not zero tokens
        (None, 16),
        (1, 1),
        (-5, 1),  # a negative budget would make llama-server reject the call
        (512, 512),
        (9999, 512),  # hard cap
    ],
)
def test_max_tokens_is_clamped_into_a_range_the_server_accepts(asked, sent, chat):
    local_text_complete("hi", base_url=_v1(chat), max_tokens=asked)

    assert chat.last_request.json_body()["max_tokens"] == sent


def test_temperature_is_forwarded_as_given(chat):
    """No clamping here: the caller owns temperature."""
    local_text_complete("hi", base_url=_v1(chat), temperature=0.7)

    assert chat.last_request.json_body()["temperature"] == 0.7


@pytest.mark.parametrize("system", [None, "", "   ", "\n\t "])
def test_an_empty_system_prompt_adds_no_system_message(system, chat):
    local_text_complete("hi", base_url=_v1(chat), system=system)

    assert [m["role"] for m in chat.last_request.json_body()["messages"]] == ["user"]


def test_a_system_prompt_is_stripped_and_sent_first(chat):
    local_text_complete("hi", base_url=_v1(chat), system="  be terse  ")

    assert chat.last_request.json_body()["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]


def test_a_huge_prompt_is_truncated_rather_than_refused(chat):
    """A runaway ranker must still get an answer, just not flood the server."""
    out = local_text_complete("x" * (_MAX_PROMPT_CHARS + 50_000), base_url=_v1(chat))

    assert out["ok"] is True
    content = chat.last_request.json_body()["messages"][-1]["content"]
    assert content.endswith("\n…[truncated]")
    assert len(content) == _MAX_PROMPT_CHARS + len("\n…[truncated]")


def test_a_huge_system_prompt_is_truncated_too(chat):
    local_text_complete(
        "hi", base_url=_v1(chat), system="s" * (_MAX_SYSTEM_CHARS + 5_000)
    )

    system = chat.last_request.json_body()["messages"][0]["content"]
    assert len(system) == _MAX_SYSTEM_CHARS + 1
    assert system.endswith("…")


def test_a_prompt_at_the_cap_is_left_alone(chat):
    exact = "y" * _MAX_PROMPT_CHARS

    local_text_complete(exact, base_url=_v1(chat))

    assert chat.last_request.json_body()["messages"][-1]["content"] == exact


@pytest.mark.parametrize("prompt", [None, "", 0])
def test_a_falsy_prompt_still_sends_a_user_message(prompt, chat):
    local_text_complete(prompt, base_url=_v1(chat))

    assert chat.last_request.json_body()["messages"] == [
        {"role": "user", "content": ""}
    ]


# --- what comes back ----------------------------------------------------------


def test_a_normal_reply_is_returned_with_no_error(chat):
    assert local_text_complete("hi", base_url=_v1(chat)) == {
        "ok": True,
        "text": "yes",
        "error": None,
    }


def test_surrounding_whitespace_is_stripped_from_the_reply(fake_http):  # noqa: F811
    fake_http.route(CHAT, json=_reply("  spaced \n"))

    assert local_text_complete("hi", base_url=_v1(fake_http))["text"] == "spaced"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ([{"type": "text", "text": "one"}, {"type": "text", "text": "two"}], "one two"),
        (["one", "two"], "one two"),
        ([{"type": "image"}], "{'type': 'image'}"),  # no text key: keep the raw part
        (42, "42"),
    ],
)
def test_multipart_and_non_string_content_is_flattened_to_text(
    content, expected, fake_http  # noqa: F811
):
    fake_http.route(CHAT, json=_reply(content))

    assert local_text_complete("hi", base_url=_v1(fake_http))["text"] == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": None}]},
        {"choices": [{}]},
        {"choices": []},
        {},
    ],
)
def test_a_reply_with_no_text_is_not_ok_and_says_empty(payload, fake_http):  # noqa: F811
    fake_http.route(CHAT, json=payload)

    assert local_text_complete("hi", base_url=_v1(fake_http)) == {
        "ok": False,
        "text": "",
        "error": "empty",
    }


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "an", "object"],
        "a bare string",
        {"choices": "not a list"},
        {"choices": ["not an object"]},
    ],
)
def test_a_reply_of_the_wrong_shape_is_reported_not_raised(payload, fake_http):  # noqa: F811
    fake_http.route(CHAT, json=payload)

    out = local_text_complete("hi", base_url=_v1(fake_http))

    assert out["ok"] is False
    assert out["text"] == ""
    assert out["error"].startswith("bad response: ")


# --- transport failures -------------------------------------------------------


def test_an_http_error_carries_the_code_and_the_server_body(fake_http):  # noqa: F811
    fake_http.route(CHAT, status=500, body="model failed to load")

    out = local_text_complete("hi", base_url=_v1(fake_http))

    assert out["ok"] is False
    assert out["text"] == ""
    assert out["error"] == "HTTP 500: model failed to load"


def test_an_http_error_whose_body_cannot_be_read_falls_back_to_the_reason(monkeypatch):
    """A socket that dies mid-error must not turn a 500 into a traceback.

    The harness cannot produce a body that fails to read, so the HTTPError is
    handed to the client directly — everything after the raise is the real code.
    """
    import remedy.core.security as security

    class _DeadBody:
        def read(self, *_a):
            raise OSError("connection reset while reading the error body")

        def close(self):
            return None

    def _raise(*_a, **_k):
        raise HTTPError(
            "http://127.0.0.1:8740/v1/chat/completions",
            502,
            "Bad Gateway",
            {},  # type: ignore[arg-type]
            _DeadBody(),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(security, "urlopen_no_redirect", _raise)

    out = local_text_complete("hi", base_url="http://127.0.0.1:8740/v1")

    assert out["ok"] is False
    assert out["error"] == "HTTP 502: Bad Gateway"


def test_a_huge_error_body_is_capped_before_it_reaches_a_log(fake_http):  # noqa: F811
    fake_http.route(CHAT, status=503, body="e" * 5_000)

    error = local_text_complete("hi", base_url=_v1(fake_http))["error"]

    assert error.startswith("HTTP 503: ")
    assert len(error) == len("HTTP 503: ") + 300


def test_an_unrouted_path_on_a_live_server_is_a_plain_http_error(fake_http):  # noqa: F811
    """Nothing is routed, so the harness 404s — the client must not raise."""
    out = local_text_complete("hi", base_url=_v1(fake_http))

    assert out["ok"] is False
    assert out["error"].startswith("HTTP 404")


def test_a_body_that_is_not_json_is_reported_not_raised(fake_http):  # noqa: F811
    fake_http.route(CHAT, body="<html>llama-server crashed</html>")

    out = local_text_complete("hi", base_url=_v1(fake_http))

    assert out["ok"] is False
    assert out["text"] == ""
    assert out["error"]  # a JSONDecodeError message, however CPython words it


def test_a_dropped_connection_is_reported_not_raised(fake_http):  # noqa: F811
    fake_http.route(CHAT, drop=True)

    out = local_text_complete("hi", base_url=_v1(fake_http))

    assert out["ok"] is False
    assert out["error"]


def test_a_server_that_never_answers_hits_our_timeout(fake_http):  # noqa: F811
    """A hung tier must not pin a job worker for ever."""
    fake_http.route(CHAT, hang=True)

    out = local_text_complete("hi", base_url=_v1(fake_http), timeout_s=0.5)

    assert out["ok"] is False
    assert out["text"] == ""
    assert out["error"]
    assert fake_http.requests_for(CHAT)  # the request really did arrive


def test_a_slow_reply_inside_the_budget_still_returns(fake_http):  # noqa: F811
    fake_http.route(CHAT, json=_reply("late"), delay_s=0.05)

    out = local_text_complete("hi", base_url=_v1(fake_http), timeout_s=10.0)

    assert out["text"] == "late"


def test_nothing_listening_on_the_port_is_reported_not_raised(fake_http):  # noqa: F811
    port = fake_http.port
    fake_http.stop()

    out = local_text_complete("hi", base_url=f"http://127.0.0.1:{port}/v1")

    assert out["ok"] is False
    assert out["error"]


# --- tier bookkeeping ---------------------------------------------------------


def test_a_successful_call_marks_the_tier_as_recently_used(chat, monkeypatch):
    import remedy.runtime.mdl_runtime as mdl_runtime

    seen: list[str] = []
    monkeypatch.setattr(mdl_runtime, "mark_tier_used", seen.append)

    local_text_complete("hi", base_url=_v1(chat))

    assert seen == ["full"]


def test_a_failed_call_marks_nothing_used(fake_http, monkeypatch):  # noqa: F811
    import remedy.runtime.mdl_runtime as mdl_runtime

    seen: list[str] = []
    monkeypatch.setattr(mdl_runtime, "mark_tier_used", seen.append)
    fake_http.route(CHAT, status=500, body="down")

    local_text_complete("hi", base_url=_v1(fake_http))

    assert seen == []


def test_when_tier_bookkeeping_is_unavailable_the_vision_clock_is_ticked(
    chat, monkeypatch
):
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.runtime as vision_runtime

    def _boom(_tier):
        raise RuntimeError("no mdl runtime here")

    ticked: list[int] = []
    monkeypatch.setattr(mdl_runtime, "mark_tier_used", _boom)
    monkeypatch.setattr(vision_runtime, "mark_used", lambda: ticked.append(1))

    assert local_text_complete("hi", base_url=_v1(chat))["ok"] is True
    assert ticked == [1]


def test_bookkeeping_that_fails_entirely_does_not_lose_the_answer(chat, monkeypatch):
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.runtime as vision_runtime

    def _boom(*_a):
        raise RuntimeError("both clocks broken")

    monkeypatch.setattr(mdl_runtime, "mark_tier_used", _boom)
    monkeypatch.setattr(vision_runtime, "mark_used", _boom)

    assert local_text_complete("hi", base_url=_v1(chat)) == {
        "ok": True,
        "text": "yes",
        "error": None,
    }


# --- confidence estimate ------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"ok": False, "text": "email"}, 0.0),  # a refusal is never confident
        ({}, 0.0),
        ({"ok": True, "text": ""}, 0.0),
        ({"ok": True, "text": "   "}, 0.0),
        ({"ok": True, "text": None}, 0.0),
        ({"ok": True, "text": "email"}, 0.85),
        ({"ok": True, "text": "one two three"}, 0.85),
        ({"ok": True, "text": "one two three four"}, 0.3),
        ({"ok": True, "text": "word word " + "x" * 30}, 0.3),  # few words, still long
        ({"ok": True, "text": "I think this is probably an email, but..."}, 0.3),
    ],
)
def test_confidence_rewards_short_answers_and_distrusts_rambling(result, expected):
    assert _ensemble_confidence(result) == expected


# --- MDL url resolution -------------------------------------------------------


@pytest.fixture()
def no_rmb(monkeypatch):
    """Pin the RMB branch off, so tier routing is what is under test.

    Without this the answer depends on the owner's real config file.
    """
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(rmb_mode, "is_local_agent_mode", lambda _cfg: False)
    return monkeypatch


@pytest.fixture()
def tiers(monkeypatch):
    """Tier liveness is a socket probe against the owner's real ports; fake it."""
    import remedy.runtime.mdl_runtime as mdl_runtime

    running: set[str] = set()
    monkeypatch.setattr(mdl_runtime, "is_tier_running", lambda name: name in running)
    return running


def test_rmb_mode_owns_local_inference_and_skips_tier_routing(monkeypatch):
    import remedy.interfaces.config as config_mod
    import remedy.runtime.mdl as mdl
    import remedy.runtime.rmb.mode as rmb_mode

    monkeypatch.setattr(config_mod, "load_config", lambda: {"llm_provider": "rmb"})
    monkeypatch.setattr(rmb_mode, "is_local_agent_mode", lambda _cfg: True)
    monkeypatch.setattr(
        rmb_mode, "rmb_chat_base_url", lambda _cfg: "http://127.0.0.1:8787/v1"
    )

    def _no_routing(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("MDL routing must not run in RMB mode")

    monkeypatch.setattr(mdl, "route_task", _no_routing)

    assert _resolve_mdl_url("text", "http://127.0.0.1:8740/v1") == (
        "http://127.0.0.1:8787/v1",
        "rmb",
    )


def test_a_broken_rmb_check_falls_through_to_tier_routing(monkeypatch, tiers):
    import remedy.interfaces.config as config_mod

    def _boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(config_mod, "load_config", _boom)
    tiers.add("medium")

    assert _resolve_mdl_url("text", "http://127.0.0.1:8740/v1") == (
        "http://127.0.0.1:8742/v1",
        "medium",
    )


def test_a_running_tier_is_used_for_its_task(no_rmb, tiers):
    tiers.add("light")

    assert _resolve_mdl_url("continuity_core", "http://127.0.0.1:8740/v1") == (
        "http://127.0.0.1:8741/v1",
        "light",
    )


def test_the_full_tier_falls_back_to_the_legacy_server_rather_than_starting_one(
    no_rmb, tiers, monkeypatch
):
    """Legacy single-server mode is the FULL port, so nothing needs launching."""
    import remedy.runtime.mdl_runtime as mdl_runtime

    def _no_start(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("must not launch a server for the full tier")

    monkeypatch.setattr(mdl_runtime, "ensure_tier", _no_start)

    assert _resolve_mdl_url("vision_decode", "http://127.0.0.1:9999/v1") == (
        "http://127.0.0.1:9999/v1",
        "full",
    )


def test_a_stopped_tier_is_started_from_the_recorded_vision_state(
    no_rmb, tiers, monkeypatch, tmp_path
):
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.config as vision_config

    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(
        vision_config,
        "load_vision_json",
        lambda *_a, **_k: {
            "model_path": str(tmp_path / "model.gguf"),
            "mmproj_path": str(tmp_path / "mmproj.gguf"),
            "runtime_binary": str(binary),
        },
    )
    calls: list[tuple] = []

    def _ensure(tier, **kwargs):
        calls.append((tier, kwargs))
        return {"ok": True}

    monkeypatch.setattr(mdl_runtime, "ensure_tier", _ensure)

    assert _resolve_mdl_url("brief_update", "http://127.0.0.1:8740/v1") == (
        "http://127.0.0.1:8742/v1",
        "medium",
    )
    assert calls[0][0] == "medium"
    assert calls[0][1]["runtime_binary"] == str(binary)
    assert calls[0][1]["mmproj_path"] == str(tmp_path / "mmproj.gguf")


def test_a_stale_runtime_binary_path_is_replaced_by_the_installed_one(
    no_rmb, tiers, monkeypatch, tmp_path
):
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.config as vision_config
    import remedy.vision.install as vision_install

    installed = tmp_path / "installed" / "llama-server.exe"
    installed.parent.mkdir()
    installed.write_bytes(b"")
    monkeypatch.setattr(
        vision_config,
        "load_vision_json",
        lambda *_a, **_k: {
            "model_path": str(tmp_path / "model.gguf"),
            "runtime_binary": str(tmp_path / "gone" / "llama-server.exe"),
        },
    )
    monkeypatch.setattr(
        vision_install, "runtime_binary_path", lambda *_a, **_k: installed
    )
    calls: list[dict] = []

    def _ensure(_tier, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mdl_runtime, "ensure_tier", _ensure)

    _resolve_mdl_url("brief_update", "http://127.0.0.1:8740/v1")

    assert calls[0]["runtime_binary"] == str(installed)
    assert calls[0]["mmproj_path"] is None


@pytest.mark.parametrize(
    "vstate",
    [
        {},  # nothing installed yet
        {"runtime_binary": "x"},  # no model
        {"model_path": "m.gguf"},  # no runtime binary anywhere
    ],
)
def test_an_incomplete_install_never_tries_to_launch_a_server(
    vstate, no_rmb, tiers, monkeypatch
):
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.config as vision_config
    import remedy.vision.install as vision_install

    monkeypatch.setattr(
        vision_config, "load_vision_json", lambda *_a, **_k: dict(vstate)
    )
    monkeypatch.setattr(vision_install, "runtime_binary_path", lambda *_a, **_k: None)

    def _no_start(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("launched a server without a model or a binary")

    monkeypatch.setattr(mdl_runtime, "ensure_tier", _no_start)

    assert _resolve_mdl_url("brief_update", "http://127.0.0.1:8740/v1") == (
        "http://127.0.0.1:8740/v1",
        "legacy",
    )


def test_a_tier_that_will_not_start_degrades_to_the_light_tier(
    no_rmb, tiers, monkeypatch, tmp_path
):
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.config as vision_config

    tiers.add("light")
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(
        vision_config,
        "load_vision_json",
        lambda *_a, **_k: {"model_path": "m.gguf", "runtime_binary": str(binary)},
    )
    monkeypatch.setattr(
        mdl_runtime, "ensure_tier", lambda *_a, **_k: {"ok": False, "error": "no vram"}
    )

    assert _resolve_mdl_url("brief_update", "http://127.0.0.1:8740/v1") == (
        "http://127.0.0.1:8741/v1",
        "light",
    )


def test_a_crash_while_starting_a_tier_still_degrades_to_light(
    no_rmb, tiers, monkeypatch, tmp_path
):
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.config as vision_config

    tiers.add("light")
    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(
        vision_config,
        "load_vision_json",
        lambda *_a, **_k: {"model_path": "m.gguf", "runtime_binary": str(binary)},
    )

    def _boom(*_a, **_k):
        raise OSError("CreateProcess failed")

    monkeypatch.setattr(mdl_runtime, "ensure_tier", _boom)

    assert _resolve_mdl_url("brief_update", "http://127.0.0.1:8740/v1")[1] == "light"


def test_when_routing_itself_is_unavailable_the_caller_keeps_its_own_base_url(
    no_rmb, monkeypatch
):
    import remedy.runtime.mdl as mdl

    def _boom(*_a, **_k):
        raise ImportError("mdl not built")

    monkeypatch.setattr(mdl, "route_task", _boom)

    assert _resolve_mdl_url("text", "http://127.0.0.1:8740/v1") == (
        "http://127.0.0.1:8740/v1",
        "legacy",
    )


def test_with_no_tiers_and_no_legacy_url_the_result_is_an_empty_base_url(no_rmb, tiers):
    """An empty base_url is not an error here — local_text_complete answers it."""
    base, tier = _resolve_mdl_url("text")

    assert (base, tier) == ("", "legacy")
    assert local_text_complete("hi", base_url=base)["error"] == "no base_url"


def test_an_unknown_task_kind_routes_to_the_default_tier(no_rmb, tiers):
    tiers.add("medium")

    assert _resolve_mdl_url("something_new", "") == (
        "http://127.0.0.1:8742/v1",
        "medium",
    )


# --- queue handlers -----------------------------------------------------------


@pytest.fixture()
def handlers(monkeypatch):
    """Register the handlers on a throwaway queue and hand it back.

    Handlers are called directly rather than submitted: submitting would start
    the queue's worker thread, and none of these tests are about it.
    """
    import remedy.runtime.jobs as jobs

    queue = jobs.LocalJobQueue()
    monkeypatch.setattr(jobs, "default_queue", lambda: queue)
    monkeypatch.setattr(local_infer, "_handlers_ready", False)
    ensure_handlers_registered()
    return queue


def _job(kind: str, payload):
    from remedy.runtime.jobs import LocalJob
    from remedy.runtime.roles import LocalRole

    return LocalJob(role=LocalRole.NANO, kind=kind, payload=payload)


def test_every_local_job_kind_gets_a_handler(handlers):
    assert set(handlers.status()["handlers"]) == {
        "brief_update",
        "continuity_core",
        "library_rerank",
        "nano_classify",
        "spread_plan",
        "vision_decode",
        "worker_summarize",
    }


def test_registering_twice_does_not_replace_live_handlers(handlers):
    sentinel = object()
    handlers.register("vision_decode", sentinel)

    ensure_handlers_registered()

    assert handlers._handlers["vision_decode"] is sentinel


def test_a_queue_that_lost_its_handlers_is_repopulated(handlers):
    handlers._handlers.pop("vision_decode")

    ensure_handlers_registered()

    assert "vision_decode" in handlers.status()["handlers"]


@pytest.fixture()
def resolved(monkeypatch, fake_http):  # noqa: F811
    """Point every handler's tier lookup at the fake server; record the task kind."""
    seen: list[tuple[str, str, str]] = []

    def _resolve(task_kind, base_url="", prompt=""):
        seen.append((task_kind, base_url, prompt))
        return _v1(fake_http), "full"

    monkeypatch.setattr(local_infer, "_resolve_mdl_url", _resolve)
    fake_http.route(CHAT, json=_reply("email"))
    fake_http.seen_tasks = seen  # type: ignore[attr-defined]
    return fake_http


@pytest.mark.parametrize(
    ("kind", "task_kind", "max_tokens"),
    [
        ("nano_classify", "nano_classify", 8),
        ("spread_plan", "text", 64),
        ("worker_summarize", "text", 64),
        ("library_rerank", "library_rerank", 24),
    ],
)
def test_a_text_handler_defaults_the_limits_the_payload_left_out(
    kind, task_kind, max_tokens, handlers, resolved
):
    out = handlers._handlers[kind](_job(kind, {"prompt": "is this an email?"}))

    assert out["ok"] is True
    assert out["text"] == "email"
    assert resolved.seen_tasks[0][0] == task_kind
    body = resolved.last_request.json_body()
    assert body["max_tokens"] == max_tokens
    assert body["messages"][-1]["content"] == "is this an email?"


@pytest.mark.parametrize("kind", ["nano_classify", "library_rerank"])
def test_a_labelling_handler_ships_a_default_system_prompt(kind, handlers, resolved):
    handlers._handlers[kind](_job(kind, {"prompt": "p"}))

    system = resolved.last_request.json_body()["messages"][0]
    assert system["role"] == "system"
    assert system["content"].strip()


@pytest.mark.parametrize("kind", ["spread_plan", "worker_summarize"])
def test_a_free_text_handler_sends_no_system_prompt_unless_asked(
    kind, handlers, resolved
):
    handlers._handlers[kind](_job(kind, {"prompt": "p"}))

    assert [m["role"] for m in resolved.last_request.json_body()["messages"]] == ["user"]


@pytest.mark.parametrize("kind", ["nano_classify", "spread_plan", "library_rerank"])
def test_a_payload_may_override_the_defaults(kind, handlers, resolved):
    handlers._handlers[kind](
        _job(kind, {"prompt": "p", "max_tokens": 5, "system": "custom rules"})
    )

    body = resolved.last_request.json_body()
    assert body["max_tokens"] == 5
    assert body["messages"][0] == {"role": "system", "content": "custom rules"}


@pytest.mark.parametrize(
    "kind", ["nano_classify", "spread_plan", "worker_summarize", "library_rerank"]
)
def test_an_empty_payload_is_a_call_with_an_empty_prompt_not_a_crash(
    kind, handlers, resolved
):
    """Job payloads come off a queue; a missing key must not kill the worker."""
    out = handlers._handlers[kind](_job(kind, None))

    assert out["ok"] is True
    assert resolved.last_request.json_body()["messages"][-1]["content"] == ""


def test_a_text_job_forwards_its_own_temperature(handlers, resolved):
    handlers._handlers["spread_plan"](
        _job("spread_plan", {"prompt": "p", "temperature": 0.9})
    )

    assert resolved.last_request.json_body()["temperature"] == 0.9


def test_the_vision_handler_passes_the_payloads_limits_to_the_decoder(
    handlers, monkeypatch, fake_http  # noqa: F811
):
    import remedy.vision.decoder as decoder

    calls: list[tuple] = []

    def _decode(path, **kw):
        calls.append((path, kw))
        return {"ok": True, "text": "a screenshot"}

    monkeypatch.setattr(decoder, "decode_image", _decode)
    monkeypatch.setattr(
        local_infer, "_resolve_mdl_url", lambda *_a, **_k: (_v1(fake_http), "full")
    )

    out = handlers._handlers["vision_decode"](
        _job(
            "vision_decode",
            {
                "path": "C:/shots/a.png",
                "timeout_s": 5,
                "max_image_bytes": 1024,
                "extra_question": "what button is that?",
            },
        )
    )

    assert out == {"ok": True, "text": "a screenshot"}
    path, kw = calls[0]
    assert path == "C:/shots/a.png"
    assert kw["timeout_s"] == 5.0
    assert kw["max_image_bytes"] == 1024
    assert kw["extra_question"] == "what button is that?"
    assert kw["base_url"] == _v1(fake_http)


def test_the_vision_handler_defaults_a_generous_timeout_and_size_cap(
    handlers, monkeypatch, fake_http  # noqa: F811
):
    import remedy.vision.decoder as decoder

    calls: list[tuple] = []

    def _decode(path, **kw):
        calls.append((path, kw))
        return {"ok": True}

    monkeypatch.setattr(decoder, "decode_image", _decode)
    monkeypatch.setattr(
        local_infer, "_resolve_mdl_url", lambda *_a, **_k: (_v1(fake_http), "full")
    )

    handlers._handlers["vision_decode"](_job("vision_decode", {}))

    path, kw = calls[0]
    assert path == ""  # decode_image is the one that reports the missing file
    assert kw["timeout_s"] == 90.0
    assert kw["max_image_bytes"] == 4 * 1024 * 1024
    assert kw["extra_question"] is None


@pytest.mark.parametrize(
    ("kind", "module_path", "func_name", "port"),
    [
        (
            "brief_update",
            "remedy.memory.harness.local_brief",
            "process_brief_update_job",
            8742,
        ),
        (
            "continuity_core",
            "remedy.memory.partner_state.continuity",
            "process_continuity_core_job",
            8741,
        ),
    ],
)
def test_a_memory_handler_injects_the_routed_base_url_into_the_payload(
    kind, module_path, func_name, port, handlers, monkeypatch
):
    import importlib

    module = importlib.import_module(module_path)
    seen: list[dict] = []

    def _process(job):
        seen.append(dict(job.payload))
        return {"ok": True}

    monkeypatch.setattr(module, func_name, _process)

    job = _job(kind, {"session_id": "s1"})
    assert handlers._handlers[kind](job) == {"ok": True}

    assert seen[0]["mdl_base_url"] == f"http://127.0.0.1:{port}/v1"
    assert seen[0]["session_id"] == "s1"  # the caller's payload survives
    assert job.payload["mdl_base_url"] == f"http://127.0.0.1:{port}/v1"


@pytest.mark.parametrize("kind", ["brief_update", "continuity_core"])
def test_a_memory_handler_survives_an_empty_payload(kind, handlers, monkeypatch):
    import remedy.memory.harness.local_brief as local_brief
    import remedy.memory.partner_state.continuity as continuity

    seen: list[dict] = []

    def _process(job):
        seen.append(job.payload)
        return "ok"

    monkeypatch.setattr(local_brief, "process_brief_update_job", _process)
    monkeypatch.setattr(continuity, "process_continuity_core_job", _process)

    assert handlers._handlers[kind](_job(kind, None)) == "ok"
    assert "mdl_base_url" in seen[0]


# --- speculative escalation ---------------------------------------------------


@pytest.fixture()
def light_tier(monkeypatch, fake_http):  # noqa: F811
    """nano_classify resolved to the LIGHT tier, with escalation aimed here too."""
    import remedy.runtime.mdl as mdl
    import remedy.vision.config as vision_config

    monkeypatch.setattr(
        local_infer, "_resolve_mdl_url", lambda *_a, **_k: (_v1(fake_http), "light")
    )
    monkeypatch.setattr(vision_config, "load_vision_json", lambda *_a, **_k: {})
    monkeypatch.setattr(
        mdl,
        "escalate_routing",
        lambda _routing: mdl.MdlRouting(
            tier="medium",
            base_url=_v1(fake_http),
            port=fake_http.port,
            n_layers=16,
        ),
    )
    return fake_http


def test_a_rambling_light_tier_answer_is_retried_on_a_deeper_tier(handlers, light_tier):
    light_tier.route_sequence(
        CHAT,
        [
            Canned(json=_reply("well it could be an email or maybe a calendar invite")),
            Canned(json=_reply("email")),
        ],
    )

    out = handlers._handlers["nano_classify"](_job("nano_classify", {"prompt": "p"}))

    assert len(light_tier.requests_for(CHAT)) == 2
    assert out["text"] == "email"


def test_a_confident_light_tier_answer_is_not_retried(handlers, light_tier):
    light_tier.route(CHAT, json=_reply("email"))

    out = handlers._handlers["nano_classify"](_job("nano_classify", {"prompt": "p"}))

    assert len(light_tier.requests_for(CHAT)) == 1
    assert out["text"] == "email"


def test_a_failed_light_tier_call_is_not_escalated(handlers, light_tier):
    """Escalating a dead server only doubles the wait; the error must surface."""
    light_tier.route(CHAT, status=500, body="tier down")

    out = handlers._handlers["nano_classify"](_job("nano_classify", {"prompt": "p"}))

    assert len(light_tier.requests_for(CHAT)) == 1
    assert out["ok"] is False
    assert out["error"].startswith("HTTP 500")


def test_an_escalation_that_lands_back_on_light_does_not_call_twice(
    handlers, light_tier, monkeypatch
):
    import remedy.runtime.mdl as mdl

    monkeypatch.setattr(
        mdl,
        "escalate_routing",
        lambda _r: mdl.MdlRouting(
            tier="light",
            base_url=_v1(light_tier),
            port=light_tier.port,
            n_layers=4,
        ),
    )
    light_tier.route(CHAT, json=_reply("maybe an email or maybe not, hard to say"))

    handlers._handlers["nano_classify"](_job("nano_classify", {"prompt": "p"}))

    assert len(light_tier.requests_for(CHAT)) == 1


def test_a_rambling_answer_from_a_deeper_tier_is_not_escalated(
    handlers, monkeypatch, fake_http  # noqa: F811
):
    import remedy.runtime.mdl as mdl

    monkeypatch.setattr(
        local_infer, "_resolve_mdl_url", lambda *_a, **_k: (_v1(fake_http), "medium")
    )

    def _no_escalation(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("only the light tier is speculative")

    monkeypatch.setattr(mdl, "escalate_routing", _no_escalation)
    fake_http.route(CHAT, json=_reply("it could honestly be either of those things"))

    out = handlers._handlers["nano_classify"](_job("nano_classify", {"prompt": "p"}))

    assert len(fake_http.requests_for(CHAT)) == 1
    assert out["ok"] is True


def test_escalation_starts_the_deeper_tier_before_asking_it(
    handlers, light_tier, monkeypatch, tmp_path
):
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.config as vision_config

    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(
        vision_config,
        "load_vision_json",
        lambda *_a, **_k: {
            "model_path": str(tmp_path / "model.gguf"),
            "mmproj_path": str(tmp_path / "mmproj.gguf"),
            "runtime_binary": str(binary),
        },
    )
    started: list[tuple] = []

    def _ensure(tier, **kwargs):
        started.append((tier, kwargs))
        return {"ok": True}

    monkeypatch.setattr(mdl_runtime, "ensure_tier", _ensure)
    light_tier.route_sequence(
        CHAT,
        [
            Canned(json=_reply("hard to say, could be either of those two")),
            Canned(json=_reply("email")),
        ],
    )

    out = handlers._handlers["nano_classify"](_job("nano_classify", {"prompt": "p"}))

    assert started[0][0] == "medium"
    assert started[0][1]["runtime_binary"] == str(binary)
    assert out["text"] == "email"


def test_a_deeper_tier_that_will_not_start_is_still_asked(
    handlers, light_tier, monkeypatch, tmp_path
):
    """The escalated server may already be up; a failed launch is not a refusal."""
    import remedy.runtime.mdl_runtime as mdl_runtime
    import remedy.vision.config as vision_config

    binary = tmp_path / "llama-server.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(
        vision_config,
        "load_vision_json",
        lambda *_a, **_k: {"model_path": "m.gguf", "runtime_binary": str(binary)},
    )

    def _boom(*_a, **_k):
        raise OSError("no vram left")

    monkeypatch.setattr(mdl_runtime, "ensure_tier", _boom)
    light_tier.route_sequence(
        CHAT,
        [
            Canned(json=_reply("hard to say, could be either of those two")),
            Canned(json=_reply("email")),
        ],
    )

    out = handlers._handlers["nano_classify"](_job("nano_classify", {"prompt": "p"}))

    assert len(light_tier.requests_for(CHAT)) == 2
    assert out["text"] == "email"


def test_the_escalated_call_reuses_the_payloads_own_limits(handlers, light_tier):
    light_tier.route_sequence(
        CHAT,
        [
            Canned(json=_reply("a long uncertain rambling sort of an answer here")),
            Canned(json=_reply("email")),
        ],
    )

    handlers._handlers["nano_classify"](
        _job("nano_classify", {"prompt": "p", "max_tokens": 3, "system": "custom"})
    )

    second = json.loads(light_tier.requests_for(CHAT)[1].text)
    assert second["max_tokens"] == 3
    assert second["messages"][0] == {"role": "system", "content": "custom"}
