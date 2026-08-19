# Agent Warden

Turn lecture transcripts into self-contained HTML notes. This repo ships two pieces:

| Folder | What it is |
|---|---|
| [`agent-warden-opencode/`](agent-warden-opencode/) | **Notes Studio** — local web UI that runs the pipeline with [OpenCode](https://opencode.ai) or [Command Code](https://commandcode.ai/docs/reference/cli) |
| [`make-transcript-notes-kit-3agent/`](make-transcript-notes-kit-3agent/) | The 3-agent toolkit: skill files, quality-gate scripts, HTML template |

Git tracks only those two folders (plus this README and `.gitignore`). Transcripts, companion docs, topic mappings, and generated notes stay on your machine.

```
transcript.txt
    → Agent 1 Extractor   → dense.md + extraction_manifest.json
    → Agent 2 Enricher    → enriched.md + sections/ + topic YAML
    → Agent 3 Formatter   → <prefix>_notes/<prefix>_notes.html
```

Agent 4 (interactive enhancer) is optional and skipped by Notes Studio.

---

## What you need

- **Python 3.10+**
- One or more supported agent CLIs on PATH, signed in:
  - **[OpenCode CLI](https://opencode.ai)** (`opencode`)
  - **[Command Code](https://commandcode.ai/docs/reference/cli)** (`cmdc` on Windows)
  - **[Claude Code CLI](https://code.claude.com)** (`claude`)
  - **[OpenAI Codex CLI](https://platform.openai.com)** (`codex`)
  - **[Reasonix CLI](https://github.com/esengine/reasonix)** (`reasonix`)
  - **[Pi Harness](https://github.com/earendil-works/pi-coding-agent)** (`pi`)
  - **[Antigravity CLI](https://github.com/google/antigravity)** (`agy`)
  - **[Cursor Agent CLI](https://cursor.com)** (`cursor-agent` / `cursor`, authenticated via `CURSOR_API_KEY`)
- Toolkit Python deps (PyYAML):

```bash
python -m pip install -r make-transcript-notes-kit-3agent/requirements.txt
```

Notes Studio itself uses the standard library only.

---

## Local folders (not in git)

Create these next to the two app folders. Notes Studio looks for them at the **workspace root** (this directory).

```
agent_warden/
├── agent-warden-opencode/          ← tracked
├── make-transcript-notes-kit-3agent/  ← tracked
├── transcript files/               ← put cleaned .txt transcripts here
├── companion_docs/                 ← optional slides / textbook extracts per subject
│   └── DMML/                       ← folder name = subject abbreviation
├── topic_mappings/                 ← YAML catalogs (created as you run)
└── outputs/                        ← generated notes (created as you run)
```

You can override the workspace with `NOTES_WORKSPACE` if the repo is not at `E:\agent_warden`.

### Transcripts

1. Download lecture `.vtt` captions.
2. Clean them to `.txt` (strips timestamps; much smaller for the model):

```bash
python make-transcript-notes-kit-3agent/utils/clean_vtt.py path\to\Lecture01.vtt
```

3. Put the `.txt` files in `transcript files/`.

Filenames that include the course and lecture number auto-fill the UI, for example:

- `Data_Management_for_Machine_Learning - Lecture 1.txt` → subject **DMML**, prefix `DMML_Lecture_1`
- `NLP_Lecture_9.txt` → **NLP**, `NLP_Lecture_9`

### Companion docs (optional, Agent 2)

Put extracted slide text or textbook chapter extracts under `companion_docs/<ABBR>/` (for example `companion_docs/DMML/`). If that folder exists, Notes Studio selects it automatically.

---

## How to use Notes Studio (recommended)

From `agent-warden-opencode/`:

```bash
start.bat
```

Or:

```bash
python agent-warden-opencode/app/server.py
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). Optional: `--port 8787` / `--host 127.0.0.1`.

1. Pick a **transcript**. Subject, lecture prefix, and number fill in from the filename when possible.
2. Choose which agents to run (1–3). If this lecture already has artifacts, use **Resume** or **Retry failed**.
3. Pick backend (OpenCode is the default), then model and reasoning effort if you want something other than the default (`opencode-go/deepseek-v4-flash` / `max`, or Command Code `deepseek/deepseek-v4-flash` / `high`).
4. Press **Run pipeline**. Each phase streams the live agent session. After each agent, the toolkit gates run (`lint_dense.py`, `verify_manifest.py`, `lint.py`). Failures trigger up to two fix sessions.
5. When Agent 3 finishes, open `notes.html` from the UI. Past runs survive refresh; you can resume, retry, archive, or delete an output folder.

Run history is appended to `outputs/<Subject>/<Prefix>/<prefix>_run_events.jsonl`.

---

## How to use the toolkit without the UI

Each agent should run in a **fresh** session with only its skill file loaded. Full prompts and gates: [`make-transcript-notes-kit-3agent/how_to_use.md`](make-transcript-notes-kit-3agent/how_to_use.md).

1. **Agent 1** — attach `SKILL_agent1_extractor.md` and one cleaned transcript. Writes `outputs/<Subject>/<Prefix>/` dense draft + manifest.
2. **Agent 2** — attach `SKILL_agent2_enricher.md`, the dense draft, and `companion_docs/<ABBR>/`. Writes enriched markdown, splits sections, updates `topic_mappings/<Subject>.yaml`.
3. **Agent 3** — attach `SKILL_agent3_formatter.md` and the enriched draft. Converts sections to HTML, adds SEO / exam revision / prerequisites, runs `lint.py`.

Output layout:

```
outputs/<Subject>/<LecturePrefix>/
├── <LecturePrefix>_notes_dense.md
├── <LecturePrefix>_extraction_manifest.json
├── <LecturePrefix>_notes_enriched.md
├── sections/
└── <LecturePrefix>_notes/
    └── <LecturePrefix>_notes.html
```

---

## Built-in subjects

Abbreviations the UI already knows (you can add more in the UI; they land in `subjects.json`):

ACI, BDA, BDS, DMML, DNN, DRL, DSA, DW/DWH, ISM, MFML, ML, NLP, SEML, SPA.

---

## More detail

- Notes Studio internals: [`agent-warden-opencode/README.md`](agent-warden-opencode/README.md)
- Pipeline contract, gates, and section splitting: [`make-transcript-notes-kit-3agent/README.md`](make-transcript-notes-kit-3agent/README.md)
- Manual chat workflow: [`make-transcript-notes-kit-3agent/how_to_use.md`](make-transcript-notes-kit-3agent/how_to_use.md)
