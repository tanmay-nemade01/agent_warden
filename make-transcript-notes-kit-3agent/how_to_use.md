# How to Use — Transcript Notes Kit (3-Agent Pipeline)

## Prerequisites

### 1. Lecture Transcripts
- Download `.vtt` files individually from each lecture video.
- Rename transcripts by lecture number (e.g., `Lecture01.vtt`).

### 2. Textbook PDFs
- Name PDFs as per the course handout convention: `T1.pdf`, `T2.pdf`, `R1.pdf`, `R2.pdf`, etc.

### 3. Companion Documents
- Extract text from lecture slides or other companion docs and store as `.txt`.

### 4. Toolkit Files
- Ensure `make-transcript-notes-kit-3agent/` is available with all scripts, prompts, and templates.

### 5. Install Dependencies

```bash
python -m pip install -r make-transcript-notes-kit-3agent/requirements.txt
```

---

## Step 1 — Extract Relevant Chapters (One-Time Setup)

This step reduces the volume of textbook data by extracting only the chapters relevant to your course.

1. Get the chapter-wise breakdown of textbook resources from your course handout's curriculum or learning plan section.
2. Open a new Claude chat window and pass:
   - The course curriculum (copied chapter-wise breakdown).
   - The textbook PDFs (`T1.pdf`, `T2.pdf`, `R1.pdf`, `R2.pdf`, etc.).
3. Ask Claude to extract only those chapters relevant to the course.
4. Store the output in a folder: `enriching_docs/<Subject>/` (e.g., `enriching_docs/DRL/`).
5. Copy the extracted slide `.txt` files into the same `enriching_docs/<Subject>/` folder.

> **Note:** This is a one-time step. The enriched docs can be reused across all lectures in the course.

---

## Step 2 — Clean Transcript Files

Use `clean_vtt.py` to strip timestamps and metadata from `.vtt` files, reducing token usage by ~80%.

```bash
python clean_vtt.py
```

- All `.vtt` files in the current directory are cleaned and saved as `.txt`.
- Delete the original `.vtt` files to avoid naming confusion.

---

## Step 3 — Run the 3-Agent Pipeline

> **Important:** Each agent must run in a **new chat window with no prior chat history**. This ensures the highest quality output. A single agent cannot match the quality of three specialized agents working sequentially.

### Agent 1 — Extractor

1. Open a **new chat window**.
2. Pass **one** cleaned lecture transcript `.txt` file.
3. Attach the Agent 1 prompt: `SKILL_agent1_extractor.md`.
4. Run the agent.

**Input:** Cleaned `.txt` transcript.
**Output:** `<LecturePrefix>_notes_dense.md` — an exhaustive draft (500+ lines, more is better).

---

### Agent 2 — Enricher

1. Open a **new chat window**.
2. Pass the Agent 1 output: `<LecturePrefix>_notes_dense.md`.
3. Pass the entire `enriching_docs/<Subject>/` folder — the agent will extract the relevant files based on names.
4. Attach the Agent 2 prompt: `SKILL_agent2_enricher.md`.
5. Run the agent.

**Input:** Dense draft + enriching_docs subject folder.
**Output:** `<LecturePrefix>_notes_enriched.md` — detailed notes enriched with textbook information (700+ lines, more is better).

---

### Agent 3 — Formatter

1. Open a **new chat window**.
2. Pass the Agent 2 output: `<LecturePrefix>_notes_enriched.md`.
3. Attach the Agent 3 prompt: `SKILL_agent3_formatter.md`.
4. Run the agent. It will automatically run script-based Markdown-to-HTML section conversion (`python scripts/section_splitter.py convert ...`), inspect HTML for structural errors (unclosed tags, nested callouts), fill metadata & exam revision notes, and run `lint.py`.

**Input:** Enriched markdown.
**Output:** `<LecturePrefix>_notes.html` — final HTML file (800+ lines).


> **Note:** The HTML file has no styling and appears as plain text. This is intentional — the agent tends to over-focus on styling at the expense of content quality. If the agent gets stuck on sentence length or lint issues, stop it manually.

---

## Output Structure

All outputs are saved under `outputs/<Subject>/<LecturePrefix>/`:

```
outputs/<Subject>/<LecturePrefix>/
├── <LecturePrefix>_notes_dense.md          ← Agent 1 output
├── <LecturePrefix>_notes_enriched.md       ← Agent 2 output
├── sections/                              ← Split sections for processing
└── <LecturePrefix>_notes/
    └── <LecturePrefix>_notes.html          ← Agent 3 final HTML
```

---

## Tips

- **Agent 4 is optional and ignored** for now — skip it entirely.
- **Section-by-section processing** is handled automatically by the toolkit's scripts (`section_splitter.py`).
- **Topic mappings** are stored in `topic_mappings/<Subject>.yaml` and are updated automatically by Agent 2.
- **Readability scores** are advisory only — never split formulas or rewrite derivations to improve scores.
