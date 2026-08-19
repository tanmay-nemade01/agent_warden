# Notes Studio

A UI-based automation for the
[`make-transcript-notes-kit-3agent`](../make-transcript-notes-kit-3agent)
pipeline: raw lecture transcripts → dense draft → enriched notes → final
self-contained `notes.html`, driven by three sequential agents (OpenCode or
Command Code) with the toolkit's own scripts as quality gates.

Each agent is one non-interactive CLI session with **real tools**, exactly as the
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
- One or more supported agent CLIs on PATH:
  - [OpenCode CLI](https://opencode.ai) (`opencode`)
  - [Command Code CLI](https://commandcode.ai/docs/reference/cli) (`cmdc` / `command-code`)
  - [Claude Code CLI](https://code.claude.com) (`claude`)
  - [OpenAI Codex CLI](https://platform.openai.com) (`codex`)
  - [Reasonix CLI](https://github.com/esengine/reasonix) (`reasonix`)
  - [Pi Harness](https://github.com/earendil-works/pi-coding-agent) (`pi`)
  - [Antigravity CLI](https://github.com/google/antigravity) (`agy`)
  - [Cursor Agent CLI](https://cursor.com) (`cursor-agent` / `cursor`)
- An authenticated provider or API key for the backend you pick in the UI (e.g. `CURSOR_API_KEY` in environment or `.env`)

## Run

```bash
start.bat                 # or: ./start.sh / python app/server.py [--port 8787]
```

Default bind is loopback (`127.0.0.1`). Binding `--host 0.0.0.0` requires
`NOTES_STUDIO_TOKEN`; send it as `X-Notes-Token` / `Authorization: Bearer`
or `?token=` (the UI reads `?token=` from the page URL). `GET /healthz` is
unauthenticated. Generated `notes.html` is served with `Content-Security-Policy: sandbox`.

Open http://127.0.0.1:8787

1. Pick a **transcript** — subject, lecture prefix, and number auto-fill from the
   filename when possible (e.g. `Data_Management_for_Machine_Learning - Lecture 1`
   → `DMML` / `DMML_Lecture_1`).
2. Optionally adjust which agents to run (1–3). If prior artifacts exist for the
   prefix, a **Resume** / **Retry failed** banner appears.
3. Press **Run pipeline**. Each phase streams its live agent session — thinking
   blocks, tool calls, messages, and step/token metadata.
   - **OpenCode** uses `opencode run --format json --thinking`.
   - **Command Code** uses `cmdc -p --output-format json --yolo`.
   - **Claude Code** uses `claude -p --output-format stream-json --dangerously-skip-permissions`.
   - **OpenAI Codex** uses `codex exec --json --dangerously-bypass-approvals-and-sandbox`.
   - **Reasonix** uses `reasonix run -y --json -m <model>`.
   - **Pi Harness** uses `pi --print --mode json -m <model> --no-session`.
   - **Antigravity** uses `agy --model <model> --mode json --auto`.
   - **Cursor Agent** uses `cursor-agent -p --output-format stream-json --force --model <model>` (authenticated via `CURSOR_API_KEY`).
   Gates show PASS/FAIL under each phase; failing gates trigger fix sessions (max 2) before the phase reports.
   If a stage still fails, that stage is retried automatically up to 3 times
   (each transcript file has its own budget). **Failed runs** sit at the top
   of the page after those retries are exhausted.
4. **Live runs** show a cost/time rollup (per agent + total). **Past runs**
   survives refresh — resume, retry, open `notes.html`, or **Archive** /
   **Delete** the output folder. Refresh mid-run replays buffered SSE events
   so logs come back. Companion docs auto-select when a matching
   `companion_docs/<ABBR>` folder exists.

## Model & Backends

The job ticket has an **Agent backend** dropdown supporting 8 backends:
- **OpenCode** — Default: `opencode-go/deepseek-v4-flash` (effort: `max`)
- **Command Code** — Default: `deepseek/deepseek-v4-flash` (effort: `high`)
- **Claude Code** — Default: `claude-3-7-sonnet` (effort: `high`)
- **OpenAI Codex** — Default: `gpt-5.4` (standard model selection)
- **Reasonix** — Default: `deepseek/deepseek-v4-flash` (prefix-cache optimized, effort: `high`)
- **Pi Harness** — Default: `claude-3-7-sonnet` (minimalist modular harness, effort: `high`)
- **Antigravity** — Default: `gemini-3.7-flash` (effort: `high`)
- **Cursor Agent** — Default: `composer-2.5` (effort: `high`, authenticated via `CURSOR_API_KEY`)

On each UI load the server discovers OpenCode models via
`opencode models opencode-go --verbose`. Command Code's `--list-models` is slow,
so the UI starts from the DeepSeek V4 Flash default until you click
**refresh models**. Claude Code, OpenAI Codex, Reasonix, Pi Harness, Antigravity, and Cursor Agent provide catalog fallback presets
covering their flagship and lightweight models.

## Layout

```
agent-warden-opencode/
├── app/
│   ├── config.py       workspace/toolkit/subject/model constants
│   ├── paths.py        confine() for user-supplied path segments
│   ├── permissions.py  per-job OpenCode / Command Code write fences
│   ├── gates.py        toolkit script wrappers + [PASS]/[WARN]/[FAIL] parsing
│   ├── pipeline.py     3-phase orchestrator (OpenCode or Command Code + fix loops)
│   ├── server.py       stdlib HTTP + SSE server
│   └── static/index.html
├── tests/
├── start.bat
└── start.sh
```

Run history per lecture is appended to
`outputs/<Subject>/<Prefix>/<prefix>_run_events.jsonl`.
