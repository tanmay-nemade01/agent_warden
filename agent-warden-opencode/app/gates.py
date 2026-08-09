"""Run the toolkit's verification scripts as external gates.

Each gate maps to one toolkit script. Output is streamed as it runs, and the
final result (passed / warned / failed) is derived from the script's
[PASS]/[WARN]/[FAIL] markers plus its exit code, mirroring what the warden
used to do with its container runner — minus the containers.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

from . import config

BRACKET = re.compile(r"^\s*\[(PASS|WARN|FAIL)\]\s*(.*)$")


@dataclass
class GateResult:
    gate: str
    passed: bool
    findings: list[str] = field(default_factory=list)
    output: str = ""
    exit_code: int = 0

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"


def _run(cmd: list[str], timeout: int = 600) -> GateResult:
    """Run a gate command, capture output, classify by markers + exit code."""
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        creationflags=0)
    output = (proc.stdout or "") + (proc.stderr or "")
    findings = []
    for line in output.splitlines():
        m = BRACKET.match(line)
        if m:
            findings.append(f"{m.group(1)}: {m.group(2).strip()}")
    has_fail = bool(re.search(r"\[FAIL\]", output))
    has_warn = bool(re.search(r"\[WARN\]", output))
    passed = proc.returncode == 0 and not has_fail
    if not passed:
        if not findings:
            findings.append(f"exit_code={proc.returncode}: {output.strip()[:500]}")
    return GateResult(
        gate=cmd[1] if len(cmd) > 1 else "gate",
        passed=passed,
        findings=findings,
        output=output,
        exit_code=proc.returncode,
    )


def _py() -> list[str]:
    return [sys.executable or "python"]


def gate_lint_dense(target: str, lecture_num: str, phase: str) -> GateResult:
    return _run(_py() + [
        str(config.TOOLKIT / "scripts" / "lint_dense.py"),
        target, "--lecture-num", lecture_num, "--phase", phase])


def gate_verify_manifest(manifest: str, target: str, phase: str) -> GateResult:
    return _run(_py() + [
        str(config.TOOLKIT / "scripts" / "verify_manifest.py"),
        manifest, target, "--phase", phase])


def gate_lint_html(target: str) -> GateResult:
    return _run(_py() + [str(config.TOOLKIT / "scripts" / "lint.py"), target])


def gate_update_topic_mapping(subject: str, lecture_num: str, topic: str,
                              html_path: str, topics_file: str) -> GateResult:
    return _run(_py() + [
        str(config.TOOLKIT / "scripts" / "update_topic_mapping.py"),
        subject, lecture_num, topic, html_path, topics_file])


def dump(result: GateResult) -> dict:
    return {
        "gate": result.gate,
        "status": result.status,
        "passed": result.passed,
        "findings": result.findings,
        "exit_code": result.exit_code,
        "output": result.output[-4000:],
    }
