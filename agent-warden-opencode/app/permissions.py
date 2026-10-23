"""Per-job agent permission fences.

OpenCode v2: a temp ``opencode.json`` plus ``OPENCODE_CONFIG`` /
``OPENCODE_CONFIG_CONTENT``. Permissions use the native v2 shape —
an ordered ``permissions`` array of ``{action, resource, effect}``
(last match wins; ``shell`` replaces v1 ``bash``, ``subagent`` replaces
v1 ``task``). Keep ``--auto`` so anything not explicitly denied runs
unattended. Command Code: deny/allow lists that still apply under ``--yolo``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TOOLKIT_REL = "make-transcript-notes-kit-3agent"

#: Where agents may write when a subject has no profile of its own.
DEFAULT_WRITE_ROOTS = ("outputs/**", "topic_mappings/**")


def opencode_permission_block(write_roots: list[str] | None = None) -> list[dict]:
    """Ordered v2 permission rules — last matching rule wins.

    Catch-all edit deny first, then allow the subject's own write roots
    (output tree + topic mappings); shell allowlist with toolkit-redirect
    denies; never interrupt with questions or spawn subagents from these
    headless phases.
    """
    roots = [r for r in (write_roots or DEFAULT_WRITE_ROOTS) if r]
    block = [
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "read", "resource": "*.env", "effect": "deny"},
        {"action": "read", "resource": "*.env.*", "effect": "deny"},
        {"action": "read", "resource": "*.env.example", "effect": "allow"},
        {"action": "edit", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": f"{TOOLKIT_REL}/**", "effect": "deny"},
        {"action": "edit", "resource": "*.env", "effect": "deny"},
        {"action": "edit", "resource": "*.env.*", "effect": "deny"},
    ]
    for root in roots:
        block.append({"action": "edit", "resource": root, "effect": "allow"})
    block.extend([
        {"action": "shell", "resource": "*", "effect": "allow"},
        {"action": "shell", "resource": f"python {TOOLKIT_REL}/scripts/*.py *", "effect": "allow"},
        {"action": "shell", "resource": f"python.exe {TOOLKIT_REL}/scripts/*.py *", "effect": "allow"},
        {"action": "shell", "resource": f"* > *{TOOLKIT_REL}*", "effect": "deny"},
        {"action": "shell", "resource": f"* >> *{TOOLKIT_REL}*", "effect": "deny"},
        {"action": "question", "resource": "*", "effect": "deny"},
        {"action": "subagent", "resource": "*", "effect": "deny"},
    ])
    return block


def _base_opencode_config() -> dict:
    """Read user's global opencode config if present, preserving custom providers."""
    for cand in [Path.home() / ".config" / "opencode" / "opencode.jsonc",
                 Path.home() / ".config" / "opencode" / "opencode.json"]:
        if cand.is_file():
            try:
                lines = [l for l in cand.read_text(encoding="utf-8").splitlines()
                         if not l.strip().startswith("//")]
                txt = "\n".join(lines)
                txt = re.sub(r",(\s*[}\]])", r"\1", txt)
                data = json.loads(txt)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return {}


def opencode_config(write_roots: list[str] | None = None) -> dict:
    cfg = _base_opencode_config().copy()
    cfg["$schema"] = "https://opencode.ai/config.json"
    cfg["permissions"] = opencode_permission_block(write_roots)
    cfg.pop("permission", None)
    return cfg


def commandcode_permissions(write_roots: list[str] | None = None) -> dict:
    """Deny beats allow, including under ``--yolo`` / bypass."""
    roots = [r for r in (write_roots or DEFAULT_WRITE_ROOTS) if r]
    allow = []
    for root in roots:
        allow.append(f"Edit({root})")
        allow.append(f"Write({root})")
    return {
        "deny": [
            f"Edit({TOOLKIT_REL}/**)",
            f"Write({TOOLKIT_REL}/**)",
            "Edit(*.env)",
            "Write(*.env)",
            "Edit(*.env.*)",
            "Write(*.env.*)",
            "Read(*.env)",
            "Read(*.env.*)",
        ],
        "allow": allow + [
            f"Shell(python {TOOLKIT_REL}/scripts/*.py *)",
            f"Shell(python.exe {TOOLKIT_REL}/scripts/*.py *)",
        ],
    }


def commandcode_settings(write_roots: list[str] | None = None) -> dict:
    return {"permissions": commandcode_permissions(write_roots)}


def claude_allowed_tools() -> str:
    """Allowed tools string for `claude --allowedTools`."""
    return "Read,Edit,Write,Bash,Glob,Grep"


def codex_sandbox_mode() -> str:
    """Sandbox policy for OpenAI Codex exec."""
    return "workspace-write"


def reasonix_auto_flag() -> str:
    """Auto confirmation flag for unattended Reasonix runs."""
    return "-y"


def pi_sandbox_args() -> list[str]:
    """Non-interactive JSON mode arguments for Pi Harness."""
    return ["--print", "--mode", "json", "--no-session", "--approve"]


def antigravity_args() -> list[str]:
    """Non-interactive streaming JSON flags for Antigravity (AGY)."""
    return ["--output-format", "stream-json", "--dangerously-skip-permissions"]


def cursor_sandbox_args() -> list[str]:
    """Non-interactive streaming JSON and auto-force arguments for Cursor Agent."""
    return ["-p", "--output-format", "stream-json", "--force"]


def write_job_configs(out_dir: Path,
                      write_roots: list[str] | None = None) -> dict[str, Path]:
    """Write per-run config files under the lecture output tree (writable)."""
    job = Path(out_dir) / "_job"
    job.mkdir(parents=True, exist_ok=True)
    oc = job / "opencode.json"
    cc = job / "commandcode-settings.json"
    prompt = job / "prompt.txt"
    oc.write_text(json.dumps(opencode_config(write_roots), indent=2) + "\n",
                  encoding="utf-8")
    cc.write_text(json.dumps(commandcode_settings(write_roots), indent=2) + "\n",
                  encoding="utf-8")
    return {"dir": job, "opencode": oc, "commandcode": cc, "prompt": prompt}


def opencode_env(config_path: Path,
                 write_roots: list[str] | None = None,
                 extra_env: dict[str, str] | None = None) -> dict[str, str]:
    from . import config
    # The inline content must match the file it points at, otherwise the
    # subject's own write roots would be replaced by the defaults.
    blob = json.dumps(opencode_config(write_roots))
    env = {
        "OPENCODE_CONFIG": str(config_path),
        "OPENCODE_CONFIG_CONTENT": blob,
        # Without this, OpenCode sends max_tokens=32000 even when the
        # model catalog allows far more (DeepSeek V4 output=384000).
        # OpenCode still clamps with min(catalog_output, this value).
        "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": str(
            config.OPENCODE_OUTPUT_TOKEN_MAX),
    }
    if extra_env:
        env.update(extra_env)
    return env
