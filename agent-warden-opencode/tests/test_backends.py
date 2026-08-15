from __future__ import annotations

import json
import threading
import time as _time
from pathlib import Path

from app import config, permissions
from app.pipeline import Pipeline


def test_stall_watchdog_kills_silent_agent(monkeypatch):
    """A phase with no output past the stall threshold is killed and flagged,
    so the retry machinery can resume instead of waiting out the 6h ceiling."""

    class FakeProc:
        def __init__(self):
            self._dead = False

        def poll(self):
            return 0 if self._dead else None

    monkeypatch.setattr(config, "PHASE_STALL_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(config, "PHASE_TIMEOUT_SECONDS", 3600)

    p = Pipeline.__new__(Pipeline)
    p._timed_out = False
    p._stalled = False
    p._current_phase = "extractor"
    p._last_tool = "bash"
    p._last_activity = _time.time() - 10  # silent far beyond the threshold
    events: list[dict] = []
    p._log = events.append
    proc = FakeProc()

    def fake_kill():
        proc._dead = True

    p._kill = fake_kill

    watcher = threading.Thread(target=p._timeout_watch, args=(proc,))
    watcher.start()
    watcher.join(timeout=10)

    assert watcher.is_alive() is False
    assert p._stalled is True
    assert p._timed_out is False
    assert any(e.get("type") == "phase_stall" for e in events)


def test_normalize_backend():
    assert config.normalize_backend(None) == config.BACKEND_OPENCODE
    assert config.normalize_backend("opencode") == config.BACKEND_OPENCODE
    assert config.normalize_backend("oc") == config.BACKEND_OPENCODE
    assert config.normalize_backend("commandcode") == config.BACKEND_COMMANDCODE
    assert config.normalize_backend("cmdc") == config.BACKEND_COMMANDCODE
    assert config.normalize_backend("cc") == config.BACKEND_COMMANDCODE
    assert config.normalize_backend("claude") == config.BACKEND_CLAUDE
    assert config.normalize_backend("claudecode") == config.BACKEND_CLAUDE
    assert config.normalize_backend("anthropic") == config.BACKEND_CLAUDE
    assert config.normalize_backend("codex") == config.BACKEND_CODEX
    assert config.normalize_backend("openaicodex") == config.BACKEND_CODEX
    assert config.normalize_backend("openai") == config.BACKEND_CODEX
    assert config.normalize_backend("unknown_xyz") == config.DEFAULT_BACKEND


def test_backend_meta():
    for bid in (config.BACKEND_OPENCODE, config.BACKEND_COMMANDCODE,
                config.BACKEND_CLAUDE, config.BACKEND_CODEX):
        meta = config.backend_meta(bid)
        assert meta["id"] == bid
        assert "label" in meta
        assert "model" in meta
        assert "provider" in meta


def test_fallback_models_all_backends():
    for bid in (config.BACKEND_OPENCODE, config.BACKEND_COMMANDCODE,
                config.BACKEND_CLAUDE, config.BACKEND_CODEX):
        catalog = config.list_models(backend=bid)
        assert catalog["ok"] is True
        assert catalog["backend"] == bid
        assert len(catalog["models"]) >= 1
        assert catalog["default_model"] is not None


def test_permissions_helpers():
    allowed_tools = permissions.claude_allowed_tools()
    assert "Read" in allowed_tools
    assert "Edit" in allowed_tools
    assert "Bash" in allowed_tools

    sandbox_mode = permissions.codex_sandbox_mode()
    assert "workspace-write" in sandbox_mode


def test_parse_claude_events():
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.stats = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "reasoning": 0}}
    pipeline._claude_tools = {}
    pipeline.EVENT_TEXT_CAP = 4000
    pipeline.EVENT_TOOL_OUTPUT_CAP = 4000

    # message_start
    start_line = json.dumps({
        "type": "message_start",
        "message": {"model": "claude-3-7-sonnet", "usage": {"input_tokens": 150}},
    })
    events = pipeline._parse_claude_line(start_line)
    assert len(events) == 1
    assert events[0]["type"] == "step_start"
    assert events[0]["part"]["model"] == "claude-3-7-sonnet"

    # content_block_start with thinking
    think_line = json.dumps({
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": "Analyzing lecture transcript..."},
    })
    events = pipeline._parse_claude_line(think_line)
    assert len(events) == 1
    assert events[0]["type"] == "reasoning"
    assert "Analyzing" in events[0]["part"]["text"]

    # content_block_start with tool_use
    tool_line = json.dumps({
        "type": "content_block_start",
        "index": 1,
        "content_block": {
            "type": "tool_use",
            "id": "tool_123",
            "name": "Read",
            "input": {"path": "transcript.txt"},
        },
    })
    events = pipeline._parse_claude_line(tool_line)
    assert len(events) == 1
    assert events[0]["type"] == "tool_use"
    assert events[0]["part"]["tool"] == "Read"
    assert events[0]["part"]["callID"] == "tool_123"

    # tool_result
    result_line = json.dumps({
        "type": "tool_result",
        "tool_use_id": "tool_123",
        "tool": "Read",
        "content": "File content sample",
    })
    events = pipeline._parse_claude_line(result_line)
    assert len(events) == 1
    assert events[0]["type"] == "tool_use"
    assert events[0]["part"]["state"]["status"] == "completed"
    assert events[0]["part"]["state"]["output"] == "File content sample"

    # message_delta with usage
    finish_line = json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 75},
    })
    events = pipeline._parse_claude_line(finish_line)
    assert len(events) == 1
    assert events[0]["type"] == "step_finish"
    assert events[0]["part"]["tokens"]["output"] == 75


def test_parse_codex_events():
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.stats = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "reasoning": 0}}
    pipeline._codex_tools = {}
    pipeline.EVENT_TEXT_CAP = 4000
    pipeline.EVENT_TOOL_OUTPUT_CAP = 4000

    # step_start
    start_line = json.dumps({"type": "step_start", "part": {"turn": 1}})
    events = pipeline._parse_codex_line(start_line)
    assert len(events) == 1
    assert events[0]["type"] == "step_start"
    assert events[0]["part"]["turn"] == 1

    # tool_use
    tool_line = json.dumps({
        "type": "tool_use",
        "tool": "exec",
        "call_id": "call_abc",
        "input": {"command": "python scripts/lint.py"},
    })
    events = pipeline._parse_codex_line(tool_line)
    assert len(events) == 1
    assert events[0]["type"] == "tool_use"
    assert events[0]["part"]["tool"] == "exec"
    assert events[0]["part"]["callID"] == "call_abc"

    # tool_completed
    result_line = json.dumps({
        "type": "tool_completed",
        "tool": "exec",
        "call_id": "call_abc",
        "output": "PASS",
    })
    events = pipeline._parse_codex_line(result_line)
    assert len(events) == 1
    assert events[0]["type"] == "tool_use"
    assert events[0]["part"]["state"]["status"] == "completed"
    assert events[0]["part"]["state"]["output"] == "PASS"

    # text
    text_line = json.dumps({"type": "text", "text": "PHASE_COMPLETE"})
    events = pipeline._parse_codex_line(text_line)
    assert len(events) == 1
    assert events[0]["type"] == "text"
    assert events[0]["part"]["text"] == "PHASE_COMPLETE"

    # step_finish
    finish_line = json.dumps({
        "type": "step_finish",
        "stop_reason": "completed",
        "usage": {"input_tokens": 200, "output_tokens": 100},
    })
    events = pipeline._parse_codex_line(finish_line)
    assert len(events) == 1
    assert events[0]["type"] == "step_finish"
    assert events[0]["part"]["tokens"]["input"] == 200
    assert events[0]["part"]["tokens"]["output"] == 100
