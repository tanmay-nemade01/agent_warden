"""Per-job agent permission fences.

OpenCode: a temp ``opencode.json`` plus ``OPENCODE_CONFIG`` /
``OPENCODE_CONFIG_CONTENT`` / ``OPENCODE_PERMISSION``. Keep ``--auto``.
Command Code: deny/allow lists that still apply under ``--yolo``.
"""
from __future__ import annotations

import json
from pathlib import Path

TOOLKIT_REL = "make-transcript-notes-kit-3agent"


def opencode_permission_block() -> dict:
    """Last matching rule wins — catch-all deny, then allow outputs/YAML."""
    return {
        "question": "deny",
        "task": "deny",
        "read": {
            "*": "allow",
            "*.env": "deny",
            "*.env.*": "deny",
            "*.env.example": "allow",
        },
        "edit": {
            "*": "deny",
            f"{TOOLKIT_REL}/**": "deny",
            "*.env": "deny",
            "*.env.*": "deny",
            "outputs/**": "allow",
            "topic_mappings/**": "allow",
        },
        "bash": {
            "*": "allow",
            f"python {TOOLKIT_REL}/scripts/*.py *": "allow",
            f"python.exe {TOOLKIT_REL}/scripts/*.py *": "allow",
            f"* > *{TOOLKIT_REL}*": "deny",
            f"* >> *{TOOLKIT_REL}*": "deny",
        },
    }


def opencode_config() -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "permission": opencode_permission_block(),
    }


def commandcode_permissions() -> dict:
    """Deny beats allow, including under ``--yolo`` / bypass."""
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
        "allow": [
            "Edit(outputs/**)",
            "Write(outputs/**)",
            "Edit(topic_mappings/**)",
            "Write(topic_mappings/**)",
            f"Shell(python {TOOLKIT_REL}/scripts/*.py *)",
            f"Shell(python.exe {TOOLKIT_REL}/scripts/*.py *)",
        ],
    }


def commandcode_settings() -> dict:
    return {"permissions": commandcode_permissions()}


def claude_allowed_tools() -> str:
    """Allowed tools string for `claude --allowedTools`."""
    return "Read,Edit,Write,Bash,Glob,Grep"


def codex_sandbox_mode() -> str:
    """Sandbox policy for OpenAI Codex exec."""
    return "workspace-write"


def write_job_configs(out_dir: Path) -> dict[str, Path]:
    """Write per-run config files under the lecture output tree (writable)."""
    job = Path(out_dir) / "_job"
    job.mkdir(parents=True, exist_ok=True)
    oc = job / "opencode.json"
    cc = job / "commandcode-settings.json"
    prompt = job / "prompt.txt"
    oc.write_text(json.dumps(opencode_config(), indent=2) + "\n", encoding="utf-8")
    cc.write_text(json.dumps(commandcode_settings(), indent=2) + "\n",
                  encoding="utf-8")
    return {"dir": job, "opencode": oc, "commandcode": cc, "prompt": prompt}


def opencode_env(config_path: Path) -> dict[str, str]:
    from . import config
    blob = json.dumps(opencode_config())
    return {
        "OPENCODE_CONFIG": str(config_path),
        "OPENCODE_CONFIG_CONTENT": blob,
        "OPENCODE_PERMISSION": json.dumps(opencode_permission_block()),
        # Without this, OpenCode sends max_tokens=32000 even when the
        # model catalog allows far more (DeepSeek V4 output=384000).
        # OpenCode still clamps with min(catalog_output, this value).
        "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": str(
            config.OPENCODE_OUTPUT_TOKEN_MAX),
    }
