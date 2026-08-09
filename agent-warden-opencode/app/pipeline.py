"""Three-agent notes pipeline, driven by the opencode CLI.

Each phase is one `opencode run` invocation (a fresh session, matching the
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
                 model: str | None = None, variant: str | None = None):
        self.subject = subject
        self.abbr = abbr
        self.prefix = prefix
        self.lecture_num = lecture_num
        self.transcript = str(Path(transcript).resolve())
        self.phases = [p for p in (phases or [1, 2, 3]) if p in AGENTS]
        self.emit = emit or (lambda ev: None)
        self.docs_dir = docs_dir
        self.run_id = run_id or f"{abbr}:{prefix}"
        self.model = (model or config.MODEL).strip() or config.MODEL
        # Empty string means "no --variant" (model has no effort knobs).
        self.variant = (config.VARIANT if variant is None
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
        """Run opencode non-interactively; stream structured events."""
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
        lines: list[str] = []
        try:
            assert self.proc.stdout is not None
            for raw in self.proc.stdout:
                line = raw.rstrip("\r\n")
                lines.append(line)
                if line.lstrip().startswith("{"):
                    event = self._parse_agent_event(line)
                    if event is not None:
                        self._accumulate_stats(AGENTS[agent], event)
                        self._log({"type": "agent_event",
                                   "phase": AGENTS[agent],
                                   "event": event})
                    else:
                        self._log({"type": "log", "phase": AGENTS[agent],
                                   "line": line})
                else:
                    self._log({"type": "log", "phase": AGENTS[agent],
                               "line": line})
                if self.stop_flag:
                    self._kill()
                    raise PhaseError("stopped by user")
        except KeyboardInterrupt:
            self._kill()
            raise PhaseError("interrupted")
        code = self.proc.wait()
        return code, lines

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
    def run(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.run_log = self.out / f"{self.prefix}_run_events.jsonl"
        self.stats["started_at"] = time.time()
        self._log({"type": "pipeline_start", "subject": self.subject,
                   "abbr": self.abbr, "prefix": self.prefix,
                   "lecture_num": self.lecture_num,
                   "transcript": self.transcript, "phases": self.phases,
                   "model": self.model, "variant": self.variant})
        try:
            for num in self.phases:
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
            self._log({"type": "pipeline_end", "status": "done",
                       "stats": self._stats_snapshot()})
        except PhaseError as exc:
            if self._phase_bucket is not None:
                # Close out the in-flight phase bucket so totals stay honest.
                running = next((p for p, b in self.stats["phases"].items()
                                if b is self._phase_bucket), None)
                if running:
                    self._end_phase_stats(running)
            self._log({"type": "pipeline_end", "status": "error",
                       "error": str(exc), "stats": self._stats_snapshot()})
        except Exception as exc:  # noqa: BLE001
            if self._phase_bucket is not None:
                running = next((p for p, b in self.stats["phases"].items()
                                if b is self._phase_bucket), None)
                if running:
                    self._end_phase_stats(running)
            self._log({"type": "pipeline_end", "status": "error",
                       "error": f"{type(exc).__name__}: {exc}",
                       "stats": self._stats_snapshot()})
