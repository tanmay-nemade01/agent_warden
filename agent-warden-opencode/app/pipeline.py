"""Three-agent notes pipeline, driven by an agent CLI (OpenCode or Command Code).

Each phase is one non-interactive CLI invocation (a fresh session, matching the
toolkit's "one agent per new chat window" design). The agent has real tools
(read/write/edit/bash) and performs its phase exactly as its SKILL file
instructs, including running the toolkit scripts itself. After the agent
finishes, this runner independently re-runs the phase's gates and, if they
fail, launches fix sessions (up to MAX_FIX_ROUNDS) with the findings fed back.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import config, gates, permissions
from .paths import confine, resolve_under

AGENTS = {1: "extractor", 2: "enricher", 3: "formatter"}

_COMMON_ADAPT = """\
# Runtime adaptation for this automated run

- Working directory (cwd): {workspace} — relative paths below resolve from here.
- Output directory (create it if missing): {out}
- Toolkit root (READ and RUN scripts only — never write here): {toolkit}
- Lecture number: {lecture_num}
- Subject: {subject} ({abbr})

Whenever the skill says to run `python scripts/<name>.py <args>`, run it as:

    python make-transcript-notes-kit-3agent/scripts/<name>.py <args>

from the working directory above. You may write under the output directory.
Do not write into the toolkit (scripts, utils, templates, SKILL files).
Do not use the question tool — proceed autonomously.

When your phase is complete, print exactly this line as the final line of
your reply:

    PHASE_COMPLETE
"""

ADAPTATION_BY_AGENT = {
    1: _COMMON_ADAPT + """
## Agent 1 scope
- Transcript (read-only): {transcript}
- Write only: dense draft and extraction manifest under the output directory.
- Do not read companion docs. Do not touch topic_mappings/.
""",
    2: _COMMON_ADAPT + """
## Agent 2 scope
- Read: dense draft + extraction manifest under the output directory.
- Companion docs (read-only): {docs_line}
- Write: enriched draft, sections/, and topic mapping YAML **only** via
  `python make-transcript-notes-kit-3agent/scripts/update_topic_mapping.py`.
- Do not rewrite toolkit scripts. Do not edit the original transcript.
""",
    3: _COMMON_ADAPT + """
## Agent 3 scope
- Read: enriched draft, extraction manifest, templates/notes.html, and the
  topic mapping YAML (read-only — do not write YAML).
- Write: HTML notes under the output directory.
- You are a renderer. If placeholders, TODOs, or `*[verify]*` remain, stop
  and leave them in place so the orchestrator can return the work to Agent 2.
  Do not invent missing instructional content.
""",
}

_BOUNCE_RE = re.compile(
    r"\*\[verify\]|\*\[verify:|\bTODO\b|placeholder|"
    r"return (the )?(affected )?section to Agent 2|"
    r"blocking (upstream|Agent 2)",
    re.I,
)

# After OpenCode hits its output-token cap, `opencode run` exits 0 with no
# tool calls. The TUI/desktop just keep the session open; we do the same
# with --session and this nudge so max-effort thinking is not thrown away.
_CONTINUE_AFTER_TRUNCATION = """\
Your previous turn was truncated at the output-token cap (finish reason \
"length") before any tool calls were emitted. Continue the same work from \
where you left off: write the required output files now, then run this \
phase's gates. Do not restart from scratch. Do not re-read the skill \
unless a needed file is missing. When finished, print exactly: PHASE_COMPLETE
"""

_RESUME_AFTER_STALL = """\
Your previous turn stalled (no model output for a long time) and was \
interrupted. Continue the same work from where you left off: write the \
required output files now, then run this phase's gates. Do not restart \
from scratch. Do not re-read the skill unless a needed file is missing. \
When finished, print exactly: PHASE_COMPLETE
"""


def is_truncated_step(reason: str | None, tokens: dict | None = None) -> bool:
    """True when a step ended because the model hit max_tokens (or dropped)."""
    r = (reason or "").strip().lower()
    if r == "length":
        return True
    if r == "unknown":
        toks = tokens or {}
        try:
            return int(toks.get("output") or 0) == 0
        except (TypeError, ValueError):
            return True
    return False



class PhaseError(RuntimeError):
    pass


class BounceToEnricher(PhaseError):
    """Formatter found Agent 2 defects; re-queue enricher then formatter."""


class Pipeline:
    """Sequential 3-agent pipeline with gate verification and fix loops."""

    def __init__(self, subject: str, abbr: str, prefix: str, lecture_num: str,
                 transcript: str, phases: list[int] | None = None,
                 emit=None, docs_dir: str | None = None, run_id: str = "",
                 model: str | None = None, variant: str | None = None,
                 backend: str | None = None):
        self.subject = subject
        self.abbr = abbr
        self.prefix = prefix
        self.lecture_num = lecture_num
        resolved_t = resolve_under(config.TRANSCRIPTS_DIR, transcript)
        if resolved_t is None:
            # Allow an already-absolute path that still sits under workspace
            # transcripts, or a path the UI listed.
            raw = Path(transcript)
            try:
                resolved_t = raw.resolve()
                resolved_t.relative_to(config.TRANSCRIPTS_DIR.resolve())
            except (ValueError, OSError):
                raise PhaseError(
                    f"transcript is not under {config.TRANSCRIPTS_DIR}") from None
        self.transcript = str(resolved_t)
        self.phases = [p for p in (phases or [1, 2, 3]) if p in AGENTS]
        self.emit = emit or (lambda ev: None)
        self.docs_dir = docs_dir
        self.run_id = run_id or f"{abbr}:{prefix}"
        self.backend = config.normalize_backend(backend)
        self.model, self.variant = config.resolve_model_choice(
            model, variant, backend=self.backend)
        out = confine(config.OUTPUTS_DIR, subject, prefix)
        if out is None:
            raise PhaseError("invalid subject or prefix")
        self.out = out
        self.proc: subprocess.Popen | None = None
        self.stop_flag = False
        self.run_log: Path | None = None
        self.stats = {
            "cost": 0.0,
            "tokens": {"input": 0, "output": 0, "reasoning": 0},
            "phases": {},
            "started_at": None,
        }
        self._phase_bucket: dict | None = None
        self._phase_t0: float | None = None
        self._cc_tools: dict[str, dict] = {}
        self._claude_tools: dict[str, dict] = {}
        self._codex_tools: dict[str, dict] = {}
        self._timed_out = False
        self._stalled = False
        self._last_activity = 0.0
        self._resume_session: str | None = None
        self._job_files: dict[str, Path] | None = None
        self._current_phase: str | None = None
        self._last_tool: str = ""
        self._last_step_reason: str = ""
        self._last_step_tokens: dict = {}
        self.stage_retries: dict[int, int] = {1: 0, 2: 0, 3: 0}

    def get_phase_retries(self) -> dict[str, int]:
        return {AGENTS[k]: self.stage_retries.get(k, 0) for k in (1, 2, 3)}


    # ------------------------------------------------------------------ utils
    def _log(self, ev: dict):
        ev.setdefault("time", time.time())
        self.emit(ev)
        if self.run_log:
            try:
                with open(self.run_log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
            except OSError:
                pass

    def _default_docs_dir(self) -> Path | None:
        """Resolve a default companion-docs folder by name scan — no
        hardcoded mapping, so new subject folders just work."""
        return config.default_docs_dir(self.abbr, self.subject)

    def _docs_line(self) -> str:
        if self.docs_dir == "__none__":
            return "(none selected — enrich from the draft and web research if needed)"
        if self.docs_dir and self.docs_dir != "__none__":
            docs_dir = resolve_under(config.WORKSPACE, self.docs_dir)
            if docs_dir and docs_dir.is_dir():
                return str(docs_dir)
            return "(selected folder missing — enrich from the draft)"
        doc_root = self._default_docs_dir()
        return str(doc_root) if doc_root else "(no companion-docs folder for this subject)"

    def _agent_message(self, agent: int, extra: str = "") -> str:
        skill = config.skill_path(agent)
        docs_note = ""
        if agent == 2:
            docs_note = (f"- Enrichment docs directory: {self._docs_line()} "
                         "(read-only; use only files relevant to this lecture)\n")
        adapt = ADAPTATION_BY_AGENT[agent].format(
            workspace=config.WORKSPACE,
            transcript=self.transcript,
            out=self.out,
            toolkit=config.TOOLKIT,
            lecture_num=self.lecture_num,
            subject=self.subject,
            abbr=self.abbr,
            docs_line=self._docs_line(),
        )
        return (f"Read the skill file at {skill} and follow its instructions "
                f"exactly. It defines your role and the complete process.\n\n"
                f"{docs_note}{adapt}\n{extra}")

    # Payloads that can carry large tool outputs / reasoning text.
    EVENT_TEXT_CAP = 6000          # chars kept per event field
    EVENT_TOOL_OUTPUT_CAP = 8000   # chars kept of a tool's output

    def _ensure_job_files(self) -> dict[str, Path]:
        if self._job_files is None:
            self.out.mkdir(parents=True, exist_ok=True)
            self._job_files = permissions.write_job_configs(self.out)
        return self._job_files

    def _timeout_watch(self, proc: subprocess.Popen):
        deadline = time.time() + config.PHASE_TIMEOUT_SECONDS
        while proc.poll() is None:
            now = time.time()
            if now >= deadline:
                self._timed_out = True
                self._log({"type": "phase_timeout",
                           "seconds": config.PHASE_TIMEOUT_SECONDS})
                self._kill()
                return
            if (self._last_activity
                    and now - self._last_activity > config.PHASE_STALL_TIMEOUT_SECONDS):
                self._stalled = True
                self._log({"type": "phase_stall",
                           "seconds": config.PHASE_STALL_TIMEOUT_SECONDS,
                           "phase": self._current_phase,
                           "last_activity": self._last_activity,
                           "last_tool": self._last_tool})
                self._kill()
                return
            time.sleep(1.0)

    def _popen(self, cmd: list[str], *, stdin_data: str | None = None,
               extra_env: dict | None = None,
               cwd: str | None = None) -> None:
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        if stdin_data is not None:
            kwargs["stdin"] = subprocess.PIPE
        if cwd:
            kwargs["cwd"] = cwd
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True
        self._timed_out = False
        self._stalled = False
        self._last_activity = time.time()
        self.proc = subprocess.Popen(cmd, **kwargs)
        if stdin_data is not None and self.proc.stdin is not None:
            try:
                self.proc.stdin.write(stdin_data)
                self.proc.stdin.close()
            except OSError:
                pass
        threading.Thread(target=self._timeout_watch, args=(self.proc,),
                         daemon=True).start()

    def _run_agent(self, agent: int, message: str, title: str,
                   extra_env: dict | None = None) -> tuple[int, list[str]]:
        """Run the selected backend; resume the OpenCode session if truncated."""
        if self.backend == config.BACKEND_COMMANDCODE:
            return self._run_commandcode(agent, message, title, extra_env)
        if self.backend == config.BACKEND_CLAUDE:
            return self._run_claude(agent, message, title, extra_env)
        if self.backend == config.BACKEND_CODEX:
            return self._run_codex(agent, message, title, extra_env)
        self._oc_session_id = None
        resume = self._resume_session
        self._resume_session = None
        if resume:
            self._log({
                "type": "session_resume",
                "phase": AGENTS[agent],
                "session": resume,
                "reason": "stall retry",
            })
        code, lines = self._run_opencode(
            agent, _RESUME_AFTER_STALL if resume else message,
            title, extra_env, session_id=resume)
        n = 0
        while (not self.stop_flag
               and is_truncated_step(self._last_step_reason,
                                     self._last_step_tokens)
               and self._oc_session_id
               and n < config.MAX_TRUNCATION_CONTINUES):
            n += 1
            self._log({
                "type": "session_continue",
                "phase": AGENTS[agent],
                "continue": n,
                "max_continues": config.MAX_TRUNCATION_CONTINUES,
                "reason": self._last_step_reason,
                "session": self._oc_session_id,
                "tokens": dict(self._last_step_tokens or {}),
            })
            more_code, more = self._run_opencode(
                agent, _CONTINUE_AFTER_TRUNCATION, title, extra_env,
                session_id=self._oc_session_id)
            code = more_code
            lines.extend(more)
        return code, lines

    def _note_opencode_meta(self, line: str) -> None:
        """Capture session id and step_finish reason from raw JSONL."""
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(raw, dict):
            return
        sid = raw.get("sessionID")
        if not sid:
            part = raw.get("part")
            if isinstance(part, dict):
                sid = part.get("sessionID")
        if isinstance(sid, str) and sid.startswith("ses_"):
            self._oc_session_id = sid
        if raw.get("type") == "step_finish":
            part = raw.get("part") if isinstance(raw.get("part"), dict) else {}
            self._last_step_reason = str(part.get("reason") or "")
            toks = part.get("tokens")
            self._last_step_tokens = toks if isinstance(toks, dict) else {}

    def _stream_process(self, agent: int, parse_line) -> tuple[int, list[str]]:
        lines: list[str] = []
        try:
            assert self.proc is not None and self.proc.stdout is not None
            for raw in self.proc.stdout:
                self._last_activity = time.time()
                line = raw.rstrip("\r\n")
                lines.append(line)
                self._note_opencode_meta(line)
                events = parse_line(line)
                if events:
                    for event in events:
                        if event.get("type") == "tool_use":
                            part = event.get("part") or {}
                            self._last_tool = str(
                                part.get("tool") or part.get("title") or "")
                        self._accumulate_stats(AGENTS[agent], event)
                        self._log({"type": "agent_event",
                                   "phase": AGENTS[agent],
                                   "event": event})
                elif line.strip():
                    self._log({"type": "log", "phase": AGENTS[agent],
                               "line": line})
                if self.stop_flag:
                    self._kill()
                    raise PhaseError("stopped by user")
                if self._timed_out:
                    raise PhaseError(
                        f"{AGENTS[agent]} exceeded "
                        f"{config.PHASE_TIMEOUT_SECONDS}s timeout")
        except KeyboardInterrupt:
            self._kill()
            raise PhaseError("interrupted")
        code = self.proc.wait() if self.proc else 1
        if self._timed_out:
            raise PhaseError(
                f"{AGENTS[agent]} exceeded "
                f"{config.PHASE_TIMEOUT_SECONDS}s timeout")
        if self._stalled:
            raise PhaseError(
                f"{AGENTS[agent]} produced no output for "
                f"{config.PHASE_STALL_TIMEOUT_SECONDS}s — "
                f"provider request hung")
        return code, lines

    def _run_opencode(self, agent: int, message: str, title: str,
                      extra_env: dict | None = None,
                      session_id: str | None = None) -> tuple[int, list[str]]:
        job = self._ensure_job_files()
        job["prompt"].write_text(message, encoding="utf-8")
        exe = config.find_opencode()
        cmd = [exe, "run", "--auto", "-m", self.model]
        if self.variant:
            cmd.extend(["--variant", self.variant])
        cmd.extend([
            "--format", "json", "--thinking",
            "--dir", str(config.WORKSPACE),
        ])
        if session_id:
            # Pin the id — never `--continue` (that is "last session" and
            # races under parallel runs).
            cmd.extend(["--session", session_id])
        else:
            cmd.extend(["--title", title, "--file", str(job["prompt"])])
        env = permissions.opencode_env(job["opencode"])
        if extra_env:
            env.update(extra_env)
        self._last_step_reason = ""
        self._last_step_tokens = {}
        self._log({"type": "phase_cmd", "phase": AGENTS[agent],
                   "cmd": " ".join(cmd[:10]) + " ...",
                   "session": session_id})
        self._popen(cmd, stdin_data=message, extra_env=env,
                    cwd=str(config.WORKSPACE))

        def parse(line: str) -> list[dict]:
            if not line.lstrip().startswith("{"):
                return []
            event = self._parse_agent_event(line)
            return [event] if event is not None else []

        return self._stream_process(agent, parse)

    def _run_commandcode(self, agent: int, message: str, title: str,
                         extra_env: dict | None = None) -> tuple[int, list[str]]:
        """Headless Command Code: `cmdc -p` with JSON events and --yolo.

        Prompt goes on stdin to avoid Windows argv length limits. Deny rules
        in the per-job settings still apply under --yolo.
        """
        job = self._ensure_job_files()
        job["prompt"].write_text(message, encoding="utf-8")
        argv = config.find_commandcode_argv()
        cmd = argv + [
            "-p",
            "--output-format", "json",
            "-m", self.model,
            "--yolo",
            "--trust",
            "--skip-onboarding",
            "--no-auto-update",
            "--no-skills",
            "--max-turns", str(config.COMMANDCODE_MAX_TURNS),
            "-n", title,
        ]
        if self.variant:
            cmd.extend(["--effort", self.variant])
        env = {"NO_COLOR": "1", "FORCE_COLOR": "0"}
        if extra_env:
            env.update(extra_env)
        self._cc_tools = {}
        self._log({"type": "phase_cmd", "phase": AGENTS[agent],
                   "cmd": " ".join(cmd[:10]) + " ..."})
        self._popen(cmd, stdin_data=message, extra_env=env,
                    cwd=str(config.WORKSPACE))
        return self._stream_process(agent, self._parse_commandcode_line)

    def _run_claude(self, agent: int, message: str, title: str,
                    extra_env: dict | None = None) -> tuple[int, list[str]]:
        """Headless Claude Code: `claude -p` with stream-json events."""
        job = self._ensure_job_files()
        job["prompt"].write_text(message, encoding="utf-8")
        argv = config.find_claude_argv()
        allowed_tools = permissions.claude_allowed_tools()
        cmd = argv + [
            "-p",
            "--output-format", "stream-json",
            "--model", self.model,
            "--dangerously-skip-permissions",
            "--max-turns", str(config.CLAUDE_MAX_TURNS),
            "--allowedTools", allowed_tools,
        ]
        env = {"NO_COLOR": "1", "FORCE_COLOR": "0"}
        if extra_env:
            env.update(extra_env)
        self._claude_tools = {}
        self._log({"type": "phase_cmd", "phase": AGENTS[agent],
                   "cmd": " ".join(cmd[:10]) + " ..."})
        self._popen(cmd, stdin_data=message, extra_env=env,
                    cwd=str(config.WORKSPACE))
        return self._stream_process(agent, self._parse_claude_line)

    def _run_codex(self, agent: int, message: str, title: str,
                   extra_env: dict | None = None) -> tuple[int, list[str]]:
        """Headless OpenAI Codex: `codex exec --json`."""
        job = self._ensure_job_files()
        job["prompt"].write_text(message, encoding="utf-8")
        argv = config.find_codex_argv()
        cmd = argv + [
            "exec",
            "--json",
            "-m", self.model,
            "--dangerously-bypass-approvals-and-sandbox",
            "-C", str(config.WORKSPACE),
        ]
        env = {"NO_COLOR": "1", "FORCE_COLOR": "0"}
        if extra_env:
            env.update(extra_env)
        self._codex_tools = {}
        self._log({"type": "phase_cmd", "phase": AGENTS[agent],
                   "cmd": " ".join(cmd[:10]) + " ..."})
        self._popen(cmd, stdin_data=message, extra_env=env,
                    cwd=str(config.WORKSPACE))
        return self._stream_process(agent, self._parse_codex_line)

    @staticmethod
    def _parse_agent_event(line: str) -> dict | None:
        """Parse one `opencode run --format json` line into a UI-safe event.

        Keeps the event shape (`type`, `part`) but truncates the payload
        fields that can be huge (tool outputs, reasoning, file reads).
        """
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict) or not raw.get("type"):
            return None
        ev_type = raw.get("type")
        if ev_type not in {"step_start", "step_finish", "reasoning",
                           "tool_use", "text"}:
            return None
        part = raw.get("part") or {}
        part = {k: v for k, v in part.items()
                if k not in {"id", "messageID", "sessionID"}}
        if ev_type == "reasoning":
            text = part.get("text")
            if isinstance(text, str) and len(text) > Pipeline.EVENT_TEXT_CAP:
                part["text"] = text[:Pipeline.EVENT_TEXT_CAP]
                part["trimmed"] = True
        elif ev_type == "text":
            text = part.get("text")
            if isinstance(text, str) and len(text) > Pipeline.EVENT_TEXT_CAP:
                part["text"] = text[:Pipeline.EVENT_TEXT_CAP]
                part["trimmed"] = True
        elif ev_type == "tool_use":
            state = part.get("state") or {}
            state = {k: v for k, v in state.items()
                     if k not in {"metadata"}}
            out = state.get("output")
            if isinstance(out, str) and len(out) > Pipeline.EVENT_TOOL_OUTPUT_CAP:
                state["output"] = out[:Pipeline.EVENT_TOOL_OUTPUT_CAP]
                state["output_trimmed"] = True
            part["state"] = state
        return {"type": ev_type, "part": part,
                "ts": raw.get("timestamp")}

    @staticmethod
    def _usage_tokens(usage) -> dict:
        if not isinstance(usage, dict):
            return {"input": 0, "output": 0, "reasoning": 0}

        def g(*keys):
            for k in keys:
                if usage.get(k) is not None:
                    try:
                        return int(usage[k] or 0)
                    except (TypeError, ValueError):
                        return 0
            return 0

        return {
            "input": g("input", "inputTokens", "input_tokens", "promptTokens",
                       "prompt_tokens"),
            "output": g("output", "outputTokens", "output_tokens",
                        "completionTokens", "completion_tokens"),
            "reasoning": g("reasoning", "reasoningTokens", "reasoning_tokens",
                           "cacheReadTokens"),
        }

    @staticmethod
    def _usage_cost(usage) -> float:
        if not isinstance(usage, dict):
            return 0.0
        for k in ("cost", "total_cost", "totalCost"):
            if usage.get(k) is not None:
                try:
                    return float(usage[k] or 0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _parse_commandcode_line(self, line: str) -> list[dict]:
        """Map one Command Code NDJSON line to UI agent_event dicts."""
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, dict):
            return []
        if raw.get("type") == "event" and isinstance(raw.get("event"), dict):
            ev = raw["event"]
        elif raw.get("type") == "result":
            return self._commandcode_result(raw)
        else:
            ev = raw
        et = ev.get("type")
        if not et:
            return []
        if et == "turn_start":
            return [{"type": "step_start", "part": {
                "turn": ev.get("turnNumber")}}]
        if et in {"turn_end"}:
            usage = ev.get("usage") or {}
            return [{"type": "step_finish", "part": {
                "reason": ev.get("stopReason") or et,
                "tokens": self._usage_tokens(usage),
                "cost": self._usage_cost(usage),
            }}]
        if et == "thinking_end":
            text = ev.get("text") or ev.get("thinking") or ""
            if not isinstance(text, str) or not text:
                return []
            trimmed = False
            if len(text) > self.EVENT_TEXT_CAP:
                text = text[:self.EVENT_TEXT_CAP]
                trimmed = True
            return [{"type": "reasoning", "part": {
                "text": text, "trimmed": trimmed}}]
        if et == "message_end":
            text = ev.get("text") or ev.get("content") or ev.get("finalText") or ""
            if isinstance(text, list):
                text = "".join(
                    (p.get("text") or "") if isinstance(p, dict) else str(p)
                    for p in text)
            if not isinstance(text, str) or not text.strip():
                return []
            trimmed = False
            if len(text) > self.EVENT_TEXT_CAP:
                text = text[:self.EVENT_TEXT_CAP]
                trimmed = True
            return [{"type": "text", "part": {
                "text": text, "trimmed": trimmed}}]
        if et == "tool_running":
            call_id = str(ev.get("toolCallId") or ev.get("id") or "")
            rec = {
                "tool": ev.get("toolName") or ev.get("tool") or "tool",
                "title": ev.get("description") or "",
                "input": ev.get("input"),
            }
            if call_id:
                self._cc_tools[call_id] = rec
            part = {
                "tool": rec["tool"],
                "title": rec["title"],
                "callID": call_id,
                "state": {"status": "running"},
            }
            if rec["input"] is not None:
                part["state"]["input"] = rec["input"]
            return [{"type": "tool_use", "part": part}]
        if et in {"tool_completed", "tool_errored"}:
            call_id = str(ev.get("toolCallId") or ev.get("id") or "")
            rec = self._cc_tools.get(call_id) or {
                "tool": ev.get("toolName") or ev.get("tool") or "tool",
                "title": ev.get("description") or "",
            }
            out = ev.get("result") if et == "tool_completed" else ev.get("error")
            if out is None:
                out = ev.get("output") or ""
            if not isinstance(out, str):
                try:
                    out = json.dumps(out, ensure_ascii=False)
                except (TypeError, ValueError):
                    out = str(out)
            trimmed = False
            if len(out) > self.EVENT_TOOL_OUTPUT_CAP:
                out = out[:self.EVENT_TOOL_OUTPUT_CAP]
                trimmed = True
            status = "completed" if et == "tool_completed" else "error"
            part = {
                "tool": rec.get("tool") or "tool",
                "title": rec.get("title") or "",
                "callID": call_id,
                "state": {"status": status, "output": out},
            }
            if trimmed:
                part["state"]["output_trimmed"] = True
            if rec.get("input") is not None:
                part["state"]["input"] = rec["input"]
            return [{"type": "tool_use", "part": part}]
        return []

    def _commandcode_result(self, raw: dict) -> list[dict]:
        text = raw.get("finalText") or ""
        if raw.get("subtype") == "error":
            err = raw.get("error") or "command-code error"
            text = text or str(err)
        if not isinstance(text, str) or not text.strip():
            usage = raw.get("usage") or {}
            if usage:
                return [{"type": "step_finish", "part": {
                    "reason": raw.get("stopReason") or raw.get("subtype")
                    or "result",
                    "tokens": self._usage_tokens(usage),
                    "cost": self._usage_cost(usage),
                }}]
            return []
        clipped = text
        trimmed = False
        if len(clipped) > self.EVENT_TEXT_CAP:
            clipped = clipped[:self.EVENT_TEXT_CAP]
            trimmed = True
        out = [{"type": "text", "part": {
            "text": clipped, "trimmed": trimmed}}]
        usage = raw.get("usage") or {}
        if usage and not self.stats["tokens"]["input"] and not self.stats["tokens"]["output"]:
            out.append({"type": "step_finish", "part": {
                "reason": raw.get("stopReason") or raw.get("subtype") or "result",
                "tokens": self._usage_tokens(usage),
                "cost": self._usage_cost(usage),
            }})
        return out

    def _parse_claude_line(self, line: str) -> list[dict]:
        """Map one Claude Code NDJSON line to UI agent_event dicts."""
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, dict):
            return []
        et = raw.get("type")
        if not et:
            return []
        if et == "message_start":
            msg = raw.get("message") or {}
            usage = msg.get("usage") or {}
            return [{"type": "step_start", "part": {
                "model": msg.get("model"),
                "tokens": self._usage_tokens(usage),
            }}]
        if et in {"message_delta", "turn_end"}:
            delta = raw.get("delta") or {}
            usage = raw.get("usage") or delta.get("usage") or {}
            return [{"type": "step_finish", "part": {
                "reason": delta.get("stop_reason") or "end_turn",
                "tokens": self._usage_tokens(usage),
                "cost": self._usage_cost(raw),
            }}]
        if et in {"result", "cost"}:
            usage = raw.get("usage") or {}
            cost = float(raw.get("total_cost_usd") or raw.get("cost") or 0.0)
            return [{"type": "step_finish", "part": {
                "reason": "result",
                "tokens": self._usage_tokens(usage),
                "cost": cost,
            }}]
        if et == "content_block_start":
            cb = raw.get("content_block") or {}
            cbt = cb.get("type")
            if cbt in {"thinking", "reasoning"}:
                text = cb.get("thinking") or cb.get("text") or ""
                if text:
                    trimmed = len(text) > self.EVENT_TEXT_CAP
                    return [{"type": "reasoning", "part": {
                        "text": text[:self.EVENT_TEXT_CAP] if trimmed else text,
                        "trimmed": trimmed,
                    }}]
            elif cbt == "tool_use":
                call_id = str(cb.get("id") or "")
                rec = {
                    "tool": cb.get("name") or "tool",
                    "title": cb.get("name") or "",
                    "input": cb.get("input"),
                }
                if call_id:
                    self._claude_tools[call_id] = rec
                part = {
                    "tool": rec["tool"],
                    "title": rec["title"],
                    "callID": call_id,
                    "state": {"status": "running"},
                }
                if rec["input"] is not None:
                    part["state"]["input"] = rec["input"]
                return [{"type": "tool_use", "part": part}]
            elif cbt == "text":
                text = cb.get("text") or ""
                if text.strip():
                    trimmed = len(text) > self.EVENT_TEXT_CAP
                    return [{"type": "text", "part": {
                        "text": text[:self.EVENT_TEXT_CAP] if trimmed else text,
                        "trimmed": trimmed,
                    }}]
        if et == "content_block_delta":
            delta = raw.get("delta") or {}
            dt = delta.get("type")
            if dt in {"thinking_delta"}:
                text = delta.get("thinking") or ""
                if text:
                    trimmed = len(text) > self.EVENT_TEXT_CAP
                    return [{"type": "reasoning", "part": {
                        "text": text[:self.EVENT_TEXT_CAP] if trimmed else text,
                        "trimmed": trimmed,
                    }}]
            elif dt in {"text_delta"}:
                text = delta.get("text") or ""
                if text.strip():
                    trimmed = len(text) > self.EVENT_TEXT_CAP
                    return [{"type": "text", "part": {
                        "text": text[:self.EVENT_TEXT_CAP] if trimmed else text,
                        "trimmed": trimmed,
                    }}]
        # Direct tool event forms
        if et in {"tool_use", "tool_running"}:
            call_id = str(raw.get("id") or raw.get("toolCallId") or "")
            part = {
                "tool": raw.get("name") or raw.get("tool") or "tool",
                "title": raw.get("name") or raw.get("title") or "",
                "callID": call_id,
                "state": {"status": "running"},
            }
            if raw.get("input") is not None:
                part["state"]["input"] = raw.get("input")
            return [{"type": "tool_use", "part": part}]
        if et in {"tool_result", "tool_completed", "tool_errored"}:
            call_id = str(raw.get("tool_use_id") or raw.get("id") or "")
            out = raw.get("content") or raw.get("output") or raw.get("result") or ""
            if not isinstance(out, str):
                try:
                    out = json.dumps(out, ensure_ascii=False)
                except (TypeError, ValueError):
                    out = str(out)
            trimmed = len(out) > self.EVENT_TOOL_OUTPUT_CAP
            part = {
                "tool": raw.get("tool") or raw.get("name") or "tool",
                "title": raw.get("name") or raw.get("title") or "",
                "callID": call_id,
                "state": {
                    "status": "completed" if et != "tool_errored" else "error",
                    "output": out[:self.EVENT_TOOL_OUTPUT_CAP] if trimmed else out,
                },
            }
            if trimmed:
                part["state"]["output_trimmed"] = True
            return [{"type": "tool_use", "part": part}]
        if et == "text":
            text = raw.get("text") or ""
            if text.strip():
                trimmed = len(text) > self.EVENT_TEXT_CAP
                return [{"type": "text", "part": {
                    "text": text[:self.EVENT_TEXT_CAP] if trimmed else text,
                    "trimmed": trimmed,
                }}]
        return []

    def _parse_codex_line(self, line: str) -> list[dict]:
        """Map one OpenAI Codex JSONL line to UI agent_event dicts."""
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, dict):
            return []
        et = raw.get("type")
        if not et:
            return []
        if et in {"step_start", "turn_start"}:
            turn = raw.get("turn") or (raw.get("part") or {}).get("turn")
            return [{"type": "step_start", "part": {"turn": turn}}]
        if et in {"step_finish", "turn_end"}:
            usage = raw.get("usage") or (raw.get("part") or {}).get("tokens") or {}
            reason = (raw.get("stop_reason") or raw.get("reason")
                      or (raw.get("part") or {}).get("reason") or "stop")
            return [{"type": "step_finish", "part": {
                "reason": reason,
                "tokens": self._usage_tokens(usage),
                "cost": self._usage_cost(raw),
            }}]
        if et in {"tool_use", "tool_call"}:
            call_id = str(raw.get("call_id") or raw.get("id") or "")
            rec = {
                "tool": raw.get("tool") or raw.get("name") or "tool",
                "title": raw.get("title") or raw.get("tool") or "",
                "input": raw.get("input") or raw.get("args"),
            }
            if call_id:
                self._codex_tools[call_id] = rec
            part = {
                "tool": rec["tool"],
                "title": rec["title"],
                "callID": call_id,
                "state": {"status": "running"},
            }
            if rec["input"] is not None:
                part["state"]["input"] = rec["input"]
            return [{"type": "tool_use", "part": part}]
        if et in {"tool_completed", "tool_result", "tool_error"}:
            call_id = str(raw.get("call_id") or raw.get("id") or "")
            rec = self._codex_tools.get(call_id) or {
                "tool": raw.get("tool") or "tool",
                "title": raw.get("title") or "",
            }
            out = raw.get("output") or raw.get("result") or raw.get("error") or ""
            if not isinstance(out, str):
                try:
                    out = json.dumps(out, ensure_ascii=False)
                except (TypeError, ValueError):
                    out = str(out)
            trimmed = len(out) > self.EVENT_TOOL_OUTPUT_CAP
            part = {
                "tool": rec.get("tool") or "tool",
                "title": rec.get("title") or "",
                "callID": call_id,
                "state": {
                    "status": "completed" if et != "tool_error" else "error",
                    "output": out[:self.EVENT_TOOL_OUTPUT_CAP] if trimmed else out,
                },
            }
            if trimmed:
                part["state"]["output_trimmed"] = True
            return [{"type": "tool_use", "part": part}]
        if et in {"text", "message"}:
            text = raw.get("text") or raw.get("content") or ""
            if text.strip():
                trimmed = len(text) > self.EVENT_TEXT_CAP
                return [{"type": "text", "part": {
                    "text": text[:self.EVENT_TEXT_CAP] if trimmed else text,
                    "trimmed": trimmed,
                }}]
        return []

    def _accumulate_stats(self, phase: str, event: dict):
        if event.get("type") != "step_finish":
            return
        part = event.get("part") or {}
        toks = part.get("tokens") or {}
        cost = float(part.get("cost") or 0)
        self.stats["cost"] += cost
        for k in ("input", "output", "reasoning"):
            self.stats["tokens"][k] += int(toks.get(k) or 0)
        if self._phase_bucket is not None:
            self._phase_bucket["cost"] += cost
            for k in ("input", "output", "reasoning"):
                self._phase_bucket["tokens"][k] += int(toks.get(k) or 0)

    def _begin_phase_stats(self, phase: str):
        self._phase_t0 = time.time()
        self._phase_bucket = {
            "cost": 0.0,
            "tokens": {"input": 0, "output": 0, "reasoning": 0},
        }
        self.stats["phases"][phase] = self._phase_bucket

    def _end_phase_stats(self, phase: str) -> dict:
        secs = round(time.time() - (self._phase_t0 or time.time()), 1)
        bucket = self._phase_bucket or {
            "cost": 0.0, "tokens": {"input": 0, "output": 0, "reasoning": 0}}
        agent_num = {v: k for k, v in AGENTS.items()}.get(phase)
        out = {
            "seconds": secs,
            "cost": round(float(bucket["cost"]), 6),
            "tokens": dict(bucket["tokens"]),
            "retries": self.stage_retries.get(agent_num, 0) if agent_num else 0,
        }
        self.stats["phases"][phase] = out
        self._phase_bucket = None
        self._phase_t0 = None
        return out

    def _stats_snapshot(self) -> dict:
        started = self.stats.get("started_at")
        total_secs = 0.0
        if started:
            total_secs = round(time.time() - float(started), 1)
        else:
            total_secs = round(sum(
                float((p or {}).get("seconds") or 0)
                for p in self.stats["phases"].values()), 1)
        phases_out = {}
        for p_name, p_val in self.stats["phases"].items():
            p_dict = dict(p_val or {})
            agent_num = {v: k for k, v in AGENTS.items()}.get(p_name)
            if "retries" not in p_dict and agent_num:
                p_dict["retries"] = self.stage_retries.get(agent_num, 0)
            phases_out[p_name] = p_dict
        return {
            "seconds": total_secs,
            "cost": round(float(self.stats["cost"]), 6),
            "tokens": dict(self.stats["tokens"]),
            "phases": phases_out,
            "phase_retries": self.get_phase_retries(),
        }

    def _kill(self):
        proc = self.proc
        if proc is None or proc.poll() is not None:
            self.proc = None
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.proc = None

    def stop(self):
        self.stop_flag = True
        self._kill()

    def _mtime_map(self, paths: list[Path]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for p in paths:
            try:
                out[str(p)] = p.stat().st_mtime if p.is_file() else None
            except OSError:
                out[str(p)] = None
        return out

    def _fail_if_stale(self, agent: int, code: int, before: dict[str, float | None],
                       paths: list[Path]) -> None:
        missing = [p.name for p in paths if not p.is_file()]
        if missing:
            raise PhaseError(
                f"{AGENTS[agent]} exited {code} without producing "
                + ", ".join(missing))
        after = self._mtime_map(paths)
        unchanged = all(before.get(str(p)) == after.get(str(p)) for p in paths)
        if unchanged and code != 0:
            raise PhaseError(
                f"{AGENTS[agent]} exited {code} without updating artifacts "
                f"(refusing to gate stale files)")

    # ---------------------------------------------------------------- phases
    def _phase_extractor(self) -> bool:
        dense = self.out / f"{self.prefix}_notes_dense.md"
        manifest = self.out / f"{self.prefix}_extraction_manifest.json"
        self.out.mkdir(parents=True, exist_ok=True)
        targets = [dense, manifest]
        before = self._mtime_map(targets)
        msg = self._agent_message(1, (
            f"Process the transcript into {dense} and {manifest} as the skill "
            "specifies, then run the dense-draft lint gate and the manifest "
            "verifier yourself until both pass."))
        code, _ = self._run_agent(1, msg, f"extractor {self.prefix}")
        if self.stop_flag:
            return False
        self._fail_if_stale(1, code, before, targets)
        return self._verify(1, [
            ("lint_dense", gates.gate_lint_dense(
                str(dense), self.lecture_num, "dense")),
            ("verify_manifest", gates.gate_verify_manifest(
                str(manifest), str(dense), "dense")),
        ])

    def _phase_enricher(self) -> bool:
        dense = self.out / f"{self.prefix}_notes_dense.md"
        manifest = self.out / f"{self.prefix}_extraction_manifest.json"
        enriched = self.out / f"{self.prefix}_notes_enriched.md"
        if not dense.is_file():
            raise PhaseError(f"missing {dense.name} — run the extractor first")
        before = self._mtime_map([enriched])
        msg = self._agent_message(2, (
            f"Enrich {dense} (manifest: {manifest}) into {enriched} exactly as "
            "the skill specifies — split, enrich each section, assemble, bind "
            "summaries, update the topic mapping YAML, then run the enriched "
            "lint and manifest verifier until both pass."))
        code, _ = self._run_agent(2, msg, f"enricher {self.prefix}")
        if self.stop_flag:
            return False
        self._fail_if_stale(2, code, before, [enriched])
        return self._verify(2, [
            ("lint_dense", gates.gate_lint_dense(
                str(enriched), self.lecture_num, "enriched")),
            ("verify_manifest", gates.gate_verify_manifest(
                str(manifest), str(enriched), "enriched")),
        ])

    def _phase_formatter(self) -> bool:
        enriched = self.out / f"{self.prefix}_notes_enriched.md"
        manifest = self.out / f"{self.prefix}_extraction_manifest.json"
        html = self.out / f"{self.prefix}_notes" / f"{self.prefix}_notes.html"
        if not enriched.is_file():
            raise PhaseError(f"missing {enriched.name} — run the enricher first")
        before = self._mtime_map([html])
        msg = self._agent_message(3, (
            f"Format {enriched} into {html} exactly as the skill specifies — "
            "split, convert sections, assemble the body, fill the "
            f"templates/notes.html placeholders including metadata, exam "
            "revision and prerequisite knowledge from the topic mapping YAML, "
            "then run lint.py and the manifest verifier (phase html) until "
            "both pass."))
        code, _ = self._run_agent(3, msg, f"formatter {self.prefix}")
        if self.stop_flag:
            return False
        self._fail_if_stale(3, code, before, [html])
        return self._verify(3, [
            ("lint_html", gates.gate_lint_html(str(html))),
            ("verify_manifest", gates.gate_verify_manifest(
                str(manifest), str(html), "html")),
        ])

    # -------------------------------------------------------------- gate loop
    def _should_bounce_to_enricher(
            self, gate_results: list[tuple[str, gates.GateResult]]) -> bool:
        for _name, res in gate_results:
            blob = "\n".join(res.findings) + "\n" + (res.output or "")
            if _BOUNCE_RE.search(blob):
                return True
        enriched = self.out / f"{self.prefix}_notes_enriched.md"
        if enriched.is_file():
            try:
                text = enriched.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if "*[verify]" in text:
                return True
        return False

    def _verify(self, agent: int, gate_results: list[tuple[str, gates.GateResult]]) -> bool:
        """Run gates, then fix sessions until they pass or rounds run out."""
        rounds = 0
        while True:
            ok = True
            for name, res in gate_results:
                self._log({"type": "gate", "phase": AGENTS[agent],
                           "gate": name, "result": gates.dump(res)})
                if not res.passed:
                    ok = False
            if ok:
                return True
            if agent == 3 and self._should_bounce_to_enricher(gate_results):
                raise BounceToEnricher(
                    "formatter gates found Agent 2 defects "
                    "(*[verify]*/placeholder) — returning to enricher")
            rounds += 1
            if rounds >= config.MAX_FIX_ROUNDS or self.stop_flag:
                raise PhaseError(
                    f"{AGENTS[agent]} gates still failing after {rounds} round(s)")
            findings = []
            for name, res in gate_results:
                items = "\n".join(res.findings)
                findings.append(f"--- {name} ---\n{items[:2000]}")
            extra = (
                "The verification gates on your output files reported FAILs. "
                "Fix the underlying content (read the files, correct the real "
                "issues, never silence a warning by deleting content), then "
                "re-run the gates yourself exactly as the skill specifies until "
                f"they PASS. Files to fix:\n{findings}\n\n"
                f"When finished, print exactly: PHASE_COMPLETE")
            fix_msg = self._agent_message(agent, extra)
            code, _ = self._run_agent(
                agent, fix_msg, f"fix {AGENTS[agent]} {self.prefix}")
            if self.stop_flag:
                return False
            if code != 0:
                raise PhaseError(
                    f"fix session for {AGENTS[agent]} exited {code}")
            # Recompute gate results against the (possibly repaired) files.
            gate_results = self._fresh_gates(agent, gate_results)

    def _fresh_gates(self, agent: int,
                     prev: list[tuple[str, gates.GateResult]]
                     ) -> list[tuple[str, gates.GateResult]]:
        """Re-run the phase's gates after a fix session (targets unchanged)."""
        dense = self.out / f"{self.prefix}_notes_dense.md"
        manifest = self.out / f"{self.prefix}_extraction_manifest.json"
        enriched = self.out / f"{self.prefix}_notes_enriched.md"
        html = self.out / f"{self.prefix}_notes" / f"{self.prefix}_notes.html"
        if agent == 1:
            return [
                ("lint_dense", gates.gate_lint_dense(
                    str(dense), self.lecture_num, "dense")),
                ("verify_manifest", gates.gate_verify_manifest(
                    str(manifest), str(dense), "dense")),
            ]
        if agent == 2:
            return [
                ("lint_dense", gates.gate_lint_dense(
                    str(enriched), self.lecture_num, "enriched")),
                ("verify_manifest", gates.gate_verify_manifest(
                    str(manifest), str(enriched), "enriched")),
            ]
        return [
            ("lint_html", gates.gate_lint_html(str(html))),
            ("verify_manifest", gates.gate_verify_manifest(
                str(manifest), str(html), "html")),
        ]

    # ------------------------------------------------------------------ run
    def run(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.run_log = self.out / f"{self.prefix}_run_events.jsonl"
        self.stats["started_at"] = time.time()
        max_retries = config.MAX_STAGE_RETRIES
        hb_stop = threading.Event()

        def heartbeat():
            while not hb_stop.wait(15.0):
                if self.stop_flag:
                    break
                self.emit({
                    "type": "heartbeat",
                    "phase": self._current_phase,
                    "last_tool": self._last_tool,
                    "alive": True,
                    "time": time.time(),
                    "stats": self._stats_snapshot(),
                })

        hb_thread = threading.Thread(target=heartbeat, daemon=True)
        hb_thread.start()
        try:
            self._run_phases(max_retries)
        finally:
            hb_stop.set()

    def _run_phases(self, max_retries: int):
        self._log({"type": "pipeline_start", "subject": self.subject,
                   "abbr": self.abbr, "prefix": self.prefix,
                   "lecture_num": self.lecture_num,
                   "transcript": self.transcript, "phases": self.phases,
                   "backend": self.backend,
                   "model": self.model, "variant": self.variant,
                   "stage_retries": max_retries,
                   "phase_retries": self.get_phase_retries()})

        self.stage_retries = {num: 0 for num in (1, 2, 3)}
        runners = {
            1: self._phase_extractor,
            2: self._phase_enricher,
            3: self._phase_formatter,
        }
        i = 0
        while i < len(self.phases):
            num = self.phases[i]
            name = AGENTS[num]
            try:
                self._current_phase = name
                self._log({"type": "phase_start", "phase": name,
                           "attempt": self.stage_retries[num] + 1,
                           "max_retries": max_retries,
                           "phase_retries": self.get_phase_retries()})
                self._begin_phase_stats(name)
                done = runners[num]()
                phase_stats = self._end_phase_stats(name)
                self._log({"type": "phase_end", "phase": name,
                           "ok": done, "seconds": phase_stats["seconds"],
                           "stats": phase_stats,
                           "phase_retries": self.get_phase_retries()})
                if self.stop_flag:
                    self._log({"type": "pipeline_end", "status": "stopped",
                               "stats": self._stats_snapshot()})
                    return
                i += 1
            except BounceToEnricher as exc:
                if self._phase_bucket is not None:
                    self._end_phase_stats(name)
                if self.stop_flag:
                    self._log({"type": "pipeline_end", "status": "stopped",
                               "stats": self._stats_snapshot()})
                    return
                self.stage_retries[2] += 1
                err = str(exc)
                self._log({
                    "type": "bounce_to_enricher",
                    "from_phase": name,
                    "retry": self.stage_retries[2],
                    "max_retries": max_retries,
                    "error": err,
                    "phase_retries": self.get_phase_retries(),
                })
                if self.stage_retries[2] > max_retries:
                    self._log({
                        "type": "retry_exhausted",
                        "retries": self.stage_retries[2] - 1,
                        "max_retries": max_retries,
                        "failed_phase": "enricher",
                        "error": err,
                        "phase_retries": self.get_phase_retries(),
                    })
                    self._log({"type": "pipeline_end", "status": "error",
                               "error": err, "retries_exhausted": True,
                               "failed_phase": "enricher",
                               "stats": self._stats_snapshot()})
                    return
                self.phases = self.phases[:i] + [2, 3]
                continue
            except (PhaseError, Exception) as exc:  # noqa: BLE001
                if self._phase_bucket is not None:
                    self._end_phase_stats(name)
                if self.stop_flag:
                    self._log({"type": "pipeline_end", "status": "stopped",
                               "stats": self._stats_snapshot()})
                    return
                err = (str(exc) if isinstance(exc, PhaseError)
                       else f"{type(exc).__name__}: {exc}")
                self.stage_retries[num] += 1
                if self.stage_retries[num] <= max_retries:
                    # If the phase died on a silent provider hang, keep the
                    # OpenCode session so the retry resumes work instead of
                    # restarting the extractor from scratch.
                    if (self._stalled and self.backend == config.BACKEND_OPENCODE
                            and self._oc_session_id):
                        self._resume_session = self._oc_session_id
                    self._log({
                        "type": "retry_start",
                        "retry": self.stage_retries[num],
                        "max_retries": max_retries,
                        "failed_phase": name,
                        "resuming_from": name,
                        "error": err,
                        "phase_retries": self.get_phase_retries(),
                    })
                    continue
                self._log({
                    "type": "retry_exhausted",
                    "retries": self.stage_retries[num] - 1,
                    "max_retries": max_retries,
                    "failed_phase": name,
                    "error": err,
                    "phase_retries": self.get_phase_retries(),
                })
                self._log({"type": "pipeline_end", "status": "error",
                           "error": err, "retries_exhausted": True,
                           "failed_phase": name,
                           "stats": self._stats_snapshot()})
                return

        self._log({"type": "pipeline_end", "status": "done",
                   "stats": self._stats_snapshot()})

