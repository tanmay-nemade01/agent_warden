"""Static configuration for the transcript-notes automation.

Everything is relative to the workspace root (repo parent of this package,
or ``NOTES_WORKSPACE``), which is also the working directory for every agent
CLI run so that the toolkit's own instructions (outputs/, topic_mappings/,
extracted_pdfs/ paths) resolve as-is.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
from pathlib import Path

from .paths import confine, is_safe_component

_DEFAULT_WORKSPACE = Path(__file__).resolve().parents[2]
WORKSPACE = Path(os.environ.get("NOTES_WORKSPACE", str(_DEFAULT_WORKSPACE))).resolve()
TOOLKIT = WORKSPACE / "make-transcript-notes-kit-3agent"
TRANSCRIPTS_DIR = WORKSPACE / "transcript files"
OUTPUTS_DIR = WORKSPACE / "outputs"
TOPIC_MAPPINGS_DIR = WORKSPACE / "topic_mappings"
DOCS_DIR = (WORKSPACE / "companion_docs"
            if (WORKSPACE / "companion_docs").is_dir()
            else WORKSPACE / "extracted_pdfs")

BACKEND_OPENCODE = "opencode"
BACKEND_COMMANDCODE = "commandcode"
BACKEND_CLAUDE = "claude"
BACKEND_CODEX = "codex"
DEFAULT_BACKEND = BACKEND_OPENCODE

BACKENDS = {
    BACKEND_OPENCODE: {
        "id": BACKEND_OPENCODE,
        "label": "OpenCode",
        "model": "opencode-go/deepseek-v4-flash",
        "variant": "max",
        "provider": "opencode-go",
        "effort_variants": ["low", "high", "max"],
    },
    BACKEND_COMMANDCODE: {
        "id": BACKEND_COMMANDCODE,
        "label": "Command Code",
        "model": "deepseek/deepseek-v4-flash",
        "variant": "high",
        "provider": "commandcode",
        "effort_variants": ["low", "medium", "high"],
    },
    BACKEND_CLAUDE: {
        "id": BACKEND_CLAUDE,
        "label": "Claude Code",
        "model": "claude-3-7-sonnet",
        "variant": "high",
        "provider": "anthropic",
        "effort_variants": ["low", "medium", "high", "max"],
    },
    BACKEND_CODEX: {
        "id": BACKEND_CODEX,
        "label": "OpenAI Codex",
        "model": "gpt-5.4",
        "variant": "",
        "provider": "openai",
        "effort_variants": [],
    },
}

# OpenCode defaults (kept as top-level names for existing callers).
MODEL = BACKENDS[BACKEND_OPENCODE]["model"]
VARIANT = BACKENDS[BACKEND_OPENCODE]["variant"]
MODEL_PROVIDER = BACKENDS[BACKEND_OPENCODE]["provider"]
MAX_FIX_ROUNDS = 3          # 1 initial attempt + up to 2 gate-fix sessions per attempt
MAX_STAGE_RETRIES = 3       # after a phase fails, retry that phase this many times
MAX_PARALLEL_RUNS = 2       # in-flight CLI agents; extras wait for a slot
PHASE_TIMEOUT_SECONDS = 6 * 60 * 60  # generous ceiling; notes take a while
COMMANDCODE_MAX_TURNS = 250
CLAUDE_MAX_TURNS = 250
CODEX_MAX_TURNS = 250
# OpenCode sends min(model.limit.output, this env). Default 32k is far
# too small for max-effort thinking (finish reason length/unknown, 0
# output tokens). DeepSeek V4 catalog output is 384k; 128k still truncated
# mid-thought and forced session-continues. Keep this >= catalog output
# so the model limit is the real cap, not our ceiling.
OPENCODE_OUTPUT_TOKEN_MAX = 384000
MAX_TRUNCATION_CONTINUES = 3  # resume same OpenCode session after reason=length
# Prefer higher effort when falling back from the default "max".
_VARIANT_RANK = ("none", "minimal", "low", "medium", "high", "xhigh",
                 "thinking", "max")


def normalize_backend(backend: str | None) -> str:
    raw = (backend or DEFAULT_BACKEND).strip().lower().replace("-", "").replace("_", "")
    if raw in {"commandcode", "cmdc", "cc"}:
        return BACKEND_COMMANDCODE
    if raw in {"claude", "claudecode", "anthropic"}:
        return BACKEND_CLAUDE
    if raw in {"codex", "openaicodex", "openai"}:
        return BACKEND_CODEX
    if raw in {"opencode", "oc"}:
        return BACKEND_OPENCODE
    return DEFAULT_BACKEND


def backend_meta(backend: str | None = None) -> dict:
    return BACKENDS[normalize_backend(backend)]

SUBJECTS = {
    "ACI":  "Artificial Computational Intelligence",
    "BDA":  "Big Data Analytics",
    "BDS":  "Big Data Systems",
    "DMML": "Data Management for Machine Learning",
    "DNN":  "Deep Neural Networks",
    "DRL":  "Deep Reinforcement Learning",
    "DSA":  "Data Structures and Algorithms",
    "DW":   "Data Warehousing",
    "DWH":  "Data Warehousing",
    "ISM":  "Introduction to Statistical Methods",
    "MFML": "Mathematical Foundations for Machine Learning",
    "ML":   "Machine Learning",
    "NLP":  "Natural Language Processing",
    "SEML": "Software Engineering for Machine Learning",
    "SPA":  "Stream Processing and Analytics",
}

SUBJECTS_FILE = WORKSPACE / "subjects.json"


def all_subjects() -> dict[str, str]:
    """Built-in subjects merged with user-created ones (from subjects.json)."""
    merged = dict(SUBJECTS)
    if SUBJECTS_FILE.is_file():
        try:
            custom = json.loads(SUBJECTS_FILE.read_text(encoding="utf-8"))
            for abbr, name in custom.items():
                if isinstance(name, str) and name.strip():
                    merged[abbr.upper()] = name.strip()
        except (OSError, ValueError):
            pass
    return merged


def save_subject(abbr: str, name: str) -> dict[str, str]:
    """Persist a user-created subject and create its companion-docs folder and topic mapping YAML."""
    abbr = abbr.strip().upper()
    name = name.strip()
    if not is_safe_component(abbr) or not is_safe_component(name):
        raise ValueError("subject abbreviation or name is not a safe path component")
    docs_folder = confine(DOCS_DIR, abbr)
    yaml_path = confine(TOPIC_MAPPINGS_DIR, f"{name}.yaml")
    if docs_folder is None or yaml_path is None:
        raise ValueError("subject abbreviation or name is not a safe path component")
    subjects = all_subjects()
    subjects[abbr] = name
    data = {a: n for a, n in sorted(subjects.items())
            if a not in SUBJECTS}
    SUBJECTS_FILE.write_text(json.dumps(data, indent=2) + "\n",
                             encoding="utf-8")
    docs_folder.mkdir(parents=True, exist_ok=True)
    if not yaml_path.is_file():
        yaml_path.write_text(f'subject_name: "{name}"\nlectures: []\n', encoding="utf-8")
    return subjects


def default_docs_dir(abbr: str, subject: str = "") -> Path | None:
    """Best-guess companion-docs folder for a subject: first subfolder of
    companion_docs/ whose name matches the subject abbreviation (or full
    subject name) case-insensitively, otherwise None. Never hardcoded —
    adapts to the folders actually present."""
    if not DOCS_DIR.is_dir():
        return None
    wanted = {abbr.lower(), subject.lower()}
    for p in sorted(DOCS_DIR.iterdir()):
        if p.is_dir() and p.name.lower() in wanted:
            return p
    return None


_OPENCODE_EXE: str | None = None
_COMMANDCODE_ARGV: list[str] | None = None
_CLAUDE_ARGV: list[str] | None = None
_CODEX_ARGV: list[str] | None = None
_MODELS_CACHE: dict[str, dict] = {}
_MODELS_CACHE_AT: dict[str, float] = {}
_MODELS_CACHE_TTL = 300.0  # seconds (OpenCode)
_COMMANDCODE_MODELS_TTL = 900.0  # CLI is slow to start; keep the list longer


def find_opencode() -> str:
    """Resolve a real opencode executable (skip the .ps1 shim)."""
    global _OPENCODE_EXE
    if _OPENCODE_EXE:
        return _OPENCODE_EXE
    for candidate in shutil.which("opencode.exe"), shutil.which("opencode"):
        if candidate and candidate.lower().endswith(".exe"):
            _OPENCODE_EXE = candidate
            return candidate
    pkg_roots = [
        Path(os.environ.get("APPDATA", "")) / "npm",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
    ]
    for root in pkg_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if "opencode-ai" in dirnames:
                exe = Path(dirpath) / "opencode-ai" / "bin" / "opencode.exe"
                if exe.is_file():
                    _OPENCODE_EXE = str(exe)
                    return _OPENCODE_EXE
            dirnames[:] = [d for d in dirnames
                           if d not in {"__pycache__", ".git"}]
    # Last resort: whatever which finds (may be a .cmd wrapper).
    _OPENCODE_EXE = shutil.which("opencode") or "opencode"
    return _OPENCODE_EXE


def find_commandcode_argv() -> list[str]:
    """Argv prefix for Command Code, skipping PowerShell shims.

    On Windows the user-facing name is `cmdc` (`cmd` is taken). Invoking the
    `.ps1` wrapper from Python hangs; prefer `node …/command-code/dist/index.mjs`.
    """
    global _COMMANDCODE_ARGV
    if _COMMANDCODE_ARGV:
        return _COMMANDCODE_ARGV

    def _from_bindir(bindir: Path) -> list[str] | None:
        mjs = bindir / "node_modules" / "command-code" / "dist" / "index.mjs"
        if not mjs.is_file():
            return None
        node = bindir / "node.exe"
        node_exe = str(node) if node.is_file() else (
            shutil.which("node.exe") or shutil.which("node") or "node")
        return [node_exe, str(mjs)]

    for name in ("cmdc.cmd", "command-code.cmd", "cmdc", "command-code"):
        found = shutil.which(name)
        if not found:
            continue
        path = Path(found)
        if path.suffix.lower() == ".ps1":
            cmd = path.with_suffix(".cmd")
            if cmd.is_file():
                path = cmd
        argv = _from_bindir(path.parent)
        if argv:
            _COMMANDCODE_ARGV = argv
            return argv

    npm = Path(os.environ.get("APPDATA", "")) / "npm"
    if npm.is_dir():
        argv = _from_bindir(npm)
        if argv:
            _COMMANDCODE_ARGV = argv
            return argv
        mjs = (npm / "node_modules" / "command-code" / "dist" / "index.mjs")
        if mjs.is_file():
            node_exe = shutil.which("node.exe") or shutil.which("node") or "node"
            _COMMANDCODE_ARGV = [node_exe, str(mjs)]
            return _COMMANDCODE_ARGV

    fallback = shutil.which("cmdc.cmd") or shutil.which("command-code.cmd") \
        or shutil.which("cmdc") or "cmdc"
    _COMMANDCODE_ARGV = [fallback]
    return _COMMANDCODE_ARGV


def find_claude_argv() -> list[str]:
    """Argv prefix for Claude Code, skipping PowerShell shims."""
    global _CLAUDE_ARGV
    if _CLAUDE_ARGV:
        return _CLAUDE_ARGV
    for name in ("claude.exe", "claude.cmd", "claude"):
        found = shutil.which(name)
        if found:
            path = Path(found)
            if path.suffix.lower() == ".ps1":
                cmd = path.with_suffix(".cmd")
                if cmd.is_file():
                    path = cmd
            _CLAUDE_ARGV = [str(path)]
            return _CLAUDE_ARGV
    # Check WinGet and AppData locations
    pkg_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Claude",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path(os.environ.get("APPDATA", "")) / "npm",
    ]
    for root in pkg_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for f in ("claude.exe", "claude.cmd"):
                if f in filenames:
                    p = Path(dirpath) / f
                    _CLAUDE_ARGV = [str(p)]
                    return _CLAUDE_ARGV
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
    _CLAUDE_ARGV = ["claude"]
    return _CLAUDE_ARGV


def find_claude() -> str:
    return find_claude_argv()[0]


def find_codex_argv() -> list[str]:
    """Argv prefix for OpenAI Codex CLI."""
    global _CODEX_ARGV
    if _CODEX_ARGV:
        return _CODEX_ARGV
    for name in ("codex.exe", "codex.cmd", "codex"):
        found = shutil.which(name)
        if found:
            path = Path(found)
            if path.suffix.lower() == ".ps1":
                cmd = path.with_suffix(".cmd")
                if cmd.is_file():
                    path = cmd
            _CODEX_ARGV = [str(path)]
            return _CODEX_ARGV
    # Check OpenAI Codex installer paths
    pkg_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OpenAI" / "Codex" / "bin",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
    ]
    for root in pkg_roots:
        if not root.is_dir():
            continue
        for candidate in (root / "codex.exe", root / "codex.cmd"):
            if candidate.is_file():
                _CODEX_ARGV = [str(candidate)]
                return _CODEX_ARGV
    _CODEX_ARGV = ["codex"]
    return _CODEX_ARGV


def find_codex() -> str:
    return find_codex_argv()[0]


def _parse_models_verbose(stdout: str) -> list[dict]:
    """Parse `opencode models <provider> --verbose` (id line + JSON blob)."""
    import re
    models: list[dict] = []
    lines = (stdout or "").splitlines()
    i = 0
    id_re = re.compile(r"^[\w.-]+/[\w.@+-]+$")
    while i < len(lines):
        line = lines[i].strip()
        if not id_re.match(line):
            i += 1
            continue
        model_id = line
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines) or not lines[i].strip().startswith("{"):
            models.append({
                "id": model_id, "name": model_id.split("/", 1)[-1],
                "variants": [], "reasoning": False, "status": "unknown",
            })
            continue
        blob: list[str] = []
        depth = 0
        while i < len(lines):
            raw = lines[i]
            blob.append(raw)
            depth += raw.count("{") - raw.count("}")
            i += 1
            if depth <= 0:
                break
        try:
            meta = json.loads("\n".join(blob))
        except ValueError:
            models.append({
                "id": model_id, "name": model_id.split("/", 1)[-1],
                "variants": [], "reasoning": False, "status": "unknown",
            })
            continue
        variants = meta.get("variants") or {}
        variant_ids = list(variants.keys()) if isinstance(variants, dict) else []
        caps = meta.get("capabilities") or {}
        models.append({
            "id": f"{meta.get('providerID') or model_id.split('/', 1)[0]}/"
                  f"{meta.get('id') or model_id.split('/', 1)[-1]}",
            "name": meta.get("name") or model_id.split("/", 1)[-1],
            "family": meta.get("family") or "",
            "status": meta.get("status") or "active",
            "reasoning": bool(caps.get("reasoning")),
            "variants": variant_ids,
            "cost": meta.get("cost") or {},
            "limit": meta.get("limit") or {},
        })
    return models


def preferred_variant(variants: list[str],
                      wanted: str | None = None) -> str:
    """Pick wanted variant if present, else the highest-ranked available."""
    wanted = wanted or VARIANT
    if not variants:
        return ""
    if wanted in variants:
        return wanted
    rank = {name: i for i, name in enumerate(_VARIANT_RANK)}
    return max(variants, key=lambda v: rank.get(v, -1))


def _fallback_models(backend: str) -> list[dict]:
    meta = backend_meta(backend)
    backend_id = meta["id"]
    if backend_id == BACKEND_CLAUDE:
        return [
            {
                "id": "claude-3-7-sonnet",
                "name": "Claude 3.7 Sonnet",
                "family": "claude-sonnet",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {},
            },
            {
                "id": "claude-3-5-sonnet",
                "name": "Claude 3.5 Sonnet",
                "family": "claude-sonnet",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "claude-3-5-haiku",
                "name": "Claude 3.5 Haiku",
                "family": "claude-haiku",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "claude-3-opus",
                "name": "Claude 3 Opus",
                "family": "claude-opus",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {},
            },
        ]
    if backend_id == BACKEND_CODEX:
        return [
            {
                "id": "gpt-5.4",
                "name": "GPT-5.4",
                "family": "gpt-5",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "gpt-5.4-mini",
                "name": "GPT-5.4 Mini",
                "family": "gpt-5",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "family": "gpt-4o",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "family": "gpt-4o",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "o3-mini",
                "name": "o3-mini",
                "family": "o3",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high"],
                "cost": {},
                "limit": {},
            },
            {
                "id": "codex-local",
                "name": "Codex Local (Ollama)",
                "family": "local",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {},
            },
        ]
    return [{
        "id": meta["model"],
        "name": "DeepSeek V4 Flash",
        "family": "deepseek-flash",
        "status": "active",
        "reasoning": True,
        "variants": list(meta["effort_variants"]),
        "cost": {},
        "limit": {},
    }]


def _cache_get(key: str, ttl: float, refresh: bool):
    import time as _time
    now = _time.time()
    cached = _MODELS_CACHE.get(key)
    if (not refresh and cached is not None
            and (now - _MODELS_CACHE_AT.get(key, 0)) < ttl):
        return cached
    return None


def _cache_put(key: str, result: dict) -> dict:
    import time as _time
    _MODELS_CACHE[key] = result
    _MODELS_CACHE_AT[key] = _time.time()
    return result


def _parse_commandcode_models(stdout: str, effort_variants: list[str]) -> list[dict]:
    """Parse `cmdc --list-models` (copy-pasteable ids, possibly with ANSI)."""
    import re
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    id_re = re.compile(r"^[\w.-]+(?:/[\w.@+-]+)?$")
    skip = {"model", "models", "id", "name", "provider", "available", "copy"}
    models: list[dict] = []
    seen: set[str] = set()
    text = stdout or ""
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            raw = json.loads(stripped)
            rows = raw if isinstance(raw, list) else (raw.get("models") or [])
            for row in rows:
                if isinstance(row, str):
                    mid = row.strip()
                    name = mid.split("/", 1)[-1]
                elif isinstance(row, dict):
                    mid = str(row.get("id") or row.get("model") or "").strip()
                    name = str(row.get("name") or mid.split("/", 1)[-1])
                else:
                    continue
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                models.append({
                    "id": mid, "name": name, "family": "",
                    "status": "active", "reasoning": True,
                    "variants": list(effort_variants), "cost": {}, "limit": {},
                })
            if models:
                return models
        except ValueError:
            pass
    for line in text.splitlines():
        line = ansi.sub("", line).strip()
        if not line or set(line) <= {"-", "=", " "}:
            continue
        token = line.split()[0].strip("`,;|")
        if token.lower() in skip or not id_re.match(token):
            continue
        if token in seen:
            continue
        seen.add(token)
        rest = line[len(line.split()[0]):].strip(" -|")
        name = rest or token.split("/", 1)[-1]
        models.append({
            "id": token, "name": name, "family": "",
            "status": "active", "reasoning": True,
            "variants": list(effort_variants), "cost": {}, "limit": {},
        })
    return models


def _list_opencode_models(provider: str, refresh: bool, timeout: float) -> dict:
    import subprocess

    fallback = _fallback_models(BACKEND_OPENCODE)
    exe = find_opencode()
    if not exe:
        return {
            "ok": False, "backend": BACKEND_OPENCODE, "provider": provider,
            "models": fallback, "default_model": MODEL,
            "default_variant": VARIANT,
            "error": "opencode executable not found",
        }
    cmd = [exe, "models", provider, "--verbose"]
    if refresh:
        cmd.append("--refresh")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False, "backend": BACKEND_OPENCODE, "provider": provider,
            "models": fallback, "default_model": MODEL,
            "default_variant": VARIANT,
            "error": f"{type(exc).__name__}: {exc}",
        }
    models = _parse_models_verbose(proc.stdout or "")
    models = [m for m in models
              if (m.get("status") or "active") in {"active", "unknown"}]
    if not models:
        err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        return {
            "ok": False, "backend": BACKEND_OPENCODE, "provider": provider,
            "models": fallback, "default_model": MODEL,
            "default_variant": VARIANT,
            "error": f"no models parsed ({err})",
        }
    ids = {m["id"] for m in models}
    default_model = MODEL if MODEL in ids else models[0]["id"]
    variants = next((m["variants"] for m in models if m["id"] == default_model),
                    [])
    return {
        "ok": True, "backend": BACKEND_OPENCODE, "provider": provider,
        "models": models, "default_model": default_model,
        "default_variant": preferred_variant(variants, VARIANT),
        "error": None,
    }


def _list_commandcode_models(refresh: bool, timeout: float) -> dict:
    """Live `cmdc --list-models` is slow; skip unless refresh=True or cached."""
    import subprocess

    meta = backend_meta(BACKEND_COMMANDCODE)
    fallback = _fallback_models(BACKEND_COMMANDCODE)
    argv = find_commandcode_argv()
    if not argv:
        return {
            "ok": False, "backend": BACKEND_COMMANDCODE,
            "provider": meta["provider"], "models": fallback,
            "default_model": meta["model"], "default_variant": meta["variant"],
            "error": "command-code executable not found",
        }
    if not refresh:
        return {
            "ok": True, "backend": BACKEND_COMMANDCODE,
            "provider": meta["provider"], "models": fallback,
            "default_model": meta["model"], "default_variant": meta["variant"],
            "error": None,
            "hint": "live list not loaded — Command Code is slow to start; click refresh",
        }
    cmd = argv + ["--list-models", "--skip-onboarding", "--no-auto-update"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False, "backend": BACKEND_COMMANDCODE,
            "provider": meta["provider"], "models": fallback,
            "default_model": meta["model"], "default_variant": meta["variant"],
            "error": f"{type(exc).__name__}: {exc}",
        }
    models = _parse_commandcode_models(
        (proc.stdout or "") + "\n" + (proc.stderr or ""),
        list(meta["effort_variants"]))
    if not models:
        err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        return {
            "ok": False, "backend": BACKEND_COMMANDCODE,
            "provider": meta["provider"], "models": fallback,
            "default_model": meta["model"], "default_variant": meta["variant"],
            "error": f"no models parsed ({err})",
        }
    ids = {m["id"] for m in models}
    default_model = meta["model"] if meta["model"] in ids else models[0]["id"]
    variants = next((m["variants"] for m in models if m["id"] == default_model),
                    list(meta["effort_variants"]))
    return {
        "ok": True, "backend": BACKEND_COMMANDCODE,
        "provider": meta["provider"], "models": models,
        "default_model": default_model,
        "default_variant": preferred_variant(variants, meta["variant"]),
        "error": None,
    }


def _list_claude_models(refresh: bool, timeout: float) -> dict:
    meta = backend_meta(BACKEND_CLAUDE)
    models = _fallback_models(BACKEND_CLAUDE)
    argv = find_claude_argv()
    default_model = meta["model"]
    variants = next((m["variants"] for m in models if m["id"] == default_model),
                    list(meta["effort_variants"]))
    return {
        "ok": True,
        "backend": BACKEND_CLAUDE,
        "provider": meta["provider"],
        "models": models,
        "default_model": default_model,
        "default_variant": preferred_variant(variants, meta["variant"]),
        "error": None,
    }


def _list_codex_models(refresh: bool, timeout: float) -> dict:
    meta = backend_meta(BACKEND_CODEX)
    models = _fallback_models(BACKEND_CODEX)
    argv = find_codex_argv()
    default_model = meta["model"]
    variants = next((m["variants"] for m in models if m["id"] == default_model),
                    list(meta["effort_variants"]))
    return {
        "ok": True,
        "backend": BACKEND_CODEX,
        "provider": meta["provider"],
        "models": models,
        "default_model": default_model,
        "default_variant": preferred_variant(variants, meta["variant"]),
        "error": None,
    }


def list_models(provider: str | None = None, refresh: bool = False,
                timeout: float | None = None, backend: str | None = None) -> dict:
    """Discover models for a backend.

    Returns {"ok", "backend", "provider", "models", "default_model",
    "default_variant", "error"}. Falls back to the hardcoded default when the
    CLI fails. Cached so UI startup stays snappy. Command Code's CLI is slow
    to start, so a live `--list-models` runs only when refresh=True.
    """
    backend = normalize_backend(backend)
    meta = backend_meta(backend)
    provider = provider or meta["provider"]
    cache_key = f"{backend}:{provider}"
    ttl = (_COMMANDCODE_MODELS_TTL if backend == BACKEND_COMMANDCODE
           else _MODELS_CACHE_TTL)
    cached = _cache_get(cache_key, ttl, refresh)
    if cached is not None:
        return cached
    if backend == BACKEND_COMMANDCODE:
        result = _list_commandcode_models(
            refresh=refresh, timeout=timeout if timeout is not None else 180.0)
    elif backend == BACKEND_CLAUDE:
        result = _list_claude_models(
            refresh=refresh, timeout=timeout if timeout is not None else 30.0)
    elif backend == BACKEND_CODEX:
        result = _list_codex_models(
            refresh=refresh, timeout=timeout if timeout is not None else 30.0)
    else:
        result = _list_opencode_models(
            provider=provider, refresh=refresh,
            timeout=timeout if timeout is not None else 45.0)
    return _cache_put(cache_key, result)


def resolve_model_choice(model: str | None = None,
                         variant: str | None = None,
                         catalog: dict | None = None,
                         backend: str | None = None) -> tuple[str, str]:
    """Validate / normalize a UI model+variant choice against the catalog."""
    backend = normalize_backend(backend or (catalog or {}).get("backend"))
    meta = backend_meta(backend)
    catalog = catalog or list_models(backend=backend)
    models = {m["id"]: m for m in catalog.get("models") or []}
    chosen = (model or "").strip() or catalog.get("default_model") or meta["model"]
    if chosen not in models and "/" not in chosen and models:
        prefixed = f"{catalog.get('provider') or meta['provider']}/{chosen}"
        if prefixed in models:
            chosen = prefixed
    row = models.get(chosen)
    if row is None:
        # Unknown to catalog — still allow explicit provider/model strings.
        if "/" not in chosen and backend == BACKEND_OPENCODE:
            chosen = catalog.get("default_model") or meta["model"]
            row = models.get(chosen)
    variants = list((row or {}).get("variants") or [])
    raw_variant = "" if variant is None else str(variant).strip()
    if not variants:
        return chosen, ""
    if raw_variant and raw_variant in variants:
        return chosen, raw_variant
    return chosen, preferred_variant(variants, raw_variant or meta["variant"])


def skill_path(agent: str) -> Path:
    return TOOLKIT / f"SKILL_agent{agent}_extractor.md" if agent == 1 else (
        TOOLKIT / f"SKILL_agent{agent}_enricher.md" if agent == 2 else
        TOOLKIT / f"SKILL_agent{agent}_formatter.md")


def _natural_key(p: Path):
    import re
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', p.name)]


def list_transcripts() -> list[dict]:
    subjects = all_subjects()
    out = []
    if TRANSCRIPTS_DIR.is_dir():
        for p in sorted(TRANSCRIPTS_DIR.glob("*.txt"), key=_natural_key):
            guess = guess_from_filename(p.name, subjects)
            out.append({
                "name": p.name, "path": str(p), "bytes": p.stat().st_size,
                "guess": guess,
            })
    return out


def list_outputs(subject: str, prefix: str) -> list[dict]:
    base = confine(OUTPUTS_DIR, subject, prefix)
    if not base or not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            rel = p.relative_to(base).as_posix()
            out.append({"name": rel, "path": str(p), "bytes": p.stat().st_size})
    return out


def _norm_key(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def abbr_for_subject(subject: str) -> str:
    for abbr, name in all_subjects().items():
        if name == subject:
            return abbr
    return ""


def guess_from_filename(filename: str,
                        subjects: dict[str, str] | None = None) -> dict:
    """Infer subject abbr, lecture number, and lecture prefix from a
    transcript filename.

    Examples:
      'Data_Management_for_Machine_Learning - Lecture 1.txt'
          -> DMML, lecture 1, DMML_Lecture_1
      'NLP_Lecture_9.txt' -> NLP, 9, NLP_Lecture_9
    """
    import re
    subjects = subjects or all_subjects()
    stem = Path(filename).stem
    lecture = ""
    m = re.search(r"(?:lecture|lec|l)[_\s.\-]*(\d+)\s*$", stem, re.I)
    if not m:
        m = re.search(r"[_\s.\-](\d+)\s*$", stem)
    if m:
        lecture = str(int(m.group(1)))

    abbr = ""
    score = 0
    # Prefer an explicit abbreviation prefix (NLP_..., DMML-...).
    for a in subjects:
        if re.match(rf"^{re.escape(a)}(?:[_\W]|$)", stem, re.I):
            abbr, score = a, 10_000
            break
    if not abbr:
        norm_stem = _norm_key(stem)
        for a, name in subjects.items():
            key = _norm_key(name)
            if key and key in norm_stem and len(key) > score:
                abbr, score = a, len(key)

    prefix = ""
    if abbr and lecture:
        prefix = f"{abbr}_Lecture_{lecture}"
    elif abbr:
        prefix = f"{abbr}_Lecture"
    return {"abbr": abbr, "lecture_num": lecture, "prefix": prefix,
            "subject": subjects.get(abbr, "")}


def output_paths(subject: str, prefix: str) -> dict[str, Path]:
    base = confine(OUTPUTS_DIR, subject, prefix)
    if base is None:
        raise ValueError("invalid subject or prefix")
    return {
        "base": base,
        "dense": base / f"{prefix}_notes_dense.md",
        "manifest": base / f"{prefix}_extraction_manifest.json",
        "enriched": base / f"{prefix}_notes_enriched.md",
        "html": base / f"{prefix}_notes" / f"{prefix}_notes.html",
        "events": base / f"{prefix}_run_events.jsonl",
    }


def artifacts_status(subject: str, prefix: str) -> dict:
    """Which phase artifacts exist, and which phases to run to resume."""
    try:
        paths = output_paths(subject, prefix)
    except ValueError:
        return {
            "subject": subject, "prefix": prefix, "abbr": "",
            "artifacts": {"extractor": False, "enricher": False,
                          "formatter": False},
            "files": {}, "resume_phases": [1, 2, 3],
            "resume_label": "start from extractor", "html_url": None,
            "events_path": None, "error": "invalid subject or prefix",
        }
    has_extractor = paths["dense"].is_file() and paths["manifest"].is_file()
    has_enricher = paths["enriched"].is_file()
    has_formatter = paths["html"].is_file()
    if not has_extractor:
        resume = [1, 2, 3]
        label = "start from extractor"
    elif not has_enricher:
        resume = [2, 3]
        label = "resume from enricher"
    elif not has_formatter:
        resume = [3]
        label = "resume from formatter"
    else:
        resume = []
        label = "complete"

    def _meta(p: Path) -> dict | None:
        if not p.is_file():
            return None
        st = p.stat()
        return {"name": p.name, "bytes": st.st_size, "mtime": st.st_mtime}

    return {
        "subject": subject,
        "prefix": prefix,
        "abbr": abbr_for_subject(subject),
        "artifacts": {
            "extractor": has_extractor,
            "enricher": has_enricher,
            "formatter": has_formatter,
        },
        "files": {
            "dense": _meta(paths["dense"]),
            "manifest": _meta(paths["manifest"]),
            "enriched": _meta(paths["enriched"]),
            "html": _meta(paths["html"]),
        },
        "resume_phases": resume,
        "resume_label": label,
        "html_url": (
            f"/outputs/{urllib_quote(subject)}/{urllib_quote(prefix)}/"
            f"{urllib_quote(prefix + '_notes')}/{urllib_quote(prefix + '_notes.html')}"
            if has_formatter else None),
        "events_path": str(paths["events"]) if paths["events"].is_file() else None,
    }


def urllib_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def _empty_token_bucket() -> dict:
    return {"input": 0, "output": 0, "reasoning": 0, "cost": 0.0}


def summarize_run_events(events_path: Path, subject: str = "",
                         prefix: str = "") -> dict:
    """Fold a run_events.jsonl into a history-friendly summary."""
    summary = {
        "subject": subject,
        "prefix": prefix,
        "abbr": abbr_for_subject(subject) if subject else "",
        "status": "unknown",
        "error": None,
        "transcript": None,
        "lecture_num": "",
        "phases_requested": [],
        "started_at": None,
        "ended_at": None,
        "mtime": events_path.stat().st_mtime if events_path.is_file() else 0,
        "seconds": 0.0,
        "cost": 0.0,
        "tokens": _empty_token_bucket(),
        "phase_stats": {},
        "last_failed_phase": None,
        "model": None,
        "variant": None,
        "backend": None,
        "current_phase": None,
    }
    phase_secs: dict[str, float] = {}
    phase_cost: dict[str, float] = {}
    phase_tok: dict[str, dict] = {}
    current_phase = None
    if not events_path.is_file():
        return summary
    try:
        with open(events_path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                et = ev.get("type")
                if et == "pipeline_start":
                    summary["subject"] = ev.get("subject") or summary["subject"]
                    summary["prefix"] = ev.get("prefix") or summary["prefix"]
                    summary["abbr"] = (abbr_for_subject(summary["subject"])
                                       or summary["abbr"])
                    summary["transcript"] = ev.get("transcript")
                    summary["lecture_num"] = str(ev.get("lecture_num") or "")
                    summary["phases_requested"] = ev.get("phases") or []
                    summary["model"] = ev.get("model")
                    summary["variant"] = ev.get("variant")
                    summary["backend"] = ev.get("backend")
                    summary["started_at"] = ev.get("time")
                    summary["status"] = "running"
                elif et == "phase_start":
                    current_phase = ev.get("phase")
                    summary["current_phase"] = current_phase
                    if current_phase and current_phase not in phase_tok:
                        phase_tok[current_phase] = _empty_token_bucket()
                        phase_cost[current_phase] = 0.0
                elif et == "phase_end":
                    ph = ev.get("phase") or current_phase
                    if ph:
                        phase_secs[ph] = float(ev.get("seconds") or 0)
                        if not ev.get("ok"):
                            summary["last_failed_phase"] = ph
                            summary["current_phase"] = ph
                        elif summary.get("current_phase") == ph:
                            summary["current_phase"] = None
                elif et == "agent_event":
                    aev = ev.get("event") or {}
                    if aev.get("type") == "step_finish":
                        part = aev.get("part") or {}
                        toks = part.get("tokens") or {}
                        cost = float(part.get("cost") or 0)
                        ph = ev.get("phase") or current_phase or "_"
                        bucket = phase_tok.setdefault(ph, _empty_token_bucket())
                        for k in ("input", "output", "reasoning"):
                            bucket[k] += int(toks.get(k) or 0)
                            summary["tokens"][k] += int(toks.get(k) or 0)
                        bucket["cost"] = float(bucket.get("cost") or 0) + cost
                        phase_cost[ph] = float(phase_cost.get(ph) or 0) + cost
                        summary["cost"] += cost
                elif et == "pipeline_end":
                    summary["status"] = ev.get("status") or summary["status"]
                    summary["error"] = ev.get("error")
                    summary["ended_at"] = ev.get("time")
                    if summary["status"] in ("done", "stopped", "error"):
                        if summary["status"] != "error":
                            summary["current_phase"] = None
                    stats = ev.get("stats") or {}
                    if stats:
                        if "cost" in stats:
                            summary["cost"] = float(stats["cost"] or 0)
                        if "tokens" in stats:
                            summary["tokens"] = {
                                **_empty_token_bucket(),
                                **(stats.get("tokens") or {}),
                            }
                        if "seconds" in stats:
                            summary["seconds"] = float(stats["seconds"] or 0)
                        if "phases" in stats:
                            summary["phase_stats"] = stats["phases"]
    except OSError:
        pass

    if not summary["phase_stats"]:
        for ph, secs in phase_secs.items():
            summary["phase_stats"][ph] = {
                "seconds": secs,
                "cost": round(phase_cost.get(ph, 0.0), 6),
                "tokens": phase_tok.get(ph, _empty_token_bucket()),
            }
    if not summary["seconds"]:
        summary["seconds"] = round(sum(phase_secs.values()), 1)
        if summary["started_at"] and summary["ended_at"]:
            summary["seconds"] = round(
                float(summary["ended_at"]) - float(summary["started_at"]), 1)

    arts = artifacts_status(summary["subject"], summary["prefix"]) \
        if summary["subject"] and summary["prefix"] else None
    if arts:
        summary["artifacts"] = arts["artifacts"]
        summary["resume_phases"] = arts["resume_phases"]
        summary["resume_label"] = arts["resume_label"]
        summary["html_url"] = arts["html_url"]
        # Fallback: if all artifacts exist on disk, infer status as "done" if it was unknown or stale
        if not arts["resume_phases"] and summary["status"] in ("unknown", "running", None):
            summary["status"] = "done"
        # Retry = failed phase + everything after it, else resume suggestion.
        failed = summary.get("last_failed_phase")
        phase_nums = {"extractor": 1, "enricher": 2, "formatter": 3}
        if failed and failed in phase_nums and summary["status"] == "error":
            start = phase_nums[failed]
            summary["retry_phases"] = [n for n in (1, 2, 3) if n >= start]
            summary["retry_label"] = f"retry from {failed}"
        else:
            summary["retry_phases"] = arts["resume_phases"]
            summary["retry_label"] = arts["resume_label"]
    else:
        summary["artifacts"] = {}
        summary["resume_phases"] = [1, 2, 3]
        summary["resume_label"] = "start from extractor"
        summary["retry_phases"] = [1, 2, 3]
        summary["retry_label"] = "start from extractor"
        summary["html_url"] = None

    summary["cost"] = round(float(summary["cost"] or 0), 6)
    return summary


def read_run_events(subject: str, prefix: str, offset: int = 0,
                    limit: int = 1500) -> dict:
    """Page events from outputs/<subject>/<prefix>/*_run_events.jsonl."""
    try:
        events_path = output_paths(subject, prefix)["events"]
    except ValueError:
        return {"error": "invalid subject or prefix", "events": [], "total": 0}
    if not events_path.is_file():
        alts = sorted(events_path.parent.glob("*_run_events.jsonl")) if events_path.parent.is_dir() else []
        if not alts:
            return {"events": [], "total": 0, "path": None}
        events_path = alts[-1]
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 1500), 4000))
    events: list[dict] = []
    total = 0
    try:
        with open(events_path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if offset <= total < offset + limit:
                    events.append(ev)
                total += 1
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "events": [], "total": 0}
    return {
        "events": events,
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": total > offset + len(events),
        "path": str(events_path),
    }


def list_past_runs(limit: int = 60) -> list[dict]:
    """Scan outputs/*/ for run_events.jsonl and return newest-first summaries."""
    if not OUTPUTS_DIR.is_dir():
        return []
    found: list[tuple[float, Path, str, str]] = []
    for subject_dir in OUTPUTS_DIR.iterdir():
        if not subject_dir.is_dir() or subject_dir.name.startswith("_"):
            continue
        for prefix_dir in subject_dir.iterdir():
            if not prefix_dir.is_dir():
                continue
            events = prefix_dir / f"{prefix_dir.name}_run_events.jsonl"
            if not events.is_file():
                # Fall back to any run-events file in the folder.
                alts = sorted(prefix_dir.glob("*_run_events.jsonl"))
                if not alts:
                    continue
                events = alts[-1]
            try:
                mtime = events.stat().st_mtime
            except OSError:
                continue
            found.append((mtime, events, subject_dir.name, prefix_dir.name))
    found.sort(key=lambda t: t[0], reverse=True)
    return [
        summarize_run_events(path, subject, prefix)
        for _, path, subject, prefix in found[:limit]
    ]


def delete_past_run(subject: str, prefix: str, archive: bool = False) -> dict:
    """Delete or archive an outputs/<subject>/<prefix>/ folder.

    Archive moves it under outputs/_archive/<subject>/<prefix>_<utc>/.
    """
    import shutil
    from datetime import datetime, timezone

    subject = (subject or "").strip()
    prefix = (prefix or "").strip()
    if not subject or not prefix:
        raise ValueError("subject and prefix required")
    if subject.startswith("_"):
        raise ValueError("invalid subject or prefix")
    base = confine(OUTPUTS_DIR, subject, prefix)
    if base is None:
        raise ValueError("invalid subject or prefix")
    try:
        base.relative_to(OUTPUTS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("path escapes outputs dir") from exc
    if not base.is_dir():
        raise ValueError(f"no output folder for {subject}/{prefix}")

    if archive:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest_root = OUTPUTS_DIR / "_archive" / subject
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / f"{prefix}_{stamp}"
        if dest.exists():
            raise ValueError(f"archive target already exists: {dest.name}")
        shutil.move(str(base), str(dest))
        return {"action": "archived", "path": str(dest),
                "subject": subject, "prefix": prefix}

    shutil.rmtree(base)
    # Remove empty subject folder.
    parent = base.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    return {"action": "deleted", "subject": subject, "prefix": prefix}
