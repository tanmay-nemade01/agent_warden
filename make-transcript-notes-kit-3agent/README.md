# make-transcript-notes-kit — 3-Agent Pipeline

Turn `.txt` lecture transcripts into self-contained, lecture-faithful HTML notes
using **three specialized agents** — each loads only its phase's rules,
dramatically reducing context-window bloat.

The target is not a generic textbook summary. The notes preserve how an idea
was taught: motivating questions, student misconceptions, terminology
corrections, professor intuition, worked reasoning, and the transitions that
made the explanation understandable.

## How it works

```text
Agent 1 (Extractor)          Agent 2 (Teaching Editor)     Agent 3 (Formatter)          Agent 4 (Enhancer) [OPTIONAL]
─────────────────          ──────────────────           ───────────────────          ────────────────────────────
Reads: SKILL_agent1_       Reads: SKILL_agent2_        Reads: SKILL_agent3_        Reads: SKILL_agent4_
       extractor.md               enricher.md                 formatter.md                enhancer.md

Input: raw .txt             Input: split dense draft     Input: split enriched draft  Input: Agent 3's final HTML
       transcript                  (markdown sections)          (markdown + annotations)

Does: exhaustive extraction,    Does: manifest closure,     Does: section-by-section     Does: opportunity audit,
      PII stripping,               lecture-flow preservation,   HTML conversion,              web research for
      teaching-moment manifest,    math reconciliation,         SEO metadata,                 external resources,
      source anchors               selective teaching spine,    exam-summary rendering,       interactive widget
                                   smart readability checks,    prerequisite HTML,            injection (playgrounds,
                                   topic mapping YAML update    HTML/SEO lint gate            step-throughs, concept
                                   (processed per-section)                                    checks, concept maps,
                                                                                              visual diagrams),
                                                                                              per-lecture CSS/JS

Output: dense draft         Output: enriched sections    Output: <LecturePrefix>_notes.html   Output: <LecturePrefix>_notes.html (modified)
        + manifest          (markdown + :::annotations)                                       + _enhancements.css
                            + updated topic_mappings/*.yaml                                    + _enhancements.js
                                                                                              + _enhancement_audit.json
```

## Topic Mapping

The `topic_mappings/` directory (at the workspace root) contains YAML files cataloguing every topic covered in every lecture. Topic mapping is a collaborative effort between **Agent 2** (which writes topics to the YAML) and **Agent 3** (which reads topics to build prerequisite references).

### Topic Mapping Flow
1. **Agent 2 (Enricher)**:
   - Scans the completed enriched draft to compile all `##` and `###` heading titles.
   - Updates the subject's YAML file (`topic_mappings/<Subject>.yaml`) with this lecture's topics by running the `update_topic_mapping.py` script.
2. **Agent 3 (Formatter)**:
   - Reads the YAML file for the same subject to identify overlapping topics in previous lectures (lectures with a lower number).
   - Generates the **Prerequisite Knowledge** HTML section listing prior coverage.
   - Does NOT write back to the YAML (Agent 2 has already updated it).

### Benefits
- **Students see the full map** — every "this was covered in Lecture N" reference is explicit and complete.
- **No Agent 3 YAML write-back** — simplifies Phase 3 and avoids late-pipeline write-back errors.
- **Accurate mapping** — topics are extracted directly from the finalized enriched markdown.

## Output Structure

Each lecture is self-contained within its own folder structure under `outputs/<Subject>/<LecturePrefix>/` (e.g. `outputs/Machine Learning/ML_Lecture_6/`):

```
outputs/<Subject>/<LecturePrefix>/
├── <LecturePrefix>_notes_dense.md             ← Agent 1 output (exhaustive draft)
├── <LecturePrefix>_notes_enriched.md          ← Agent 2 output (enriched markdown)
├── sections/                                  ← Split sections, inventory, and traceable summaries
└── <LecturePrefix>_notes/                     ← Agent 3 + Agent 4 output directory
    ├── <LecturePrefix>_notes.html             ← Agent 3 HTML output (modified in-place by Agent 4)
    ├── <LecturePrefix>_enhancements.css       ← Agent 4 per-lecture CSS (optional)
    ├── <LecturePrefix>_enhancements.js        ← Agent 4 per-lecture JS (optional)
    └── <LecturePrefix>_enhancement_audit.json ← Agent 4 audit report (optional)
```

All metadata, SEO tags (Open Graph, Twitter Cards, structured data), and lecture content are embedded directly in the single HTML file — **no companion `.json` files are created**. The BitsNotes viewer reads everything from the HTML itself via `<script id="lecture-metadata">`.

## Setup

```bash
python -m pip install -r make-transcript-notes-kit-3agent/requirements.txt
```

Topic mapping defaults to `<workspace>/topic_mappings/`. Set
`BITSNOTES_TOPIC_MAPPINGS_DIR` when the toolkit is used from another layout.

## Usage

Run the process in the following sequence:

```
1. "Use Agent 1 (extractor) to create the project directory outputs/<Subject>/<LecturePrefix>/ and process transcript.txt into <LecturePrefix>_notes_dense.md and <LecturePrefix>_extraction_manifest.json."

2. "Use Agent 2 (enricher) to split <LecturePrefix>_notes_dense.md, close every essential manifest item section-by-section, preserve Q&A/correction flow, resolve all *[verify]* markers, apply only useful teaching-spine elements, assemble and re-split <LecturePrefix>_notes_enriched.md, then run lint_dense.py and verify_manifest.py --phase enriched. Treat readability warnings as advisory and never rewrite correct math to shorten text."

3. "Use Agent 3 (formatter) to re-split <LecturePrefix>_notes_enriched.md, convert every section without inventing or dropping content, assemble strictly, read topic_mappings/<Subject>.yaml for prerequisite detection, run lint.py, and produce the final HTML under <LecturePrefix>_notes/. Return placeholders or missing instructional content to Agent 2."

4. (OPTIONAL) "Use Agent 4 (enhancer) to audit <LecturePrefix>_notes.html for interactive enhancement & diagram opportunities, present the audit for approval, then inject approved elements (parameter playgrounds, algorithm step-throughs, concept checks, curated external resources, concept maps, visual diagrams/flowcharts) directly into <LecturePrefix>_notes.html with per-lecture CSS/JS files. If no content qualifies for enhancement, stop after the audit."
```

## What's inside

```
make-transcript-notes-kit-3agent/
├── SKILL_agent1_extractor.md  ← Agent 1: Exhaustive extraction
├── SKILL_agent2_enricher.md   ← Agent 2: Teaching editor + manifest closure
├── SKILL_agent3_formatter.md  ← Agent 3: Format-only HTML + SEO + lint
├── SKILL_agent4_enhancer.md   ← Agent 4: Interactive enhancements (optional post-processor)
├── templates/
│   └── notes.html             ← HTML template (with {{PREREQUISITE_KNOWLEDGE}})
├── scripts/
│   ├── lint.py                ← HTML quality gate
│   ├── lint_dense.py          ← Markdown gate with math-aware prose warnings
│   ├── verify_manifest.py     ← Source-anchored semantic handoff gate
│   ├── _plain_language.py     ← Shared word lists for lint
│   ├── section_splitter.py    ← Splits drafts; refuses partial assembly by default
│   ├── topic_mapping_utils.py ← YAML parser + coverage search (acronym-aware)
│   └── update_topic_mapping.py ← YAML updater (called by Agent 2)
└── references/
    ├── enhancements_reference.css ← Reference CSS for Agent 4 interactive widgets (theme-matched)
    └── prompts.md                 ← Prompt cookbook

Workspace root (shared):
├── topic_mappings/
│   ├── Artificial Computational Intelligence.yaml
│   ├── Data Management for Machine Learning.yaml
│   ├── Deep Reinforcement Learning.yaml
│   ├── Mathematical Foundations for Machine Learning.yaml
│   ├── Natural Language Processing.yaml
│   └── Software Engineering for Machine Learning.yaml
```

## Token comparison

| Approach | Files agent loads | Approx. tokens |
|---|---|---|
| Original toolkit (14 files) | SKILL.md + 8 guidelines + 4 other | ~12,000+ |
| Optimized (1 file) | SKILL.md | ~5,000 |
| **3-agent (this)** | **1 focused file per agent** | **~4,000-7,000 each, depending on phase** |

## Section-by-section processing (Agents 2 & 3)

Large markdown files cause attention degradation during LLM calls — leading to skipped concepts, math notation drift, and malformed tags.

To solve this, Agent 2 splits the draft at the start of Phase 2:

1. **Split** — `scripts/section_splitter.py split` breaks `dense-draft.md` into per-section files and locks heading numbering in `_inventory.json`.
2. **Enrich (Agent 2)** — Agent 2 enriches each section (`section_XX.md`) individually. It focuses all its attention on one concept at a time, ensuring maximum depth and quality without context bloat or output length issues.
3. **Refresh and bind** — after enrichment, Agent 2 re-splits the assembled draft and binds every section summary to its heading ID and SHA-256. Agent 3 re-splits and validates those bindings before conversion.
4. **Convert (Agent 3)** — Agent 3 converts each enriched section to HTML without content recovery or invention.
5. **Assemble strictly** — `section_splitter.py assemble` fails if any expected section is missing. Partial notes cannot ship silently.

This confines errors to individual sections and makes quality checks targeted.

## Fidelity contract

`<LecturePrefix>_extraction_manifest.json` uses schema version 2. It is a
**condensed checksum of the dense draft**, not a content store: each essential
example, Q&A, formula, and teaching moment carries a `source_anchor` (timestamp
or source line range) and a one-line summary, but the full detail lives in the
prose. Misconception and vocabulary-correction items also record the trigger and
resolution. The manifest should stay short — roughly one line per major item,
not hundreds of lines.

Run the verifier twice:

```bash
python scripts/verify_manifest.py manifest.json notes_dense.md --phase dense
python scripts/verify_manifest.py manifest.json notes_enriched.md --phase enriched
python scripts/verify_manifest.py manifest.json final_notes.html --phase html
```

The enriched check requires Q&A structure for student-triggered corrections.
Missing essential items are failures, not warnings.

## Readability policy for math-heavy notes

Readability is a judgment aid, not an optimization target. The linters remove
LaTeX environments, inline/display math, symbol definitions, code, and tables
before computing prose metrics. Sentence-length, Flesch, and fancy-word
findings are advisory. Agents should fix genuinely confusing prose once, but
must not split formulas or repeatedly rewrite derivations to improve a score.
