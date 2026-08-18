from __future__ import annotations

import json
import threading
import time as _time
from pathlib import Path
from unittest.mock import patch

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
    assert config.normalize_backend("reasonix") == config.BACKEND_REASONIX
    assert config.normalize_backend("rx") == config.BACKEND_REASONIX
    assert config.normalize_backend("pi") == config.BACKEND_PI
    assert config.normalize_backend("piharness") == config.BACKEND_PI
    assert config.normalize_backend("antigravity") == config.BACKEND_ANTIGRAVITY
    assert config.normalize_backend("agy") == config.BACKEND_ANTIGRAVITY
    assert config.normalize_backend("googleantigravity") == config.BACKEND_ANTIGRAVITY
    assert config.normalize_backend("gemini") == config.BACKEND_ANTIGRAVITY
    assert config.normalize_backend("unknown_xyz") == config.DEFAULT_BACKEND


def test_backend_meta():
    for bid in (config.BACKEND_OPENCODE, config.BACKEND_COMMANDCODE,
                config.BACKEND_CLAUDE, config.BACKEND_CODEX,
                config.BACKEND_REASONIX, config.BACKEND_PI,
                config.BACKEND_ANTIGRAVITY):
        meta = config.backend_meta(bid)
        assert meta["id"] == bid
        assert "label" in meta
        assert "model" in meta
        assert "provider" in meta


def test_fallback_models_all_backends():
    for bid in (config.BACKEND_OPENCODE, config.BACKEND_COMMANDCODE,
                config.BACKEND_CLAUDE, config.BACKEND_CODEX,
                config.BACKEND_REASONIX, config.BACKEND_PI,
                config.BACKEND_ANTIGRAVITY):
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

    agy_args = permissions.antigravity_args()
    assert "--mode" in agy_args
    assert "json" in agy_args
    assert "--auto" in agy_args


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


def test_parse_claude_cli_stream_events():
    """Test native Claude Code 2.x CLI stream-json envelopes."""
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.stats = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "reasoning": 0}}
    pipeline._claude_tools = {}
    pipeline.EVENT_TEXT_CAP = 4000
    pipeline.EVENT_TOOL_OUTPUT_CAP = 4000

    # system / init envelope
    sys_line = json.dumps({
        "type": "system",
        "subtype": "init",
        "model": "claude-opus-5",
        "session_id": "test-session-123",
    })
    events = pipeline._parse_claude_line(sys_line)
    assert len(events) == 1
    assert events[0]["type"] == "step_start"
    assert events[0]["part"]["model"] == "claude-opus-5"

    # assistant envelope with thinking, text, and tool_use
    asst_line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_001",
            "role": "assistant",
            "model": "claude-3-7-sonnet",
            "usage": {"input_tokens": 120, "output_tokens": 45},
            "content": [
                {"type": "thinking", "thinking": "Analyzing requirements..."},
                {"type": "text", "text": "I will read the file."},
                {"type": "tool_use", "id": "call_1", "name": "Read", "input": {"path": "main.py"}},
            ],
        },
    })
    events = pipeline._parse_claude_line(asst_line)
    assert len(events) == 4
    assert events[0]["type"] == "step_start"
    assert events[0]["part"]["tokens"]["input"] == 120
    assert events[1]["type"] == "reasoning"
    assert "Analyzing" in events[1]["part"]["text"]
    assert events[2]["type"] == "text"
    assert events[2]["part"]["text"] == "I will read the file."
    assert events[3]["type"] == "tool_use"
    assert events[3]["part"]["tool"] == "Read"
    assert events[3]["part"]["callID"] == "call_1"
    assert events[3]["part"]["state"]["status"] == "running"

    # user envelope with tool_result
    user_line = json.dumps({
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "print('hello')", "is_error": False}
            ]
        }
    })
    events = pipeline._parse_claude_line(user_line)
    assert len(events) == 1
    assert events[0]["type"] == "tool_use"
    assert events[0]["part"]["tool"] == "Read"
    assert events[0]["part"]["callID"] == "call_1"
    assert events[0]["part"]["state"]["status"] == "completed"
    assert events[0]["part"]["state"]["output"] == "print('hello')"

    # result envelope
    res_line = json.dumps({
        "type": "result",
        "subtype": "success",
        "result": "Task completed successfully",
        "total_cost_usd": 0.0035,
        "usage": {"input_tokens": 200, "output_tokens": 80},
    })
    events = pipeline._parse_claude_line(res_line)
    assert len(events) == 1
    assert events[0]["type"] == "step_finish"
    assert events[0]["part"]["tokens"]["input"] == 200
    assert events[0]["part"]["tokens"]["output"] == 80
    assert events[0]["part"]["cost"] == 0.0035


def test_parse_codex_cli_stream_events():
    """Test native OpenAI Codex CLI JSONL stream events."""
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.stats = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "reasoning": 0}}
    pipeline._codex_tools = {}
    pipeline.EVENT_TEXT_CAP = 4000
    pipeline.EVENT_TOOL_OUTPUT_CAP = 4000

    # thread.started
    t_start = json.dumps({"type": "thread.started", "thread_id": "01a01429-e43d-7a52"})
    events = pipeline._parse_codex_line(t_start)
    assert len(events) == 1
    assert events[0]["type"] == "step_start"
    assert events[0]["part"]["thread"] == "01a01429-e43d-7a52"

    # turn.started
    turn_start = json.dumps({"type": "turn.started"})
    events = pipeline._parse_codex_line(turn_start)
    assert len(events) == 1
    assert events[0]["type"] == "step_start"

    # item.completed (agent_message)
    msg_item = json.dumps({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": "Running tests now."},
    })
    events = pipeline._parse_codex_line(msg_item)
    assert len(events) == 1
    assert events[0]["type"] == "text"
    assert events[0]["part"]["text"] == "Running tests now."

    # item.started (command_execution)
    cmd_start = json.dumps({
        "type": "item.started",
        "item": {"id": "item_1", "type": "command_execution", "command": "python -m pytest", "status": "in_progress"},
    })
    events = pipeline._parse_codex_line(cmd_start)
    assert len(events) == 1
    assert events[0]["type"] == "tool_use"
    assert events[0]["part"]["tool"] == "exec"
    assert events[0]["part"]["callID"] == "item_1"
    assert events[0]["part"]["state"]["status"] == "running"
    assert events[0]["part"]["state"]["input"]["command"] == "python -m pytest"

    # item.completed (command_execution)
    cmd_done = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": "python -m pytest",
            "aggregated_output": "59 passed in 10s",
            "exit_code": 0,
            "status": "completed",
        },
    })
    events = pipeline._parse_codex_line(cmd_done)
    assert len(events) == 1
    assert events[0]["type"] == "tool_use"
    assert events[0]["part"]["callID"] == "item_1"
    assert events[0]["part"]["state"]["status"] == "completed"
    assert events[0]["part"]["state"]["output"] == "59 passed in 10s"

    # turn.completed
    turn_done = json.dumps({
        "type": "turn.completed",
        "usage": {
            "input_tokens": 15000,
            "cached_input_tokens": 10000,
            "output_tokens": 120,
            "reasoning_output_tokens": 25,
        },
    })
    events = pipeline._parse_codex_line(turn_done)
    assert len(events) == 1
    assert events[0]["type"] == "step_finish"
    assert events[0]["part"]["tokens"]["input"] == 15000
    assert events[0]["part"]["tokens"]["output"] == 120
    assert events[0]["part"]["tokens"]["reasoning"] == 25


def test_format_commandcode_model_name():
    assert config._format_commandcode_model_name("deepseek/deepseek-v4-flash") == "DeepSeek V4 Flash"
    assert config._format_commandcode_model_name("moonshotai/kimi-k3") == "Kimi K3"
    assert config._format_commandcode_model_name("claude-sonnet-5") == "Claude Sonnet 5"
    assert config._format_commandcode_model_name("claude-sonnet-4-6") == "Claude Sonnet 4.6"
    assert config._format_commandcode_model_name("gpt-5.4") == "GPT-5.4"
    assert config._format_commandcode_model_name("gpt-5.4-mini") == "GPT-5.4 Mini"
    assert config._format_commandcode_model_name("google/gemini-3.7-flash") == "Gemini 3.7 Flash"
    assert config._format_commandcode_model_name("qwen/qwen3.7-max") == "Qwen 3.7 Max"
    assert config._format_commandcode_model_name("zai-org/glm-5.2") == "GLM 5.2"


def test_parse_commandcode_models():
    sample_output = """Available models  ·  55 models

Open Source

deepseek/deepseek-v4-pro             hybrid-attention long-context reasoning
deepseek/deepseek-v4-flash           fast hybrid-attention reasoning (default)
moonshotai/kimi-k3                   long-horizon coding & knowledge work with 1M context

Anthropic

claude-sonnet-5                      best combo of speed & intelligence (recommended)
claude-opus-5                        most intelligent Opus for agents and coding

OpenAI

gpt-5.4                              frontier model for general complex work
gpt-5.4-mini                         fast, cost-effective model for everyday tasks

Pass the full id, or just the short name after the last "/":
cmdc --model moonshotai/kimi-k2.5
Docs:  https://commandcode.ai/docs/reference/cli/models
"""
    variants = ["low", "medium", "high"]
    models = config._parse_commandcode_models(sample_output, variants)
    assert len(models) == 7

    # Check DeepSeek V4 Flash (reasoning model)
    ds = next(m for m in models if m["id"] == "deepseek/deepseek-v4-flash")
    assert ds["name"] == "DeepSeek V4 Flash"
    assert ds["family"] == "Open Source"
    assert "fast hybrid-attention" in ds["description"]
    assert ds["reasoning"] is True
    assert ds["variants"] == variants

    # Check Kimi K3 (non-reasoning model)
    kimi = next(m for m in models if m["id"] == "moonshotai/kimi-k3")
    assert kimi["name"] == "Kimi K3"
    assert kimi["reasoning"] is False
    assert kimi["variants"] == []

    # Check Claude Sonnet 5
    cs = next(m for m in models if m["id"] == "claude-sonnet-5")
    assert cs["name"] == "Claude Sonnet 5"
    assert cs["family"] == "Anthropic"
    assert "best combo of speed" in cs["description"]
    assert cs["reasoning"] is True

    # Check GPT-5.4
    gpt = next(m for m in models if m["id"] == "gpt-5.4")
    assert gpt["name"] == "GPT-5.4"
    assert gpt["family"] == "OpenAI"
    assert "frontier model" in gpt["description"]
    assert gpt["reasoning"] is False
    assert gpt["variants"] == []


def test_parse_opencode_models_multi_provider():
    sample_verbose = """deepseek/deepseek-v4-flash
{
  "id": "deepseek-v4-flash",
  "providerID": "deepseek",
  "name": "DeepSeek V4 Flash",
  "family": "deepseek-flash",
  "status": "active",
  "capabilities": {
    "reasoning": true
  },
  "variants": {
    "low": {"reasoningEffort": "low"},
    "high": {"reasoningEffort": "high"},
    "max": {"reasoningEffort": "max"}
  }
}
xiaomi/mimo-v2.5
{
  "id": "mimo-v2.5",
  "providerID": "xiaomi",
  "name": "MiMo-V2.5",
  "family": "mimo",
  "status": "active",
  "capabilities": {
    "reasoning": true
  },
  "variants": {
    "low": {"reasoningEffort": "low"},
    "medium": {"reasoningEffort": "medium"},
    "high": {"reasoningEffort": "high"}
  }
}
opencode-go/glm-5.2
{
  "id": "glm-5.2",
  "providerID": "opencode-go",
  "name": "GLM 5.2",
  "status": "active",
  "capabilities": {
    "reasoning": false
  }
}
"""
    models = config._parse_models_verbose(sample_verbose)
    assert len(models) == 3

    ds = next(m for m in models if m["id"] == "deepseek/deepseek-v4-flash")
    assert ds["name"] == "DeepSeek V4 Flash"
    assert ds["provider"] == "deepseek"
    assert ds["reasoning"] is True
    assert ds["variants"] == ["low", "high", "max"]

    xm = next(m for m in models if m["id"] == "xiaomi/mimo-v2.5")
    assert xm["name"] == "MiMo-V2.5"
    assert xm["provider"] == "xiaomi"
    assert xm["reasoning"] is True
    assert xm["variants"] == []

    glm = next(m for m in models if m["id"] == "opencode-go/glm-5.2")
    assert glm["name"] == "GLM 5.2"
    assert glm["provider"] == "opencode-go"
    assert glm["reasoning"] is False


def test_resolve_model_choice_multi_provider():
    catalog = {
        "ok": True,
        "backend": config.BACKEND_OPENCODE,
        "provider": "",
        "default_model": "opencode-go/deepseek-v4-flash",
        "default_variant": "max",
        "models": [
            {
                "id": "deepseek/deepseek-v4-flash",
                "name": "DeepSeek V4 Flash",
                "provider": "deepseek",
                "variants": ["low", "high", "max"],
            },
            {
                "id": "xiaomi/mimo-v2.5",
                "name": "MiMo-V2.5",
                "provider": "xiaomi",
                "variants": [],
            },
            {
                "id": "opencode-go/deepseek-v4-flash",
                "name": "DeepSeek V4 Flash",
                "provider": "opencode-go",
                "variants": ["low", "high", "max"],
            },
        ],
    }

    # Resolve DeepSeek provider
    model, variant = config.resolve_model_choice(
        "deepseek/deepseek-v4-flash", "high", catalog=catalog, backend="opencode")
    assert model == "deepseek/deepseek-v4-flash"
    assert variant == "high"

    # Resolve Xiaomi provider (no reasoning effort variant)
    model, variant = config.resolve_model_choice(
        "xiaomi/mimo-v2.5", "medium", catalog=catalog, backend="opencode")
    assert model == "xiaomi/mimo-v2.5"
    assert variant == ""


def test_parse_agent_event_error_capture(tmp_path):
    transcript = tmp_path / "dummy_transcript.txt"
    transcript.write_text("Hello", encoding="utf-8")
    with patch("app.config.TRANSCRIPTS_DIR", tmp_path), \
         patch("app.config.OUTPUTS_DIR", tmp_path):
        pipeline = Pipeline("dummy_subject", "DS", "dummy_prefix", "1", str(transcript))
    
    error_line = json.dumps({
        "type": "error",
        "timestamp": 123456789,
        "error": {
            "name": "APIError",
            "data": {
                "message": "Invalid API Key",
                "statusCode": 401,
            }
        }
    })
    ev = pipeline._parse_agent_event(error_line)
    assert ev is not None
    assert ev["type"] == "text"
    assert "Invalid API Key" in ev["part"]["text"]
    assert pipeline._last_agent_error == "OpenCode error: Invalid API Key"


def test_parse_models_verbose_openrouter():
    sample_stdout = """openrouter/~anthropic/claude-fable-latest
{
  "id": "~anthropic/claude-fable-latest",
  "providerID": "openrouter",
  "name": "Claude Fable Latest",
  "family": "claude-fable",
  "status": "active",
  "capabilities": {
    "reasoning": true
  },
  "variants": {
    "low": {},
    "high": {}
  }
}
openrouter/meta-llama/llama-3.3-70b-instruct
{
  "id": "meta-llama/llama-3.3-70b-instruct",
  "providerID": "openrouter",
  "name": "Llama 3.3 70B",
  "family": "llama",
  "status": "active",
  "capabilities": {
    "reasoning": false
  }
}
openrouter/nvidia/nemotron-3-super-120b-a12b:free
{
  "id": "nvidia/nemotron-3-super-120b-a12b:free",
  "providerID": "openrouter",
  "name": "Nemotron 3 Super Free",
  "family": "nemotron",
  "status": "active",
  "capabilities": {
    "reasoning": false
  }
}
"""
    models = config._parse_models_verbose(sample_stdout)
    assert len(models) == 3
    ids = [m["id"] for m in models]
    assert "openrouter/~anthropic/claude-fable-latest" in ids
    assert "openrouter/meta-llama/llama-3.3-70b-instruct" in ids
    assert "openrouter/nvidia/nemotron-3-super-120b-a12b:free" in ids
    assert all(m["provider"] == "openrouter" for m in models)


def test_parse_reasonix_line():
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._reasonix_thinking = ""
    pipeline.EVENT_TEXT_CAP = 4000
    pipeline.EVENT_TOOL_OUTPUT_CAP = 4000

    # Control packets return []
    assert pipeline._parse_reasonix_line(json.dumps({"kind": "turn_phase", "phase": "working"})) == []
    assert pipeline._parse_reasonix_line(json.dumps({"kind": "stream_attempt", "streamAttempt": {"action": "begin"}})) == []

    # Thinking / reasoning deltas accumulate; nothing emitted until flushed
    assert pipeline._parse_reasonix_line(json.dumps({
        "kind": "reasoning",
        "text": "Analyzing prefix cache...",
    })) == []
    assert pipeline._parse_reasonix_line(json.dumps({
        "kind": "reasoning",
        "text": " found 3 tokens.",
    })) == []

    # Text deltas are ignored in favor of complete message
    assert pipeline._parse_reasonix_line(json.dumps({"kind": "text", "text": "Extracted"})) == []

    # Message flushes accumulated reasoning, then emits the text as plain log lines
    evs = pipeline._parse_reasonix_line(json.dumps({
        "kind": "message",
        "text": "Extracted section notes",
    }))
    assert len(evs) == 2
    assert evs[0] == {"type": "log", "line": "Analyzing prefix cache... found 3 tokens."}
    assert evs[1] == {"type": "log", "line": "Extracted section notes"}

    # Tool frames become plain log lines (no structured tool blocks)
    evs = pipeline._parse_reasonix_line(json.dumps({
        "kind": "tool_running",
        "toolCallId": "call_123",
        "toolName": "read_file",
        "input": {"path": "dense.md"},
    }))
    assert len(evs) == 1
    assert evs[0] == {"type": "log", "line": "→ read_file"}

    evs = pipeline._parse_reasonix_line(json.dumps({
        "kind": "tool_completed",
        "toolCallId": "call_123",
        "result": "File contents here",
    }))
    assert len(evs) == 1
    assert evs[0] == {"type": "log", "line": "File contents here"}

    # tool_result carries its output nested under tool.output
    evs = pipeline._parse_reasonix_line(json.dumps({
        "kind": "tool_result",
        "tool": {"id": "call_123", "name": "bash", "output": "the answer is 496"},
    }))
    assert len(evs) == 1
    assert evs[0] == {"type": "log", "line": "the answer is 496"}

    # tool_result with no output emits nothing
    assert pipeline._parse_reasonix_line(json.dumps({
        "kind": "tool_result",
        "tool": {"id": "call_123", "name": "bash"},
    })) == []

    # tool_dispatch only logs once a tool call carries args; tool_progress is dropped
    assert pipeline._parse_reasonix_line(json.dumps({
        "kind": "tool_dispatch", "tool": {"name": "bash", "partial": True},
    })) == []
    evs = pipeline._parse_reasonix_line(json.dumps({
        "kind": "tool_dispatch", "tool": {"name": "bash", "args": "{\"command\": \"ls\"}"},
    }))
    assert len(evs) == 1
    assert evs[0] == {"type": "log", "line": "→ bash"}
    assert pipeline._parse_reasonix_line(json.dumps({
        "kind": "tool_progress", "tool": {"id": "x", "output": "..."},
    })) == []

    # Step finish with cache read tokens
    evs = pipeline._parse_reasonix_line(json.dumps({
        "kind": "usage",
        "usage": {
            "promptTokens": 1000,
            "completionTokens": 200,
            "cacheHitTokens": 850,
        },
        "cost": 0.0012,
    }))
    assert len(evs) == 1
    assert evs[0]["type"] == "log"
    assert "step done" in evs[0]["line"]
    assert evs[0]["part"]["tokens"]["input"] == 1000
    assert evs[0]["part"]["tokens"]["output"] == 200
    assert evs[0]["part"]["tokens"]["reasoning"] == 850

    # Top-level result
    evs = pipeline._parse_reasonix_line(json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 850,
        }
    }))
    assert len(evs) == 1
    assert evs[0]["type"] == "log"
    assert "done · success" in evs[0]["line"]
    assert evs[0]["part"]["tokens"]["input"] == 1000
    assert evs[0]["part"]["tokens"]["output"] == 200
    assert evs[0]["part"]["tokens"]["reasoning"] == 850


def test_format_pi_model_name():
    family, name = config._format_pi_model_name("~deepseek/deepseek-v4-flash-latest")
    assert family == "deepseek"
    assert name == "DeepSeek V4 Flash Latest"

    family, name = config._format_pi_model_name("deepseek/deepseek-v4-flash")
    assert family == "deepseek"
    assert name == "DeepSeek V4 Flash"

    family, name = config._format_pi_model_name("deepseek/deepseek-v4-flash-0731")
    assert family == "deepseek"
    assert name == "DeepSeek V4 Flash (0731 Snapshot)"

    family, name = config._format_pi_model_name("deepseek/deepseek-v4-pro-0813")
    assert family == "deepseek"
    assert name == "DeepSeek V4 Pro (0813 Snapshot)"

    family, name = config._format_pi_model_name("~anthropic/claude-sonnet-latest")
    assert family == "anthropic"
    assert name == "Claude Sonnet Latest"

    family, name = config._format_pi_model_name("google/gemini-3.7-flash")
    assert family == "google"
    assert name == "Gemini 3.7 Flash"


def test_parse_human_tokens():
    assert config._parse_human_tokens("1.0M") == 1_000_000
    assert config._parse_human_tokens("1M") == 1_000_000
    assert config._parse_human_tokens("384K") == 384_000
    assert config._parse_human_tokens("393.2K") == 393_200
    assert config._parse_human_tokens("128000") == 128_000
    assert config._parse_human_tokens("") == 0


def test_parse_pi_models():
    sample_stdout = """provider    model                               context  max-out  thinking  images
openrouter  ~deepseek/deepseek-v4-flash-latest  1.0M     384K     yes       no    
openrouter  deepseek/deepseek-v4-flash          1.0M     384K     yes       no    
openrouter  deepseek/deepseek-v4-flash-0731     1.0M     393.2K   yes       no    
openrouter  deepseek/deepseek-v4-pro            1.0M     384K     yes       no    
openrouter  deepseek/deepseek-chat              128K     16K      no        no    
openrouter  anthropic/claude-3-7-sonnet         1M       128K     yes       yes   
openrouter  google/gemini-2.5-flash             1.0M     65.5K    yes       yes   
"""
    variants = ["low", "medium", "high", "max"]
    models = config._parse_pi_models(sample_stdout, variants)
    assert len(models) == 7

    # 1. Latest alias
    latest = next(m for m in models if m["id"] == "~deepseek/deepseek-v4-flash-latest")
    assert latest["name"] == "DeepSeek V4 Flash Latest"
    assert latest["family"] == "deepseek"
    assert latest["reasoning"] is True
    assert latest["variants"] == variants
    assert latest["limit"]["context"] == 1_000_000
    assert latest["limit"]["output"] == 384_000

    # 2. Standard alias
    std = next(m for m in models if m["id"] == "deepseek/deepseek-v4-flash")
    assert std["name"] == "DeepSeek V4 Flash"
    assert std["family"] == "deepseek"
    assert std["reasoning"] is True
    assert std["variants"] == variants

    # 3. Snapshot
    snap = next(m for m in models if m["id"] == "deepseek/deepseek-v4-flash-0731")
    assert snap["name"] == "DeepSeek V4 Flash (0731 Snapshot)"
    assert snap["family"] == "deepseek"
    assert snap["limit"]["output"] == 393_200

    # 4. Non-reasoning model
    chat = next(m for m in models if m["id"] == "deepseek/deepseek-chat")
    assert chat["name"] == "DeepSeek Chat"
    assert chat["reasoning"] is False
    assert chat["variants"] == []


def test_parse_pi_line():
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._pi_tools = {}
    pipeline.EVENT_TEXT_CAP = 4000
    pipeline.EVENT_TOOL_OUTPUT_CAP = 4000

    # Turn start
    evs = pipeline._parse_pi_line(json.dumps({
        "type": "turn_start",
        "turn": 1,
    }))
    assert len(evs) == 1
    assert evs[0]["type"] == "step_start"

    # Delta text
    evs = pipeline._parse_pi_line(json.dumps({
        "type": "delta",
        "delta": "Hello from Pi Harness",
    }))
    assert len(evs) == 1
    assert evs[0]["type"] == "text"
    assert "Pi Harness" in evs[0]["part"]["text"]

    # Step finish with usage & nested dict cost
    evs = pipeline._parse_pi_line(json.dumps({
        "type": "turn_end",
        "message": {
            "role": "assistant",
            "usage": {
                "input": 500,
                "output": 80,
                "cacheRead": 120,
                "cost": {"total": 0.0005},
            },
            "stopReason": "stop",
        },
    }))
    assert len(evs) == 1
    assert evs[0]["type"] == "step_finish"
    assert evs[0]["part"]["tokens"]["input"] == 500
    assert evs[0]["part"]["tokens"]["output"] == 80
    assert evs[0]["part"]["tokens"]["reasoning"] == 120
    assert evs[0]["part"]["cost"] == 0.0005

    # Error payload inside message
    error_line = json.dumps({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "stopReason": "error",
            "errorMessage": "404: No endpoints available matching your guardrail restrictions",
        },
    })
    evs = pipeline._parse_pi_line(error_line)
    assert len(evs) == 1
    assert evs[0]["type"] == "text"
    assert "No endpoints available" in evs[0]["part"]["text"]
    assert "Pi error: 404: No endpoints" in pipeline._last_agent_error

    # Message content blocks (reasoning + tool_use) on message_end
    msg_end = json.dumps({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Planning extraction strategy..."},
                {"type": "tool_use", "id": "call_99", "name": "read_file", "input": {"path": "transcript.txt"}},
            ],
        },
    })
    evs = pipeline._parse_pi_line(msg_end)
    assert len(evs) == 2
    assert evs[0]["type"] == "reasoning"
    assert "Planning extraction" in evs[0]["part"]["text"]
    assert evs[1]["type"] == "tool_use"
    assert evs[1]["part"]["callID"] == "call_99"
    assert evs[1]["part"]["state"]["status"] == "running"

    # Tool results and usage on turn_end
    turn_end = json.dumps({
        "type": "turn_end",
        "message": {
            "role": "assistant",
            "usage": {"input": 200, "output": 50},
            "stopReason": "tool_use",
        },
        "toolResults": [
            {"toolCallId": "call_99", "toolName": "read_file", "result": "Lecture 1 text", "isError": False},
        ],
    })
    evs = pipeline._parse_pi_line(turn_end)
    assert len(evs) == 2
    assert evs[0]["type"] == "tool_use"
    assert evs[0]["part"]["callID"] == "call_99"
    assert evs[0]["part"]["state"]["status"] == "completed"
    assert evs[0]["part"]["state"]["output"] == "Lecture 1 text"
    assert evs[1]["type"] == "step_finish"


def test_pi_sandbox_args():
    args = permissions.pi_sandbox_args()
    assert "--print" in args
    assert "--mode" in args
    assert "json" in args
    assert "--no-session" in args
    assert "--approve" in args


def test_parse_antigravity_events():
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.stats = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "reasoning": 0}}
    pipeline._antigravity_tools = {}
    pipeline.EVENT_TEXT_CAP = 4000
    pipeline.EVENT_TOOL_OUTPUT_CAP = 4000

    # Step start
    start_line = json.dumps({
        "type": "step_start",
        "turn": 1,
        "model": "gemini-3.7-flash",
    })
    evs = pipeline._parse_antigravity_line(start_line)
    assert len(evs) == 1
    assert evs[0]["type"] == "step_start"
    assert evs[0]["part"]["turn"] == 1
    assert evs[0]["part"]["model"] == "gemini-3.7-flash"

    # Reasoning / thinking
    think_line = json.dumps({
        "type": "reasoning",
        "text": "Analyzing lecture transcript structure...",
    })
    evs = pipeline._parse_antigravity_line(think_line)
    assert len(evs) == 1
    assert evs[0]["type"] == "reasoning"
    assert "Analyzing lecture" in evs[0]["part"]["text"]

    # Tool use
    tool_line = json.dumps({
        "type": "tool_use",
        "id": "call_agy_1",
        "name": "view_file",
        "input": {"AbsolutePath": "transcript.txt"},
    })
    evs = pipeline._parse_antigravity_line(tool_line)
    assert len(evs) == 1
    assert evs[0]["type"] == "tool_use"
    assert evs[0]["part"]["tool"] == "view_file"
    assert evs[0]["part"]["callID"] == "call_agy_1"
    assert evs[0]["part"]["state"]["status"] == "running"

    # Tool result
    result_line = json.dumps({
        "type": "tool_result",
        "id": "call_agy_1",
        "name": "view_file",
        "output": "Transcript sample lines",
    })
    evs = pipeline._parse_antigravity_line(result_line)
    assert len(evs) == 1
    assert evs[0]["type"] == "tool_use"
    assert evs[0]["part"]["callID"] == "call_agy_1"
    assert evs[0]["part"]["state"]["status"] == "completed"
    assert evs[0]["part"]["state"]["output"] == "Transcript sample lines"

    # Text
    text_line = json.dumps({
        "type": "text",
        "text": "Completed extraction phase.",
    })
    evs = pipeline._parse_antigravity_line(text_line)
    assert len(evs) == 1
    assert evs[0]["type"] == "text"
    assert "Completed extraction" in evs[0]["part"]["text"]

    # Step finish
    finish_line = json.dumps({
        "type": "step_finish",
        "reason": "stop",
        "usage": {"input": 1500, "output": 400, "reasoning": 120},
        "cost": 0.0005,
    })
    evs = pipeline._parse_antigravity_line(finish_line)
    assert len(evs) == 1
    assert evs[0]["type"] == "step_finish"
    assert evs[0]["part"]["tokens"]["input"] == 1500
    assert evs[0]["part"]["tokens"]["output"] == 400
    assert evs[0]["part"]["tokens"]["reasoning"] == 120

    # Error line
    error_line = json.dumps({
        "type": "error",
        "error": {"message": "Resource quota exceeded"},
    })
    evs = pipeline._parse_antigravity_line(error_line)
    assert len(evs) == 1
    assert evs[0]["type"] == "text"
    assert "Resource quota exceeded" in evs[0]["part"]["text"]
    assert pipeline._last_agent_error == "Antigravity error: Resource quota exceeded"


