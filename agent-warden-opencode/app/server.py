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
from app.pipeline import Pipeline, PhaseError  # noqa: E402

HOST = "127.0.0.1"
PORT = 8787
STATIC = Path(__file__).parent / "static"


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


def next_run_id() -> str:
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
    for rid, run in list(STATE["runs"].items()):
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
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, status=200, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, fmt, *args):  # quiet
        pass

    # --------------------------------------------------------------- routes
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_static("index.html")
        elif path == "/api/config":
            refresh = (query.get("refresh_models") or ["0"])[0] in (
                "1", "true", "yes")
            self._json(self._config(refresh_models=refresh))
        elif path == "/api/models":
            refresh = (query.get("refresh") or ["0"])[0] in (
                "1", "true", "yes")
            self._json(config.list_models(refresh=refresh))
        elif path == "/api/docs":
            self._json(self._docs_folders())
        elif path == "/api/history":
            limit = int((query.get("limit") or ["60"])[0] or 60)
            self._json({"runs": config.list_past_runs(limit=min(limit, 200))})
        elif path == "/api/artifacts":
            subject = (query.get("subject") or [""])[0]
            prefix = (query.get("prefix") or [""])[0]
            if not subject or not prefix:
                return self._json({"error": "subject and prefix required"}, 400)
            arts = config.artifacts_status(subject, prefix)
            events = config.output_paths(subject, prefix)["events"]
            if events.is_file():
                arts["last_run"] = config.summarize_run_events(
                    events, subject, prefix)
            else:
                arts["last_run"] = None
            self._json(arts)
        elif path == "/api/events":
            self._sse()
        elif path == "/api/outputs":
            subject = (query.get("subject") or [""])[0]
            prefix = (query.get("prefix") or [""])[0]
            self._json({"files": config.list_outputs(subject, prefix)})
        elif path.startswith("/outputs/"):
            self._serve_output(path)
        else:
            self._text("not found", 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/run":
            self._start_run()
        elif path == "/api/stop":
            self._stop_run()
        elif path == "/api/subjects":
            self._add_subject()
        elif path == "/api/history/delete":
            self._delete_history()
        else:
            self._text("not found", 404)

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
        self.end_headers()
        self.wfile.write(body)

    def _serve_output(self, path: str):
        rel = path[len("/outputs/"):]
        p = safe_path(config.OUTPUTS_DIR, rel)
        if not p or not p.is_file():
            return self._text("not found", 404)
        body = p.read_bytes()
        ctype = {"html": "text/html; charset=utf-8",
                 "md": "text/markdown; charset=utf-8",
                 "json": "application/json; charset=utf-8",
                 "yaml": "text/yaml; charset=utf-8"}.get(
            p.suffix.lstrip("."), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _config(self, refresh_models: bool = False) -> dict:
        subjects = [{"abbr": a, "subject": v}
                    for a, v in sorted(config.all_subjects().items())]
        docs = self._docs_folders()
        docs_by_abbr = {}
        for a, name in config.all_subjects().items():
            matched = config.default_docs_dir(a, name)
            if matched:
                docs_by_abbr[a] = {"name": matched.name, "path": str(matched)}
        catalog = config.list_models(refresh=refresh_models)
        return {
            "workspace": str(config.WORKSPACE),
            "model": catalog.get("default_model") or config.MODEL,
            "variant": catalog.get("default_variant") or config.VARIANT,
            "models": catalog.get("models") or [],
            "models_ok": bool(catalog.get("ok")),
            "models_error": catalog.get("error"),
            "model_provider": catalog.get("provider") or config.MODEL_PROVIDER,
            "subjects": subjects,
            "transcripts": config.list_transcripts(),
            "docs": docs,
            "docs_by_abbr": docs_by_abbr,
            "runs": [{"run_id": rid, "subject": r["subject"],
                      "abbr": r["abbr"], "prefix": r["prefix"]}
                     for rid, r in STATE["runs"].items()],
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

    def _start_run(self):
        body = self._body()
        abbr = body.get("abbr") or body.get("subject")
        subjects = config.all_subjects()
        if abbr not in subjects:
            return self._json({"error": f"unknown subject {abbr!r}"}, 400)
        subj = subjects[abbr]  # full subject name
        docs = body.get("docs") or None
        if docs and docs != "__none__":
            docs = str(Path(docs).resolve())
            try:
                Path(docs).resolve().relative_to(config.WORKSPACE.resolve())
            except ValueError:
                return self._json(
                    {"error": "docs folder must be under the workspace"}, 400)
        resume = bool(body.get("resume"))
        retry = bool(body.get("retry"))
        phases_in = body.get("phases")
        phases = [int(p) for p in (phases_in or [1, 2, 3])]
        catalog = config.list_models()
        model, variant = config.resolve_model_choice(
            body.get("model"), body.get("variant"), catalog)
        transcripts = body.get("transcripts") or []
        single = body.get("transcript")
        if single:
            transcripts.append(single)
        transcripts = [t for t in dict.fromkeys(
            t.strip() for t in transcripts) if t]

        # Resume/retry from a past run can omit transcripts if the prior
        # pipeline_start recorded one that still exists on disk.
        if not transcripts and (resume or retry) and body.get("prefix"):
            events = config.output_paths(subj, body["prefix"].strip())["events"]
            if events.is_file():
                prev = config.summarize_run_events(events, subj,
                                                   body["prefix"].strip())
                if prev.get("transcript") and Path(prev["transcript"]).is_file():
                    transcripts = [prev["transcript"]]

        if not transcripts:
            return self._json({"error": "transcript file required"}, 400)

        jobs, rejected = [], []
        for t in transcripts:
            prefix = (body.get("prefix") or "").strip()
            lecture_num = str(body.get("lecture_num") or "").strip()
            if len(transcripts) > 1:
                # Multiple files: derive a unique prefix per transcript.
                derived = _derive_prefix(Path(t).stem)
                guess = config.guess_from_filename(Path(t).name, subjects)
                if not prefix:
                    prefix = guess.get("prefix") or derived[0]
                else:
                    # Keep user stem but still uniquify per file.
                    per = guess.get("prefix") or derived[0]
                    if prefix != per:
                        prefix = f"{prefix}_{derived[0]}" if not guess.get("prefix") \
                            else guess["prefix"]
                if not lecture_num:
                    lecture_num = guess.get("lecture_num") or derived[1]
            else:
                # Single file: fill gaps from filename guess.
                guess = config.guess_from_filename(Path(t).name, subjects)
                if not prefix:
                    prefix = guess.get("prefix") or _derive_prefix(Path(t).stem)[0]
                if not lecture_num:
                    lecture_num = (guess.get("lecture_num")
                                   or _prefix_lecture(prefix)
                                   or _derive_prefix(Path(t).stem)[1])
            if not prefix:
                return self._json({"error": "prefix required (e.g. "
                                   "NLP_Lecture_9)"}, 400)
            if not lecture_num:
                lecture_num = _prefix_lecture(prefix)

            # Resolve phases: explicit UI selection wins unless resume/retry.
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

            dup = next((r for r in STATE["runs"].values()
                        if r["subject"] == subj
                        and r["prefix"] == prefix), None)
            if dup:
                rejected.append({"transcript": t, "prefix": prefix,
                                 "reason": f"already running as {dup['run_id']}"})
                continue
            run_id = next_run_id()
            run = {"pipeline": None, "subject": subj,
                   "abbr": abbr, "prefix": prefix, "run_id": run_id}
            STATE["runs"][run_id] = run
            STATE["last_error"] = None

            def emit(ev: dict, _rid=run_id, _run=run):
                ev["run_id"] = _rid
                ev["subject"] = _run["subject"]
                ev["prefix"] = _run["prefix"]
                BUS.publish(ev)

            pipeline = Pipeline(
                subject=subj, abbr=abbr, prefix=prefix,
                lecture_num=lecture_num, transcript=t,
                phases=job_phases, emit=emit, docs_dir=docs, run_id=run_id,
                model=model, variant=variant)
            run["pipeline"] = pipeline
            jobs.append({"run_id": run_id, "subject": subj,
                         "prefix": prefix, "lecture_num": lecture_num,
                         "phases": job_phases, "model": model,
                         "variant": variant})

            def worker(_rid=run_id, _p=pipeline):
                try:
                    _p.run()
                except Exception as exc:  # noqa: BLE001
                    BUS.publish({"type": "pipeline_end", "run_id": _rid,
                                 "status": "error",
                                 "error": f"{type(exc).__name__}: {exc}"})
                finally:
                    STATE["runs"].pop(_rid, None)
                    BUS.publish({"type": "idle", "run_id": _rid})
                    # Keep buffer briefly for late reconnects, then drop.
                    threading.Timer(120.0, BUS.drop_run, args=(_rid,)).start()

            threading.Thread(target=worker, daemon=True).start()
        return self._json({"ok": bool(jobs), "jobs": jobs,
                           "rejected": rejected})

    def _stop_run(self):
        body = self._body()
        run_id = body.get("run_id")
        targets = ([run_id] if run_id else list(STATE["runs"].keys()))
        stopped = []
        for rid in targets:
            run = STATE["runs"].get(rid)
            if run and run["pipeline"]:
                run["pipeline"].stop()
                stopped.append(rid)
        self._json({"ok": True, "stopped": stopped})

    def _delete_history(self):
        body = self._body()
        subject = (body.get("subject") or "").strip()
        prefix = (body.get("prefix") or "").strip()
        archive = bool(body.get("archive"))
        if not subject or not prefix:
            return self._json({"error": "subject and prefix required"}, 400)
        # Refuse to delete while a live run owns this prefix.
        busy = next((r for r in STATE["runs"].values()
                     if r["subject"] == subject and r["prefix"] == prefix), None)
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
        subjects = config.save_subject(abbr, name)
        return self._json({"ok": True, "abbr": abbr, "name": name,
                           "subjects": [{"abbr": a, "subject": v}
                                        for a, v in sorted(subjects.items())]})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        # Preload jsonl only for runs whose memory buffer is currently empty.
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
    global HOST, PORT
    args = sys.argv[1:]
    if "--host" in args:
        HOST = args[args.index("--host") + 1]
    if "--port" in args:
        PORT = int(args[args.index("--port") + 1])
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
    print(f"Notes Studio on http://{HOST}:{PORT}", flush=True)
    print(f"Workspace: {config.WORKSPACE}", flush=True)
    print(f"Model: {config.MODEL} (variant: {config.VARIANT})", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            out.close(); err.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
