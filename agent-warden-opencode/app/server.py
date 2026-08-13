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
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402
from app.paths import confine, resolve_under  # noqa: E402
from app.pipeline import Pipeline, PhaseError  # noqa: E402

HOST = "127.0.0.1"
PORT = 8787
STATIC = Path(__file__).parent / "static"
MAX_BODY = 1 * 1024 * 1024
REQUIRE_TOKEN = False
STATE_LOCK = threading.RLock()
RUN_SLOTS = threading.Semaphore(config.MAX_PARALLEL_RUNS)


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
            "replay": True,
        })
    return out


def _load_jsonl_replay(rid: str, run: dict) -> list[dict]:
    pipeline = run.get("pipeline")
    log = getattr(pipeline, "run_log", None) if pipeline else None
    if not log or not Path(log).is_file():
        return [{
            "type": "pipeline_start",
            "run_id": rid,
            "subject": run["subject"],
            "prefix": run["prefix"],
            "abbr": run.get("abbr"),
            "replay": True,
        }]
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
        return [{
            "type": "pipeline_start",
            "run_id": rid,
            "subject": run["subject"],
            "prefix": run["prefix"],
            "abbr": run.get("abbr"),
            "replay": True,
        }]
    return out or [{
        "type": "pipeline_start",
        "run_id": rid,
        "subject": run["subject"],
        "prefix": run["prefix"],
        "abbr": run.get("abbr"),
        "replay": True,
    }]


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
                "max_parallel": config.MAX_PARALLEL_RUNS,
            })
        if ((path.startswith("/api/") or path.startswith("/outputs/"))
                and not self._api_authed()):
            return self._json({"error": "unauthorized"}, 401)
        if path == "/api/config":
            refresh = (query.get("refresh_models") or ["0"])[0] in (
                "1", "true", "yes")
            backend = (query.get("backend") or [None])[0]
            return self._json(self._config(refresh_models=refresh, backend=backend))
        if path == "/api/models":
            refresh = (query.get("refresh") or ["0"])[0] in (
                "1", "true", "yes")
            backend = (query.get("backend") or [None])[0]
            return self._json(config.list_models(refresh=refresh, backend=backend))
        if path == "/api/docs":
            return self._json(self._docs_folders())
        if path == "/api/history":
            try:
                limit = int((query.get("limit") or ["60"])[0] or 60)
            except ValueError:
                limit = 60
            return self._json({"runs": config.list_past_runs(
                limit=min(max(limit, 1), 200))})
        if path == "/api/artifacts":
            subject = (query.get("subject") or [""])[0]
            prefix = (query.get("prefix") or [""])[0]
            if not subject or not prefix:
                return self._json({"error": "subject and prefix required"}, 400)
            if confine(config.OUTPUTS_DIR, subject, prefix) is None:
                return self._json({"error": "invalid subject or prefix"}, 400)
            arts = config.artifacts_status(subject, prefix)
            events = config.output_paths(subject, prefix)["events"]
            if events.is_file():
                arts["last_run"] = config.summarize_run_events(
                    events, subject, prefix)
            else:
                arts["last_run"] = None
            return self._json(arts)
        if path == "/api/events":
            return self._sse()
        if path == "/api/run-events":
            subject = (query.get("subject") or [""])[0]
            prefix = (query.get("prefix") or [""])[0]
            if confine(config.OUTPUTS_DIR, subject, prefix) is None:
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
                subject, prefix, offset=offset, limit=limit))
        if path == "/api/outputs":
            subject = (query.get("subject") or [""])[0]
            prefix = (query.get("prefix") or [""])[0]
            if confine(config.OUTPUTS_DIR, subject, prefix) is None:
                return self._json({"error": "invalid subject or prefix"}, 400)
            return self._json({"files": config.list_outputs(subject, prefix)})
        if path.startswith("/outputs/"):
            return self._serve_output(path)
        return self._text("not found", 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
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
        rel = path[len("/outputs/"):]
        p = safe_path(config.OUTPUTS_DIR, rel)
        if not p or not p.is_file():
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
        for a, name in config.all_subjects().items():
            matched = config.default_docs_dir(a, name)
            if matched:
                docs_by_abbr[a] = {"name": matched.name, "path": str(matched)}
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
            "backends": [{"id": b["id"], "label": b["label"]}
                         for b in config.BACKENDS.values()],
            "model": catalog.get("default_model") or bmeta["model"],
            "variant": catalog.get("default_variant") or bmeta["variant"],
            "models": catalog.get("models") or [],
            "models_ok": bool(catalog.get("ok")),
            "models_error": catalog.get("error"),
            "models_hint": catalog.get("hint"),
            "model_provider": catalog.get("provider") or bmeta["provider"],
            "subjects": subjects,
            "transcripts": config.list_transcripts(),
            "docs": docs,
            "docs_by_abbr": docs_by_abbr,
            "runs": runs,
            "max_parallel": config.MAX_PARALLEL_RUNS,
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

    def _preview_run(self):
        body = self._body()
        if body is None:
            return
        planned = self._plan_jobs(body, start=False)
        if planned is None:
            return
        self._json(planned)

    def _plan_jobs(self, body: dict, start: bool) -> dict | None:
        abbr = body.get("abbr") or body.get("subject")
        subjects = config.all_subjects()
        if abbr not in subjects:
            self._json({"error": f"unknown subject {abbr!r}"}, 400)
            return None
        subj = subjects[abbr]
        if confine(config.OUTPUTS_DIR, subj) is None:
            self._json({"error": "invalid subject"}, 400)
            return None
        docs = body.get("docs") or None
        if docs and docs != "__none__":
            resolved_docs = resolve_under(config.WORKSPACE, docs)
            if resolved_docs is None:
                self._json(
                    {"error": "docs folder must be under the workspace"}, 400)
                return None
            docs = str(resolved_docs)
        resume = bool(body.get("resume"))
        retry = bool(body.get("retry"))
        phases_in = body.get("phases")
        phases = [int(p) for p in (phases_in or [1, 2, 3])]
        backend = config.normalize_backend(body.get("backend"))
        catalog = config.list_models(backend=backend)
        model, variant = config.resolve_model_choice(
            body.get("model"), body.get("variant"), catalog, backend=backend)
        transcripts = body.get("transcripts") or []
        single = body.get("transcript")
        if single:
            transcripts.append(single)
        transcripts = [t for t in dict.fromkeys(
            t.strip() for t in transcripts) if t]
        if not transcripts and (resume or retry) and body.get("prefix"):
            events = config.output_paths(subj, body["prefix"].strip())["events"]
            if events.is_file():
                prev = config.summarize_run_events(events, subj,
                                                   body["prefix"].strip())
                if prev.get("transcript") and Path(prev["transcript"]).is_file():
                    transcripts = [prev["transcript"]]
        if not transcripts:
            self._json({"error": "transcript file required"}, 400)
            return None

        jobs, rejected = [], []
        for t in transcripts:
            prefix, lecture_num = _job_identity(
                t, body, transcripts, subjects)
            if not prefix:
                self._json({"error": "prefix required (e.g. NLP_Lecture_9)"}, 400)
                return None
            if confine(config.OUTPUTS_DIR, subj, prefix) is None:
                rejected.append({"transcript": t, "prefix": prefix,
                                 "reason": "invalid prefix"})
                continue
            tpath = resolve_under(config.TRANSCRIPTS_DIR, t)
            if tpath is None:
                rejected.append({"transcript": t, "prefix": prefix,
                                 "reason": "transcript is not under transcripts dir"})
                continue
            t = str(tpath)
            if not lecture_num:
                lecture_num = _prefix_lecture(prefix)
            job_phases = list(phases)
            if resume or retry:
                arts = config.artifacts_status(subj, prefix)
                events = config.output_paths(subj, prefix)["events"]
                last = (config.summarize_run_events(events, subj, prefix)
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
            spec = {
                "transcript": t, "subject": subj, "abbr": abbr,
                "prefix": prefix, "lecture_num": lecture_num,
                "phases": job_phases, "backend": backend,
                "model": model, "variant": variant, "docs": docs,
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
                       "abbr": abbr, "prefix": prefix, "run_id": run_id}
                STATE["runs"][run_id] = run
                STATE["last_error"] = None
            self._launch_job(spec, run_id, run)
            jobs.append({"run_id": run_id, **{k: spec[k] for k in
                        ("subject", "prefix", "lecture_num", "phases",
                         "backend", "model", "variant")}})
        return {"ok": bool(jobs), "jobs": jobs, "rejected": rejected,
                "max_parallel": config.MAX_PARALLEL_RUNS}

    def _launch_job(self, spec: dict, run_id: str, run: dict):
        def emit(ev: dict, _rid=run_id, _run=run):
            ev["run_id"] = _rid
            ev["subject"] = _run["subject"]
            ev["prefix"] = _run["prefix"]
            BUS.publish(ev)

        pipeline = Pipeline(
            subject=spec["subject"], abbr=spec["abbr"], prefix=spec["prefix"],
            lecture_num=spec["lecture_num"], transcript=spec["transcript"],
            phases=spec["phases"], emit=emit, docs_dir=spec["docs"],
            run_id=run_id, model=spec["model"], variant=spec["variant"],
            backend=spec["backend"])
        run["pipeline"] = pipeline

        def worker(_rid=run_id, _p=pipeline, _run=run):
            emit({"type": "queued", "run_id": _rid,
                  "subject": _run["subject"], "prefix": _run["prefix"]})
            while True:
                if _p.stop_flag:
                    BUS.publish({"type": "pipeline_end", "run_id": _rid,
                                 "status": "stopped"})
                    return
                if RUN_SLOTS.acquire(timeout=0.5):
                    break
            try:
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
                RUN_SLOTS.release()
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
        archive = bool(body.get("archive"))
        if not subject or not prefix:
            return self._json({"error": "subject and prefix required"}, 400)
        if confine(config.OUTPUTS_DIR, subject, prefix) is None:
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
            result = config.delete_past_run(subject, prefix, archive=archive)
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


def _job_identity(transcript: str, body: dict, transcripts: list[str],
                  subjects: dict) -> tuple[str, str]:
    prefix = (body.get("prefix") or "").strip()
    lecture_num = str(body.get("lecture_num") or "").strip()
    name = Path(transcript).name
    stem = Path(transcript).stem
    derived = _derive_prefix(stem)
    guess = config.guess_from_filename(name, subjects)
    if len(transcripts) > 1:
        if not prefix:
            prefix = guess.get("prefix") or derived[0]
        else:
            per = guess.get("prefix") or derived[0]
            if prefix != per:
                prefix = (f"{prefix}_{derived[0]}" if not guess.get("prefix")
                          else guess["prefix"])
        if not lecture_num:
            lecture_num = guess.get("lecture_num") or derived[1]
    else:
        if not prefix:
            prefix = guess.get("prefix") or derived[0]
        if not lecture_num:
            lecture_num = (guess.get("lecture_num")
                           or _prefix_lecture(prefix)
                           or derived[1])
    return prefix, lecture_num


def _prefix_lecture(prefix: str) -> str:
    parts = prefix.split("_")
    if parts and parts[-1].isdigit():
        return parts[-1]
    return ""


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
    print(f"  Model:     {config.MODEL} (variant: {config.VARIANT})",
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
    print(f"Model: {config.MODEL} (variant: {config.VARIANT})", flush=True)

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
