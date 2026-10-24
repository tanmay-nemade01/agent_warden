# Review & Improvement Report: `make-transcript-notes-kit-3agent`

**Status:** All improvements fully implemented and verified. Test suite expanded from 18 to 67 passing tests (`pytest` 100% green).

**Summary:** The 3-phase architecture has been strengthened across all layers:
- **Data safety:** Added corrupt YAML protections, atomic file replacement, and automatic `.bak` backups.
- **Gate reliability:** Eliminated false positives on legitimate academic content and closed false negatives.
- **Preprocessing & Rendering:** Resolved WebVTT content drops, fenced code heading splits, Markdown table pipes in math spans, blockquote Q&A paragraph separation, and heading classes.
- **Prompts & Scalability:** Unified policies on new topics, summary invalidation, artifact cleanup, and scaled prerequisite link generation.


---

## Critical

### C1. `verify_manifest.py` flow-order check produces false failures

`scripts/verify_manifest.py:590-599`

```python
ordered_positions = [position for _, position in sorted(positions)]
if (
    ordered_positions != sorted(ordered_positions)
    or len(ordered_positions) != len(set(ordered_positions))
):
```

`keyword_match_position()` (`:169-183`) returns the position of the *Nth earliest keyword anywhere in the section*, not the event's actual narrative location. Two events that share generic words like "reward", "update", or "model" resolve to the same offset. The `len(set(...))` check then fails even when the lecture is perfectly ordered.

**Confirmed:** a synthetic, correctly ordered lecture produced:

```
[FAIL] Lecture flow order (1.1): Teaching events appear out of source order...
```

**Fix:** remove the uniqueness requirement, and only enforce order when positions actually differ. Better: search sequentially from the previous event's position, and downgrade to `WARN` unless the manifest has explicit `source_anchor` line ranges.

```python
last = -1
for cid, positions in positions_by_concept.items():
    for order, position in sorted(positions):
        if position < last:
            report.fail(
                f"Lecture flow order ({cid})",
                "Teaching events appear out of source order inside the concept section.",
            )
            break
        last = max(last, position)
```

---

### C2. `update_topic_mapping.py` silently overwrites corrupt YAML

`scripts/topic_mapping_utils.py:35-48` returns `None` on **any** parse error:

```python
try:
    return yaml.safe_load(text)
except Exception:
    return None
```

`scripts/update_topic_mapping.py:117-128` then treats `None` as "file does not exist":

```python
topic_map = TMU.load_topic_map(subject_name)
...
if topic_map is None:
    lectures = []          # ← discards the existing file
```

The updater writes only the new lecture back to the same path.

**Confirmed:** with a malformed existing YAML, lecture 1 disappeared after updating lecture 2.

**Fix:** distinguish "missing" from "unparseable". Make `_parse_yaml` raise, or add `load_topic_map(..., strict=True)`. In the updater, if the file exists but fails to parse, abort with the path and parser error. Write atomically and keep a `.bak`.

```python
if topic_map is None and os.path.exists(candidate_path):
    raise RuntimeError(
        f"Refusing to overwrite unparseable topic map: {candidate_path}"
    )
```

---

### C3. Splitter splits on `##` inside fenced code blocks

`scripts/section_splitter.py:98`

```python
h2_matches = list(H2_PATTERN.finditer(text))
```

There is no fence tracking. A lecture containing a code sample with `## comment` creates a bogus section.

**Confirmed:** `## not a heading` inside a fenced block became `section_02.md`.

**Fix:** scan line-by-line and track ``` / ~~~ fences. Only accept `^## ` when not inside a fence. Add a regression test.

---

### C4. `clean_vtt.py` drops spoken content and leaks `NOTE` blocks

`utils/clean_vtt.py:64-66` skips only the `NOTE` line, not its continuation:

```python
if line.startswith('WEBVTT') or line.startswith('NOTE') ...:
    i += 1
    continue
```

`:78-83` treats any line before a timestamp as a cue ID. `CUE_ID_RE` (`:8`) allows periods:

```python
CUE_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")
```

So a short spoken line like `Okay.` matches and is dropped.

**Confirmed output:** `that continues here This is the real content.` — "Okay." missing, NOTE text leaked.

**Fix:**
- On `NOTE`/`STYLE`/`REGION`, advance to the next blank line.
- Only treat a line as a cue ID if it has no sentence-ending punctuation and looks like a slug/number. Prefer content when ambiguous.

---

## High

### H1. Converter drops required heading classes and emits a duplicate H1

`scripts/convert_md_to_html.py:188-200` emits plain `<h1>/<h2>/<h3>` tags. The template expects:

- `h1.lecture-title`
- `h2.section-title`
- `h3.subsection-title`

(`templates/notes.html:57,75-77`)

The preamble `# Title` is converted to `<h1>` and assembled into `<main>` (`section_splitter.py:121-125`, `308-313`), so the page ends up with two H1s.

**Fix:** map the classes during conversion. Drop or demote the preamble H1 when the template already provides `{{TITLE}}`.

---

### H2. Table and blockquote parsing corrupt valid content

- `convert_md_to_html.py:118` splits on every `|`:

```python
parts = [p.strip() for p in line.strip().strip("|").split("|")]
```

`\(a|b\)`, `P(A|B)`, and escaped `\|` all become extra cells. **Confirmed:** a two-column table with conditional probability became three cells.

- `:251-259` joins all `>` lines into one `<p>`:

```html
<p><strong>Q:</strong> Why? <strong>A:</strong> Because.</p>
```

**Fix:** split on unescaped `|` outside math spans; support `\|`. For blockquotes, emit separate `<p>` elements per source line, or at least preserve `<br>` so Q&A stays readable.

---

### H3. Lint false positives on legitimate academic content

All confirmed:

| Check | Location | False positive |
|---|---|---|
| Pipeline jargon | `lint.py:975-999` | `Feature Extractor` heading, `Agent 1`/`Agent 2` in MARL content |
| Placeholder | `lint.py:1072` | NLP lectures discussing "the placeholder token" |
| Secrets | `lint.py:689-704` | Teaching example `api_key = "sk-..."` inside `<code>` |
| Raw `$` | `lint_dense.py:167` | Currency `$5` |
| PII | `lint.py:70`, `lint_dense.py:31` | Missing `re.I`, so `Professor Bishop` is missed; lowercase citations can false-positive |

`check_pii_and_secrets` scans raw HTML, including `<code>`, `<pre>`, and SVG text. `lint_dense` treats professor/university mentions as `WARN`, while `lint.py` makes them `FAIL`.

**Fix:** scope checks to visible prose (exclude `code`/`pre`/`svg`), add course-term allowlists, require pipeline context for `Agent \d`, allow `$` when followed by a digit, and share one severity policy between both linters.

---

### H4. Lint false negatives and contract drift

- `check_metadata` (`lint.py:448-488`) validates required fields but accepts extra keys. Agent 3's red list says extra metadata fields are an automatic fail. **Confirmed:** a `quiz` key passes.
- `check_template_hygiene` (`lint.py:344-349`) strips `<script>` before scanning, so `{{LECTURE_TITLE}}` inside `lecture-metadata` JSON passes. **Confirmed.**
- SEO (`lint.py:390-395`): `>155` chars is `WARN`, but Agent 3 says fail. Docstring says 140-155; code accepts 100-155. A "good" description is reported as `WARN`.

**Fix:** reject unknown metadata keys; scan raw text (or metadata JSON separately) for `{{...}}`; pick one threshold and align docs, code, and rubric.

---

### H5. Prompt-level contradictions that cause rework

- **New topics:** `SKILL_agent2_enricher.md:167` explicitly allows new major `##` topics from professor companion docs, while `:12`, `:163`, and `:516` prohibit them. This is the biggest prompt contradiction. Decide one policy. If new sections are allowed, they must be added **before** the summary bind.
- **HTML:** `:534` says "No HTML tags yet", but `:187` and `:364-390` require inline `<figure><svg>`. Reword to "no rendered HTML blocks except the required SVG figure."
- **Summary binding:** `:92-98` re-splits the enriched draft without `--invalidate-summaries`, then immediately runs `bind-summaries`. If enrichment added/removed/renumbered sections, old summaries are orphaned or mismatched. Either invalidate and regenerate, or bind before structural edits and forbid later section changes.
- **Agent 3 input:** `SKILL_agent3_formatter.md:17` says stop if summaries are missing; `:87` says summary validation is non-blocking. Align on non-blocking.
- **Cleanup:** Agent 2 `:129` lists the only files allowed to remain, but `:550` says never delete `*_run_events.jsonl`. Add the log file to the allowed list.
- **Output path:** Agent 2 `:121-124` standardizes `file_name` as `<Subject>/<LecturePrefix>_notes/<LecturePrefix>_notes.html`, missing `outputs/<Subject>/<LecturePrefix>/`. Real `topic_mappings/Machine Learning.yaml` contains the wrong path and mojibake.

---

### H6. Prerequisite generator is unusable at scale

`scripts/topic_mapping_utils.py:387-435` lists **every major topic from every earlier lecture**, with no overlap filtering, no cap, and no links — despite the text saying "review the linked notes."

For `Machine Learning` lecture 99 it produced ~235 lines, including "Course Logistics."

**Fix:** match current lecture topics against prior topics (token/embedding overlap), exclude non-technical items, cap at ~8-12, group by lecture, and emit `<a href="...">` using the corrected `file_name`. Omit the section when confidence is low.

---

### H7. Manifest provenance is weaker than documented

- `clean_vtt` strips timestamps, but manifest examples use timestamps (`00:42:10`); only line ranges are actually available. Add `--keep-timestamps` or standardize on line ranges.
- `verify_manifest` ignores `anchor_quote`; it matches description/summary keywords only (`:169-183`). Agent 1 is told to drop `anchor_quote`, so the strongest provenance field is dead.
- Examples use literal placeholder IDs `L.T`, `L.T.example.1`; agents may copy them.
- `teaching_moments` requires trigger/resolution/preferred_term all inside one Q&A block; prose corrections fail.
- `exam_guidance` is `WARN`-only and still looks for the legacy "Exam Guidance Summary"; `named_references` is never verified.
- Keyword matching is substring-based and ignores tokens under 4 characters, so acronyms are not checked.

**Fix:** make `anchor_quote` the primary check with token-set overlap, use explicit `source_anchor` ranges, replace placeholder IDs with `<concept-id>`, allow prose teaching moments, and verify named references.

---

### H8. `lint_dense` appendix and verify-marker handling

- `check_numbering` (`:95`, `:116`) exempts only the exact heading `Key Industry Applications`. `Glossary`, `Further Reading`, or any other non-dotted appendix fails.
- `find_handwaving` (`:284`) runs before resolved `*[verify: ... "after simplification"]*` markers are stripped, so a verified marker can hard-fail as hand-waving. Agent 1 is instructed to create such markers.

**Fix:** exempt all non-dotted appendix headings (or require an `<!-- appendix -->` marker); strip resolved verify markers before hand-waving detection and flag only unresolved ones.

---

## Medium

| Area | Issue | Fix |
|---|---|---|
| Heading IDs | `make_slug` produces `5.1-concept`, inventory uses `5.1`; Agent 3's "IDs match inventory" check can never pass (`convert_md_to_html.py:107-111`) | Emit IDs from inventory or use the dotted prefix only |
| SVG IDs | Duplicate `id="arrow"` / `flow-arrow` across figures | Prefix with figure slug or drop IDs |
| Subject resolution | Fuzzy acronym matching can select the wrong YAML (`topic_mapping_utils.py:90-93`) | Use `subjects.json` as the canonical map |
| Dependencies | `requirements.txt` lacks `pytest` and a Python floor; code uses `Path \| None` (needs ≥3.10) | Add `pytest`, declare Python ≥3.10 |
| Windows output | Lint prints em dashes/arrows/emoji; PowerShell shows mojibake; YAML contains literal `�?"` | Force UTF-8, use ASCII in console output, repair YAML |
| Inline conversion | `process_inline` does not escape `&`; links with `)` break; escaped markdown unsupported | Escape `&`, parse balanced-paren links, support `\*`/`\_` |
| Docs | `how_to_use.md:47` says `python clean_vtt.py` (actual `utils/clean_vtt.py`); `:51` says delete `.vtt` though the default keeps them | Fix commands and document `--keep`/`--delete` |
| README | Claims ~4-7k tokens/agent; actual SKILLs are ~45-48 KB (~11-12k tokens) for Agents 1-2, ~25 KB (~6k) for Agent 3. File tree omits converter/tests/utils/analogy bank; topic list shows 6 stale subjects vs ~50 | Recalculate, regenerate tree from disk, list actual subjects |
| Review upkeep | The previous version of this file was stale: it claimed fixed bugs were still present and proposed already-implemented features. This file now replaces it. | Keep this review current, or convert it to a changelog once items are fixed |

---

## Test gaps

The 18 existing tests do not cover:

- `##` inside fenced code
- corrupt YAML update
- VTT `NOTE` blocks / short spoken lines
- table cells containing `|` in math
- blockquote Q&A splitting
- lint false positives (jargon, placeholder, secrets, `$`)
- metadata extra keys / placeholders inside metadata
- prerequisite overlap and cap
- flow-order false positive

Add these as regression tests before refactoring. They are cheap and will prevent the next round of regressions.

---

## Implementation status (current working tree)

All review items below have been implemented and covered by regression tests in
`scripts/test_review_regressions.py` (run with `python -m pytest scripts`).

| Item | Status | Notes |
|---|---|---|
| C1 flow-order false failures | Fixed | Sequential keyword search; order violations are FAIL only with explicit `source_anchor` line ranges, otherwise WARN. `anchor_quote` is primary evidence. |
| C2 corrupt YAML overwrite | Fixed | `load_topic_map(..., strict=True)` raises `TopicMapParseError`; updater aborts on corrupt-but-present files; writes are atomic with a `.bak`. |
| C3 splitter fence handling | Fixed | Fence-aware heading scan; `##` inside ```/~~~ is ignored. |
| C4 VTT content loss | Fixed | `NOTE`/`STYLE`/`REGION` blocks skipped to the next blank line; cue-ID detection rejects sentence punctuation; `--keep-timestamps` added. |
| H1 heading classes / duplicate H1 | Fixed | `h1.lecture-title`, `h2.section-title`, `h3.subsection-title`; preamble H1 dropped when the template supplies `{{TITLE}}`. |
| H2 tables and blockquotes | Fixed | Table splitting respects `\|` and pipes inside math; blockquote Q/A lines become separate `<p>` elements. |
| H3 lint false positives | Fixed | Jargon needs pipeline context; `placeholder token` allowed; secrets/PII scoped to visible prose; currency `$5` allowed; shared PII severity. |
| H4 lint false negatives | Fixed | Unknown metadata keys FAIL; placeholders inside metadata JSON detected; description length is a hard 100-155 contract. |
| H5 prompt contradictions | Fixed | No new major `##` topics from any companion doc (supporting `###`/callouts allowed); summary invalidation documented; cleanup list includes `*_run_events.jsonl`; output path now `outputs/<Subject>/<LecturePrefix>/<LecturePrefix>_notes/...`. |
| H6 prerequisite generator | Fixed | Distinctive-token overlap, administrative/appendix filtering, cap (default 10), grouped by lecture, links to previous notes, empty output at low confidence. |
| H7 manifest provenance | Fixed | `anchor_quote` primary, token-boundary/acronym keyword matching, explicit line-range anchors, prose teaching moments, named-reference verification, placeholder IDs replaced with `<concept-id>`. |
| H8 `lint_dense` | Fixed | All non-dotted appendix headings exempt (plus `<!-- appendix -->` marker); resolved verify markers stripped before hand-waving and readability checks. |
| Medium items | Fixed | Dotted heading IDs, unique SVG IDs enforced in prompts + lint warning, canonical `subjects.json` resolution, pytest/Python floor, ASCII console output, inline escaping/balanced links, docs, README, YAML path repair. |
| Test gaps | Closed | `scripts/test_review_regressions.py` covers every gap listed in this review. |

---

## Verification & Test Suite Summary

All 67 automated tests in the test suite pass with zero errors:

- `scripts/test_pipeline.py`: Core pipeline regression tests (now includes coverage for fenced code splitting, corrupt YAML updates, flow-order keyword verification, VTT NOTE/cue cleaning, table pipes in math, blockquote Q&A splitting, academic false positives, metadata hygiene, prerequisite HTML generation, and SEO boundaries).
- `scripts/test_review_regressions.py`: Comprehensive targeted tests for Critical (C1-C4), Rendering (H1-H2), Lint (H3-H4, H8), and Provenance/Prerequisite (H6-H7) fixes.
- `scripts/test_integration.py`: Topic mapping integration tests verifying subject resolution via canonical `subjects.json` and atomic topic map updates.

### Running Tests
To run the complete test suite:
```bash
pytest -v
```

