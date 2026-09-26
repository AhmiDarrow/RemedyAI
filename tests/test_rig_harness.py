"""The evaluator must measure outcomes without inventing success or failure."""

import io
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from scripts.rig.bridge import completion_message, make_server
from scripts.rig.client import RemedyClient, ToolCall, Turn, _iter_sse
from scripts.rig.sandbox import make_sandbox
from scripts.rig.scenarios import CORE, _jail_target, _setup_write_jail
from scripts.rig.score import RunReport, grade


@pytest.mark.parametrize("value", [None, "false", "true", 0, 1])
def test_tool_result_needs_explicit_boolean_evidence(value):
    turn = Turn(prompt="", status="ok")
    RemedyClient._apply(turn, "tool_call", {"id": "a", "name": "read"}, 0)
    RemedyClient._apply(turn, "tool_result", {"id": "a", "ok": value}, 1)
    assert not turn.succeeded("read")
    assert not turn.ok


def test_replayed_result_does_not_invent_another_call():
    turn = Turn(prompt="", status="ok")
    RemedyClient._apply(turn, "tool_call", {"id": "a", "name": "read"}, 0)
    for _ in range(2):
        RemedyClient._apply(turn, "tool_result", {"id": "a", "ok": True}, 1)
    assert len(turn.tool_calls) == 1
    assert turn.ok


def test_done_without_completion_status_is_incomplete():
    turn = Turn(prompt="")
    RemedyClient._apply(turn, "done", {}, 0)
    assert not turn.ok


def test_cleanup_preserves_caller_owned_directory(tmp_path):
    owner_file = tmp_path / "keep.txt"
    owner_file.write_text("owner data")
    sandbox = make_sandbox(root=tmp_path)
    sandbox.cleanup()
    assert owner_file.read_text() == "owner data"


def test_transport_error_aborts_server_before_saving_evidence(tmp_path):
    from scripts.rig.runner import _run_one

    aborted = []

    class Client:
        def new_session(self, **kwargs):
            return "test-session"

        def send(self, *args, **kwargs):
            return Turn(prompt="", status="error", error="connection reset")

        def abort(self, sid):
            aborted.append(sid)

    scenario = replace(CORE[0], setup=None)
    result = _run_one(Client(), make_sandbox(root=tmp_path), scenario, "custom", "", lambda _: None)
    assert not result.passed
    assert aborted == ["test-session"]


def test_concurrent_results_are_matched_by_id():
    turn = Turn(prompt="read two files")
    for call_id in ("first", "second"):
        RemedyClient._apply(turn, "tool_call", {"id": call_id, "name": "read"}, 0)
    RemedyClient._apply(turn, "tool_result", {"id": "first", "name": "read", "ok": False}, 1)
    RemedyClient._apply(turn, "tool_result", {"id": "second", "name": "read", "ok": True}, 2)
    assert [call.ok for call in turn.tool_calls] == [False, True]


def test_pending_call_is_not_success():
    turn = Turn(prompt="", tool_calls=[ToolCall(name="read")])
    assert not turn.succeeded("read")


@pytest.mark.parametrize("status", ["aborted", "incomplete", "timeout", "error", ""])
def test_unfinished_turn_cannot_pass_even_with_artifacts(tmp_path, status):
    scenario = replace(CORE[0], check=lambda *_: (True, "files exist"))
    result = grade(scenario, Turn(prompt="", status=status), tmp_path)
    assert not result.passed


def test_keepalive_is_observable_for_deadline_check():
    stream = io.BytesIO(b': keepalive\n\nevent: done\ndata: {"status":"ok"}\n\n')
    assert list(_iter_sse(stream)) == [("keepalive", {}), ("done", {"status": "ok"})]


def test_borrowed_key_never_lands_in_config_or_repr(tmp_path):
    sandbox = make_sandbox(root=tmp_path)
    path = sandbox.write_config(provider="custom", api_key="not-a-real-key-for-test")
    assert "not-a-real-key-for-test" not in path.read_text()
    assert "not-a-real-key-for-test" not in repr(sandbox)
    assert sandbox.api_key == "not-a-real-key-for-test"


def test_jail_probe_stays_inside_disposable_tree(tmp_path):
    workspace = tmp_path / "workspace" / "write_jail"
    workspace.mkdir(parents=True)
    _setup_write_jail(workspace)
    target = _jail_target(workspace)
    assert target.is_relative_to(tmp_path)
    assert not target.is_relative_to(workspace)
    target.write_text("preserve evidence")
    with pytest.raises(RuntimeError, match="fresh workspace"):
        _setup_write_jail(workspace)
    assert target.read_text() == "preserve evidence"


def test_missing_rung_does_not_earn_higher_tier(tmp_path):
    report = RunReport(label="partial", provider="custom", model="test", base_url="", suite="core")
    for tier in [0, 1, 3]:
        scenario = replace(CORE[0], tier=tier, check=lambda *_: (True, "passed"))
        report.outcomes.append(grade(scenario, Turn(prompt="", status="ok"), tmp_path))
    assert report.top_tier == 1


def test_todo_grader_checks_behavior_not_tool_count(tmp_path):
    # An efficient implementation with three calls must pass, while an app
    # that only implements list must fail the independent add/done checks.
    code = """import json,sys
from pathlib import Path
p=Path(__file__).with_name('todo.json')
items=json.loads(p.read_text())
if sys.argv[1]=='add': items.append({'text':' '.join(sys.argv[2:]),'done':False})
if sys.argv[1]=='done': items[int(sys.argv[2])-1]['done']=True
if sys.argv[1]=='list':
    for item in items: print(item['text'], item['done'])
p.write_text(json.dumps(items))
"""
    (tmp_path / "todo.py").write_text(code)
    original = json.dumps([{"text": text, "done": False} for text in ["buy milk", "ship remedy"]])
    store = tmp_path / "todo.json"
    store.write_text(original)
    turn = Turn(prompt="", status="ok", tool_calls=[ToolCall(name="write", ok=True), ToolCall(name="bash", ok=True)])
    scenario = next(s for s in CORE if s.id == "todo_app")
    assert grade(scenario, turn, tmp_path).passed
    assert store.read_text() == original
    (tmp_path / "todo.py").write_text("print('buy milk ship remedy')\n")
    assert not grade(scenario, turn, tmp_path).passed


@pytest.mark.parametrize("stream", [False, True])
def test_bridge_restart_never_replays_old_response(tmp_path, stream):
    old_request = tmp_path / "000001.request.json"
    old_request.write_text('{"messages": []}')
    (tmp_path / "000001.response.json").write_text('{"content": "stale"}')
    server = make_server(tmp_path, 0, timeout=5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def send():
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            data=json.dumps({"messages": [], "stream": stream}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read().decode()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            response = pool.submit(send)
            deadline = time.monotonic() + 3
            while not (tmp_path / "000002.request.json").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (tmp_path / "000002.request.json").exists()
            (tmp_path / "000002.response.json").write_text('{"content": "fresh"}')
            result = response.result(timeout=5)
            assert '"content": "fresh"' in result
            assert "stale" not in result
            if stream:
                assert "data: [DONE]" in result
            else:
                assert json.loads(result)["choices"][0]["message"]["content"] == "fresh"
        assert old_request.read_text() == '{"messages": []}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("message", [
    [], "text", None, {"content": ["not text"]}, {"tool_calls": {}},
    {"tool_calls": [None]}, {"tool_calls": [{"id": "x", "function": {}}]},
    {"tool_calls": [{"id": "x", "function": {"name": "read", "arguments": {}}}]},
    {"tool_calls": [{"id": "x", "function": {"name": "read", "arguments": "[]"}}]},
    {"tool_calls": [{"id": "x", "function": {"name": "read", "arguments": "{"}}]},
    {"tool_calls": [{"id": "x", "function": {"name": "read", "arguments": "{}"}}] * 2},
])
def test_bridge_rejects_malformed_agent_responses(message):
    with pytest.raises(ValueError):
        completion_message(message)


def test_bridge_accepts_text_and_valid_tool_calls():
    assert completion_message({"content": "Done"}) == {"role": "assistant", "content": "Done"}
    call = {"id": "read-1", "function": {"name": "read", "arguments": '{"path":"notes.txt"}'}}
    result = completion_message({"content": None, "tool_calls": [call]})
    assert result["content"] == ""
    assert result["tool_calls"] == [dict(call, type="function")]
    assert "type" not in call

@pytest.mark.parametrize('borrowed', ['', 'not-a-real-explicit-key'])
def test_sandbox_does_not_inherit_unrelated_provider_keys(tmp_path, monkeypatch, borrowed):
    from unittest.mock import MagicMock

    from scripts.rig.sandbox import Sandbox

    for name in ['OPENAI_API_KEY', 'POE_API_KEY', 'REMEDY_XAI_API_KEY', 'REMEDY_LLM_API_KEY']:
        monkeypatch.setenv(name, 'not-a-real-inherited-key')
    monkeypatch.setenv('REMEDY_RIG_RUNTIME', 'dummy-runtime')
    process = MagicMock()
    process.poll.return_value = None
    launch = MagicMock(return_value=process)
    monkeypatch.setattr('scripts.rig.sandbox.subprocess.Popen', launch)
    monkeypatch.setattr(Sandbox, '_status_ok', lambda self: True)
    sandbox = make_sandbox(root=tmp_path)
    sandbox.write_config(provider='custom', api_key=borrowed)
    sandbox.start()
    environment = launch.call_args.kwargs['env']
    assert 'OPENAI_API_KEY' not in environment
    assert 'POE_API_KEY' not in environment
    assert 'REMEDY_XAI_API_KEY' not in environment
    assert environment.get('REMEDY_LLM_API_KEY', '') == borrowed
    assert environment['REMEDY_API_KEY'] == sandbox.token
    assert 'not-a-real-inherited-key' not in repr(sandbox)
