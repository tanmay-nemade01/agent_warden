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
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .paths import confine, is_safe_component, rel_glob, resolve_configured

_DEFAULT_WORKSPACE = Path(__file__).resolve().parents[2]
WORKSPACE = Path(os.environ.get("NOTES_WORKSPACE", str(_DEFAULT_WORKSPACE))).resolve()
TOOLKIT = WORKSPACE / "make-transcript-notes-kit-3agent"
TRANSCRIPTS_DIR = WORKSPACE / "transcript files"
OUTPUTS_DIR = WORKSPACE / "outputs"
TOPIC_MAPPINGS_DIR = WORKSPACE / "topic_mappings"
DOCS_DIR = (WORKSPACE / "companion_docs"
            if (WORKSPACE / "companion_docs").is_dir()
            else WORKSPACE / "extracted_pdfs")

def _load_env_file():
    for cand in [WORKSPACE / ".env", _DEFAULT_WORKSPACE / ".env", Path.cwd() / ".env"]:
        if cand.is_file():
            try:
                for line in cand.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass

_load_env_file()

BACKEND_OPENCODE = "opencode"
BACKEND_COMMANDCODE = "commandcode"
BACKEND_CLAUDE = "claude"
BACKEND_CODEX = "codex"
BACKEND_REASONIX = "reasonix"
BACKEND_PI = "pi"
BACKEND_ANTIGRAVITY = "antigravity"
BACKEND_CURSOR = "cursor"
DEFAULT_BACKEND = BACKEND_OPENCODE

BACKENDS = {
    BACKEND_OPENCODE: {
        "id": BACKEND_OPENCODE,
        "label": "OpenCode",
        "model": "opencode-go/deepseek-v4.1-flash",
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
        "model": "gpt-5.4-mini",
        "variant": "",
        "provider": "openai",
        "effort_variants": [],
    },
    BACKEND_REASONIX: {
        "id": BACKEND_REASONIX,
        "label": "Reasonix",
        "model": "deepseek/deepseek-v4-flash",
        "variant": "high",
        "provider": "deepseek",
        "effort_variants": ["low", "medium", "high", "max"],
    },
    BACKEND_PI: {
        "id": BACKEND_PI,
        "label": "Pi Harness",
        "model": "deepseek/deepseek-v4-flash",
        "variant": "high",
        "provider": "openrouter",
        "effort_variants": ["low", "medium", "high", "max"],
    },
    BACKEND_ANTIGRAVITY: {
        "id": BACKEND_ANTIGRAVITY,
        "label": "Antigravity",
        "model": "gemini-3.8-flash",
        "variant": "high",
        "provider": "google",
        "effort_variants": ["low", "medium", "high", "max"],
    },
    BACKEND_CURSOR: {
        "id": BACKEND_CURSOR,
        "label": "Cursor Agent",
        "model": "composer-2.5",
        "variant": "high",
        "provider": "cursor",
        "effort_variants": ["low", "medium", "high", "max"],
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
# Kill an agent phase if it produces no output for this long. Healthy runs
# stream events continuously; the longest observed quiet stretch (a single
# max-effort thinking block) was ~11 min. A hung provider request emits
# nothing for 60+ min (idle TCP connection, no client-side timeout), so 15
# minutes separates legitimate thinking from a dead session.
PHASE_STALL_TIMEOUT_SECONDS = 15 * 60
COMMANDCODE_MAX_TURNS = 250
CLAUDE_MAX_TURNS = 250
CODEX_MAX_TURNS = 250
REASONIX_MAX_TURNS = 250
PI_MAX_TURNS = 250
ANTIGRAVITY_MAX_TURNS = 250
CURSOR_MAX_TURNS = 250
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
    if raw in {"reasonix", "rx", "deepseekreasonix"}:
        return BACKEND_REASONIX
    if raw in {"pi", "piharness", "picodingagent"}:
        return BACKEND_PI
    if raw in {"antigravity", "agy", "googleantigravity", "gemini"}:
        return BACKEND_ANTIGRAVITY
    if raw in {"cursor", "cursoragent", "cursorcli", "anysphere"}:
        return BACKEND_CURSOR
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
SUBJECT_INSTRUCTIONS_FILE = WORKSPACE / "subject_instructions.json"


def all_subject_instructions() -> dict[str, dict[str, str]]:
    """Return all stored extra instructions per subject abbreviation.
    Format: { "ABBR": { "extractor": "...", "enricher": "...", "formatter": "..." } }
    """
    if not SUBJECT_INSTRUCTIONS_FILE.is_file():
        return {}
    try:
        data = json.loads(SUBJECT_INSTRUCTIONS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        cleaned: dict[str, dict[str, str]] = {}
        for k, v in data.items():
            if isinstance(k, str) and isinstance(v, dict):
                k_upper = k.strip().upper()
                phase_map: dict[str, str] = {}
                for phase, inst in v.items():
                    norm_phase = str(phase).strip().lower()
                    norm_phase = {"1": "extractor", "2": "enricher", "3": "formatter"}.get(norm_phase, norm_phase)
                    if norm_phase in ("extractor", "enricher", "formatter") and isinstance(inst, str) and inst.strip():
                        phase_map[norm_phase] = inst.strip()
                if phase_map:
                    cleaned[k_upper] = phase_map
        return cleaned
    except (OSError, ValueError):
        return {}


def get_subject_instructions(abbr: str) -> dict[str, str]:
    """Get extra instructions for a specific subject abbreviation."""
    if not abbr:
        return {}
    abbr = abbr.strip().upper()
    return all_subject_instructions().get(abbr, {})


def save_subject_instructions(abbr: str, instructions: dict[str, str]) -> dict[str, dict[str, str]]:
    """Persist extra instructions for a subject abbreviation.
    If all instructions for the subject are empty, removes the subject entry.
    """
    abbr = abbr.strip().upper()
    if not is_safe_component(abbr):
        raise ValueError("subject abbreviation is not a safe path component")
    all_inst = all_subject_instructions()
    phase_map: dict[str, str] = {}
    for phase, inst in (instructions or {}).items():
        norm_phase = str(phase).strip().lower()
        norm_phase = {"1": "extractor", "2": "enricher", "3": "formatter"}.get(norm_phase, norm_phase)
        if norm_phase in ("extractor", "enricher", "formatter") and isinstance(inst, str) and inst.strip():
            phase_map[norm_phase] = inst.strip()
    if phase_map:
        all_inst[abbr] = phase_map
    else:
        all_inst.pop(abbr, None)
    try:
        SUBJECT_INSTRUCTIONS_FILE.write_text(
            json.dumps(all_inst, indent=2) + "\n",
            encoding="utf-8"
        )
    except OSError:
        pass
    return all_inst



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
    subjects = all_subjects()
    subjects[abbr] = name
    data = {a: n for a, n in sorted(subjects.items())
            if a not in SUBJECTS}
    SUBJECTS_FILE.write_text(json.dumps(data, indent=2) + "\n",
                             encoding="utf-8")
    # The subject now resolves, so honour any profile it already has.
    prof = subject_profile(abbr)
    docs_folder = prof.resolve_docs() or confine(DOCS_DIR, abbr)
    yaml_path = prof.resolve_topic_mapping() or confine(
        TOPIC_MAPPINGS_DIR, f"{name}.yaml")
    if docs_folder is not None:
        docs_folder.mkdir(parents=True, exist_ok=True)
    if yaml_path is not None and not yaml_path.is_file():
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(f'subject_name: "{name}"\nlectures: []\n',
                             encoding="utf-8")
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


# ---------------------------------------------------------------------------
# Subject profiles
# ---------------------------------------------------------------------------
# A profile binds one subject to the places that differ from subject to
# subject: where its transcripts come from, where its companion docs live,
# how its output folder is named, and how a lecture prefix is spelled.
# Everything else in the pipeline is shared.
#
#   subject_profiles.json
#   {
#     "QC": {
#       "transcripts_dir": "transcript files/QC",
#       "docs_dir":        "companion_docs/QC",
#       "output_root":     "outputs",
#       "output_template": "{subject}/{prefix}",
#       "prefix_template": "{abbr}_Lecture_{lecture}"
#     }
#   }
#
# Every field is optional. A subject without an entry keeps the legacy
# behaviour exactly: transcripts from the shared flat dir, docs auto-detected
# under companion_docs/, outputs at outputs/<Subject Name>/<Prefix>/.

SUBJECT_PROFILES_FILE = WORKSPACE / "subject_profiles.json"

DEFAULT_OUTPUT_TEMPLATE = "{subject}/{prefix}"
DEFAULT_PREFIX_TEMPLATE = "{abbr}_Lecture_{lecture}"

#: Placeholders understood by ``output_template``.
OUTPUT_TEMPLATE_FIELDS = ("subject", "abbr", "prefix")
#: Placeholders understood by ``prefix_template``.
PREFIX_TEMPLATE_FIELDS = ("subject", "abbr", "lecture")


@dataclass(frozen=True)
class SubjectProfile:
    """Per-subject binding of transcripts, docs, outputs and prefix naming."""

    abbr: str
    name: str
    transcripts_dir: Path
    output_root: Path
    output_template: str = DEFAULT_OUTPUT_TEMPLATE
    prefix_template: str = DEFAULT_PREFIX_TEMPLATE
    docs_dir: Path | None = None
    topic_mapping: Path | None = None
    explicit: bool = False

    # ------------------------------------------------------------- naming
    def prefix_for(self, lecture_num: str | None) -> str:
        """Build a lecture prefix from the profile's template.

        ``{lecture}`` collapses cleanly when no lecture number is known,
        so ``{abbr}_Lecture_{lecture}`` yields ``QC_Lecture``.
        """
        return self.prefix_for_fields(str(lecture_num or "").strip())

    def prefix_for_fields(self, lecture_num: str) -> str:
        tpl = self.prefix_template or DEFAULT_PREFIX_TEMPLATE
        try:
            out = tpl.format(subject=self.name, abbr=self.abbr,
                             lecture=str(lecture_num or ""))
        except (KeyError, IndexError, ValueError):
            return f"{self.abbr}_Lecture_{lecture_num}".rstrip("_")
        out = re.sub(r"\s+", "_", out).strip("_-. ")
        return out or f"{self.abbr}_Lecture_{lecture_num}".rstrip("_")

    def output_rel(self, prefix: str, subject: str | None = None) -> str | None:
        """Expand the output template into a path relative to the root.

        Returns ``None`` when the template expands to something unsafe
        (empty segment, separator inside a segment, ``..``).
        """
        name = subject if subject is not None else self.name
        tpl = self.output_template or DEFAULT_OUTPUT_TEMPLATE
        try:
            raw = tpl.format(subject=name, abbr=self.abbr, prefix=prefix)
        except (KeyError, IndexError, ValueError):
            return None
        parts = [p.strip() for p in raw.replace("\\", "/").split("/")]
        parts = [p for p in parts if p not in ("", ".")]
        if not parts or any(not is_safe_component(p) for p in parts):
            return None
        return "/".join(parts)

    def output_dir(self, prefix: str, subject: str | None = None) -> Path | None:
        """Absolute output directory for one lecture, confined to the root."""
        rel = self.output_rel(prefix, subject=subject)
        if rel is None:
            return None
        try:
            root = Path(self.output_root).resolve()
        except (OSError, RuntimeError):
            return None
        joined = root.joinpath(*rel.split("/"))
        try:
            resolved = joined.resolve()
            resolved.relative_to(root)
        except (ValueError, OSError, RuntimeError):
            return None
        return resolved

    # -------------------------------------------------------------- paths
    def resolve_docs(self) -> Path | None:
        """The companion-docs folder for this subject.

        An explicit ``docs_dir`` wins; otherwise fall back to the
        auto-detected ``companion_docs/<abbr|name>`` folder.
        """
        if self.docs_dir is not None:
            return self.docs_dir if self.docs_dir.is_dir() else None
        return default_docs_dir(self.abbr, self.name)

    def resolve_topic_mapping(self) -> Path | None:
        """The topic-mapping YAML this subject's lectures update."""
        if self.topic_mapping is not None:
            return self.topic_mapping
        candidate = TOPIC_MAPPINGS_DIR / f"{self.name}.yaml"
        return candidate

    def write_roots(self) -> list[str]:
        """Workspace-relative globs this subject's agents may write to.

        The lecture output tree plus the topic-mapping folder (Agent 2
        updates that YAML). Both are workspace-relative because the agent
        runs with the workspace as its working directory.
        """
        roots: list[str] = []
        topic_dir = (self.topic_mapping.parent if self.topic_mapping
                     else TOPIC_MAPPINGS_DIR)
        for path in (self.output_root, topic_dir):
            if path is None:
                continue
            rel = rel_glob(WORKSPACE, path)
            if rel and rel != ".":
                roots.append(f"{rel}/**")
        if not roots:
            roots = ["outputs/**", "topic_mappings/**"]
        return roots


def _default_profile(abbr: str, name: str) -> SubjectProfile:
    return SubjectProfile(
        abbr=abbr, name=name,
        transcripts_dir=TRANSCRIPTS_DIR,
        output_root=OUTPUTS_DIR,
        output_template=DEFAULT_OUTPUT_TEMPLATE,
        prefix_template=DEFAULT_PREFIX_TEMPLATE,
        docs_dir=None, topic_mapping=None, explicit=False)


def _load_profile_entries() -> dict[str, dict]:
    """Raw per-subject overrides from ``subject_profiles.json``."""
    if not SUBJECT_PROFILES_FILE.is_file():
        return {}
    try:
        data = json.loads(SUBJECT_PROFILES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k.strip().upper()] = v
    return out


def _profile_from_entry(abbr: str, name: str, entry: dict) -> SubjectProfile:
    """Build a profile from a raw override dict, falling back to defaults."""
    prof = _default_profile(abbr, name)

    def _path(key: str, default: Path | None) -> Path | None:
        raw = entry.get(key)
        if raw is None or not isinstance(raw, str) or not raw.strip():
            return default
        resolved = resolve_configured(WORKSPACE, raw)
        return resolved if resolved is not None else default

    transcripts = _path("transcripts_dir", prof.transcripts_dir)
    output_root = _path("output_root", prof.output_root)
    docs = _path("docs_dir", None)
    topic = _path("topic_mapping", None)

    out_tpl = entry.get("output_template")
    out_tpl = out_tpl.strip() if isinstance(out_tpl, str) and out_tpl.strip() \
        else prof.output_template
    pfx_tpl = entry.get("prefix_template")
    pfx_tpl = pfx_tpl.strip() if isinstance(pfx_tpl, str) and pfx_tpl.strip() \
        else prof.prefix_template

    return SubjectProfile(
        abbr=abbr, name=name,
        transcripts_dir=transcripts or prof.transcripts_dir,
        output_root=output_root or prof.output_root,
        output_template=out_tpl,
        prefix_template=pfx_tpl,
        docs_dir=docs,
        topic_mapping=topic,
        explicit=True)


def all_profiles() -> dict[str, SubjectProfile]:
    """A profile for every known subject (built-in + user-created)."""
    subjects = all_subjects()
    entries = _load_profile_entries()
    out: dict[str, SubjectProfile] = {}
    for abbr, name in subjects.items():
        entry = entries.get(abbr)
        if entry:
            out[abbr] = _profile_from_entry(abbr, name, entry)
        else:
            out[abbr] = _default_profile(abbr, name)
    return out


def subject_profile(key: str, abbr: str | None = None) -> SubjectProfile:
    """Look up a profile by abbreviation, subject name, or either.

    *abbr* wins when given; otherwise *key* is matched against known
    abbreviations first and then against subject display names.
    """
    subjects = all_subjects()
    profiles = all_profiles()

    def _by_abbr(candidate: str) -> SubjectProfile | None:
        cand = (candidate or "").strip().upper()
        if cand in subjects:
            return profiles.get(cand)
        return None

    if abbr:
        found = _by_abbr(abbr)
        if found:
            return found
    if key:
        found = _by_abbr(key)
        if found:
            return found
        for a, name in subjects.items():
            if name == key:
                return profiles[a]
        # A display name that is not registered still gets a usable profile.
        stripped = (key or "").strip()
        if stripped:
            return _default_profile(stripped.upper(), stripped)
    return _default_profile("", "")


def profile_for_output(subject: str, abbr: str | None = None) -> SubjectProfile:
    """The profile whose output root holds *subject*'s lecture folders."""
    prof = subject_profile(subject, abbr=abbr)
    if prof.abbr:
        return prof
    return prof


def save_profile(abbr: str, fields: dict) -> SubjectProfile:
    """Persist a subject profile and return the resulting profile.

    Only the keys present in *fields* are written; passing ``None`` for a
    key clears that override and restores the default behaviour.
    """
    abbr = (abbr or "").strip().upper()
    if not abbr or not is_safe_component(abbr):
        raise ValueError("subject abbreviation is not a safe path component")
    subjects = all_subjects()
    if abbr not in subjects:
        raise ValueError(f"unknown subject {abbr!r}")
    name = subjects[abbr]

    entries = _load_profile_entries()
    entry = dict(entries.get(abbr) or {})

    allowed = {"transcripts_dir", "docs_dir", "output_root",
               "output_template", "prefix_template", "topic_mapping"}
    for key, value in (fields or {}).items():
        if key not in allowed:
            raise ValueError(f"unknown profile field {key!r}")
        if value is None:
            entry.pop(key, None)
            continue
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        value = value.strip()
        if not value:
            entry.pop(key, None)
            continue
        if key in ("output_template", "prefix_template"):
            if "{" in value and not any(
                    f"{{{f}}}" in value for f in
                    (OUTPUT_TEMPLATE_FIELDS if key == "output_template"
                     else PREFIX_TEMPLATE_FIELDS)):
                raise ValueError(
                    f"{key} must use one of "
                    f"{OUTPUT_TEMPLATE_FIELDS if key == 'output_template' else PREFIX_TEMPLATE_FIELDS}")
            entry[key] = value
            continue
        resolved = resolve_configured(WORKSPACE, value)
        if resolved is None:
            raise ValueError(f"{key} is not a valid path")
        if key == "output_root":
            # The agent runs with the workspace as cwd, so the output root
            # has to live inside it for write permissions and file serving.
            if rel_glob(WORKSPACE, resolved) is None:
                raise ValueError("output_root must be inside the workspace")
        entry[key] = value

    if entry:
        entries[abbr] = entry
    else:
        entries.pop(abbr, None)
    try:
        SUBJECT_PROFILES_FILE.write_text(
            json.dumps(entries, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not write {SUBJECT_PROFILES_FILE.name}: {exc}") from exc
    return all_profiles()[abbr]


def delete_profile(abbr: str) -> None:
    """Remove a subject's overrides (restores the default behaviour)."""
    abbr = (abbr or "").strip().upper()
    entries = _load_profile_entries()
    if abbr in entries:
        entries.pop(abbr, None)
        try:
            SUBJECT_PROFILES_FILE.write_text(
                json.dumps(entries, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"could not write {SUBJECT_PROFILES_FILE.name}: {exc}") from exc


def profile_to_dict(prof: SubjectProfile) -> dict:
    """Serialise a profile for the UI / API."""

    def _rel(path: Path | None) -> str | None:
        if path is None:
            return None
        return rel_glob(WORKSPACE, path) or str(path)

    docs = prof.resolve_docs()
    return {
        "abbr": prof.abbr,
        "subject": prof.name,
        "explicit": prof.explicit,
        "transcripts_dir": _rel(prof.transcripts_dir),
        "transcripts_dir_abs": str(prof.transcripts_dir),
        "docs_dir": _rel(prof.docs_dir) if prof.docs_dir else None,
        "docs_dir_abs": str(prof.docs_dir) if prof.docs_dir else None,
        "docs_resolved": str(docs) if docs else None,
        "output_root": _rel(prof.output_root),
        "output_root_abs": str(prof.output_root),
        "output_template": prof.output_template,
        "prefix_template": prof.prefix_template,
        "topic_mapping": _rel(prof.topic_mapping),
        "topic_mapping_abs": str(prof.topic_mapping) if prof.topic_mapping else None,
        "write_roots": prof.write_roots(),
    }


_OPENCODE_EXE: str | None = None
_COMMANDCODE_ARGV: list[str] | None = None
_CLAUDE_ARGV: list[str] | None = None
_CODEX_ARGV: list[str] | None = None
_REASONIX_ARGV: list[str] | None = None
_PI_ARGV: list[str] | None = None
_ANTIGRAVITY_ARGV: list[str] | None = None
_CURSOR_ARGV: list[str] | None = None
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


def find_reasonix_argv() -> list[str]:
    """Argv prefix for Reasonix CLI, skipping PowerShell shims."""
    global _REASONIX_ARGV
    if _REASONIX_ARGV:
        return _REASONIX_ARGV
    for name in ("reasonix.exe", "reasonix.cmd", "reasonix"):
        found = shutil.which(name)
        if found:
            path = Path(found)
            if path.suffix.lower() == ".ps1":
                cmd = path.with_suffix(".cmd")
                if cmd.is_file():
                    path = cmd
            _REASONIX_ARGV = [str(path)]
            return _REASONIX_ARGV
    pkg_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Reasonix",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path(os.environ.get("APPDATA", "")) / "npm",
    ]
    for root in pkg_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for f in ("reasonix.exe", "reasonix.cmd"):
                if f in filenames:
                    p = Path(dirpath) / f
                    _REASONIX_ARGV = [str(p)]
                    return _REASONIX_ARGV
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
    _REASONIX_ARGV = ["reasonix"]
    return _REASONIX_ARGV


def find_reasonix() -> str:
    return find_reasonix_argv()[0]


def find_pi_argv() -> list[str]:
    """Argv prefix for Pi Harness CLI, skipping PowerShell shims."""
    global _PI_ARGV
    if _PI_ARGV:
        return _PI_ARGV
    for name in ("pi.exe", "pi.cmd", "pi"):
        found = shutil.which(name)
        if found:
            path = Path(found)
            if path.suffix.lower() == ".ps1":
                cmd = path.with_suffix(".cmd")
                if cmd.is_file():
                    path = cmd
            _PI_ARGV = [str(path)]
            return _PI_ARGV
    pkg_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Pi",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path(os.environ.get("APPDATA", "")) / "npm",
    ]
    for root in pkg_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for f in ("pi.exe", "pi.cmd"):
                if f in filenames:
                    p = Path(dirpath) / f
                    _PI_ARGV = [str(p)]
                    return _PI_ARGV
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
    _PI_ARGV = ["pi"]
    return _PI_ARGV


def find_pi() -> str:
    return find_pi_argv()[0]


def find_antigravity_argv() -> list[str]:
    """Argv prefix for Antigravity CLI (agy), skipping PowerShell shims."""
    global _ANTIGRAVITY_ARGV
    if _ANTIGRAVITY_ARGV:
        return _ANTIGRAVITY_ARGV
    for env_k in ("ANTIGRAVITY_PATH", "AGY_PATH", "ANTIGRAVITY_CLI_PATH"):
        custom = os.environ.get(env_k)
        if custom and Path(custom).exists():
            _ANTIGRAVITY_ARGV = [str(Path(custom))]
            return _ANTIGRAVITY_ARGV
    for name in ("agy.exe", "agy.cmd", "agy", "antigravity.exe", "antigravity.cmd", "antigravity"):
        found = shutil.which(name)
        if found:
            path = Path(found)
            if path.suffix.lower() == ".ps1":
                cmd = path.with_suffix(".cmd")
                if cmd.is_file():
                    path = cmd
            _ANTIGRAVITY_ARGV = [str(path)]
            return _ANTIGRAVITY_ARGV
    pkg_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin",
        Path(os.environ.get("LOCALAPPDATA", "")) / "agy",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Antigravity",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Antigravity IDE" / "bin",
        Path(os.environ.get("APPDATA", "")) / "Antigravity" / "bin",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path(os.environ.get("APPDATA", "")) / "npm",
        Path.home() / ".gemini" / "antigravity-cli",
        Path.home() / ".gemini" / "antigravity" / "bin",
        Path.home() / ".gemini" / "antigravity-ide" / "bin",
    ]
    for root in pkg_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            file_map = {f.lower(): f for f in filenames}
            # Skip Electron GUI root directories that are not CLI binaries
            if "resources.pak" in file_map or "chrome_100_percent.pak" in file_map:
                continue
            for target in ("agy.exe", "agy.cmd", "agy.bat", "agy", "antigravity-cli.exe", "antigravity-cli.cmd"):
                if target in file_map:
                    p = Path(dirpath) / file_map[target]
                    _ANTIGRAVITY_ARGV = [str(p)]
                    return _ANTIGRAVITY_ARGV
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
    _ANTIGRAVITY_ARGV = ["agy"]
    return _ANTIGRAVITY_ARGV


def find_antigravity() -> str:
    return find_antigravity_argv()[0]


def find_cursor_argv() -> list[str]:
    """Argv prefix for Cursor Agent CLI (cursor-agent / agent), skipping PowerShell shims."""
    global _CURSOR_ARGV
    if _CURSOR_ARGV:
        return _CURSOR_ARGV
    for env_k in ("CURSOR_AGENT_PATH", "CURSOR_CLI", "CURSOR_PATH"):
        custom = os.environ.get(env_k)
        if custom and Path(custom).exists():
            p = Path(custom)
            _CURSOR_ARGV = [str(p)]
            return _CURSOR_ARGV
    for name in ("cursor-agent.cmd", "cursor-agent.exe", "cursor-agent",
                 "agent.cmd", "agent.exe", "agent"):
        found = shutil.which(name)
        if found:
            path = Path(found)
            if path.suffix.lower() == ".ps1":
                cmd = path.with_suffix(".cmd")
                if cmd.is_file():
                    path = cmd
            _CURSOR_ARGV = [str(path)]
            return _CURSOR_ARGV
    pkg_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor-agent",
        Path.home() / ".cursor" / "bin",
        Path.home() / ".local" / "bin",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path(os.environ.get("APPDATA", "")) / "npm",
    ]
    for root in pkg_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for f in ("cursor-agent.cmd", "cursor-agent.exe", "agent.cmd", "agent.exe"):
                if f in filenames:
                    p = Path(dirpath) / f
                    _CURSOR_ARGV = [str(p)]
                    return _CURSOR_ARGV
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
    _CURSOR_ARGV = ["cursor-agent"]
    return _CURSOR_ARGV


def find_cursor() -> str:
    return find_cursor_argv()[0]


def is_backend_available(backend: str | None = None) -> tuple[bool, str]:
    """Check if the backend executable is installed and available on PATH or disk.

    Returns (is_available, executable_path_or_command_name).
    """
    bid = normalize_backend(backend)
    if bid == BACKEND_OPENCODE:
        exe = find_opencode()
        if exe and (Path(exe).is_file() or shutil.which(exe)):
            return True, exe
        return False, exe or "opencode"
    if bid == BACKEND_COMMANDCODE:
        argv = find_commandcode_argv()
        if argv and (Path(argv[0]).is_file() or shutil.which(argv[0])):
            return True, argv[0]
        return False, argv[0] if argv else "cmdc"
    if bid == BACKEND_CURSOR:
        argv = find_cursor_argv()
        if argv and (Path(argv[0]).is_file() or shutil.which(argv[0])):
            return True, argv[0]
        return False, argv[0] if argv else "cursor-agent"
    if bid == BACKEND_ANTIGRAVITY:
        argv = find_antigravity_argv()
        if argv and argv[0] != "agy" and (Path(argv[0]).is_file() or shutil.which(argv[0])):
            return True, argv[0]
        if argv and argv[0] == "agy" and shutil.which("agy"):
            return True, "agy"
        return False, argv[0] if argv else "agy"
    if bid == BACKEND_CLAUDE:
        argv = find_claude_argv()
        if argv and (Path(argv[0]).is_file() or (shutil.which(argv[0]) and argv[0] != "claude") or shutil.which("claude")):
            return True, argv[0]
        return False, argv[0] if argv else "claude"
    if bid == BACKEND_CODEX:
        argv = find_codex_argv()
        if argv and (Path(argv[0]).is_file() or (shutil.which(argv[0]) and argv[0] != "codex") or shutil.which("codex")):
            return True, argv[0]
        return False, argv[0] if argv else "codex"
    if bid == BACKEND_REASONIX:
        argv = find_reasonix_argv()
        if argv and (Path(argv[0]).is_file() or (shutil.which(argv[0]) and argv[0] != "reasonix") or shutil.which("reasonix")):
            return True, argv[0]
        return False, argv[0] if argv else "reasonix"
    if bid == BACKEND_PI:
        argv = find_pi_argv()
        if argv and (Path(argv[0]).is_file() or (shutil.which(argv[0]) and argv[0] != "pi") or shutil.which("pi")):
            return True, argv[0]
        return False, argv[0] if argv else "pi"
    return False, "unknown"


def _parse_models_verbose(stdout: str) -> list[dict]:
    """Parse `opencode models [provider] --verbose` (id line + JSON blob)."""
    import re
    models: list[dict] = []
    lines = (stdout or "").splitlines()
    i = 0
    id_re = re.compile(r"^[\w.~@+-]+/[\w.~@+:/-]+$")
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
            provider_id = model_id.split("/", 1)[0] if "/" in model_id else ""
            models.append({
                "id": model_id,
                "name": model_id.split("/", 1)[-1],
                "provider": provider_id,
                "family": "",
                "variants": [],
                "reasoning": False,
                "status": "unknown",
                "cost": {},
                "limit": {},
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
            provider_id = model_id.split("/", 1)[0] if "/" in model_id else ""
            models.append({
                "id": model_id,
                "name": model_id.split("/", 1)[-1],
                "provider": provider_id,
                "family": "",
                "variants": [],
                "reasoning": False,
                "status": "unknown",
                "cost": {},
                "limit": {},
            })
            continue
        variants = meta.get("variants") or {}
        if isinstance(variants, list):
            variant_ids = [v.get("id") for v in variants
                           if isinstance(v, dict) and v.get("id")]
        else:
            variant_ids = list(variants.keys()) if isinstance(variants, dict) else []
        caps = meta.get("capabilities") or {}
        provider_id = meta.get("providerID") or (model_id.split("/", 1)[0] if "/" in model_id else "")
        family = meta.get("family") or ""
        # Xiaomi / MiMo models do not support reasoning effort variants:
        # record that they reason, but offer no effort options in the UI.
        # (OpenCode v2 capabilities are {tools, input, output} — there is
        # no `reasoning` flag, so pre-clear variants are the signal.)
        reasoning = bool(variant_ids) or bool(caps.get("reasoning"))
        if provider_id.startswith("xiaomi") or family.lower() == "mimo" or "mimo" in model_id.lower():
            variant_ids = []
        models.append({
            "id": f"{provider_id}/{meta.get('id') or model_id.split('/', 1)[-1]}" if provider_id else model_id,
            "name": meta.get("name") or model_id.split("/", 1)[-1],
            "provider": provider_id,
            "family": family,
            "status": meta.get("status") or "active",
            "reasoning": reasoning,
            "variants": variant_ids,
            "cost": _normalize_opencode_cost(meta.get("cost")),
            "limit": meta.get("limit") or {},
        })
    return models


def _normalize_opencode_cost(raw) -> dict:
    """Normalize an OpenCode v2 model `cost` payload to a flat dict.

    V2 serves `cost` as a list of per-tier entries
    (``[{"input": 1.25, "output": 4.25, "cache": {"read": ..., "write": ...}},
    {"tier": {...}, ...}]``). The UI estimate only needs the base (first)
    tier as ``{"input", "output", "cache_read", "cache_write"}`` rates per
    million tokens. V1 dicts pass through unchanged.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw:
        base = next((t for t in raw if isinstance(t, dict) and "tier" not in t),
                    next((t for t in raw if isinstance(t, dict)), {}))
        cache = base.get("cache") or {}
        out: dict = {}
        for k in ("input", "output"):
            try:
                if base.get(k) is not None:
                    out[k] = float(base[k])
            except (TypeError, ValueError):
                pass
        if isinstance(cache, dict):
            for src, dst in (("read", "cache_read"), ("write", "cache_write")):
                try:
                    if cache.get(src) is not None:
                        out[dst] = float(cache[src])
                except (TypeError, ValueError):
                    pass
        return out
    return {}


def _parse_models_plain(stdout: str) -> list[dict]:
    """Parse OpenCode v2 `opencode models` output: one `provider/model` per line."""
    import re
    models: list[dict] = []
    seen: set[str] = set()
    id_re = re.compile(r"^[\w.~@+-]+/[\w.~@+:/-]+$")
    for line in (stdout or "").splitlines():
        token = line.strip().strip("`,;|")
        if not token or token in seen or not id_re.match(token):
            continue
        seen.add(token)
        provider_id = token.split("/", 1)[0]
        models.append({
            "id": token,
            "name": token.split("/", 1)[-1],
            "provider": provider_id,
            "family": "",
            "status": "unknown",
            "reasoning": False,
            "variants": [],
            "cost": {},
            "limit": {},
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
                "id": "gpt-5.6-terra",
                "name": "GPT-5.6 Terra",
                "family": "gpt-5",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "gpt-5.6-luna",
                "name": "GPT-5.6 Luna",
                "family": "gpt-5",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "gpt-5.5",
                "name": "GPT-5.5",
                "family": "gpt-5",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "cost": {},
                "limit": {},
            },
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
    if backend_id == BACKEND_REASONIX:
        return [
            {
                "id": "deepseek/deepseek-v4-flash",
                "name": "DeepSeek V4 Flash (Prefix-Cache)",
                "family": "deepseek-flash",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {},
            },
            {
                "id": "deepseek/deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "family": "deepseek-pro",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {},
            },
            {
                "id": "deepseek/deepseek-reasoner",
                "name": "DeepSeek Reasoner (R1)",
                "family": "deepseek-r1",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "deepseek/deepseek-chat",
                "name": "DeepSeek Chat (V3)",
                "family": "deepseek-v3",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {},
            },
        ]
    if backend_id == BACKEND_PI:
        return [
            {
                "id": "deepseek/deepseek-v4-flash",
                "name": "DeepSeek V4 Flash",
                "family": "deepseek",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 1000000, "output": 384000},
            },
            {
                "id": "~deepseek/deepseek-v4-flash-latest",
                "name": "DeepSeek V4 Flash Latest",
                "family": "deepseek",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 1000000, "output": 384000},
            },
            {
                "id": "deepseek/deepseek-v4-flash-0731",
                "name": "DeepSeek V4 Flash (0731 Snapshot)",
                "family": "deepseek",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 1000000, "output": 393200},
            },
            {
                "id": "deepseek/deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "family": "deepseek",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 1000000, "output": 384000},
            },
            {
                "id": "claude-3-7-sonnet",
                "name": "Claude 3.7 Sonnet",
                "family": "anthropic",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {},
            },
            {
                "id": "claude-3-5-sonnet",
                "name": "Claude 3.5 Sonnet",
                "family": "anthropic",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {},
            },
            {
                "id": "gpt-5.4",
                "name": "GPT-5.4",
                "family": "openai",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "cost": {},
                "limit": {},
            },
        ]
    if backend_id == BACKEND_ANTIGRAVITY:
        return [
            {
                "id": "gemini-3.8-flash",
                "name": "Gemini 3.8 Flash",
                "family": "gemini",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 1000000, "output": 65536},
            },
            {
                "id": "gemini-3.8-pro",
                "name": "Gemini 3.8 Pro",
                "family": "gemini",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 2000000, "output": 65536},
            },
            {
                "id": "gemini-3.7-flash",
                "name": "Gemini 3.7 Flash",
                "family": "gemini",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 1000000, "output": 65536},
            },
            {
                "id": "gemini-3.7-pro",
                "name": "Gemini 3.7 Pro",
                "family": "gemini",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 2000000, "output": 65536},
            },
            {
                "id": "gemini-3.1-pro-preview",
                "name": "Gemini 3.1 Pro Preview",
                "family": "gemini",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 2000000, "output": 65536},
            },
            {
                "id": "gemini-2.5-flash",
                "name": "Gemini 2.5 Flash",
                "family": "gemini",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "cost": {},
                "limit": {"context": 1000000, "output": 65536},
            },
            {
                "id": "gemini-2.5-pro",
                "name": "Gemini 2.5 Pro",
                "family": "gemini",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "cost": {},
                "limit": {"context": 2000000, "output": 65536},
            },
        ]
    if backend_id == BACKEND_CURSOR:
        return [
            {
                "id": "composer-2.5",
                "name": "Cursor Composer 2.5",
                "family": "cursor",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 200000, "output": 64000},
            },
            {
                "id": "sonic",
                "name": "Cursor Sonic",
                "family": "cursor",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "supports_fast": False,
                "cost": {},
                "limit": {"context": 200000, "output": 64000},
            },
            {
                "id": "claude-3.7-sonnet",
                "name": "Claude 3.7 Sonnet",
                "family": "anthropic",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 200000, "output": 64000},
            },
            {
                "id": "claude-3.5-sonnet",
                "name": "Claude 3.5 Sonnet",
                "family": "anthropic",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 200000, "output": 8192},
            },
            {
                "id": "claude-3.5-haiku",
                "name": "Claude 3.5 Haiku",
                "family": "anthropic",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 200000, "output": 8192},
            },
            {
                "id": "gpt-5.4",
                "name": "GPT-5.4",
                "family": "openai",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 200000, "output": 64000},
            },
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "family": "openai",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 128000, "output": 16384},
            },
            {
                "id": "o3-mini",
                "name": "o3-mini",
                "family": "openai",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high"],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 200000, "output": 64000},
            },
            {
                "id": "deepseek-r1",
                "name": "DeepSeek R1",
                "family": "deepseek",
                "status": "active",
                "reasoning": True,
                "variants": [],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 128000, "output": 64000},
            },
            {
                "id": "deepseek-v3",
                "name": "DeepSeek V3",
                "family": "deepseek",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 128000, "output": 8192},
            },
            {
                "id": "gemini-2.5-pro",
                "name": "Gemini 2.5 Pro",
                "family": "gemini",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 1000000, "output": 65536},
            },
            {
                "id": "grok-3",
                "name": "Grok 3",
                "family": "xai",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 1000000, "output": 65536},
            },
            {
                "id": "grok-3-mini",
                "name": "Grok 3 Mini",
                "family": "xai",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high", "max"],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 1000000, "output": 65536},
            },
            {
                "id": "grok-2",
                "name": "Grok 2",
                "family": "xai",
                "status": "active",
                "reasoning": False,
                "variants": [],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 128000, "output": 16384},
            },
            {
                "id": "grok-code",
                "name": "Grok Code",
                "family": "xai",
                "status": "active",
                "reasoning": True,
                "variants": ["low", "medium", "high"],
                "supports_fast": True,
                "cost": {},
                "limit": {"context": 256000, "output": 32768},
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


_SPECIAL_MODEL_WORDS = {
    "gpt": "GPT",
    "glm": "GLM",
    "mimo": "MiMo",
    "minimax": "MiniMax",
    "deepseek": "DeepSeek",
    "qwen": "Qwen",
    "gemini": "Gemini",
    "claude": "Claude",
    "kimi": "Kimi",
    "grok": "Grok",
    "fugu": "Fugu",
    "muse": "Muse",
    "inkling": "Inkling",
    "laguna": "Laguna",
    "nemotron": "Nemotron",
    "step": "Step",
    "hy3": "HY3",
    "moe": "MoE",
    "xai": "xAI",
    "zai": "ZAI",
    "openai": "OpenAI",
    "meta": "Meta",
    "google": "Google",
    "sakana": "Sakana",
    "cursor": "Cursor",
    "moonshotai": "Moonshot",
    "xiaomi": "Xiaomi",
    "tencent": "Tencent",
    "nvidia": "Nvidia",
    "stepfun": "StepFun",
    "llama": "Llama",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "command": "Command",
}


def _format_commandcode_model_name(token: str) -> str:
    """Format a Command Code model ID/token into a human-readable display name."""
    import re
    if "/" in token:
        _, model_part = token.split("/", 1)
    else:
        model_part = token

    # Handle claude version patterns like 'claude-sonnet-4-6' -> 'claude-sonnet-4.6'
    model_clean = re.sub(r"(\d+)-(\d+)", r"\1.\2", model_part)

    # Handle qwen pattern like 'qwen3.7-max' -> 'qwen-3.7-max'
    model_clean = re.sub(r"^(qwen)(\d+(?:\.\d+)?)", r"\1-\2", model_clean, flags=re.IGNORECASE)

    # Handle gpt-5.4 -> GPT-5.4
    if re.match(r"^gpt-\d", model_clean, re.IGNORECASE):
        parts = model_clean.split("-", 2)
        if len(parts) == 2:
            return f"GPT-{parts[1]}"
        elif len(parts) >= 3:
            sub = " ".join(p.capitalize() for p in parts[2].split("-"))
            return f"GPT-{parts[1]} {sub}"

    parts = re.split(r"[-_]", model_clean)
    formatted = []
    for p in parts:
        lower = p.lower()
        if lower in _SPECIAL_MODEL_WORDS:
            formatted.append(_SPECIAL_MODEL_WORDS[lower])
        elif re.match(r"^[vkm]\d+(\.\d+)?$", lower):
            formatted.append(p[0].upper() + p[1:])
        elif lower.isdigit() or re.match(r"^\d+(\.\d+)+$", lower):
            formatted.append(p)
        elif re.match(r"^\d+[a-z]$", lower):
            formatted.append(p.upper())
        elif re.match(r"^\d+[a-z]\d+[a-z]$", lower):
            formatted.append(p)
        else:
            formatted.append(p.capitalize())

    return " ".join(formatted)


def _parse_commandcode_models(stdout: str, effort_variants: list[str]) -> list[dict]:
    """Parse `cmdc --list-models` (copy-pasteable ids, descriptions, section headers)."""
    import re
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    id_re = re.compile(r"^[\w.-]+(?:/[\w.@+-]+)?$")
    skip = {
        "model", "models", "id", "name", "provider", "available", "copy",
        "pass", "cmdc", "docs", "docs:", "updated", "open", "source",
    }
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
                    name = _format_commandcode_model_name(mid)
                    desc = ""
                    family = ""
                elif isinstance(row, dict):
                    mid = str(row.get("id") or row.get("model") or "").strip()
                    name = str(row.get("name") or _format_commandcode_model_name(mid))
                    desc = str(row.get("description") or "")
                    family = str(row.get("family") or "")
                else:
                    continue
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                desc_lower = desc.lower()
                mid_lower = mid.lower()
                has_reasoning = (
                    "reasoning" in desc_lower
                    or "thinking" in desc_lower
                    or "hybrid-attention" in desc_lower
                    or "thought" in desc_lower
                    or "deepseek" in mid_lower
                    or "claude" in mid_lower
                    or "o1" in mid_lower
                    or "o3" in mid_lower
                    or "fable" in mid_lower
                )
                models.append({
                    "id": mid, "name": name, "family": family,
                    "description": desc,
                    "status": "active", "reasoning": has_reasoning,
                    "variants": list(effort_variants) if has_reasoning else [], "cost": {}, "limit": {},
                })
            if models:
                return models
        except ValueError:
            pass

    current_family = ""
    for line in text.splitlines():
        line = ansi.sub("", line).strip()
        if not line or set(line) <= {"-", "=", " "}:
            continue
        # Track section headers (e.g. "Open Source", "Anthropic", "OpenAI", etc.)
        if not any(c in line for c in ["/", ":", "--"]) and len(line.split()) <= 3 and not re.search(r"\d", line):
            if line.lower() not in skip and not line.lower().startswith("available"):
                current_family = line
                continue

        tokens = line.split()
        token = tokens[0].strip("`,;|")
        if token.lower() in skip or not id_re.match(token):
            continue
        # Skip header/footer lines that aren't model IDs (must have a slash or contain digits/hyphens)
        if "/" not in token and not re.search(r"[-0-9]", token):
            continue
        if token in seen:
            continue
        seen.add(token)
        rest = line[len(tokens[0]):].strip(" -|")
        name = _format_commandcode_model_name(token)
        desc_lower = rest.lower()
        token_lower = token.lower()
        has_reasoning = (
            "reasoning" in desc_lower
            or "thinking" in desc_lower
            or "hybrid-attention" in desc_lower
            or "thought" in desc_lower
            or "deepseek" in token_lower
            or "claude" in token_lower
            or "o1" in token_lower
            or "o3" in token_lower
            or "fable" in token_lower
        )
        models.append({
            "id": token,
            "name": name,
            "family": current_family,
            "description": rest,
            "status": "active",
            "reasoning": has_reasoning,
            "variants": list(effort_variants) if has_reasoning else [],
            "cost": {},
            "limit": {},
        })
    return models


def _list_opencode_models(provider: str | None = None, refresh: bool = False,
                          timeout: float = 45.0) -> dict:
    import subprocess

    fallback = _fallback_models(BACKEND_OPENCODE)
    exe = find_opencode()
    if not exe:
        return {
            "ok": False, "backend": BACKEND_OPENCODE, "provider": provider or "",
            "models": fallback, "default_model": MODEL,
            "default_variant": VARIANT,
            "error": "opencode executable not found",
        }
    # Try OpenCode v2 API first for rich metadata (capabilities, variants, costs).
    # V2 serves {"location": {...}, "data": [{id (bare or nested), providerID,
    # capabilities {tools,input,output}, variants [{id,...}], cost [tiers],
    # limit {context,output}, status, enabled}]}. The selectable catalog ID
    # is always `providerID/id` (e.g. `opencode-go/cat-jam-free`), matching
    # `opencode models` output.
    models: list[dict] = []
    err: str = ""
    try:
        proc_api = subprocess.run(
            [exe, "api", "GET", "/api/model"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=min(timeout, 10.0),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc_api.returncode == 0 and proc_api.stdout:
            api_data = json.loads(proc_api.stdout)
            raw_items = api_data.get("data", []) if isinstance(api_data, dict) else (api_data if isinstance(api_data, list) else [])
            for item in raw_items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if item.get("enabled") is False:
                    continue
                raw_id = str(item["id"])
                p_id = item.get("providerID") or (raw_id.split("/", 1)[0] if "/" in raw_id else "")
                model_id = (raw_id if not p_id or raw_id == p_id or raw_id.startswith(f"{p_id}/")
                            else f"{p_id}/{raw_id}")
                raw_vars = item.get("variants") or []
                if isinstance(raw_vars, list):
                    variant_ids = [v["id"] for v in raw_vars if isinstance(v, dict) and "id" in v]
                elif isinstance(raw_vars, dict):
                    variant_ids = list(raw_vars.keys())
                else:
                    variant_ids = []
                family = item.get("family") or ""
                # Xiaomi / MiMo models do not support reasoning effort
                # variants: record that they reason, but offer no options.
                reasoning = bool(variant_ids)
                if p_id.startswith("xiaomi") or family.lower() == "mimo" or "mimo" in raw_id.lower():
                    variant_ids = []
                models.append({
                    "id": model_id,
                    "name": item.get("name") or raw_id.split("/", 1)[-1],
                    "provider": p_id,
                    "family": family,
                    "status": item.get("status") or "active",
                    "reasoning": reasoning,
                    "variants": variant_ids,
                    "cost": _normalize_opencode_cost(item.get("cost")),
                    "limit": item.get("limit") or {},
                })
    except Exception:
        pass

    # Fallback to OpenCode v2 CLI `opencode models` (v2 takes no --verbose/--refresh/provider flags;
    # output is one `provider/model` ID per line with no metadata).
    if not models:
        cmd = [exe, "models"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if proc.returncode == 0 and proc.stdout:
                models = _parse_models_plain(proc.stdout)
                if not models:
                    # Very old output shape (id line + JSON blob).
                    models = _parse_models_verbose(proc.stdout)
            else:
                err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "ok": False, "backend": BACKEND_OPENCODE, "provider": provider or "",
                "models": fallback, "default_model": MODEL,
                "default_variant": VARIANT,
                "error": f"{type(exc).__name__}: {exc}",
            }

    models = [m for m in models
              if (m.get("status") or "active") in {"active", "unknown", "beta"}]
    if provider:
        models = [m for m in models
                  if m.get("provider") == provider or m["id"].startswith(f"{provider}/")]
    if not models:
        return {
            "ok": False, "backend": BACKEND_OPENCODE, "provider": provider or "",
            "models": fallback, "default_model": MODEL,
            "default_variant": VARIANT,
            "error": f"no models parsed ({err or 'empty output'})",
        }
    ids = {m["id"] for m in models}
    # No hardcoded default: ask OpenCode for the user's configured default
    # model (`api GET /api/model/default`) and use it when it is in this
    # catalog; otherwise take the first available model.
    default_model = _fetch_opencode_default_model(exe, ids, timeout=timeout)
    if default_model is None:
        default_model = models[0]["id"]
    variants = next((m["variants"] for m in models if m["id"] == default_model),
                    [])
    return {
        "ok": True, "backend": BACKEND_OPENCODE, "provider": provider or "",
        "models": models, "default_model": default_model,
        "default_variant": preferred_variant(variants, VARIANT),
        "error": None,
    }


def _fetch_opencode_default_model(exe: str, ids: set[str],
                                    timeout: float = 45.0) -> str | None:
    """Return the user's configured OpenCode default model, if catalogued.

    Queries the v2 ``api GET /api/model/default`` endpoint and maps its
    ``{providerID, id}`` to the selectable ``provider/id`` form. Returns
    None when the endpoint fails or the default is not in *ids*, so callers
    can fall back to the first available model. No model ID is hardcoded.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [exe, "api", "GET", "/api/model/default"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=min(timeout, 10.0),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return None
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(data, dict) or not data.get("id"):
        return None
    raw_id = str(data["id"])
    p_id = data.get("providerID") or ""
    candidate = (raw_id if not p_id or raw_id == p_id
                 or raw_id.startswith(f"{p_id}/")
                 else f"{p_id}/{raw_id}")
    return candidate if candidate in ids else None


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


def _load_codex_cached_models() -> list[dict]:
    cache_path = Path.home() / ".codex" / "models_cache.json"
    if not cache_path.is_file():
        return []
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8", errors="replace"))
        raw_models = data.get("models") or []
        models = []
        for rm in raw_models:
            slug = rm.get("slug")
            if not slug or slug == "codex-auto-review":
                continue
            name = rm.get("display_name") or slug
            desc = rm.get("description") or ""
            reasoning = bool(rm.get("default_reasoning_summary") or "reasoning" in desc.lower())
            models.append({
                "id": slug,
                "name": name,
                "family": "openai",
                "description": desc,
                "status": "active",
                "reasoning": reasoning,
                "variants": [],
                "cost": {},
                "limit": {"context": rm.get("context_window", 0)},
            })
        return models
    except Exception:
        return []


def _list_codex_models(refresh: bool, timeout: float) -> dict:
    meta = backend_meta(BACKEND_CODEX)
    cached = _load_codex_cached_models()
    models = cached if cached else _fallback_models(BACKEND_CODEX)
    argv = find_codex_argv()
    default_model = meta["model"]
    ids = {m["id"] for m in models}
    if default_model not in ids and models:
        default_model = models[0]["id"]
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


def _list_reasonix_models(refresh: bool, timeout: float) -> dict:
    import subprocess
    meta = backend_meta(BACKEND_REASONIX)
    fallback = _fallback_models(BACKEND_REASONIX)
    argv = find_reasonix_argv()
    if not argv:
        return {
            "ok": False, "backend": BACKEND_REASONIX,
            "provider": meta["provider"], "models": fallback,
            "default_model": meta["model"], "default_variant": meta["variant"],
            "error": "reasonix executable not found",
        }
    cmd = argv + ["doctor", "--json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        default_model = meta["model"]
        variants = next((m["variants"] for m in fallback if m["id"] == default_model),
                        list(meta["effort_variants"]))
        return {
            "ok": True, "backend": BACKEND_REASONIX,
            "provider": meta["provider"], "models": fallback,
            "default_model": default_model,
            "default_variant": preferred_variant(variants, meta["variant"]),
            "error": f"{type(exc).__name__}: {exc}",
        }
    if proc.returncode == 0 and proc.stdout:
        try:
            data = json.loads(proc.stdout)
            providers = data.get("providers") or []
            models = []
            default_model = (data.get("config") or {}).get("default_model") or ""
            for p in providers:
                p_name = p.get("name") or ""
                p_model = p.get("model") or p_name
                p_models = p.get("models") or [p_model]
                is_def = bool(p.get("is_default"))
                key_present = bool(p.get("key_present"))
                for m_id in p_models:
                    model_id = p_name if len(p_models) == 1 else m_id
                    models.append({
                        "id": model_id,
                        "name": f"{p_name} ({m_id})" if p_name != m_id else p_name,
                        "family": p.get("kind") or "reasonix",
                        "provider": p_name,
                        "status": "active" if key_present else "missing_key",
                        "reasoning": True,
                        "variants": list(meta["effort_variants"]),
                        "cost": {},
                        "limit": {"context": p.get("context_window") or 1000000},
                    })
                    if is_def and not default_model:
                        default_model = model_id
            if models:
                if not default_model or not any(m["id"] == default_model for m in models):
                    default_model = models[0]["id"]
                variants = next((m["variants"] for m in models if m["id"] == default_model),
                                list(meta["effort_variants"]))
                return {
                    "ok": True,
                    "backend": BACKEND_REASONIX,
                    "provider": meta["provider"],
                    "models": models,
                    "default_model": default_model,
                    "default_variant": preferred_variant(variants, meta["variant"]),
                    "error": None,
                }
        except (ValueError, KeyError):
            pass

    default_model = meta["model"]
    variants = next((m["variants"] for m in fallback if m["id"] == default_model),
                    list(meta["effort_variants"]))
    return {
        "ok": True,
        "backend": BACKEND_REASONIX,
        "provider": meta["provider"],
        "models": fallback,
        "default_model": default_model,
        "default_variant": preferred_variant(variants, meta["variant"]),
        "error": None,
    }


def _parse_human_tokens(s: str) -> int:
    """Convert human token representations like '1.0M', '384K', '393.2K' into integers."""
    if not s or not isinstance(s, str):
        return 0
    clean = s.strip().upper()
    try:
        if clean.endswith("M"):
            return int(float(clean[:-1]) * 1_000_000)
        if clean.endswith("K"):
            return int(float(clean[:-1]) * 1_000)
        return int(float(clean))
    except (ValueError, TypeError):
        return 0


def _format_pi_model_name(token: str) -> tuple[str, str]:
    """Format a Pi Harness model token into (family, human-readable display name)."""
    import re
    is_latest = token.startswith("~") or token.endswith("-latest") or ":latest" in token
    raw = token.lstrip("~")
    family = raw.split("/")[0] if "/" in raw else ""
    model_part = raw.split("/", 1)[-1] if "/" in raw else raw

    snapshot = ""
    m_snap = re.search(r"[-_](\d{4}|\d{8}|\d{2}-\d{2})$", model_part)
    if m_snap:
        snapshot = m_snap.group(1)
        model_part = model_part[:m_snap.start()]

    name = _format_commandcode_model_name(model_part)
    if is_latest and not name.endswith("Latest"):
        name = f"{name} Latest"
    elif snapshot:
        name = f"{name} ({snapshot} Snapshot)"

    return family or "pi", name


def _parse_pi_models(stdout: str, effort_variants: list[str]) -> list[dict]:
    """Parse `pi --list-models` tabular output into catalog model dicts."""
    import re
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    models: list[dict] = []
    seen: set[str] = set()

    for line in (stdout or "").splitlines():
        line = ansi.sub("", line).strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        # Skip table header
        if parts[0].lower() == "provider" and parts[1].lower() == "model":
            continue

        provider_col = parts[0]
        model_token = parts[1]
        context_str = parts[2]
        max_out_str = parts[3]
        thinking_str = parts[4].lower()

        if model_token in seen:
            continue
        seen.add(model_token)

        family, name = _format_pi_model_name(model_token)
        has_reasoning = thinking_str == "yes"
        ctx = _parse_human_tokens(context_str)
        max_out = _parse_human_tokens(max_out_str)

        models.append({
            "id": model_token,
            "name": name,
            "family": family,
            "provider": provider_col,
            "status": "active",
            "reasoning": has_reasoning,
            "variants": list(effort_variants) if has_reasoning else [],
            "cost": {},
            "limit": {
                "context": ctx or 1000000,
                "output": max_out or 384000,
            },
        })

    return models


def _list_pi_models(refresh: bool, timeout: float) -> dict:
    import subprocess
    meta = backend_meta(BACKEND_PI)
    fallback = _fallback_models(BACKEND_PI)
    argv = find_pi_argv()
    if not argv:
        return {
            "ok": False,
            "backend": BACKEND_PI,
            "provider": meta["provider"],
            "models": fallback,
            "default_model": meta["model"],
            "default_variant": meta["variant"],
            "error": "pi executable not found",
        }
    cmd = argv + ["--list-models"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        default_model = meta["model"]
        variants = next((m["variants"] for m in fallback if m["id"] == default_model),
                        list(meta["effort_variants"]))
        return {
            "ok": True,
            "backend": BACKEND_PI,
            "provider": meta["provider"],
            "models": fallback,
            "default_model": default_model,
            "default_variant": preferred_variant(variants, meta["variant"]),
            "error": f"{type(exc).__name__}: {exc}",
        }
    if proc.returncode == 0 and proc.stdout:
        models = _parse_pi_models(proc.stdout, list(meta["effort_variants"]))
        if models:
            ids = {m["id"] for m in models}
            default_model = meta["model"] if meta["model"] in ids else (
                "deepseek/deepseek-v4-flash" if "deepseek/deepseek-v4-flash" in ids
                else ("~deepseek/deepseek-v4-flash-latest" if "~deepseek/deepseek-v4-flash-latest" in ids else models[0]["id"])
            )
            variants = next((m["variants"] for m in models if m["id"] == default_model),
                            list(meta["effort_variants"]))
            return {
                "ok": True,
                "backend": BACKEND_PI,
                "provider": meta["provider"],
                "models": models,
                "default_model": default_model,
                "default_variant": preferred_variant(variants, meta["variant"]),
                "error": None,
            }
    default_model = meta["model"]
    variants = next((m["variants"] for m in fallback if m["id"] == default_model),
                    list(meta["effort_variants"]))
    return {
        "ok": True,
        "backend": BACKEND_PI,
        "provider": meta["provider"],
        "models": fallback,
        "default_model": default_model,
        "default_variant": preferred_variant(variants, meta["variant"]),
        "error": None,
    }


def _list_antigravity_models(refresh: bool, timeout: float) -> dict:
    meta = backend_meta(BACKEND_ANTIGRAVITY)
    fallback = _fallback_models(BACKEND_ANTIGRAVITY)
    default_model = meta["model"]

    # 1. Live Google Generative Language API discovery if GEMINI_API_KEY / GOOGLE_API_KEY is available
    api_key = (os.environ.get("GEMINI_API_KEY") or
               os.environ.get("GOOGLE_API_KEY") or
               os.environ.get("ANTIGRAVITY_API_KEY") or "").strip()
    if api_key:
        try:
            import urllib.request
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            req = urllib.request.Request(url, headers={"User-Agent": "AgentWarden-NotesStudio/1.0"})
            with urllib.request.urlopen(req, timeout=min(timeout, 8.0)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = data.get("models") or []
            if items:
                dynamic_models = []
                seen_ids = set()
                for item in items:
                    raw_name = item.get("name") or ""
                    m_id = raw_name.replace("models/", "")
                    if not m_id or m_id in seen_ids:
                        continue
                    methods = item.get("supportedGenerationMethods") or []
                    if methods and "generateContent" not in methods:
                        continue
                    lower_id = m_id.lower()
                    if "gemini" not in lower_id:
                        continue
                    seen_ids.add(m_id)
                    name = item.get("displayName") or m_id
                    desc = item.get("description") or ""
                    is_reasoning = any(k in lower_id for k in ("3.8", "3.7", "3.1", "pro", "thinking"))
                    variants = list(meta["effort_variants"]) if is_reasoning else []
                    dynamic_models.append({
                        "id": m_id,
                        "name": name,
                        "family": "gemini",
                        "description": desc,
                        "status": "active",
                        "reasoning": is_reasoning,
                        "variants": variants,
                        "cost": {},
                        "limit": {
                            "context": item.get("inputTokenLimit") or 1000000,
                            "output": item.get("outputTokenLimit") or 65536,
                        },
                    })
                if dynamic_models:
                    def _sort_key(m):
                        mid = m["id"].lower()
                        if "3.8" in mid: return (0, mid)
                        if "3.7" in mid: return (1, mid)
                        if "3.1" in mid: return (2, mid)
                        if "2.5" in mid: return (3, mid)
                        return (4, mid)
                    dynamic_models.sort(key=_sort_key)
                    if not any(m["id"] == default_model for m in dynamic_models):
                        default_model = dynamic_models[0]["id"]
                    variants = next((m["variants"] for m in dynamic_models if m["id"] == default_model),
                                    list(meta["effort_variants"]))
                    return {
                        "ok": True,
                        "backend": BACKEND_ANTIGRAVITY,
                        "provider": meta["provider"],
                        "models": dynamic_models,
                        "default_model": default_model,
                        "default_variant": preferred_variant(variants, meta["variant"]),
                        "error": None,
                        "hint": f"{len(dynamic_models)} Antigravity models discovered live via Gemini API",
                    }
        except Exception:
            pass

    # 2. Antigravity CLI live discovery if executable is found and runnable
    argv = find_antigravity_argv()
    if argv and (shutil.which(argv[0]) or shutil.which("agy")):
        for subcmd in [["models", "--json"], ["--list-models"], ["models"]]:
            try:
                import subprocess
                proc = subprocess.run(
                    argv + subcmd, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=min(timeout, 8.0),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if proc.returncode == 0 and proc.stdout:
                    try:
                        data = json.loads(proc.stdout)
                        items = data if isinstance(data, list) else data.get("models") or []
                        if items:
                            cli_models = []
                            for item in items:
                                if isinstance(item, str):
                                    m_id = item
                                    m_name = item
                                elif isinstance(item, dict):
                                    m_id = item.get("id") or item.get("name") or ""
                                    m_name = item.get("name") or item.get("displayName") or m_id
                                else:
                                    continue
                                if not m_id:
                                    continue
                                is_reasoning = any(k in m_id.lower() for k in ("3.8", "3.7", "3.1", "pro", "thinking"))
                                cli_models.append({
                                    "id": m_id,
                                    "name": m_name,
                                    "family": "gemini",
                                    "status": "active",
                                    "reasoning": is_reasoning,
                                    "variants": list(meta["effort_variants"]) if is_reasoning else [],
                                    "cost": {},
                                    "limit": {"context": 1000000, "output": 65536},
                                })
                            if cli_models:
                                if not any(m["id"] == default_model for m in cli_models):
                                    default_model = cli_models[0]["id"]
                                variants = next((m["variants"] for m in cli_models if m["id"] == default_model),
                                                list(meta["effort_variants"]))
                                return {
                                    "ok": True,
                                    "backend": BACKEND_ANTIGRAVITY,
                                    "provider": meta["provider"],
                                    "models": cli_models,
                                    "default_model": default_model,
                                    "default_variant": preferred_variant(variants, meta["variant"]),
                                    "error": None,
                                    "hint": f"{len(cli_models)} Antigravity models discovered via CLI",
                                }
                    except json.JSONDecodeError:
                        grouped = {m["id"]: dict(m) for m in fallback}
                        for raw_line in proc.stdout.splitlines():
                            s_line = raw_line.strip()
                            if not s_line or s_line.lower().startswith("fetching") or "available models" in s_line.lower():
                                continue
                            parts = [p.strip() for p in s_line.split("\t") if p.strip()]
                            if not parts:
                                parts = s_line.split(None, 1)
                            if not parts:
                                continue
                            m_id = parts[0]
                            m_name = parts[1] if len(parts) > 1 else m_id
                            base_id = m_id
                            variant = None
                            for suf in ("-high", "-medium", "-low", "-max"):
                                if m_id.endswith(suf):
                                    base_id = m_id[:-len(suf)]
                                    variant = suf[1:]
                                    break
                            if base_id not in grouped:
                                is_reasoning = any(k in base_id.lower() for k in ("3.8", "3.7", "3.1", "pro", "thinking", "opus", "sonnet"))
                                grouped[base_id] = {
                                    "id": base_id,
                                    "name": m_name.rsplit("(", 1)[0].strip() if "(" in m_name else m_name,
                                    "family": "gemini" if "gemini" in base_id.lower() else "antigravity",
                                    "status": "active",
                                    "reasoning": is_reasoning,
                                    "variants": list(meta["effort_variants"]) if is_reasoning else [],
                                    "cost": {},
                                    "limit": {"context": 1000000, "output": 65536},
                                }
                            if variant and variant not in grouped[base_id]["variants"]:
                                grouped[base_id]["variants"].append(variant)
                        cli_models = list(grouped.values())
                        if cli_models:
                            if any(m["id"] == "gemini-3.8-flash" for m in cli_models):
                                default_model = "gemini-3.8-flash"
                            elif not any(m["id"] == default_model for m in cli_models):
                                default_model = cli_models[0]["id"]
                            variants = next((m["variants"] for m in cli_models if m["id"] == default_model),
                                            list(meta["effort_variants"]))
                            return {
                                "ok": True,
                                "backend": BACKEND_ANTIGRAVITY,
                                "provider": meta["provider"],
                                "models": cli_models,
                                "default_model": default_model,
                                "default_variant": preferred_variant(variants, meta["variant"]),
                                "error": None,
                                "hint": f"{len(cli_models)} Antigravity models discovered live via CLI",
                            }
            except Exception:
                pass

    # 3. Local cache files lookup (e.g. ~/.gemini/antigravity/models_cache.json)
    for cache_candidate in [
        Path.home() / ".gemini" / "antigravity" / "models_cache.json",
        Path.home() / ".gemini" / "models_cache.json",
        WORKSPACE / ".antigravity_models_cache.json",
    ]:
        if cache_candidate.is_file():
            try:
                data = json.loads(cache_candidate.read_text(encoding="utf-8", errors="replace"))
                items = data if isinstance(data, list) else data.get("models") or []
                if items and isinstance(items, list):
                    cached_models = []
                    for it in items:
                        if isinstance(it, dict) and it.get("id"):
                            cached_models.append(it)
                    if cached_models:
                        if not any(m["id"] == default_model for m in cached_models):
                            default_model = cached_models[0]["id"]
                        variants = next((m["variants"] for m in cached_models if m["id"] == default_model),
                                        list(meta["effort_variants"]))
                        return {
                            "ok": True,
                            "backend": BACKEND_ANTIGRAVITY,
                            "provider": meta["provider"],
                            "models": cached_models,
                            "default_model": default_model,
                            "default_variant": preferred_variant(variants, meta["variant"]),
                            "error": None,
                            "hint": f"Loaded {len(cached_models)} Antigravity models from local cache",
                        }
            except Exception:
                pass

    # 4. Fallback catalog
    variants = next((m["variants"] for m in fallback if m["id"] == default_model),
                    list(meta["effort_variants"]))
    hint = ("Antigravity models refreshed from catalog. Set GEMINI_API_KEY in .env or Settings for live API discovery."
            if refresh else None)
    return {
        "ok": True,
        "backend": BACKEND_ANTIGRAVITY,
        "provider": meta["provider"],
        "models": fallback,
        "default_model": default_model,
        "default_variant": preferred_variant(variants, meta["variant"]),
        "error": None,
        "hint": hint,
    }


def _list_cursor_models(refresh: bool, timeout: float) -> dict:
    meta = backend_meta(BACKEND_CURSOR)
    fallback = _fallback_models(BACKEND_CURSOR)
    api_key = os.environ.get("CURSOR_API_KEY") or os.environ.get("CURSOR_KEY") or os.environ.get("CURSOR_TOKEN") or ""

    if api_key:
        try:
            import urllib.request
            headers = {
                "Authorization": f"Bearer {api_key.strip()}",
                "User-Agent": "Cursor/1.0",
                "x-cursor-client-version": "0.45.0",
            }
            req = urllib.request.Request("https://api.cursor.com/v1/models", headers=headers)
            with urllib.request.urlopen(req, timeout=min(timeout, 8.0)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items") or data.get("models") or data.get("data") or []
            if items:
                dynamic_models = []
                seen_ids = set()
                for item in items:
                    m_id = item.get("id")
                    if not m_id or m_id in seen_ids:
                        continue
                    seen_ids.add(m_id)
                    name = item.get("displayName") or item.get("name") or m_id
                    params = {p.get("id"): p for p in item.get("parameters", [])} if isinstance(item.get("parameters"), list) else {}
                    has_effort = "effort" in params
                    raw_variants = [v.get("value") for v in params.get("effort", {}).get("values", [])] if has_effort else []
                    effort_vars = [v for v in raw_variants if v]
                    has_fast = "fast" in params

                    lower_id = m_id.lower()
                    if "grok" in lower_id:
                        family = "xai"
                    elif "claude" in lower_id or "anthropic" in lower_id:
                        family = "anthropic"
                    elif any(k in lower_id for k in ("gpt", "o1", "o3", "openai")):
                        family = "openai"
                    elif "deepseek" in lower_id:
                        family = "deepseek"
                    elif "gemini" in lower_id:
                        family = "gemini"
                    else:
                        family = "cursor"

                    dynamic_models.append({
                        "id": m_id,
                        "name": name,
                        "family": family,
                        "status": "active",
                        "reasoning": bool(effort_vars),
                        "variants": effort_vars,
                        "supports_fast": has_fast,
                        "cost": {},
                        "limit": {"context": 200000, "output": 64000},
                        "aliases": item.get("aliases", []),
                    })

                if dynamic_models:
                    default_model = meta["model"]
                    if not any(m["id"] == default_model for m in dynamic_models):
                        default_model = dynamic_models[0]["id"]
                    variants = next((m["variants"] for m in dynamic_models if m["id"] == default_model),
                                    list(meta["effort_variants"]))
                    return {
                        "ok": True,
                        "backend": BACKEND_CURSOR,
                        "provider": meta["provider"],
                        "models": dynamic_models,
                        "default_model": default_model,
                        "default_variant": preferred_variant(variants, meta["variant"]),
                        "error": None,
                    }
        except Exception:
            pass

    default_model = meta["model"]
    variants = next((m["variants"] for m in fallback if m["id"] == default_model),
                    list(meta["effort_variants"]))
    return {
        "ok": True,
        "backend": BACKEND_CURSOR,
        "provider": meta["provider"],
        "models": fallback,
        "default_model": default_model,
        "default_variant": preferred_variant(variants, meta["variant"]),
        "error": None,
    }


def clear_models_cache():
    """Clear cached model responses across backends."""
    _MODELS_CACHE.clear()
    _MODELS_CACHE_AT.clear()


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
    # OpenCode lists models across all providers if provider is None.
    effective_provider = provider if backend == BACKEND_OPENCODE else (provider or meta.get("provider", ""))
    cache_key = f"{backend}:{effective_provider or 'all'}"
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
    elif backend == BACKEND_REASONIX:
        result = _list_reasonix_models(
            refresh=refresh, timeout=timeout if timeout is not None else 30.0)
    elif backend == BACKEND_PI:
        result = _list_pi_models(
            refresh=refresh, timeout=timeout if timeout is not None else 30.0)
    elif backend == BACKEND_ANTIGRAVITY:
        result = _list_antigravity_models(
            refresh=refresh, timeout=timeout if timeout is not None else 30.0)
    elif backend == BACKEND_CURSOR:
        result = _list_cursor_models(
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
        else:
            suffix_match = next((m_id for m_id in models if m_id.endswith(f"/{chosen}")), None)
            if suffix_match:
                chosen = suffix_match
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


def transcript_sources() -> dict[str, list[str]]:
    """Map each transcript directory to the subjects that claim it.

    A subject claims the directory named by its profile. The shared flat
    ``transcript files/`` directory is claimed by every subject without a
    profile of its own — those transcripts are attributed by filename.
    """
    owners: dict[str, list[str]] = {}
    profiles = all_profiles()
    claimed: set[str] = set()
    for abbr, prof in profiles.items():
        if not prof.explicit or prof.transcripts_dir is None:
            continue
        key = str(Path(prof.transcripts_dir).resolve()).lower()
        owners.setdefault(key, []).append(abbr)
        claimed.add(key)
    shared = str(Path(TRANSCRIPTS_DIR).resolve()).lower()
    if shared not in owners:
        owners[shared] = []
    for abbr, prof in profiles.items():
        key = str(Path(prof.transcripts_dir).resolve()).lower()
        if key in claimed:
            continue
        if key == shared:
            owners[key].append(abbr)
    return {k: v for k, v in owners.items() if v or k == shared}


def list_transcripts(abbr: str | None = None) -> list[dict]:
    """Transcripts available for one subject (or all subjects).

    Each entry carries the subject that owns it (``abbr``) so the UI can
    group transcripts per subject without guessing from the filename.
    """
    subjects = all_subjects()
    out: list[dict] = []
    seen: set[str] = set()
    for key, owners in transcript_sources().items():
        directory = Path(key)
        if not directory.is_dir():
            continue
        for p in sorted(directory.glob("*.txt"), key=_natural_key):
            resolved = str(p.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            guess = guess_from_filename(p.name, subjects)
            owner = ""
            if len(owners) == 1:
                owner = owners[0]
            else:
                owner = guess.get("abbr", "")
            if owner and owner not in subjects:
                owner = ""
            if not owner and abbr:
                continue
            if abbr and owner and owner != abbr:
                continue
            merged = dict(guess)
            if owner:
                merged["abbr"] = owner
                merged["subject"] = subjects.get(owner, merged.get("subject", ""))
            out.append({
                "name": p.name, "path": resolved,
                "bytes": p.stat().st_size,
                "guess": merged,
                "abbr": owner,
                "subject": subjects.get(owner, merged.get("subject", "")),
            })
    if abbr:
        out.sort(key=lambda t: _natural_key(Path(t["name"])))
    return out


def list_outputs(subject: str, prefix: str,
                abbr: str | None = None) -> list[dict]:
    prof = subject_profile(subject, abbr=abbr)
    base = prof.output_dir(prefix, subject=subject)
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


def _extract_lecture(stem: str) -> str:
    """Best-effort lecture number from a filename stem.

    Tries, in order: trailing 'lecture/lec/l N', any trailing number,
    keyword + number anywhere ('class 3', 'week04'), and finally the
    last standalone 1-3 digit number (years like 2024 are ignored).
    """
    import re
    m = re.search(r"(?:lecture|lec|l)[_\s.\-]*(\d+)\s*$", stem, re.I)
    if not m:
        m = re.search(r"[_\s.\-](\d+)\s*$", stem)
    if not m:
        m = re.search(r"(?:lecture|lec|session|sess|class|week|wk|part"
                      r"|meeting|mtg)[_\s.\-]*(\d{1,3})\b", stem, re.I)
    if not m:
        nums = re.findall(r"(?<![\dA-Za-z])(\d{1,3})(?![\d])", stem)
        if nums:
            return str(int(nums[-1]))
        return ""
    return str(int(m.group(1)))


def guess_from_filename(filename: str,
                        subjects: dict[str, str] | None = None) -> dict:
    """Infer subject abbr, lecture number, and lecture prefix from a
    transcript filename.

    Examples:
      'Data_Management_for_Machine_Learning - Lecture 1.txt'
          -> DMML, lecture 1, DMML_Lecture_1
      'NLP_Lecture_9.txt' -> NLP, 9, NLP_Lecture_9
      'UDL_class_3_transcript.txt' -> UDL, 3, UDL_Lecture_3
    """
    import re
    subjects = subjects or all_subjects()
    stem = Path(filename).stem
    lecture = _extract_lecture(stem)

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


def output_paths(subject: str, prefix: str,
                 abbr: str | None = None) -> dict[str, Path]:
    """Artifact paths for one lecture, honouring the subject's profile.

    The output directory is resolved from the subject's profile (output
    root + output template) so different subjects can use different
    folder conventions. Subjects without a profile keep the legacy
    ``outputs/<Subject>/<Prefix>/`` layout.
    """
    prof = subject_profile(subject, abbr=abbr)
    base = prof.output_dir(prefix, subject=subject)
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


def output_rel_path(subject: str, prefix: str,
                    abbr: str | None = None) -> str:
    """The lecture folder relative to its serving root (URL-friendly).

    The serving root is the outermost output root that contains the
    subject's own root, so a subject rooted at ``outputs/QC`` is served
    as ``QC/<prefix>`` under the shared ``outputs`` root.
    """
    prof = subject_profile(subject, abbr=abbr)
    rel = prof.output_rel(prefix, subject=subject)
    if rel is None:
        return f"{subject}/{prefix}"
    root = serving_root_for(prof)
    try:
        head = Path(prof.output_root).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        head = ""
    if head in ("", "."):
        return rel
    return f"{head}/{rel}"


def serving_root_for(prof: SubjectProfile) -> Path:
    """The outermost output root that holds this subject's notes."""
    own = Path(prof.output_root).resolve()
    for root in output_roots():
        try:
            own.relative_to(root)
            return root
        except (ValueError, OSError):
            continue
    return own


def artifacts_status(subject: str, prefix: str,
                    abbr: str | None = None) -> dict:
    """Which phase artifacts exist, and which phases to run to resume."""
    try:
        paths = output_paths(subject, prefix, abbr=abbr)
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

    rel = output_rel_path(subject, prefix, abbr=abbr)
    return {
        "subject": subject,
        "prefix": prefix,
        "abbr": abbr or abbr_for_subject(subject),
        "output_rel": rel,
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
            f"/outputs/{urllib_quote_path(rel)}/"
            f"{urllib_quote(prefix + '_notes')}/{urllib_quote(prefix + '_notes.html')}"
            if has_formatter else None),
        "events_path": str(paths["events"]) if paths["events"].is_file() else None,
    }


def urllib_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def urllib_quote_path(rel: str) -> str:
    """URL-encode a relative path, keeping the separators intact.

    Quoting the whole path at once would turn ``a/b`` into ``a%2Fb``,
    which the server then reads as a single (wrong) file name.
    """
    return "/".join(urllib_quote(seg)
                    for seg in str(rel).replace("\\", "/").split("/"))


def _empty_token_bucket() -> dict:
    return {"input": 0, "output": 0, "reasoning": 0, "cost": 0.0}


def summarize_run_events(events_path: Path, subject: str = "",
                         prefix: str = "", abbr: str = "") -> dict:
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
        "phase_retries": {"extractor": 0, "enricher": 0, "formatter": 0},
        "last_failed_phase": None,
        "model": None,
        "variant": None,
        "backend": None,
        "current_phase": None,
        "output_rel": None,
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
                if ev.get("phase_retries") and isinstance(ev["phase_retries"], dict):
                    summary["phase_retries"].update(ev["phase_retries"])
                if et == "pipeline_start":
                    summary["subject"] = ev.get("subject") or summary["subject"]
                    summary["prefix"] = ev.get("prefix") or summary["prefix"]
                    summary["abbr"] = (abbr_for_subject(summary["subject"])
                                       or summary["abbr"])
                    summary["output_rel"] = (ev.get("output_rel")
                                             or summary["output_rel"])
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
                elif et in ("retry_start", "bounce_to_enricher"):
                    failed_ph = ev.get("failed_phase") or current_phase
                    if failed_ph:
                        summary["phase_retries"][failed_ph] = summary["phase_retries"].get(failed_ph, 0) + 1
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
                        if "phase_retries" in stats and isinstance(stats["phase_retries"], dict):
                            summary["phase_retries"].update(stats["phase_retries"])
    except OSError:
        pass

    if not summary["phase_stats"]:
        for ph, secs in phase_secs.items():
            summary["phase_stats"][ph] = {
                "seconds": secs,
                "cost": round(phase_cost.get(ph, 0.0), 6),
                "tokens": phase_tok.get(ph, _empty_token_bucket()),
                "retries": summary["phase_retries"].get(ph, 0),
            }
    else:
        for ph, p_stats in summary["phase_stats"].items():
            if isinstance(p_stats, dict) and "retries" not in p_stats:
                p_stats["retries"] = summary["phase_retries"].get(ph, 0)
    if not summary["seconds"]:
        summary["seconds"] = round(sum(phase_secs.values()), 1)
        if summary["started_at"] and summary["ended_at"]:
            summary["seconds"] = round(
                float(summary["ended_at"]) - float(summary["started_at"]), 1)

    arts = artifacts_status(summary["subject"], summary["prefix"],
                            abbr=abbr or summary.get("abbr") or None) \
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
                    limit: int = 1500, abbr: str | None = None) -> dict:
    """Page events from the lecture's ``*_run_events.jsonl``."""
    try:
        events_path = output_paths(subject, prefix, abbr=abbr)["events"]
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


def output_roots() -> list[Path]:
    """Every directory that may hold lecture outputs.

    The shared ``outputs/`` root plus one root per subject profile, so a
    subject that keeps its notes elsewhere is still found by the history
    scan. Roots nested inside another root are dropped — otherwise the
    same run would be reported twice.
    """
    roots: list[Path] = []

    def _add(raw) -> None:
        try:
            resolved = Path(raw).resolve()
        except (OSError, RuntimeError, TypeError):
            return
        if resolved not in roots:
            roots.append(resolved)

    _add(OUTPUTS_DIR)
    for prof in all_profiles().values():
        _add(prof.output_root)
    # Keep only the outermost roots.
    outermost: list[Path] = []
    for root in roots:
        nested = False
        for other in roots:
            if other == root:
                continue
            try:
                root.relative_to(other)
                nested = True
                break
            except ValueError:
                continue
        if not nested:
            outermost.append(root)
    return outermost


def _iter_run_event_files(root: Path, max_depth: int = 5):
    """Yield ``*_run_events.jsonl`` files under *root* (skips ``_`` dirs)."""
    if not root.is_dir():
        return
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not d.startswith("_")]
        if len(current.parts) - base_depth >= max_depth:
            dirnames[:] = []
        for name in filenames:
            if name.endswith("_run_events.jsonl"):
                yield current / name


def _identity_from_events(path: Path) -> tuple[str, str]:
    """Subject + prefix recorded by the run that wrote *path*."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "pipeline_start":
                    return (str(ev.get("subject") or ""),
                            str(ev.get("prefix") or ""))
    except OSError:
        pass
    return ("", "")


def _identity_from_path(root: Path, events: Path) -> tuple[str, str]:
    """Fallback identity derived from the folder layout."""
    try:
        rel = events.parent.relative_to(root)
    except ValueError:
        return ("", "")
    parts = rel.parts
    if len(parts) >= 2:
        return (parts[-2], parts[-1])
    if len(parts) == 1:
        return ("", parts[0])
    return ("", "")


def list_past_runs(limit: int = 60) -> list[dict]:
    """Scan every output root for run events and return newest-first summaries."""
    found: list[tuple[float, Path, str, str]] = []
    for root in output_roots():
        for events in _iter_run_event_files(root):
            subject, prefix = _identity_from_events(events)
            if not subject or not prefix:
                subject, prefix = _identity_from_path(root, events)
            if not prefix:
                continue
            try:
                mtime = events.stat().st_mtime
            except OSError:
                continue
            found.append((mtime, events, subject, prefix))
    found.sort(key=lambda t: t[0], reverse=True)
    return [
        summarize_run_events(path, subject, prefix)
        for _, path, subject, prefix in found[:limit]
    ]


def delete_past_run(subject: str, prefix: str, archive: bool = False,
                    abbr: str | None = None) -> dict:
    """Delete or archive a lecture's output folder.

    Archive moves it under ``<output root>/_archive/<subject>/<prefix>_<utc>/``.
    """
    import shutil
    from datetime import datetime, timezone

    subject = (subject or "").strip()
    prefix = (prefix or "").strip()
    if not subject or not prefix:
        raise ValueError("subject and prefix required")
    if subject.startswith("_"):
        raise ValueError("invalid subject or prefix")
    prof = subject_profile(subject, abbr=abbr)
    base = prof.output_dir(prefix, subject=subject)
    if base is None:
        raise ValueError("invalid subject or prefix")
    try:
        base.relative_to(Path(prof.output_root).resolve())
    except ValueError as exc:
        raise ValueError("path escapes the subject's output root") from exc
    if not base.is_dir():
        raise ValueError(f"no output folder for {subject}/{prefix}")

    if archive:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest_root = Path(prof.output_root) / "_archive" / subject
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / f"{prefix}_{stamp}"
        if dest.exists():
            raise ValueError(f"archive target already exists: {dest.name}")
        shutil.move(str(base), str(dest))
        return {"action": "archived", "path": str(dest),
                "subject": subject, "prefix": prefix}

    shutil.rmtree(base)
    # Remove the empty parent folder when nothing else lives there.
    parent = base.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    return {"action": "deleted", "subject": subject, "prefix": prefix}
