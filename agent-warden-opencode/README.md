# Notes Studio

A UI-based automation for the
[`make-transcript-notes-kit-3agent`](../make-transcript-notes-kit-3agent)
pipeline: raw lecture transcripts → dense draft → enriched notes → final
self-contained `notes.html`, driven by three sequential opencode agents with
the toolkit's own scripts as quality gates.

Each agent is one `opencode run` session with **real tools**, exactly as the
SKILL files were written for, and the orchestrator independently re-runs the
phase gates after each agent and launches fix sessions when they fail.

## Pipeline

```
transcript.txt ──► Agent 1 (Extractor) ──► dense.md + extraction_manifest.json
                        │  gates: lint_dense (dense) · verify_manifest (dense)
                        ▼
                Agent 2 (Enricher) ──► enriched.md + sections/ + topic YAML
                        │  gates: lint_dense (enriched) · verify_manifest (enriched)
                        ▼
                Agent 3 (Formatter) ──► <prefix>_notes/<prefix>_notes.html
                        │  gates: lint.py · verify_manifest (html)
                        ▼
                    final HTML (SEO, exam revision, prerequisites embedded)
```

Agent 4 (enhancer) is intentionally skipped, per the toolkit's own guidance.

## Requirements

- Python 3.10+ (stdlib only — no pip installs)
- [opencode CLI](https://opencode.ai) on PATH or via npm global
- An authenticated opencode provider

## Run

```bash
start.bat                 # or: python app/server.py [--port 8787]
```

Open http://127.0.0.1:8787

1. Pick a **transcript** — subject, lecture prefix, and number auto-fill from the
   filename when possible (e.g. `Data_Management_for_Machine_Learning - Lecture 1`
   → `DMML` / `DMML_Lecture_1`).
2. Optionally adjust which agents to run (1–3). If prior artifacts exist for the
   prefix, a **Resume** / **Retry failed** banner appears.
3. Press **Run pipeline**. Each phase streams its live agent session — thinking
   blocks, tool calls, messages, and step/token metadata — via
   `opencode run --format json --thinking`. Gates show PASS/FAIL under each
   phase; failing gates trigger fix sessions (max 2) before the phase reports.
4. **Live runs** show a cost/time rollup (per agent + total). **Past runs**
   survives refresh — resume, retry, open `notes.html`, or **Archive** /
   **Delete** the output folder. Refresh mid-run replays buffered SSE events
   so logs come back. Companion docs auto-select when a matching
   `companion_docs/<ABBR>` folder exists.

## Model

Defaults to `opencode-go/deepseek-v4-flash` with `max` reasoning effort.
On each UI load the server discovers available `opencode-go` models (and each
model's effort variants) via `opencode models opencode-go --verbose`, and the
job ticket lets you pick both before running.

## Layout

```
agent-warden-opencode/
├── app/
│   ├── config.py       workspace/toolkit/subject/model constants
│   ├── gates.py        toolkit script wrappers + [PASS]/[WARN]/[FAIL] parsing
│   ├── pipeline.py     3-phase orchestrator (opencode run + fix loops)
│   ├── server.py       stdlib HTTP + SSE server
│   └── static/index.html
└── start.bat
```

Run history per lecture is appended to
`outputs/<Subject>/<Prefix>/<prefix>_run_events.jsonl`.
