"""Notes Studio — single-file web server for the 3-agent transcript pipeline.

Standard-library only. Serves a single-page UI, exposes a small REST API to
start/stop runs, and streams live progress over Server-Sent Events.

Run:  python app/server.py [--port 8787] [--host 127.0.0.1]
"""
from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402
from app.paths import resolve_configured  # noqa: E402
from app.pipeline import Pipeline, PhaseError  # noqa: E402

HOST = "127.0.0.1"
PORT = 8787
STATIC = Path(__file__).parent / "static"
MAX_BODY = 4 * 1024 * 1024
REQUIRE_TOKEN = False
STATE_LOCK = threading.RLock()

# Dynamic job-slot limiter (replaces a fixed-size semaphore so the queue
# depth can be changed at runtime via POST /api/parallel).
MAX_PARALLEL = config.MAX_PARALLEL_RUNS
SLOTS_FREE = config.MAX_PARALLEL_RUNS
SLOTS_COND = threading.Condition()
# Per-subject slot usage, so one subject with 40 lectures cannot starve
# the other subjects queued behind it.
SLOTS_BY_SUBJECT: dict[str, int] = {}
# How many jobs a single subject may run at once (0 = no per-subject cap).
MAX_PARALLEL_PER_SUBJECT = 0


def get_max_parallel() -> int:
    return MAX_PARALLEL


def get_max_parallel_per_subject() -> int:
    return MAX_PARALLEL_PER_SUBJECT


def set_max_parallel_per_subject(n: int) -> int:
    """Cap the jobs one subject may hold at a time (0 = unlimited)."""
    global MAX_PARALLEL_PER_SUBJECT
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return MAX_PARALLEL_PER_SUBJECT
    MAX_PARALLEL_PER_SUBJECT = max(0, min(n, 64))
    return MAX_PARALLEL_PER_SUBJECT


def set_max_parallel(n: int) -> int:
    """Raise/lower the concurrency limit; queued workers are woken when raised."""
    global MAX_PARALLEL, SLOTS_FREE
    n = max(1, min(int(n or 1), 64))
    with SLOTS_COND:
        if n > MAX_PARALLEL:
            SLOTS_FREE += n - MAX_PARALLEL
        elif SLOTS_FREE > n:
            SLOTS_FREE = n
        MAX_PARALLEL = n
        SLOTS_COND.notify_all()
    return MAX_PARALLEL


def try_acquire_slot(subject_key: str = "", per_subject: int = 0) -> bool:
    """Claim a slot if one is free (non-blocking).

    *per_subject* caps how many jobs one subject may run at once, so a
    multi-subject batch shares the pool fairly.
    """
    global SLOTS_FREE
    key = (subject_key or "").strip()
    with SLOTS_COND:
        if SLOTS_FREE <= 0:
            return False
        if per_subject > 0 and key:
            if SLOTS_BY_SUBJECT.get(key, 0) >= per_subject:
                return False
        SLOTS_FREE -= 1
        if key:
            SLOTS_BY_SUBJECT[key] = SLOTS_BY_SUBJECT.get(key, 0) + 1
        return True


def release_slot(subject_key: str = ""):
    global SLOTS_FREE
    key = (subject_key or "").strip()
    with SLOTS_COND:
        SLOTS_FREE += 1
        if key and SLOTS_BY_SUBJECT.get(key, 0) > 0:
            SLOTS_BY_SUBJECT[key] -= 1
            if SLOTS_BY_SUBJECT[key] <= 0:
                SLOTS_BY_SUBJECT.pop(key, None)
        SLOTS_COND.notify()


class EventBus:
    """Pub/sub for SSE clients, with a per-run ring buffer for reconnect replay."""

    def __init__(self, maxlen_global: int = 12000, maxlen_run: int = 5000):
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._buf: deque = deque(maxlen=maxlen_global)
        self._by_run: dict[str, deque] = {}
        self._maxlen_run = maxlen_run

    def subscribe(self, replay: list[dict] | None = None) -> queue.Queue:
        q = queue.Queue(maxsize=8000)
        with self._lock:
            for ev in (replay or []):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    break
            # Register AFTER seeding so live events that arrive mid-seed
            # aren't duplicated in the seed AND aren't lost after it.
            self._subs.add(q)
        return q

    def subscribe_with_builder(self, builder) -> queue.Queue:
        """Call builder(get_events_for) under the lock, then subscribe.

        builder receives a function run_id -> list[event] reading the
        in-memory buffers. It must return the list to seed into the queue.
        """
        q = queue.Queue(maxsize=8000)
        with self._lock:
            def get_events_for(run_id: str) -> list[dict]:
                return list(self._by_run.get(run_id) or ())
            replay = builder(get_events_for) or []
            for ev in replay:
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    break
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            self._subs.discard(q)

    def publish(self, ev: dict):
        with self._lock:
            self._buf.append(ev)
            rid = ev.get("run_id")
            if rid:
                bucket = self._by_run.get(rid)
                if bucket is None:
                    bucket = deque(maxlen=self._maxlen_run)
                    self._by_run[rid] = bucket
                bucket.append(ev)
            for q in list(self._subs):
                self._put_drop_oldest(q, ev)

    @staticmethod
    def _put_drop_oldest(q: queue.Queue, ev: dict):
        try:
            q.put_nowait(ev)
            return
        except queue.Full:
            pass
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(ev)
        except queue.Full:
            pass

    def events_for(self, run_id: str) -> list[dict]:
        with self._lock:
            return list(self._by_run.get(run_id) or ())

    def drop_run(self, run_id: str):
        with self._lock:
            self._by_run.pop(run_id, None)


BUS = EventBus()
STATE = {
    "runs": {},            # run_id -> {"pipeline", "subject", "abbr", "prefix"}
    "last_error": None,
    "_seq": 0,
}


def inflight_summary(run: dict) -> dict:
    """History-card payload for a queued or running job."""
    pipeline = run.get("pipeline")
    stats: dict = {}
    if pipeline is not None:
        snap = getattr(pipeline, "_stats_snapshot", None)
        if callable(snap):
            try:
                stats = snap() or {}
            except Exception:  # noqa: BLE001
                stats = getattr(pipeline, "stats", None) or {}
        else:
            stats = getattr(pipeline, "stats", None) or {}
    phase = run.get("current_phase")
    if not phase and pipeline is not None:
        phase = getattr(pipeline, "_current_phase", None)
    tokens = stats.get("tokens") or {"input": 0, "output": 0, "reasoning": 0}
    abbr = run.get("abbr") or config.abbr_for_subject(run.get("subject", ""))
    get_retries = getattr(pipeline, "get_phase_retries", None) if pipeline else None
    phase_retries = (
        (get_retries() if callable(get_retries) else None)
        or stats.get("phase_retries")
        or run.get("phase_retries")
        or {"extractor": 0, "enricher": 0, "formatter": 0}
    )
    return {
        "run_id": run.get("run_id"),
        "subject": run.get("subject", ""),
        "prefix": run.get("prefix", ""),
        "abbr": abbr,
        "output_rel": (run.get("output_rel")
                       or (getattr(pipeline, "output_rel", None) if pipeline else None)
                       or ""),
        "status": "running" if run.get("active") else "queued",
        "mtime": run.get("t0") or time.time(),
        "started_at": run.get("t0"),
        "phases_requested": run.get("phases") or [],
        "in_flight": True,
        "current_phase": phase,
        "last_tool": ((getattr(pipeline, "_last_tool", None) or None)
                      if pipeline else None) or run.get("last_tool"),
        "cost": float(stats.get("cost") or 0),
        "tokens": tokens,
        "seconds": float(stats.get("seconds") or 0),
        "phase_stats": stats.get("phases") or {},
        "phase_retries": phase_retries,
        "model": getattr(pipeline, "model", None) if pipeline else run.get("model"),
        "variant": getattr(pipeline, "variant", None) if pipeline else run.get("variant"),
        "backend": getattr(pipeline, "backend", None) if pipeline else run.get("backend"),
        "last_failed_phase": run.get("last_failed_phase"),
    }


def is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    return h in {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def next_run_id() -> str:
    with STATE_LOCK:
        STATE["_seq"] += 1
        return f"run{STATE['_seq']}"


def safe_path(root: Path, rel: str) -> Path | None:
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p


def collect_replay_events(get_events_for=None,
                          jsonl_fallback: dict[str, list[dict]] | None = None
                          ) -> list[dict]:
    """Build the replay batch for in-flight runs.

    get_events_for(run_id) -> list[event] should read the in-memory buffer
    (typically under the EventBus lock). jsonl_fallback is preloaded outside
    the lock for runs whose buffer was empty at snapshot time.
    """
    get_events_for = get_events_for or (lambda _rid: [])
    jsonl_fallback = jsonl_fallback or {}
    out: list[dict] = []
    with STATE_LOCK:
        runs = list(STATE["runs"].items())
    for rid, run in runs:
        buffered = get_events_for(rid)
        if buffered:
            out.extend(buffered)
            continue
        if rid in jsonl_fallback:
            out.extend(jsonl_fallback[rid])
            continue
        out.append({
            "type": "pipeline_start",
            "run_id": rid,
            "subject": run["subject"],
            "prefix": run["prefix"],
            "abbr": run.get("abbr"),
            "output_rel": run.get("output_rel", ""),
            "replay": True,
        })
    return out


def _load_jsonl_replay(rid: str, run: dict) -> list[dict]:
    pipeline = run.get("pipeline")
    log = getattr(pipeline, "run_log", None) if pipeline else None
    fallback = {
        "type": "pipeline_start",
        "run_id": rid,
        "subject": run["subject"],
        "prefix": run["prefix"],
        "abbr": run.get("abbr"),
        "output_rel": run.get("output_rel", ""),
        "replay": True,
    }
    if not log or not Path(log).is_file():
        return [fallback]
    out = []
    try:
        with open(log, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                ev["run_id"] = rid
                ev["subject"] = run["subject"]
                ev["prefix"] = run["prefix"]
                ev["replay"] = True
                out.append(ev)
    except OSError:
        return [fallback]
    return out or [fallback]


class Handler(BaseHTTPRequestHandler):
    server_version = "NotesStudio/1.0"

    # ------------------------------------------------------------- helpers
    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, status=200, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _token_ok(self) -> bool:
        if not REQUIRE_TOKEN:
            return True
        expected = os.environ.get("NOTES_STUDIO_TOKEN") or ""
        if not expected:
            return False
        got = (self.headers.get("X-Notes-Token") or "").strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            got = auth[7:].strip()
        if not got:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            got = (qs.get("token") or [""])[0]
        return got == expected

    def _need_auth(self, mutating: bool) -> bool:
        if not REQUIRE_TOKEN:
            return False
        return mutating

    def _body(self) -> dict | None:
        raw_len = self.headers.get("Content-Length")
        try:
            length = int(raw_len or 0)
        except ValueError:
            self._json({"error": "invalid content-length"}, 400)
            return None
        if length > MAX_BODY:
            self._json({"error": "payload too large"}, 413)
            return None
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json({"error": "invalid json"}, 400)
            return None
        if not isinstance(data, dict):
            self._json({"error": "invalid json"}, 400)
            return None
        return data

    def log_message(self, fmt, *args):  # quiet
        pass

    def _api_authed(self) -> bool:
        if not REQUIRE_TOKEN:
            return True
        return self._token_ok()

    # --------------------------------------------------------------- routes
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            return self._serve_static("index.html")
        if path == "/healthz":
            with STATE_LOCK:
                n_runs = len(STATE["runs"])
            return self._json({
                "ok": True, "auth": REQUIRE_TOKEN, "runs": n_runs,
                "max_parallel": get_max_parallel(),
            })
        if ((path.startswith("/api/") or path.startswith("/outputs/"))
                and not self._api_authed()):
            return self._json({"error": "unauthorized"}, 401)
        if path == "/api/config":
            refresh = (query.get("refresh_models") or query.get("refresh") or ["0"])[0] in (
                "1", "true", "yes")
            backend = (query.get("backend") or [None])[0]
            return self._json(self._config(refresh_models=refresh, backend=backend))
        if path == "/api/models":
            refresh = (query.get("refresh") or query.get("refresh_models") or ["0"])[0] in (
                "1", "true", "yes")
            backend = (query.get("backend") or [None])[0]
            provider = (query.get("provider") or [None])[0]
            return self._json(config.list_models(provider=provider, refresh=refresh, backend=backend))
        if path == "/api/docs":
            return self._json(self._docs_folders())
        if path == "/api/profiles":
            return self._json(self._profiles())
        if path == "/api/parallel":
            return self._json({"max_parallel": get_max_parallel(),
                               "max_parallel_per_subject":
                                   get_max_parallel_per_subject(),
                               "in_flight": self._inflight_runs()})
        if path == "/api/history":
            try:
                limit = int((query.get("limit") or ["60"])[0] or 60)
            except ValueError:
                limit = 60
            runs = config.list_past_runs(
                limit=min(max(limit, 1), 200))
            inflight = self._inflight_runs()
            active_keys = {(r["subject"], r["prefix"]) for r in inflight}
            runs = [r for r in runs
                    if (r.get("subject"), r.get("prefix")) not in active_keys]
            return self._json({"runs": inflight + runs})
        if path == "/api/artifacts":
            subject = (query.get("subject") or [""])[0]
            prefix = (query.get("prefix") or [""])[0]
            abbr = (query.get("abbr") or [""])[0] or None
            if not subject or not prefix:
                return self._json({"error": "subject and prefix required"}, 400)
            try:
                config.output_paths(subject, prefix, abbr=abbr)
            except ValueError:
                return self._json({"error": "invalid subject or prefix"}, 400)
            arts = config.artifacts_status(subject, prefix, abbr=abbr)
            events = config.output_paths(subject, prefix, abbr=abbr)["events"]
            if events.is_file():
                arts["last_run"] = config.summarize_run_events(
                    events, subject, prefix, abbr=abbr or "")
            else:
                arts["last_run"] = None
            return self._json(arts)
        if path == "/api/events":
            return self._sse()
        if path == "/api/run-events":
            subject = (query.get("subject") or [""])[0]
            prefix = (query.get("prefix") or [""])[0]
            abbr = (query.get("abbr") or [""])[0] or None
            try:
                config.output_paths(subject, prefix, abbr=abbr)
            except ValueError:
                return self._json({"error": "invalid subject or prefix"}, 400)
            try:
                offset = int((query.get("offset") or ["0"])[0] or 0)
            except ValueError:
                offset = 0
            try:
                limit = int((query.get("limit") or ["1500"])[0] or 1500)
            except ValueError:
                limit = 1500
            return self._json(config.read_run_events(
                subject, prefix, offset=offset, limit=limit, abbr=abbr))
        if path == "/api/outputs":
            subject = (query.get("subject") or [""])[0]
            prefix = (query.get("prefix") or [""])[0]
            abbr = (query.get("abbr") or [""])[0] or None
            try:
                config.output_paths(subject, prefix, abbr=abbr)
            except ValueError:
                return self._json({"error": "invalid subject or prefix"}, 400)
            return self._json({"files": config.list_outputs(subject, prefix,
                                                            abbr=abbr)})
        if path == "/api/keys":
            return self._get_keys()
        if path.startswith("/outputs/"):
            return self._serve_output(path)
        return self._text("not found", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if not self._api_authed():
            return self._json({"error": "unauthorized"}, 401)
        if path == "/api/run":
            return self._start_run()
        if path == "/api/preview":
            return self._preview_run()
        if path == "/api/stop":
            return self._stop_run()
        if path == "/api/subjects":
            return self._add_subject()
        if path == "/api/subject-instructions":
            return self._set_subject_instructions()
        if path == "/api/profiles":
            if (query.get("action") or [""])[0] == "delete":
                return self._delete_profile()
            return self._save_profile()
        if path == "/api/parallel":
            return self._set_parallel()
        if path == "/api/keys":
            return self._set_keys()
        if path == "/api/history/delete":
            return self._delete_history()
        return self._text("not found", 404)

    # -------------------------------------------------------------- handlers
    def _serve_static(self, name):
        p = STATIC / name
        if not p.is_file():
            return self._text("missing", 404)
        body = p.read_bytes()
        ctype = {"html": "text/html; charset=utf-8"}.get(
            p.suffix.lstrip("."), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_output(self, path: str):
        # The path arrives percent-encoded ("Quantum%20Computing/...").
        rel = urllib.parse.unquote(path[len("/outputs/"):])
        p = None
        # Each subject may keep its notes under a different root, so try
        # every known output root and serve the first match.
        for root in config.output_roots():
            cand = safe_path(root, rel)
            if cand and cand.is_file():
                p = cand
                break
        if not p:
            return self._text("not found", 404)
        body = p.read_bytes()
        suffix = p.suffix.lstrip(".").lower()
        ctype = {"html": "text/html; charset=utf-8",
                 "md": "text/markdown; charset=utf-8",
                 "json": "application/json; charset=utf-8",
                 "yaml": "text/yaml; charset=utf-8",
                 "yml": "text/yaml; charset=utf-8",
                 "txt": "text/plain; charset=utf-8"}.get(
            suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        if suffix == "html":
            self.send_header("Content-Security-Policy", "sandbox")
        self.end_headers()
        self.wfile.write(body)

    def _config(self, refresh_models: bool = False,
                backend: str | None = None) -> dict:
        subjects = [{"abbr": a, "subject": v}
                    for a, v in sorted(config.all_subjects().items())]
        docs = self._docs_folders()
        docs_by_abbr = {}
        profiles = {}
        for a, name in config.all_subjects().items():
            prof = config.subject_profile(a)
            matched = prof.resolve_docs()
            if matched:
                docs_by_abbr[a] = {"name": matched.name, "path": str(matched)}
            profiles[a] = config.profile_to_dict(prof)
        backend = config.normalize_backend(backend)
        catalog = config.list_models(refresh=refresh_models, backend=backend)
        bmeta = config.backend_meta(backend)
        with STATE_LOCK:
            runs = [{"run_id": rid, "subject": r["subject"],
                     "abbr": r["abbr"], "prefix": r["prefix"]}
                    for rid, r in STATE["runs"].items()]
        return {
            "workspace": str(config.WORKSPACE),
            "backend": backend,
            "backends": [{"id": b["id"], "label": b["label"],
                          "available": config.is_backend_available(b["id"])[0],
                          "executable": config.is_backend_available(b["id"])[1]}
                         for b in config.BACKENDS.values()],
            "model": catalog.get("default_model") or bmeta["model"],
            "variant": catalog.get("default_variant") or bmeta["variant"],
            "models": catalog.get("models") or [],
            "models_ok": bool(catalog.get("ok")),
            "models_error": catalog.get("error"),
            "models_hint": catalog.get("hint"),
            "model_provider": catalog.get("provider") or bmeta["provider"],
            "subjects": subjects,
            "subject_instructions": config.all_subject_instructions(),
            "subject_profiles": profiles,
            "transcripts": config.list_transcripts(),
            "docs": docs,
            "docs_by_abbr": docs_by_abbr,
            "runs": runs,
            "max_parallel": get_max_parallel(),
            "max_parallel_per_subject": get_max_parallel_per_subject(),
        }

    def _docs_folders(self) -> list[dict]:
        """Companion-doc folders the user can point Agent 2 at."""
        out = []
        root = config.DOCS_DIR
        if not root.is_dir():
            return out
        for p in sorted(root.iterdir()):
            if p.is_dir():
                out.append({"name": p.name, "path": str(p)})
        return out

    def _profiles(self) -> dict:
        """Per-subject profiles plus the transcripts each subject owns."""
        profiles = config.all_profiles()
        items = []
        for abbr, prof in sorted(profiles.items()):
            entry = config.profile_to_dict(prof)
            entry["transcripts"] = config.list_transcripts(abbr=abbr)
            items.append(entry)
        return {"profiles": items,
                "file": str(config.SUBJECT_PROFILES_FILE),
                "fields": {
                    "transcripts_dir": "Folder holding this subject's "
                                       "transcripts (.txt).",
                    "docs_dir": "Companion docs folder for Agent 2. May sit "
                                "outside the workspace.",
                    "output_root": "Root folder for this subject's output. "
                                   "Must be inside the workspace.",
                    "output_template": "Path under the output root, e.g. "
                                       "'{subject}/{prefix}'.",
                    "prefix_template": "Lecture prefix, e.g. "
                                       "'{abbr}_Lecture_{lecture}'.",
                    "topic_mapping": "Topic mapping YAML for this subject.",
                }}

    def _save_profile(self):
        body = self._body()
        if body is None:
            return
        abbr = (body.get("abbr") or "").strip().upper()
        if not abbr:
            return self._json({"error": "abbr required"}, 400)
        fields = body.get("fields")
        if not isinstance(fields, dict):
            fields = {k: v for k, v in body.items()
                      if k not in ("abbr", "action")}
        try:
            prof = config.save_profile(abbr, fields)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        entry = config.profile_to_dict(prof)
        entry["transcripts"] = config.list_transcripts(abbr=abbr)
        return self._json({"ok": True, "profile": entry,
                           "profiles": self._profiles()["profiles"]})

    def _delete_profile(self):
        body = self._body()
        if body is None:
            return
        abbr = (body.get("abbr") or "").strip().upper()
        if not abbr:
            return self._json({"error": "abbr required"}, 400)
        try:
            config.delete_profile(abbr)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        return self._json({"ok": True, "profiles": self._profiles()["profiles"]})

    def _inflight_runs(self) -> list[dict]:
        """Minimal run summaries for jobs in the queue (queued or running)."""
        out = []
        with STATE_LOCK:
            runs = list(STATE["runs"].values())
        for run in runs:
            out.append(inflight_summary(run))
        out.sort(key=lambda r: r["mtime"])
        return out

    def _set_parallel(self):
        body = self._body()
        if body is None:
            return
        if body.get("max_parallel_per_subject") is not None:
            set_max_parallel_per_subject(body["max_parallel_per_subject"])
        elif "max_parallel" not in body:
            self._json(
                {"error": "max_parallel must be an integer (1-12)"}, 400)
            return
        if body.get("max_parallel") is not None:
            try:
                n = int(body.get("max_parallel"))
            except (TypeError, ValueError):
                return self._json(
                    {"error": "max_parallel must be an integer (1-64)"}, 400)
            set_max_parallel(n)
        self._json({"ok": True, "max_parallel": get_max_parallel(),
                    "max_parallel_per_subject": get_max_parallel_per_subject(),
                    "in_flight": self._inflight_runs()})

    def _get_keys(self):
        keys = ["CURSOR_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY"]
        result = {}
        for k in keys:
            val = os.environ.get(k, "")
            if val:
                masked = (val[:6] + "..." + val[-4:]) if len(val) > 12 else ("***" if val else "")
                result[k] = {"set": True, "masked": masked, "length": len(val)}
            else:
                result[k] = {"set": False, "masked": "", "length": 0}
        self._json({"ok": True, "keys": result})

    def _set_keys(self):
        body = self._body()
        if body is None:
            return
        env_file = config.WORKSPACE / ".env"
        existing: dict[str, str] = {}
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        existing[k.strip()] = v.strip().strip("'\"")
            except Exception:
                pass
        allowed_keys = {"CURSOR_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY"}
        for k, v in body.items():
            if k in allowed_keys and isinstance(v, str):
                v_clean = v.strip()
                if v_clean:
                    existing[k] = v_clean
                    os.environ[k] = v_clean
                elif v_clean == "" and k in existing:
                    # Clear if blank
                    del existing[k]
                    os.environ.pop(k, None)
        lines = [f"{k}={v}" for k, v in sorted(existing.items())]
        env_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        config.clear_models_cache()
        self._get_keys()

    def _preview_run(self):
        body = self._body()
        if body is None:
            return
        planned = self._plan_jobs(body, start=False)
        if planned is None:
            return
        self._json(planned)

    def _plan_jobs(self, body: dict, start: bool) -> dict | None:
        """Plan (and optionally start) jobs for one or many subjects.

        A body carrying a ``subjects`` list is a multi-subject batch: the
        top-level keys are shared settings and each entry may override
        them. Otherwise the body describes a single subject (legacy).
        """
        entries = body.get("subjects")
        if isinstance(entries, list) and entries:
            return self._plan_batch(body, entries, start)

        abbr = body.get("abbr") or body.get("subject")
        result = self._plan_subject_jobs(body, start)
        if result.get("error"):
            self._json({"error": result["error"]}, 400)
            return None
        return result

    def _plan_batch(self, body: dict, entries: list, start: bool) -> dict:
        """Plan jobs across many subjects, sharing the top-level settings."""
        jobs: list[dict] = []
        rejected: list[dict] = []
        shared = {k: v for k, v in body.items() if k != "subjects"}
        taken_by_subj: dict[str, set[str]] = defaultdict(set)
        for entry in entries:
            if not isinstance(entry, dict):
                rejected.append({"subject": "", "reason": "invalid subject entry"})
                continue
            merged = dict(shared)
            merged.update({k: v for k, v in entry.items() if v is not None})
            abbr = str(merged.get("abbr") or merged.get("subject") or "").strip().upper()
            merged["abbr"] = abbr
            result = self._plan_subject_jobs(merged, start, taken_prefixes=taken_by_subj[abbr])
            if result.get("error"):
                rejected.append({"subject": abbr, "reason": result["error"]})
                continue
            for j in result.get("jobs") or []:
                pfx = (j.get("prefix") or "").lower()
                if pfx:
                    taken_by_subj[abbr].add(pfx)
            jobs.extend(result.get("jobs") or [])
            rejected.extend(result.get("rejected") or [])
        return {"ok": bool(jobs), "jobs": jobs, "rejected": rejected,
                "max_parallel": get_max_parallel(),
                "subjects": len(entries)}

    def _plan_subject_jobs(self, body: dict, start: bool,
                           taken_prefixes: set[str] | None = None) -> dict:
        """Plan the jobs for a single subject."""
        abbr = str(body.get("abbr") or body.get("subject") or "").strip().upper()
        subjects = config.all_subjects()
        if abbr not in subjects:
            return {"ok": False, "error": f"unknown subject {abbr!r}",
                    "jobs": [], "rejected": []}
        subj = subjects[abbr]
        prof = config.subject_profile(abbr)
        docs = body.get("docs") or None
        if docs and docs != "__none__":
            resolved_docs = resolve_configured(config.WORKSPACE, docs)
            if resolved_docs is None:
                return {"ok": False,
                        "error": "docs folder could not be resolved",
                        "jobs": [], "rejected": []}
            docs = str(resolved_docs)
        resume = bool(body.get("resume"))
        retry = bool(body.get("retry"))
        phases_in = body.get("phases")
        phases = [int(p) for p in (phases_in or [1, 2, 3])]
        backend = config.normalize_backend(body.get("backend"))
        backend_avail, backend_exe = config.is_backend_available(backend)
        if not backend_avail:
            b_label = config.backend_meta(backend).get("label", backend)
            return {"ok": False, "error": (
                f"Backend '{b_label}' is not available on this machine "
                f"(executable '{backend_exe}' was not found on PATH). Please "
                f"select an installed backend (such as OpenCode, Command Code, "
                f"or Cursor Agent)."), "jobs": [], "rejected": []}
        catalog = config.list_models(backend=backend)
        model, variant = config.resolve_model_choice(
            body.get("model"), body.get("variant"), catalog, backend=backend)
        # Cursor fast mode: only meaningful for cursor backend
        fast = None
        if backend == config.BACKEND_CURSOR:
            raw_fast = body.get("fast")
            if raw_fast is not None:
                fast = bool(raw_fast)
        transcripts = body.get("transcripts") or []
        single = body.get("transcript")
        if single:
            transcripts.append(single)
        transcripts = [t for t in dict.fromkeys(
            t.strip() for t in transcripts) if t]
        if not transcripts and (resume or retry) and body.get("prefix"):
            events = config.output_paths(
                subj, body["prefix"].strip(), abbr=abbr)["events"]
            if events.is_file():
                prev = config.summarize_run_events(
                    events, subj, body["prefix"].strip(), abbr=abbr)
                if prev.get("transcript") and Path(prev["transcript"]).is_file():
                    transcripts = [prev["transcript"]]
        if not transcripts:
            return {"ok": False, "error": "transcript file required",
                    "jobs": [], "rejected": []}

        jobs, rejected = [], []
        idents = []
        for t in transcripts:
            prefix, lecture_num = _job_identity(
                t, body, transcripts, subjects, prof=prof)
            idents.append((t, prefix, lecture_num))
        resolved = _resolve_batch_prefixes(
            [(Path(t).stem, p) for t, p, _ in idents],
            taken=taken_prefixes)
        for (t, _, lecture_num), (prefix, adjusted) in zip(idents, resolved):
            if not prefix:
                return {"ok": False,
                        "error": "prefix required (e.g. NLP_Lecture_9)",
                        "jobs": [], "rejected": []}
            tpath = _resolve_transcript(prof, t)
            if tpath is None:
                rejected.append({"transcript": t, "prefix": prefix,
                                 "reason": "transcript is not under the "
                                           "subject's transcripts folder"})
                continue
            t = str(tpath)
            if not lecture_num:
                lecture_num = _prefix_lecture(prefix)
            job_phases = list(phases)
            if resume or retry:
                arts = config.artifacts_status(subj, prefix, abbr=abbr)
                events = config.output_paths(subj, prefix, abbr=abbr)["events"]
                last = (config.summarize_run_events(events, subj, prefix, abbr=abbr)
                        if events.is_file() else None)
                if retry and last and last.get("retry_phases"):
                    job_phases = list(last["retry_phases"])
                elif arts["resume_phases"]:
                    job_phases = list(arts["resume_phases"])
                else:
                    rejected.append({
                        "transcript": t, "prefix": prefix,
                        "reason": "already complete — select phases to re-run",
                    })
                    continue
            if not job_phases:
                rejected.append({"transcript": t, "prefix": prefix,
                                 "reason": "no phases to run"})
                continue
            extra_instructions = body.get("extra_instructions")
            if not isinstance(extra_instructions, dict):
                extra_instructions = config.get_subject_instructions(abbr)
            else:
                cleaned_inst = {}
                for ph, inst in extra_instructions.items():
                    norm_ph = str(ph).strip().lower()
                    norm_ph = {"1": "extractor", "2": "enricher", "3": "formatter"}.get(norm_ph, norm_ph)
                    if norm_ph in ("extractor", "enricher", "formatter") and isinstance(inst, str) and inst.strip():
                        cleaned_inst[norm_ph] = inst.strip()
                extra_instructions = cleaned_inst
                if body.get("save_subject_instructions"):
                    config.save_subject_instructions(abbr, extra_instructions)

            spec = {
                "transcript": t, "subject": subj, "abbr": abbr,
                "prefix": prefix, "lecture_num": lecture_num,
                "phases": job_phases, "backend": backend,
                "model": model, "variant": variant, "docs": docs,
                "adjusted": adjusted, "fast": fast,
                "extra_instructions": extra_instructions,
                "output_rel": config.output_rel_path(subj, prefix, abbr=abbr),
            }
            if not start:
                jobs.append(spec)
                continue
            with STATE_LOCK:
                dup = next((r for r in STATE["runs"].values()
                            if r["subject"] == subj
                            and r["prefix"] == prefix), None)
                if dup:
                    rejected.append({
                        "transcript": t, "prefix": prefix,
                        "reason": f"already running as {dup['run_id']}"})
                    continue
                run_id = next_run_id()
                run = {"pipeline": None, "subject": subj,
                       "abbr": abbr, "prefix": prefix, "run_id": run_id,
                       "phases": list(job_phases), "t0": time.time(),
                       "active": False, "current_phase": None,
                       "output_rel": spec.get("output_rel", "")}
                STATE["runs"][run_id] = run
                STATE["last_error"] = None
            self._launch_job(spec, run_id, run)
            jobs.append({"run_id": run_id, **{k: spec[k] for k in
                        ("subject", "abbr", "prefix", "lecture_num", "phases",
                         "backend", "model", "variant", "adjusted", "docs") if k in spec}})
        return {"ok": bool(jobs), "jobs": jobs, "rejected": rejected,
                "max_parallel": get_max_parallel()}

    def _launch_job(self, spec: dict, run_id: str, run: dict):
        def emit(ev: dict, _rid=run_id, _run=run):
            ev["run_id"] = _rid
            ev["subject"] = _run["subject"]
            ev["prefix"] = _run["prefix"]
            et = ev.get("type")
            if et == "phase_start":
                _run["current_phase"] = ev.get("phase")
            elif et == "phase_end":
                if not ev.get("ok"):
                    _run["last_failed_phase"] = ev.get("phase")
                elif _run.get("current_phase") == ev.get("phase"):
                    _run["current_phase"] = None
            elif et in ("retry_start", "bounce_to_enricher"):
                if ev.get("phase_retries"):
                    _run["phase_retries"] = dict(ev["phase_retries"])
                elif ev.get("failed_phase"):
                    ph = ev.get("failed_phase")
                    _run.setdefault("phase_retries", {"extractor": 0, "enricher": 0, "formatter": 0})
                    _run["phase_retries"][ph] = _run["phase_retries"].get(ph, 0) + 1
            elif et == "heartbeat":
                if ev.get("phase"):
                    _run["current_phase"] = ev.get("phase")
                if ev.get("last_tool"):
                    _run["last_tool"] = ev.get("last_tool")
            BUS.publish(ev)

        pipeline = Pipeline(
            subject=spec["subject"], abbr=spec["abbr"], prefix=spec["prefix"],
            lecture_num=spec["lecture_num"], transcript=spec["transcript"],
            phases=spec["phases"], emit=emit, docs_dir=spec["docs"],
            run_id=run_id, model=spec["model"], variant=spec["variant"],
            backend=spec["backend"], fast=spec.get("fast"),
            extra_instructions=spec.get("extra_instructions"))
        run["pipeline"] = pipeline

        def worker(_rid=run_id, _p=pipeline, _run=run):
            emit({"type": "queued", "run_id": _rid,
                  "subject": _run["subject"], "prefix": _run["prefix"]})
            slot_acquired = False
            subject_key = _run.get("abbr") or _run.get("subject") or ""
            per_subject = get_max_parallel_per_subject()
            try:
                while True:
                    if _p.stop_flag:
                        BUS.publish({"type": "pipeline_end", "run_id": _rid,
                                     "status": "stopped"})
                        return
                    if try_acquire_slot(subject_key, per_subject):
                        slot_acquired = True
                        break
                    time.sleep(0.25)
                with STATE_LOCK:
                    _run["active"] = True
                if _p.stop_flag:
                    BUS.publish({"type": "pipeline_end", "run_id": _rid,
                                 "status": "stopped"})
                    return
                _p.run()
            except Exception as exc:  # noqa: BLE001
                BUS.publish({"type": "pipeline_end", "run_id": _rid,
                             "status": "error",
                             "error": f"{type(exc).__name__}: {exc}"})
            finally:
                if slot_acquired:
                    release_slot(subject_key)
                with STATE_LOCK:
                    STATE["runs"].pop(_rid, None)
                BUS.publish({"type": "idle", "run_id": _rid})
                threading.Timer(120.0, BUS.drop_run, args=(_rid,)).start()

        threading.Thread(target=worker, daemon=True).start()

    def _start_run(self):
        body = self._body()
        if body is None:
            return
        planned = self._plan_jobs(body, start=True)
        if planned is None:
            return
        self._json(planned)

    def _stop_run(self):
        body = self._body()
        if body is None:
            return
        run_id = body.get("run_id")
        with STATE_LOCK:
            targets = ([run_id] if run_id else list(STATE["runs"].keys()))
            pipelines = []
            for rid in targets:
                run = STATE["runs"].get(rid)
                if run and run["pipeline"]:
                    pipelines.append((rid, run["pipeline"]))
        stopped = []
        for rid, pipe in pipelines:
            pipe.stop()
            stopped.append(rid)
        self._json({"ok": True, "stopped": stopped})

    def _delete_history(self):
        body = self._body()
        if body is None:
            return
        subject = (body.get("subject") or "").strip()
        prefix = (body.get("prefix") or "").strip()
        abbr = (body.get("abbr") or "").strip().upper() or None
        archive = bool(body.get("archive"))
        if not subject or not prefix:
            return self._json({"error": "subject and prefix required"}, 400)
        try:
            config.output_paths(subject, prefix, abbr=abbr)
        except ValueError:
            return self._json({"error": "invalid subject or prefix"}, 400)
        # Refuse to delete while a live run owns this prefix.
        with STATE_LOCK:
            busy = next((r for r in STATE["runs"].values()
                         if r["subject"] == subject and r["prefix"] == prefix),
                        None)
        if busy:
            return self._json({
                "error": f"run {busy['run_id']} is still using this prefix — stop it first"
            }, 409)
        try:
            result = config.delete_past_run(subject, prefix, archive=archive,
                                            abbr=abbr)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except OSError as exc:
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        self._json({"ok": True, **result})

    def _add_subject(self):
        body = self._body()
        if body is None:
            return
        abbr = (body.get("abbr") or "").strip().upper()
        name = (body.get("name") or "").strip()
        if not abbr or not re.match(r"^[A-Z0-9_]{1,8}$", abbr):
            return self._json({"error": "abbreviation must be 1-8 chars of "
                               "A-Z, 0-9 or _"}, 400)
        if not name:
            return self._json({"error": "subject name required"}, 400)
        if abbr in config.SUBJECTS:
            return self._json({"error": f"{abbr} is already a built-in "
                               "subject"}, 409)
        try:
            subjects = config.save_subject(abbr, name)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        return self._json({"ok": True, "abbr": abbr, "name": name,
                           "subjects": [{"abbr": a, "subject": v}
                                        for a, v in sorted(subjects.items())]})

    def _set_subject_instructions(self):
        body = self._body()
        if body is None:
            return
        abbr = (body.get("abbr") or "").strip().upper()
        if not abbr:
            return self._json({"error": "subject abbreviation required"}, 400)
        instructions = body.get("instructions")
        if instructions is not None and not isinstance(instructions, dict):
            return self._json({"error": "instructions must be a mapping of phase to text"}, 400)
        try:
            all_inst = config.save_subject_instructions(abbr, instructions or {})
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        return self._json({
            "ok": True,
            "abbr": abbr,
            "instructions": all_inst.get(abbr, {}),
            "subject_instructions": all_inst,
        })

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        # Preload jsonl only for runs whose memory buffer is currently empty.
        with STATE_LOCK:
            active = dict(STATE["runs"])
        jsonl_fallback: dict[str, list[dict]] = {}
        for rid, run in active.items():
            if not BUS.events_for(rid):
                jsonl_fallback[rid] = _load_jsonl_replay(rid, run)

        def builder(get_events_for):
            events = collect_replay_events(get_events_for, jsonl_fallback)
            if not events:
                return []
            return ([{"type": "replay_start", "count": len(events)}]
                    + events
                    + [{"type": "replay_end"}])

        q = BUS.subscribe_with_builder(builder)
        try:
            last = time.time()
            while True:
                try:
                    ev = q.get(timeout=10)
                    self.wfile.write(("data: " + json.dumps(ev) + "\n\n")
                                     .encode("utf-8"))
                    self.wfile.flush()
                    last = time.time()
                except queue.Empty:
                    if time.time() - last > 25:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            BUS.unsubscribe(q)


def _resolve_transcript(prof, raw: str) -> Path | None:
    """Resolve a transcript path and require it under the subject's folder.

    Each subject may own a transcripts folder of its own (inside or
    outside the workspace), so the check follows the profile rather than
    the shared ``transcript files/`` directory.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        base = Path(prof.transcripts_dir).resolve()
    except (OSError, RuntimeError, TypeError):
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = base / raw
    try:
        resolved = p.resolve()
        resolved.relative_to(base)
    except (ValueError, OSError, RuntimeError):
        return None
    return resolved


def _job_identity(transcript: str, body: dict, transcripts: list[str],
                  subjects: dict, prof=None) -> tuple[str, str]:
    prefix = (body.get("prefix") or "").strip()
    lecture_num = str(body.get("lecture_num") or "").strip()
    name = Path(transcript).name
    stem = Path(transcript).stem
    derived = _derive_prefix(stem)
    guess = config.guess_from_filename(name, subjects)
    abbr = (body.get("abbr") or body.get("subject") or "").strip()
    lec = guess.get("lecture_num") or derived[1]

    def _auto_prefix(num: str) -> str:
        """Prefix for a lecture number, honouring the subject's template."""
        if prof is not None:
            return prof.prefix_for(num)
        return f"{abbr}_Lecture_{num}" if num else f"{abbr}_Lecture"

    if abbr:
        # Subject was explicitly specified; do not allow a guessed prefix from
        # an unrelated transcript filename to override the chosen subject.
        # Only the lecture number is auto-derived from the transcript name.
        if not lecture_num:
            lecture_num = lec
        if len(transcripts) > 1:
            if not prefix:
                prefix = _auto_prefix(lecture_num)
            else:
                if lecture_num and not _prefix_lecture(prefix):
                    prefix = f"{prefix}_{lecture_num}"
                else:
                    prefix = f"{prefix}_{derived[0]}"
        else:
            if not prefix:
                prefix = _auto_prefix(lecture_num)
            elif not lecture_num:
                lecture_num = _prefix_lecture(prefix) or lec
        return prefix, lecture_num

    if len(transcripts) > 1:
        if not prefix:
            prefix = guess.get("prefix") or derived[0]
        else:
            per = guess.get("prefix") or derived[0]
            if prefix != per:
                prefix = (f"{prefix}_{derived[0]}" if not guess.get("prefix")
                          else guess["prefix"])
        if not lecture_num:
            lecture_num = lec
    else:
        if not prefix:
            prefix = guess.get("prefix") or derived[0]
        if not lecture_num:
            lecture_num = (guess.get("lecture_num")
                           or _prefix_lecture(prefix)
                           or derived[1])
    return prefix, lecture_num


def _prefix_lecture(prefix: str) -> str:
    parts = re.split(r"[_\-]", prefix or "")
    if parts and parts[-1].isdigit():
        return parts[-1]
    return ""


_PREFIX_TAG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}$")


def _disambiguated_prefix(base: str, stem: str,
                          taken_lower: set[str]) -> str:
    """Build a unique prefix from *base* plus a filename-derived tag.

    'UDL_Lecture' + 'UDL - Week 3' -> 'UDL_Lecture_Week_3'; falls back to
    numbered suffixes ('UDL_Lecture_2', ...) when the tag adds nothing.
    Returns '' if no acceptable candidate exists.
    """
    tag = re.sub(r"[^\w.\-]+", "_", stem).strip("_-. ")
    head = re.sub(r"[^\w]+", "", base.split("_")[0]).lower()
    bits = [b for b in tag.split("_") if b]
    while bits and re.sub(r"[^\w]+", "", bits[0]).lower() == head:
        bits.pop(0)
    candidates = []
    if bits:
        candidates.append("_".join([base] + bits))
    candidates += [f"{base}_{n}" for n in range(2, len(taken_lower) + 3)]
    for cand in candidates:
        if _PREFIX_TAG_OK.match(cand) and cand.lower() not in taken_lower:
            return cand
    return ""


def _resolve_batch_prefixes(derived: list[tuple[str, str]],
                           taken: set[str] | None = None) -> list[tuple[str, bool]]:
    """Map (stem, prefix) pairs to (unique_prefix, adjusted).

    Windows output folders are case-insensitive, so collisions are
    detected case-insensitively. Later duplicates get a disambiguated
    prefix instead of silently overwriting an earlier job's artifacts.
    """
    out: list[tuple[str, bool]] = []
    taken_set: set[str] = set(taken) if taken is not None else set()
    for stem, prefix in derived:
        adjusted = False
        if prefix.lower() in taken_set:
            uniq = _disambiguated_prefix(prefix, stem, taken_set)
            if uniq:
                prefix, adjusted = uniq, True
        taken_set.add(prefix.lower())
        out.append((prefix, adjusted))
    if taken is not None:
        taken.update(taken_set)
    return out


def _derive_prefix(stem: str) -> tuple[str, str]:
    """Turn a transcript filename stem into (prefix, lecture_num).

    'Lecture01'   -> ('Lecture01', '1')
    'NLP_L3.txt'  -> ('NLP_L3', '3')
    'lecture 5'   -> ('lecture_5', '5')
    """
    import re
    name = re.sub(r"[^\w\-.]+", "_", stem).strip("_")
    m = re.search(r"(\d+)\s*$", name)
    lecture = str(int(m.group(1))) if m else ""
    return name, lecture


def main():
    import webbrowser

    global HOST, PORT, REQUIRE_TOKEN
    args = sys.argv[1:]
    if "--host" in args:
        HOST = args[args.index("--host") + 1]
    if "--port" in args:
        PORT = int(args[args.index("--port") + 1])
    token = os.environ.get("NOTES_STUDIO_TOKEN") or ""
    if not is_loopback_host(HOST):
        if not token.strip():
            print("NOTES_STUDIO_TOKEN is required when --host is not loopback",
                  file=sys.stderr)
            sys.exit(1)
        REQUIRE_TOKEN = True
    elif token.strip():
        REQUIRE_TOKEN = True

    url = f"http://{HOST}:{PORT}"

    # ── Print startup banner to the real console (before redirect) ──
    console_out = sys.stdout
    console_err = sys.stderr
    print(flush=True, file=console_out)
    print(f"  Notes Studio is starting...", flush=True, file=console_out)
    print(f"  URL:       {url}", flush=True, file=console_out)
    print(f"  Workspace: {config.WORKSPACE}", flush=True, file=console_out)
    print(f"  Backend:   opencode (model default resolves live per backend)",
          flush=True, file=console_out)
    print(flush=True, file=console_out)

    # Log to files in the workspace root so crashes are diagnosable without
    # any shell output redirection.
    try:
        out = open(config.WORKSPACE / "server_out.log", "a", encoding="utf-8",
                   buffering=1)
        err = open(config.WORKSPACE / "server_err.log", "a", encoding="utf-8",
                   buffering=1)
        sys.stdout = out
        sys.stderr = err
    except OSError:
        pass
    print(f"Notes Studio on {url}", flush=True)
    print(f"Workspace: {config.WORKSPACE}", flush=True)
    print("Backend: opencode (model default resolves live per backend)", flush=True)

    server = ThreadingHTTPServer((HOST, PORT), Handler)

    # Print confirmation and open browser after server is ready
    print(f"  Server is live at {url}", flush=True, file=console_out)
    print(f"  Opening browser...", flush=True, file=console_out)
    print(f"  Press Ctrl+C to stop.", flush=True, file=console_out)
    print(flush=True, file=console_out)
    threading.Thread(target=lambda: webbrowser.open(url),
                     daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n  Server stopped.", flush=True, file=console_out)
        try:
            out.close(); err.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
