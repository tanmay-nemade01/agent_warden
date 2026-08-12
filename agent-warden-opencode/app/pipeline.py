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
import signal
import subprocess
import time
from pathlib import Path

from . import config, gates

AGENTS = {1: "extractor", 2: "enricher", 3: "formatter"}

ADAPTATION = """\
# Runtime adaptation for this automated run

- Working directory (cwd): {workspace} — all relative paths below resolve from here.
- Transcript: {transcript}
- Output directory (create it if missing): {out}
- Toolkit root: {toolkit}
- Lecture number: {lecture_num}
- Subject: {subject} ({abbr})

Whenever the skill says to run `python scripts/<name>.py <args>`, run it as:

    python make-transcript-notes-kit-3agent/scripts/<name>.py <args>

from the working directory above. You have full shell access and may read any
file under the working directory.

You are the ONLY agent in this session. Do not ask questions — proceed
autonomously and finish the entire phase. When your phase is complete, print
exactly this line as the final line of your reply:

    PHASE_COMPLETE
"""


class PhaseError(RuntimeError):
    pass


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
        self.transcript = str(Path(transcript).resolve())
        self.phases = [p for p in (phases or [1, 2, 3]) if p in AGENTS]
        self.emit = emit or (lambda ev: None)
        self.docs_dir = docs_dir
        self.run_id = run_id or f"{abbr}:{prefix}"
        self.backend = config.normalize_backend(backend)
        bmeta = config.backend_meta(self.backend)
        self.model = (model or bmeta["model"]).strip() or bmeta["model"]
        # Empty string means "no --variant" / "--effort" (model has no knobs).
        self.variant = (bmeta["variant"] if variant is None
                        else str(variant).strip())
        self.out = config.OUTPUTS_DIR / subject / prefix
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

    def _agent_message(self, agent: int, extra: str = "") -> str:
        skill = config.skill_path(agent)
        docs_note = ""
        if agent == 2:
            if self.docs_dir and self.docs_dir != "__none__":
                docs_dir = Path(self.docs_dir)
                if docs_dir.is_dir():
                    docs_note = (f"- Enrichment docs directory: {docs_dir} "
                                 "(read-only; use only files relevant to this "
                                 "lecture)\n")
                else:
                    docs_note = ("- The selected enrichment docs directory does "
                                 "not exist; enrich from the transcript content "
                                 "and web research if needed.\n")
            elif self.docs_dir == "__none__":
                docs_note = ("- No companion docs selected for this run; enrich "
                             "from the transcript content and web research if "
                             "needed.\n")
            else:
                doc_root = self._default_docs_dir()
                docs_note = (f"- Enrichment docs directory: {doc_root} (read-only; use "
                             f"only files relevant to this lecture)\n" if doc_root
                             else "- No enrichment docs folder exists for this subject; enrich "
                                  "from the transcript content and web research if needed.\n")
        adapt = ADAPTATION.format(
            workspace=config.WORKSPACE,
            transcript=self.transcript,
            out=self.out,
            toolkit=config.TOOLKIT,
            lecture_num=self.lecture_num,
            subject=self.subject,
            abbr=self.abbr,
        )
        return (f"Read the skill file at {skill} and follow its instructions "
                f"exactly. It defines your role and the complete process.\n\n"
                f"{docs_note}{adapt}\n{extra}")

    # Payloads that can carry large tool outputs / reasoning text.
    EVENT_TEXT_CAP = 6000          # chars kept per event field
    EVENT_TOOL_OUTPUT_CAP = 8000   # chars kept of a tool's output

    def _run_agent(self, agent: int, message: str, title: str,
                   extra_env: dict | None = None) -> tuple[int, list[str]]:
        """Run the selected backend non-interactively; stream structured events."""
        if self.backend == config.BACKEND_COMMANDCODE:
            return self._run_commandcode(agent, message, title, extra_env)
        return self._run_opencode(agent, message, title, extra_env)

    def _stream_process(self, agent: int, parse_line) -> tuple[int, list[str]]:
        lines: list[str] = []
        try:
            assert self.proc is not None and self.proc.stdout is not None
            for raw in self.proc.stdout:
                line = raw.rstrip("\r\n")
                lines.append(line)
                events = parse_line(line)
                if events:
                    for event in events:
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
        except KeyboardInterrupt:
            self._kill()
            raise PhaseError("interrupted")
        code = self.proc.wait() if self.proc else 1
        return code, lines

    def _run_opencode(self, agent: int, message: str, title: str,
                      extra_env: dict | None = None) -> tuple[int, list[str]]:
        exe = config.find_opencode()
        cmd = [exe, "run", "--auto", "-m", self.model]
        if self.variant:
            cmd.extend(["--variant", self.variant])
        cmd.extend([
            "--format", "json", "--thinking",
            "--dir", str(config.WORKSPACE),
            "--title", title, message,
        ])
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        self._log({"type": "phase_cmd", "phase": AGENTS[agent],
                   "cmd": " ".join(cmd[:8]) + " ..."})
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def parse(line: str) -> list[dict]:
            if not line.lstrip().startswith("{"):
                return []
            event = self._parse_agent_event(line)
            return [event] if event is not None else []

        return self._stream_process(agent, parse)

    def _run_commandcode(self, agent: int, message: str, title: str,
                         extra_env: dict | None = None) -> tuple[int, list[str]]:
        """Headless Command Code: `cmdc -p` with JSON events and --yolo.

        Prompt goes on stdin to avoid Windows argv length limits. The CLI is
        slow to start; that is expected. cwd is the workspace so toolkit paths
        resolve. See https://commandcode.ai/docs/headless
        """
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
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        env["FORCE_COLOR"] = "0"
        if extra_env:
            env.update(extra_env)
        self._cc_tools = {}
        self._log({"type": "phase_cmd", "phase": AGENTS[agent],
                   "cmd": " ".join(cmd[:10]) + " ..."})
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", env=env, cwd=str(config.WORKSPACE),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(message)
            self.proc.stdin.close()
        except OSError:
            pass
        return self._stream_process(agent, self._parse_commandcode_line)

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
        out = {
            "seconds": secs,
            "cost": round(float(bucket["cost"]), 6),
            "tokens": dict(bucket["tokens"]),
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
        return {
            "seconds": total_secs,
            "cost": round(float(self.stats["cost"]), 6),
            "tokens": dict(self.stats["tokens"]),
            "phases": dict(self.stats["phases"]),
        }

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    capture_output=True, timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def stop(self):
        self.stop_flag = True
        self._kill()

    # ---------------------------------------------------------------- phases
    def _phase_extractor(self) -> bool:
        dense = self.out / f"{self.prefix}_notes_dense.md"
        manifest = self.out / f"{self.prefix}_extraction_manifest.json"
        self.out.mkdir(parents=True, exist_ok=True)
        msg = self._agent_message(1, (
            f"Process the transcript into {dense} and {manifest} as the skill "
            "specifies, then run the dense-draft lint gate and the manifest "
            "verifier yourself until both pass."))
        code, _ = self._run_agent(1, msg, f"extractor {self.prefix}")
        if self.stop_flag:
            return False
        if not dense.is_file() or not manifest.is_file():
            raise PhaseError(
                f"extractor exited {code} without producing {dense.name} / "
                f"{manifest.name}")
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
        msg = self._agent_message(2, (
            f"Enrich {dense} (manifest: {manifest}) into {enriched} exactly as "
            "the skill specifies — split, enrich each section, assemble, bind "
            "summaries, update the topic mapping YAML, then run the enriched "
            "lint and manifest verifier until both pass."))
        code, _ = self._run_agent(2, msg, f"enricher {self.prefix}")
        if self.stop_flag:
            return False
        if not enriched.is_file():
            raise PhaseError(
                f"enricher exited {code} without producing {enriched.name}")
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
        if not html.is_file():
            raise PhaseError(
                f"formatter exited {code} without producing {html.name}")
        return self._verify(3, [
            ("lint_html", gates.gate_lint_html(str(html))),
            ("verify_manifest", gates.gate_verify_manifest(
                str(manifest), str(html), "html")),
        ])

    # -------------------------------------------------------------- gate loop
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
            rounds += 1
            if rounds >= config.MAX_FIX_ROUNDS or self.stop_flag:
                raise PhaseError(
                    f"{AGENTS[agent]} gates still failing after {rounds} round(s)")
            findings = []
            for name, res in gate_results:
                items = "\n".join(res.findings)
                findings.append(f"--- {name} ---\n{items[:2000]}")
            fix_msg = (
                "The verification gates on your output files reported FAILs. "
                "Fix the underlying content (read the files, correct the real "
                "issues, never silence a warning by deleting content), then "
                "re-run the gates yourself exactly as the skill specifies until "
                f"they PASS. Files to fix:\n{findings}\n\n"
                f"When finished, print exactly: PHASE_COMPLETE")
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
    MAX_AUTO_RETRIES = 3

    def run(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.run_log = self.out / f"{self.prefix}_run_events.jsonl"
        self.stats["started_at"] = time.time()
        self._log({"type": "pipeline_start", "subject": self.subject,
                   "abbr": self.abbr, "prefix": self.prefix,
                   "lecture_num": self.lecture_num,
                   "transcript": self.transcript, "phases": self.phases,
                   "backend": self.backend,
                   "model": self.model, "variant": self.variant})

        phases_to_run = list(self.phases)
        retry_count = 0

        while True:
            try:
                for num in phases_to_run:
                    name = AGENTS[num]
                    self._log({"type": "phase_start", "phase": name})
                    self._begin_phase_stats(name)
                    done = {
                        1: self._phase_extractor,
                        2: self._phase_enricher,
                        3: self._phase_formatter,
                    }[num]()
                    phase_stats = self._end_phase_stats(name)
                    self._log({"type": "phase_end", "phase": name,
                               "ok": done, "seconds": phase_stats["seconds"],
                               "stats": phase_stats})
                    if self.stop_flag:
                        self._log({"type": "pipeline_end", "status": "stopped",
                                   "stats": self._stats_snapshot()})
                        return
                # All phases completed successfully.
                self._log({"type": "pipeline_end", "status": "done",
                           "stats": self._stats_snapshot()})
                return

            except (PhaseError, Exception) as exc:  # noqa: BLE001
                # Close out the in-flight phase bucket so totals stay honest.
                if self._phase_bucket is not None:
                    running = next((p for p, b in self.stats["phases"].items()
                                    if b is self._phase_bucket), None)
                    if running:
                        self._end_phase_stats(running)

                if self.stop_flag:
                    self._log({"type": "pipeline_end", "status": "stopped",
                               "stats": self._stats_snapshot()})
                    return

                retry_count += 1
                # Determine which phase failed.
                failed_phase_num = None
                for num in phases_to_run:
                    name = AGENTS[num]
                    # The failed phase is the one that was running (has a
                    # bucket dict, not a final summary) or never got a summary.
                    bucket = self.stats["phases"].get(name)
                    if bucket is None or isinstance(bucket, dict) and "seconds" not in bucket:
                        failed_phase_num = num
                        break
                if failed_phase_num is None:
                    # Fallback: assume the last phase in the list failed.
                    failed_phase_num = phases_to_run[-1] if phases_to_run else self.phases[-1]

                if retry_count <= self.MAX_AUTO_RETRIES:
                    # Decide where to resume: if agent 1 failed, restart
                    # from agent 1; otherwise resume from the failed agent.
                    if failed_phase_num == self.phases[0]:
                        phases_to_run = list(self.phases)
                    else:
                        phases_to_run = [p for p in self.phases
                                         if p >= failed_phase_num]

                    self._log({
                        "type": "retry_start",
                        "retry": retry_count,
                        "max_retries": self.MAX_AUTO_RETRIES,
                        "failed_phase": AGENTS.get(failed_phase_num, "unknown"),
                        "resuming_from": AGENTS.get(phases_to_run[0], "unknown"),
                        "error": (str(exc) if isinstance(exc, PhaseError)
                                  else f"{type(exc).__name__}: {exc}"),
                    })
                    continue  # retry
                else:
                    # Exhausted all retries — fall through to manual mode.
                    self._log({
                        "type": "retry_exhausted",
                        "retries": retry_count - 1,
                        "failed_phase": AGENTS.get(failed_phase_num, "unknown"),
                        "error": (str(exc) if isinstance(exc, PhaseError)
                                  else f"{type(exc).__name__}: {exc}"),
                    })
                    self._log({"type": "pipeline_end", "status": "error",
                               "error": (str(exc) if isinstance(exc, PhaseError)
                                         else f"{type(exc).__name__}: {exc}"),
                               "retries_exhausted": True,
                               "stats": self._stats_snapshot()})
                    return

