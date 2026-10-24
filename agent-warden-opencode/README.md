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
                    final HTML (SEO, prerequisites embedded)
```

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
5. **Working on many subjects at once?** In the **New job** ticket, uncheck **All transcripts belong to the same subject** to assign individual subjects and companion doc folders to each transcript — see [Many subjects in parallel](#many-subjects-in-parallel).

## Model & Backends

The job ticket has an **Agent backend** dropdown supporting 8 backends.
No model is hardcoded: switching backends re-loads that backend's catalog
automatically and selects its live default (OpenCode uses your configured
default from `opencode api GET /api/model/default`). The presets below are
only last-resort fallbacks when live discovery fails:

- **OpenCode** — live catalog via the OpenCode v2 API (`opencode api GET /api/model`)
- **Command Code** — `deepseek/deepseek-v4-flash` fallback (effort: `high`)
- **Claude Code** — `claude-3-7-sonnet` fallback (effort: `high`)
- **OpenAI Codex** — `gpt-5.4` fallback (standard model selection)
- **Reasonix** — `deepseek/deepseek-v4-flash` fallback (prefix-cache optimized, effort: `high`)
- **Pi Harness** — `claude-3-7-sonnet` fallback (minimalist modular harness, effort: `high`)
- **Antigravity** — `gemini-3.7-flash` fallback (effort: `high`)
- **Cursor Agent** — `composer-2.5` fallback (effort: `high`, authenticated via `CURSOR_API_KEY`)

On each UI load the server discovers OpenCode models via
the OpenCode v2 API (`opencode api GET /api/model`) or `opencode models`. Command Code's `--list-models` is slow,
so the UI starts from its cached/fallback list until you click
**refresh models**. Claude Code, OpenAI Codex, Reasonix, Pi Harness, Antigravity, and Cursor Agent provide catalog fallback presets
covering their flagship and lightweight models.

## Many subjects in parallel

A **subject profile** binds one subject to the few things that actually
differ from subject to subject: where its transcripts come from, where its
companion docs live, how its output folder is named, and how a lecture
prefix is spelled. Everything else in the pipeline is shared.

Profiles live in `subject_profiles.json` (workspace root) or can be edited directly:

```json
{
  "QC": {
    "transcripts_dir": "transcript files/QC",
    "docs_dir":        "companion_docs/QC",
    "output_root":     "outputs",
    "output_template": "{abbr}/notes/{prefix}",
    "prefix_template": "{abbr}-L{lecture}",
    "topic_mapping":   "topic_mappings/Quantum Computing.yaml"
  }
}
```

| field | meaning | default |
| --- | --- | --- |
| `transcripts_dir` | folder holding this subject's `.txt` transcripts (may be outside the workspace) | the shared `transcript files/` |
| `docs_dir` | companion docs for Agent 2 (may be outside the workspace) | auto-detected `companion_docs/<ABBR>` |
| `output_root` | root for this subject's notes — must be inside the workspace | `outputs` |
| `output_template` | path under the root; `{subject}`, `{abbr}`, `{prefix}` | `{subject}/{prefix}` |
| `prefix_template` | lecture prefix; `{subject}`, `{abbr}`, `{lecture}` | `{abbr}_Lecture_{lecture}` |
| `topic_mapping` | the subject's topic-mapping YAML | `topic_mappings/<Subject>.yaml` |

A subject with no profile entry keeps the legacy behaviour exactly
(`outputs/<Subject Name>/<Prefix>/`, transcripts from the shared flat
directory, docs auto-detected).

### Running a batch

In the **New job** ticket:
- Check the transcript files you wish to process.
- Leave **All transcripts belong to the same subject** checked if they all share the selected subject and companion doc folder.
- Or **uncheck** it if the transcripts belong to different subjects. When unchecked, each transcript card lets you select its individual subject and companion doc folder (with an auto-detect button), while sharing backend, model, phases, and parallelism.
- Review the live batch preview table and click **Start pipeline**. The server plans one job per transcript/subject and starts them all under your shared parallelism budget (with an optional per-subject cap, so a subject with 40 lectures cannot starve the others).

The same thing over the API — `POST /api/run`:

```json
{
  "phases": [1, 2, 3],
  "backend": "opencode",
  "model": "opencode-go/deepseek-v4.1-flash",
  "subjects": [
    {"abbr": "QC", "transcripts": ["D:/.../transcript files/QC/lecture_1.txt"]},
    {"abbr": "CV", "transcripts": ["D:/.../transcript files/CV/lecture_1.txt"]}
  ]
}
```

Top-level keys are shared settings; each entry may override them. A
subject whose transcript is not under its own transcripts folder is
rejected with a reason instead of silently running on the wrong file.

Because a subject's output folder may not be the skill's default
`outputs/<Subject>/<LecturePrefix>/`, the orchestrator injects an explicit
path substitution into the agent prompt, and the per-job write fence is
derived from that subject's real output root.

## Layout

```
agent-warden-opencode/
├── app/
│   ├── config.py       workspace/toolkit/subject/model constants + subject profiles
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
`<output root>/<path>/<prefix>_run_events.jsonl`.
