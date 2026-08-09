"""Static configuration for the transcript-notes automation.

Everything is relative to the workspace root (E:\\agent_warden), which is also
the working directory for every opencode run so that the toolkit's own
instructions (outputs/, topic_mappings/, extracted_pdfs/ paths) resolve as-is.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
from pathlib import Path

WORKSPACE = Path(os.environ.get("NOTES_WORKSPACE", r"E:\agent_warden")).resolve()
TOOLKIT = WORKSPACE / "make-transcript-notes-kit-3agent"
TRANSCRIPTS_DIR = WORKSPACE / "transcript files"
OUTPUTS_DIR = WORKSPACE / "outputs"
TOPIC_MAPPINGS_DIR = WORKSPACE / "topic_mappings"
DOCS_DIR = (WORKSPACE / "companion_docs"
            if (WORKSPACE / "companion_docs").is_dir()
            else WORKSPACE / "extracted_pdfs")

MODEL = "opencode-go/deepseek-v4-flash"
VARIANT = "max"
MODEL_PROVIDER = "opencode-go"
MAX_FIX_ROUNDS = 3          # 1 initial attempt + up to 2 fix sessions per phase
PHASE_TIMEOUT_SECONDS = 6 * 60 * 60  # generous ceiling; notes take a while
# Prefer higher effort when falling back from the default "max".
_VARIANT_RANK = ("none", "minimal", "low", "medium", "high", "xhigh",
                 "thinking", "max")

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
    subjects = all_subjects()
    subjects[abbr] = name
    data = {a: n for a, n in sorted(subjects.items())
            if a not in SUBJECTS}
    SUBJECTS_FILE.write_text(json.dumps(data, indent=2) + "\n",
                             encoding="utf-8")
    (DOCS_DIR / abbr).mkdir(parents=True, exist_ok=True)
    yaml_path = TOPIC_MAPPINGS_DIR / f"{name}.yaml"
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
_MODELS_CACHE: dict | None = None
_MODELS_CACHE_AT: float = 0.0
_MODELS_CACHE_TTL = 300.0  # seconds


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


def list_models(provider: str | None = None, refresh: bool = False,
                timeout: float = 45.0) -> dict:
    """Discover models (and reasoning variants) via `opencode models`.

    Returns {"ok", "provider", "models", "default_model", "default_variant",
    "error"}. Falls back to the hardcoded default when the CLI fails.
    Cached briefly so UI startup /api/config stays snappy.
    """
    import subprocess
    import time as _time

    global _MODELS_CACHE, _MODELS_CACHE_AT
    provider = provider or MODEL_PROVIDER
    now = _time.time()
    if (not refresh and _MODELS_CACHE is not None
            and _MODELS_CACHE.get("provider") == provider
            and (now - _MODELS_CACHE_AT) < _MODELS_CACHE_TTL):
        return _MODELS_CACHE

    fallback = [{
        "id": MODEL,
        "name": "DeepSeek V4 Flash",
        "family": "deepseek-flash",
        "status": "active",
        "reasoning": True,
        "variants": ["low", "high", "max"],
        "cost": {},
        "limit": {},
    }]
    exe = find_opencode()
    if not exe:
        result = {
            "ok": False, "provider": provider, "models": fallback,
            "default_model": MODEL, "default_variant": VARIANT,
            "error": "opencode executable not found",
        }
        _MODELS_CACHE, _MODELS_CACHE_AT = result, now
        return result
    cmd = [exe, "models", provider, "--verbose"]
    if refresh:
        cmd.append("--refresh")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {
            "ok": False, "provider": provider, "models": fallback,
            "default_model": MODEL, "default_variant": VARIANT,
            "error": f"{type(exc).__name__}: {exc}",
        }
        _MODELS_CACHE, _MODELS_CACHE_AT = result, now
        return result
    models = _parse_models_verbose(proc.stdout or "")
    # Prefer active models; keep unknowns if status missing.
    models = [m for m in models
              if (m.get("status") or "active") in {"active", "unknown"}]
    if not models:
        err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        result = {
            "ok": False, "provider": provider, "models": fallback,
            "default_model": MODEL, "default_variant": VARIANT,
            "error": f"no models parsed ({err})",
        }
        _MODELS_CACHE, _MODELS_CACHE_AT = result, now
        return result
    ids = {m["id"] for m in models}
    default_model = MODEL if MODEL in ids else models[0]["id"]
    variants = next((m["variants"] for m in models if m["id"] == default_model),
                    [])
    default_variant = preferred_variant(variants, VARIANT)
    result = {
        "ok": True, "provider": provider, "models": models,
        "default_model": default_model, "default_variant": default_variant,
        "error": None,
    }
    _MODELS_CACHE, _MODELS_CACHE_AT = result, now
    return result


def resolve_model_choice(model: str | None = None,
                         variant: str | None = None,
                         catalog: dict | None = None) -> tuple[str, str]:
    """Validate / normalize a UI model+variant choice against the catalog."""
    catalog = catalog or list_models()
    models = {m["id"]: m for m in catalog.get("models") or []}
    chosen = (model or "").strip() or catalog.get("default_model") or MODEL
    if chosen not in models and "/" not in chosen and models:
        # Allow bare model id when provider is implied.
        prefixed = f"{catalog.get('provider') or MODEL_PROVIDER}/{chosen}"
        if prefixed in models:
            chosen = prefixed
    meta = models.get(chosen)
    if meta is None:
        # Unknown to catalog — still allow explicit provider/model strings.
        if "/" not in chosen:
            chosen = catalog.get("default_model") or MODEL
            meta = models.get(chosen)
    variants = list((meta or {}).get("variants") or [])
    raw_variant = "" if variant is None else str(variant).strip()
    if not variants:
        return chosen, ""
    if raw_variant and raw_variant in variants:
        return chosen, raw_variant
    return chosen, preferred_variant(variants, raw_variant or VARIANT)


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
    base = OUTPUTS_DIR / subject / prefix
    if not base.is_dir():
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
    base = OUTPUTS_DIR / subject / prefix
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
    paths = output_paths(subject, prefix)
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
                    summary["started_at"] = ev.get("time")
                    summary["status"] = "running"
                elif et == "phase_start":
                    current_phase = ev.get("phase")
                    if current_phase and current_phase not in phase_tok:
                        phase_tok[current_phase] = _empty_token_bucket()
                        phase_cost[current_phase] = 0.0
                elif et == "phase_end":
                    ph = ev.get("phase") or current_phase
                    if ph:
                        phase_secs[ph] = float(ev.get("seconds") or 0)
                        if not ev.get("ok"):
                            summary["last_failed_phase"] = ph
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
    if subject.startswith("_") or ".." in subject or ".." in prefix:
        raise ValueError("invalid subject or prefix")
    base = (OUTPUTS_DIR / subject / prefix).resolve()
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
