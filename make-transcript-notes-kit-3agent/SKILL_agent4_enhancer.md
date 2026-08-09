---
name: enhancer
description: >-
  Phase 4 of make-transcript-notes-kit. A standalone post-processing agent that
  takes Agent 3's final HTML file and injects interactive learning elements —
  parameter playgrounds, algorithm step-through visualizers, concept check cards,
  curated external resources, concept relationship maps, and visual diagrams/flowcharts.
  Modifies the existing HTML file directly in-place alongside per-lecture CSS and JS files.
  Never modifies Agents 1–3's files, scripts, or intermediate artifacts. Operates on an audit-first,
  no-force principle: if no content in the HTML naturally warrants interactivity or visual diagramming,
  the agent says so and stops. Every interactive element or diagram must trace to specific
  content in the source HTML with zero invented educational content.
  Trigger after Agent 3 (formatter).
---

# Agent 4 — Enhancer

**Your job:** Take Agent 3's final HTML file (`<LecturePrefix>_notes/<LecturePrefix>_notes.html`) and add interactive learning elements and visual diagrams directly into the HTML file that serve as cognitive relief between dense text sections and help readers internalize difficult concepts through manipulation and visualization. You are a post-processor, not a content author. Every element you add must trace directly to specific content already present in the HTML. If the HTML does not naturally lend itself to interactivity or visual diagrams, say so and stop.

**Your input:**
1. Agent 3's final HTML: `outputs/<Subject>/<LecturePrefix>/<LecturePrefix>_notes/<LecturePrefix>_notes.html`

**Your output:** Inside the same `<LecturePrefix>_notes/` directory:
1. `<LecturePrefix>_notes.html` — the existing HTML file, modified in-place with injected interactive learning elements and visual diagrams
2. `<LecturePrefix>_enhancements.css` — per-lecture CSS for enhancement widgets only
3. `<LecturePrefix>_enhancements.js` — per-lecture JS for interactive widgets
4. `<LecturePrefix>_enhancement_audit.json` — the opportunity audit report

**The existing `<LecturePrefix>_notes.html` is edited directly in-place after user audit approval. Original text and structure outside interactive widgets must remain intact.**

---

## Core rules for this phase

1. **No-force policy** — If no content in the HTML qualifies for any of the 6 enhancement types, produce the audit file documenting "no opportunities found" and STOP. Shipping a lecture with zero enhancements is an acceptable, expected outcome. Never manufacture interactivity or diagrams for their own sake.
2. **Source-lock** — Every interactive element or visual diagram must trace to a specific, identifiable element in the original HTML — a formula (`\[ ... \]`), a worked example (`.example-box`), a tree/graph diagram (`<pre>`), a verbal visual intuition description ("Visual Intuition", chart specs with axes), a state transition sequence (e.g. MDP backup tree), an architectural pipeline, a Q&A block (`.important-note` with **Q:**/**A:**), or an exam revision entry (`.exam-revision-entry`). If you cannot cite the exact heading ID or element, the enhancement is rejected.
3. **No invented content** — You do not write explanations, definitions, formulas, or educational prose. You restructure existing HTML content into interactive or visual diagrammatic form. The words, numbers, math, step sequences, and axis/node labels come from the HTML.
4. **Progressive enhancement** — All enhancements are additive overlays. The original HTML content remains intact and fully readable with JavaScript disabled. Every widget includes a `<noscript>` fallback.
5. **CSS isolation** — The per-lecture `_enhancements.css` file must NOT define rules for any existing CSS class from `/lecture-notes.css`. All enhancement classes use the `enh-` prefix (e.g., `.enh-playground`, `.enh-stepper`, `.enh-concept-check`, `.enh-diagram`). No `!important` overrides on existing styles.
6. **Strict File Attachment Guard Rail** — Focus *only and only* on the HTML file attached to the prompt/context. Do *not* search for or read other lecture files, enriched drafts, or section files. Your sole input is the final HTML.
7. **Strict Script Creation Guard Rail** — You are strictly prohibited from creating or writing any script (Python, Bash, JS, etc.) inside the toolkit folder (`make-transcript-notes-kit-3agent` or its subfolders). The only files you create or modify are the 4 output files listed above, inside the lecture's `<LecturePrefix>_notes/` directory.
8. **Agents 1–3 are untouchable** — Never modify, read, or reference SKILL files, templates, scripts, or intermediate artifacts belonging to Agents 1–3. You operate exclusively on Agent 3's final HTML output.

---

## Step 0 — Parse the HTML

Read the input HTML file and extract a structured inventory:

1. **Headings:** Every `<h2>` and `<h3>` with their `id` attributes, heading text, and nesting structure.
2. **Formulas:** Every `\[ ... \]` display math block. Record the parent heading, the raw LaTeX string, and any surrounding symbol definitions (text containing "where \(...\) is..." patterns).
3. **Worked examples:** Every `.example-box` div. Record the parent heading, the step structure (numbered lists, computation lines), and all numerical values.
4. **Tree/graph diagrams:** Every `<pre>` block containing ASCII art with `/`, `\`, `|`, or `---` connectors. Record the parent heading and the raw text.
5. **Step-by-step walkthroughs:** Every numbered list (`<ol>`) or sequential structure inside an `.example-box` with ≥ 5 steps that describe state changes (value updates, node selections, decisions). Record the parent heading, step count, and per-step content.
6. **Q&A blocks:** Every `.important-note` div containing `<strong>Q:</strong>` / `<strong>A:</strong>` patterns. Record the parent heading and the Q/A text.
7. **Exam revision entries:** Every `.exam-revision-entry` div with its `.revision-check` (self-check question) content.
8. **Relationship language:** Phrases in body text that explicitly connect sections: "this builds on...", "unlike X...", "this is an optimization of...", "alternative to...", "feeds into...", "requires...". Record the source heading, target concept, and the exact phrase.
9. **Visual intuition & chart specifications:** Text containing "Visual Intuition", chart descriptions specifying axes (X/Y), curve shapes (linear, exponential, U-shaped, S-curve, loss landscapes, value functions), landmarks (peaks, troughs, asymptotes, crossing points), or visual takeaways.
10. **State transitions, pipelines, flowcharts, & architecture descriptions:** Text or ASCII describing sequence of operations, state transitions (e.g. RL MDP state-action-reward backups, state machine transitions, neural network layer stacks, data pipelines, decision flowcharts).

Save this inventory internally (not as a file) for use in Step 1.

---

## Step 1 — Opportunity Audit

For each `<h2>` section in the inventory, evaluate it against the qualification criteria for each of the 5 enhancement types. A section may qualify for 0, 1, or multiple types.

### Enhancement Type 1: Interactive Parameter Playground

**Qualifies when ALL of:**
- The section contains a display math formula (`\[ ... \]`) inside a `.key-concept` div
- The formula has ≥ 2 named input variables with types/domains defined in surrounding prose
- The section contains a worked example (`.example-box`) that provides concrete numerical values for those variables (establishing plausible slider ranges)

**Does NOT qualify when ANY of:**
- The formula is purely definitional (set notation, no computable output)
- Variables lack clear numerical ranges anywhere in the section
- The formula involves multi-step derivations that cannot be expressed as a single computable expression
- The formula's output type is non-numeric (e.g., a set, a distribution, a boolean condition)

**Audit entry format:**
```json
{
  "type": "parameter-playground",
  "qualifies": true,
  "source_heading_id": "8.3.3",
  "source_heading_text": "Phase 1: Selection",
  "formula_latex": "\\text{UCT}(i) = \\frac{w_i}{n_i} + C \\sqrt{\\frac{\\ln N_p}{n_i}}",
  "variables": [
    {"name": "w_i", "label": "Win count", "type": "integer", "range_source": "worked example at 8.3.4", "min": 0, "max": 100, "default": 37},
    {"name": "n_i", "label": "Visit count", "type": "integer", "range_source": "worked example at 8.3.4", "min": 1, "max": 200, "default": 79},
    {"name": "N_p", "label": "Parent visits", "type": "integer", "range_source": "worked example at 8.3.4", "min": 1, "max": 300, "default": 100},
    {"name": "C", "label": "Exploration constant", "type": "float", "range_source": "prose: 'typically C = sqrt(2)'", "min": 0.1, "max": 5.0, "default": 1.414}
  ],
  "rationale": "UCT formula has 4 named variables with defined domains. Worked example provides concrete values. Exploration-exploitation tradeoff is best understood by adjusting C."
}
```

---

### Enhancement Type 2: Algorithm Step-Through Visualizer

**Qualifies when ALL of:**
- The section contains a numbered step-by-step walkthrough with ≥ 5 discrete steps
- Each step describes an explicit state change (a value update, a node selection, a pruning decision) with specific numerical values
- The section contains an associated ASCII tree/graph diagram (`<pre>` block) OR the steps describe traversal of a named structure

**Does NOT qualify when ANY of:**
- The algorithm description is prose-only with no numbered steps
- The walkthrough has < 5 steps
- Steps are conceptual descriptions without specific numerical state changes
- There is no structural diagram and the steps do not describe spatial traversal

**Audit entry format:**
```json
{
  "type": "algorithm-stepper",
  "qualifies": true,
  "source_heading_id": "8.2.4",
  "source_heading_text": "Worked Examples and Derivations",
  "step_count": 11,
  "has_tree_diagram": true,
  "tree_source": "<pre> block at line ~290",
  "nodes": ["A", "B", "C", "D", "E", "F", "G"],
  "steps_summary": [
    "Step 1: Initialize A with α=-∞, β=+∞",
    "Step 2: Traverse to B, inherit bounds",
    "..."
  ],
  "rationale": "11-step Alpha-Beta walkthrough with explicit α/β updates and a tree diagram. Currently an ASCII tree + numbered prose — extremely hard to follow as static text."
}
```

---

### Enhancement Type 3: Concept Check Card

**Qualifies when EITHER of:**
- The section has an associated `.exam-revision-entry` with a non-empty `.revision-check` field
- The section contains a conceptual Q&A block (`.important-note` with **Q:**/**A:**) that tests understanding (not an administrative question)

**Does NOT qualify when:**
- The only Q&A blocks are administrative ("Will this be on the exam?", "How are groups formed?")
- There is no `.revision-check` and no conceptual Q&A in the section
- The Q&A simply restates a definition without testing understanding

**Audit entry format:**
```json
{
  "type": "concept-check",
  "qualifies": true,
  "source_heading_id": "8.2",
  "source_heading_text": "Alpha-Beta Pruning Algorithm",
  "question_source": "revision-check",
  "question_text": "If α = 5 at a Max node and β = 3 at a Min child, does pruning occur?",
  "answer_text": "Yes. β ≤ α (3 ≤ 5), so remaining children are pruned.",
  "rationale": "Direct self-test for the pruning condition — the most examinable aspect of this concept."
}
```

---

### Enhancement Type 4: Curated External Learning Resources

**Qualifies when ALL of:**
- The section covers a concept with well-known external educational resources that the agent can verify exist
- The agent can find ≥ 1 resource that passes the verification checklist (Step 2)
- The resource genuinely aids understanding of the specific concept in the section (not just the broad topic)

**Does NOT qualify when:**
- The topic is too niche or lecture-specific for quality external resources to exist
- Available resources are low-quality, paywalled, or from unverified sources
- The agent would need to paraphrase or summarize external content rather than link to it

**Approved resource types and sources:**

| Resource Type | Approved Sources | Verification |
|---|---|---|
| Explanatory videos | YouTube — 3Blue1Brown, StatQuest, MIT OCW, Sebastian Lague, Computerphile, Two Minute Papers | Video exists, title matches topic, channel is established (≥ 10K subscribers) |
| Interactive visualizations | VisuAlgo, Algorithm Visualizer, Distill.pub, Red Blob Games, Setosa.io, Explorable Explanations | Page loads, covers the claimed topic, is interactive |
| Reference articles | Wikipedia, Stanford Encyclopedia of Philosophy, Brilliant.org (free articles), textbook companion sites | URL loads, content discusses the exact concept |
| Educational humor | XKCD (with explainxkcd link), Sandserif comics, established CS meme accounts | Must genuinely illustrate/explain the concept, not just be tangentially funny |

**Blocked sources:** Random blogs, Medium articles without verified author credentials, AI-generated content farms, Pinterest/Instagram aggregations, any site requiring account creation to view content.

**Audit entry format:**
```json
{
  "type": "external-resources",
  "qualifies": true,
  "source_heading_id": "8.2",
  "source_heading_text": "Alpha-Beta Pruning Algorithm",
  "resources": [
    {
      "title": "Alpha-Beta Pruning | Algorithms",
      "url": "https://www.youtube.com/watch?v=...",
      "type": "video",
      "source": "Sebastian Lague",
      "verified": true,
      "rationale": "Step-by-step animated walkthrough of Alpha-Beta pruning on a game tree — directly visualizes the same algorithm covered in this section"
    }
  ],
  "rationale": "Alpha-Beta pruning is a classic CS algorithm with abundant high-quality visual explanations available."
}
```

---

### Enhancement Type 5: Concept Relationship Map

**Qualifies when ALL of:**
- The lecture has ≥ 3 `<h2>` sections covering distinct concepts (excluding appendix sections like "Exam Guidance Summary" and "Key Industry Applications")
- The prose contains ≥ 2 explicit relationship statements between sections (e.g., "this builds on...", "unlike X, MCTS...", "this is an optimization applied to...")
- The relationships are directly stated in the text, not inferred

**Does NOT qualify when:**
- The lecture has ≤ 2 concept sections
- Sections are independent topics with no stated connections in the prose
- The only connections would need to be inferred by the agent

**Audit entry format:**
```json
{
  "type": "concept-map",
  "qualifies": true,
  "source": "lecture-wide",
  "concept_nodes": [
    {"id": "8.1", "label": "Static Evaluation Functions"},
    {"id": "8.2", "label": "Alpha-Beta Pruning"},
    {"id": "8.3", "label": "Monte Carlo Tree Search"}
  ],
  "edges": [
    {
      "from": "8.1",
      "to": "8.2",
      "label": "provides leaf values for",
      "source_text": "practical game-playing agents apply a depth limit to the search tree... and evaluates the resulting non-terminal board configurations",
      "source_heading": "8.1.1"
    },
    {
      "from": "8.2",
      "to": "8.3",
      "label": "alternative when heuristic eval is impossible",
      "source_text": "While Alpha-Beta pruning works exceptionally well for games with moderate branching factors and explicit static evaluation functions... it degrades when applied to games with massive search spaces",
      "source_heading": "8.3.1"
    }
  ],
  "rationale": "3 core concepts with explicitly stated progressive relationships in the prose. Map shows the pedagogical flow: evaluate → prune → go heuristic-free."
}
```

---

### Enhancement Type 6: Visual Diagram & Flowchart Generator

**Qualifies when ANY of:**
- The section contains a verbal/textual description of a chart, curve, or plot specifying X/Y axes, curve dynamics (linear, exponential, U-shaped, S-curve, loss landscape, value function), or landmark points (peaks, troughs, asymptotes, crossing points).
- The section describes state-action transitions, Markov chains, MDP backup trees (state node → action node → next state node), or dynamic programming state graphs in prose or mathematical text.
- The section contains a standalone pre-formatted ASCII tree/graph or architectural block diagram in a `<pre>` block that does NOT have an associated 5-step numerical stepper.
- The section describes a multi-stage architectural pipeline, neural network layer stack, data processing workflow, or decision tree/flowchart in prose or list form.

**Does NOT qualify when ANY of:**
- The section only mentions a concept abstractly without describing visual structure, axes, curves, components, transitions, or pipelines.
- The diagram would require inventing structural relationships, nodes, axes, or values not supported by the HTML prose or ASCII art.
- The concept is already covered by a Type 2 Algorithm Stepper in the same section.

**Audit entry format:**
```json
{
  "type": "visual-diagram",
  "qualifies": true,
  "source_heading_id": "5.2",
  "source_heading_text": "Bellman Expectation Equation & Backup Diagram",
  "diagram_category": "state-transition",
  "render_engine": "inline-svg",
  "axes_or_nodes": {
    "x_axis": null,
    "y_axis": null,
    "nodes": ["State s", "Actions a", "Rewards r", "Next States s'"],
    "landmark_or_flow": "Root state s branches to actions a with probability π(a|s), leading to next states s' with transition probability P(s'|s,a)"
  },
  "rationale": "Section explicitly describes the Bellman expectation backup tree structure in prose. Rendering an inline SVG backup diagram turns dense equations into visual intuition."
}
```

---

## Step 2 — Web Research for External Resources

For each section that qualifies for Enhancement Type 4, search the web for resources. Apply the verification protocol:

### Verification Protocol

For every candidate resource:

1. **Fetch the URL** — confirm the page loads successfully (HTTP 200). Dead links, 404s, and redirect loops are rejected.
2. **Content match** — read the page content and verify it actually discusses the specific concept (not just the broad topic). A video titled "Game Theory Basics" does NOT qualify for a section on Alpha-Beta Pruning specifically.
3. **Source reputation** — verify the source is from the approved list or meets the quality bar: established educational channel (≥ 10K subscribers for YouTube), recognized academic institution, well-known educational platform, or long-running educational project.
4. **Accessibility** — content must be freely accessible without login, paywall, or account creation.
5. **Recency** — prefer resources that are not severely outdated (within ~10 years for algorithms/CS topics, more flexible for mathematical concepts).

**If verification fails for all candidates in a section, mark the section as `"qualifies": false` for external resources. Do not include unverified links.**

---

## Step 3 — Present Audit and Stop for User Approval

Save the complete audit to `<LecturePrefix>_enhancement_audit.json`:

```json
{
  "lecture_prefix": "ACI_Lecture_08",
  "source_html": "ACI_Lecture_08_notes.html",
  "audit_timestamp": "2026-07-24T22:00:00+05:30",
  "total_sections_scanned": 4,
  "sections_with_opportunities": 3,
  "sections_with_no_opportunities": 1,
  "enhancements": [
    { ... audit entries from Steps 1–2 ... }
  ],
  "no_opportunity_sections": [
    {
      "heading_id": "8.4",
      "heading_text": "Course Syllabus Summary and Mid-Semester Exam Review",
      "reason": "Administrative/review section — no formulas with variable exploration, no algorithmic walkthroughs, no conceptual depth warranting interactive elements."
    }
  ]
}
```

**Present the audit to the user.** Summarize:
- How many sections have opportunities, how many don't
- Which enhancement types were identified, with one-line rationales
- Any sections where you found zero qualifying opportunities

**STOP HERE.** Wait for explicit user approval. The user may:
- Approve all items
- Reject specific items
- Request modifications

Do NOT proceed to Step 4 without user approval.

---

## Step 4 — Implement Approved Items

For each approved enhancement, inject the HTML directly into `<LecturePrefix>_notes.html`. Follow these structural rules:

### General Injection Rules

1. **Placement:** Enhancement widgets are injected AFTER the source content element they reference, never before or inside it.
2. **Wrapper:** Every enhancement is wrapped in a `<div class="enh-widget" data-type="[type]" data-source="[heading-id]">`.
3. **Fallback:** Every widget includes `<noscript><p class="enh-fallback">Interactive widget requires JavaScript enabled.</p></noscript>`.
4. **Attribution:** External resources include visible source attribution.
5. **No content modification:** Original lecture HTML content outside the injected widgets is never altered, moved, or deleted. Enhancements are purely additive insertions.

### Type 1: Parameter Playground Implementation

```html
<div class="enh-widget" data-type="playground" data-source="8.3.3">
  <h4 class="enh-widget-title">🎛️ Explore: UCT Formula</h4>
  <div class="enh-playground">
    <div class="enh-playground-controls">
      <!-- One slider per variable, labels and ranges from audit -->
      <label class="enh-slider-label">
        <span>w<sub>i</sub> (Win count)</span>
        <input type="range" class="enh-slider" data-var="w" min="0" max="100" value="37">
        <output class="enh-slider-value">37</output>
      </label>
      <!-- ... more sliders ... -->
    </div>
    <div class="enh-playground-output">
      <p class="enh-formula-display">\[ \text{UCT} = \frac{w_i}{n_i} + C \sqrt{\frac{\ln N_p}{n_i}} \]</p>
      <p class="enh-result">UCT = <span class="enh-computed-value">—</span></p>
      <div class="enh-bar-breakdown">
        <!-- Stacked bar showing exploitation vs exploration term -->
      </div>
    </div>
  </div>
  <noscript><p class="enh-fallback">Interactive slider widget requires JavaScript.</p></noscript>
</div>
```

**JS requirements:**
- Parse slider values on `input` event
- Compute the formula result using the exact mathematical expression from the HTML
- Update the displayed result and any visual breakdown
- All computation logic must be a direct translation of the LaTeX — no approximations, no simplifications

### Type 2: Algorithm Step-Through Implementation

```html
<div class="enh-widget" data-type="stepper" data-source="8.2.4">
  <h4 class="enh-widget-title">🌳 Step Through: Alpha-Beta Pruning</h4>
  <div class="enh-stepper">
    <div class="enh-stepper-controls">
      <button class="enh-step-prev" disabled>← Previous</button>
      <span class="enh-step-counter">Step 1 of 11</span>
      <button class="enh-step-next">Next →</button>
    </div>
    <div class="enh-stepper-diagram">
      <!-- SVG rendering of the tree, nodes colored by current step state -->
      <svg class="enh-tree-svg" viewBox="0 0 400 300">
        <!-- Nodes and edges generated from the ASCII tree structure -->
      </svg>
    </div>
    <div class="enh-stepper-narrative">
      <!-- Current step's text, extracted verbatim from the walkthrough -->
      <p class="enh-step-text">Step 1: Initialize Root Node A (Max). Set α = -∞, β = +∞.</p>
    </div>
  </div>
  <noscript><p class="enh-fallback">Step-through visualizer requires JavaScript.</p></noscript>
</div>
```

**JS requirements:**
- Step data array extracted verbatim from the numbered walkthrough in the HTML
- Each step highlights the active node, updates displayed α/β values, and shows pruning cuts
- Node positions derived from the ASCII tree structure
- Step narrative text copied verbatim from the HTML — no rewording

### Type 3: Concept Check Card Implementation

```html
<div class="enh-widget" data-type="concept-check" data-source="8.2">
  <details class="enh-concept-check">
    <summary class="enh-check-prompt">🧠 Check Your Understanding: Alpha-Beta Pruning</summary>
    <div class="enh-check-content">
      <p class="enh-check-question"><strong>Question:</strong> [verbatim from revision-check or Q&A]</p>
      <hr class="enh-check-divider">
      <p class="enh-check-answer"><strong>Answer:</strong> [verbatim from revision-check or Q&A]</p>
    </div>
  </details>
</div>
```

**No JS needed — pure HTML `<details>`/`<summary>`.**

### Type 4: External Resources Implementation

```html
<div class="enh-widget" data-type="external-resources" data-source="8.2">
  <details class="enh-resources-panel" open>
    <summary class="enh-resources-title">🌐 Explore Further: Alpha-Beta Pruning</summary>
    <div class="enh-resources-list">
      <div class="enh-resource-item">
        <a href="[verified URL]" target="_blank" rel="noopener" class="enh-resource-link">[Title]</a>
        <span class="enh-resource-meta">[Type] · [Source/Channel]</span>
        <p class="enh-resource-rationale">[1-line rationale from audit]</p>
      </div>
    </div>
  </details>
</div>
```

### Type 5: Concept Map Implementation

Injected once, at the top of `<main>` (after the hero header and prerequisite section, before the first `<h2>`):

```html
<div class="enh-widget" data-type="concept-map" data-source="lecture-wide">
  <h4 class="enh-widget-title">MAP How This Lecture Connects</h4>
  <div class="enh-concept-map">
    <div class="mermaid">
      graph TD
        A["8.1 Static Evaluation Functions"] -->|"provides leaf values for"| B["8.2 Alpha-Beta Pruning"]
        B -->|"alternative when heuristic eval is impossible"| C["8.3 Monte Carlo Tree Search"]
    </div>
    <p class="enh-map-note">Connections sourced from the lecture text. Click a topic to jump to that section.</p>
  </div>
  <noscript><p class="enh-fallback">Concept map requires JavaScript (Mermaid).</p></noscript>
</div>
```

**JS requirements:**
- Include Mermaid CDN (`<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>`) in the `<head>`
- Node click handlers scroll to the corresponding `<h2>` heading

### Type 6: Visual Diagram & Flowchart Implementation

Enhancement Type 6 injects visual diagrams directly into the lecture notes for sections that contain visual intuition descriptions, state transitions (e.g., MDP backup diagrams), ASCII art diagrams, or multi-stage architectural pipelines.

#### A. Inline SVG Diagram Template (Plots, Curves, State-Action Backup Trees, ASCII Conversions)

```html
<div class="enh-widget" data-type="visual-diagram" data-source="5.2">
  <h4 class="enh-widget-title">📊 Visual Diagram: Bellman Expectation Backup</h4>
  <figure class="enh-diagram">
    <div class="enh-diagram-canvas">
      <svg class="enh-svg-diagram" viewBox="0 0 500 300" role="img" aria-label="Bellman Expectation Backup Tree">
        <defs>
          <marker id="enh-arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--enh-accent)" />
          </marker>
        </defs>
        <!-- Nodes, edges, markers, and axis lines formatted with theme CSS custom properties -->
      </svg>
    </div>
    <figcaption class="enh-diagram-caption">
      <strong>Key Takeaway:</strong> [1-sentence insight extracted verbatim or structurally derived from the HTML prose]
    </figcaption>
  </figure>
</div>
```

#### B. Mermaid Flowchart Template (Process Pipelines & Architectural Workflows)

```html
<div class="enh-widget" data-type="visual-diagram" data-source="4.1">
  <h4 class="enh-widget-title">🔀 Flowchart: Neural Network Forward & Backward Pass</h4>
  <figure class="enh-diagram">
    <div class="enh-diagram-canvas">
      <div class="mermaid">
        graph LR
          Input["Input X"] --> Hidden["Hidden Layers H"]
          Hidden --> Output["Output \hat{Y}"]
          Output --> Loss["Loss L"]
          Loss -.->|"Backprop Gradient"| Hidden
      </div>
    </div>
    <figcaption class="enh-diagram-caption">
      <strong>Process Flow:</strong> [1-sentence description extracted from the HTML prose]
    </figcaption>
  </figure>
  <noscript><p class="enh-fallback">Diagram requires JavaScript (Mermaid).</p></noscript>
</div>
```

#### Visual Description Quality Rules (inspired by `make-transcript-notes-kit`)

When creating SVG or Mermaid diagrams from section content:
1. **Name the Axes (for plots/curves):** Name both X and Y axes with units whenever provided in the text.
2. **Describe Curve Shapes & Dynamics:** Accurately reflect whether a curve is linear, exponential, U-shaped, S-shaped, a bell curve, or a loss landscape.
3. **Highlight Landmarks:** Highlight key landmarks (peaks, troughs, crossing points, asymptotes, decision splits, or state-action nodes) using distinct circle markers, labeled callouts, or colored node fills.
4. **Clear Directional Flow:** For state transitions (MDP backup trees, Markov chains, dynamic programming grids) and architectural pipelines, use explicit directional arrowheads (`marker-end="url(#enh-arrow)"`), distinct node shapes (circles for states, rectangles for actions/processes), and explicit transition labels.
5. **Theme-Adaptive Colors:** Never hardcode hex color strings like `#000000` or `#FFFFFF` inside SVG `fill` or `stroke` attributes. Always use CSS custom properties (`var(--enh-accent)`, `var(--enh-border)`, `var(--enh-text)`, `var(--enh-bg-subtle)`, `var(--enh-text-muted)`) so diagrams automatically adapt when `html[data-theme="dark"]` is active.
6. **Strict Source Lock:** All node names, axis labels, curve behaviors, and takeaway captions MUST trace directly to the lecture HTML prose or ASCII art. Never invent unmentioned nodes or steps.

---

## Step 5 — Generate Per-Lecture CSS

Create `<LecturePrefix>_enhancements.css` in the same output directory. This file:

- Contains ONLY classes prefixed with `enh-`
- Does NOT redefine or override any class from `/lecture-notes.css`
- Does NOT use `!important`
- Provides all styling for the 6 enhancement widget types (Playgrounds, Steppers, Concept Cards, Resources, Concept Maps, Visual Diagrams)
- Inherits BitsNotes design system tokens from `/lecture-notes.css` and `tokens.css` for seamless light/dark mode adaptation
- Uses `references/enhancements_reference.css` in the toolkit as the canonical styling blueprint

### Website Theme Integration Guidelines

To ensure interactive widgets match the platform's visual identity seamlessly:
1. **Token Inheritance**: Map `--enh-*` variables directly to `bitsnotes` design tokens (`var(--accent)`, `var(--bg-card)`, `var(--bg-subtle)`, `var(--border)`, `var(--text)`, `var(--text-muted)`, `var(--r-md)`, etc.).
2. **Canonical Styling Reference**: Refer to [`references/enhancements_reference.css`](file:///e:/Projects/bitsnotes/make-transcript-notes-kit-3agent/references/enhancements_reference.css) inside the toolkit for exact class structures and theme-matched rules.
3. **Automatic Dark Mode**: Never hardcode light-only hex background colors (`#ffffff`) or dark text colors (`#000000`). Rely on `--enh-*` CSS custom properties so widgets automatically adapt when `html[data-theme="dark"]` is active.
4. **CSS Isolation**: Always prefix widget classes with `enh-` and avoid overriding core site classes.

**Required CSS structure:**

```css
/* === Enhancement Widgets — <LecturePrefix> === */
/* Styles ONLY Agent 4 enhancement widgets for this lecture.
   Consumes BitsNotes design system tokens for light/dark mode.
   See references/enhancements_reference.css for complete widget styles. */

:root {
  --enh-accent: var(--accent, #0F766E);
  --enh-accent-hover: var(--accent-hover, #115E59);
  --enh-accent-subtle: var(--accent-subtle, rgba(15, 118, 110, 0.10));
  
  --enh-bg: var(--bg-card, var(--bg, #FFFFFF));
  --enh-bg-subtle: var(--bg-subtle, #F6F8FA);
  
  --enh-border: var(--border, #D0D7DE);
  --enh-border-strong: var(--border-strong, #8B949E);
  
  --enh-text: var(--text, #1F2328);
  --enh-text-muted: var(--text-muted, #656D76);
  
  --enh-radius-sm: var(--r-sm, 6px);
  --enh-radius-md: var(--r-md, 10px);
  
  --enh-font-body: var(--font-body, 'Inter', system-ui, sans-serif);
  --enh-font-code: var(--font-code, 'Fira Code', monospace);
}

/* ... widget-specific styles (derived from references/enhancements_reference.css) ... */
```

Add a `<link>` tag for this CSS in `<LecturePrefix>_notes.html`'s `<head>`:
```html
<link rel="stylesheet" href="<LecturePrefix>_enhancements.css">
```

---

## Step 6 — Generate Per-Lecture JS

Create `<LecturePrefix>_enhancements.js` in the same output directory. This file:

- Contains ONLY the interactivity logic for enhancement widgets present in this specific lecture
- Does NOT manipulate any DOM elements outside of `.enh-widget` containers
- Uses `DOMContentLoaded` event listener for initialization
- Is structured as an IIFE to avoid global namespace pollution
- Includes inline comments documenting which HTML source each computation traces to

**Required JS structure:**

```javascript
/* === Enhancement Widgets — <LecturePrefix> === */
/* This file provides interactivity ONLY for Agent 4 enhancement widgets.
   It does NOT manipulate any DOM outside .enh-widget containers. */

(function() {
  'use strict';

  document.addEventListener('DOMContentLoaded', function() {
    // Initialize widgets present in this lecture
    initPlaygrounds();
    initSteppers();
    // Concept checks, external resources, and visual diagrams (SVG/Mermaid) need no custom JS unless interactive tooltips are active
    // Concept maps and Mermaid flowcharts initialized by Mermaid CDN
  });

  function initPlaygrounds() { /* ... */ }
  function initSteppers() { /* ... */ }
})();
```

Add a `<script>` tag for this JS at the bottom of `<LecturePrefix>_notes.html`'s `<body>`:
```html
<script src="<LecturePrefix>_enhancements.js"></script>
```

If concept maps or Mermaid flowchart visual diagrams are present, also add the Mermaid CDN in the `<head>`:
```html
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
<script>mermaid.initialize({startOnLoad: true, theme: 'neutral'});</script>
```

---

## Step 7 — Self-Verification (Anti-Hallucination Checklist)

Before saving the modified `<LecturePrefix>_notes.html`, verify EVERY injected enhancement against this checklist. **Any failure is blocking — remove the offending enhancement rather than shipping it.**

### Per-Enhancement Checks

| Check | What to verify | Failure action |
|---|---|---|
| **Source-lock** | Can you cite the exact heading ID and element type (formula, example, tree, visual intuition description, state transition, Q&A) in the original HTML that this enhancement derives from? | Remove the enhancement |
| **Content fidelity** | Is every piece of text in the widget (labels, step narratives, questions, answers, node labels, axis names) an exact copy from the HTML, or a direct structural transformation of it (e.g., LaTeX → JS math expression, prose → SVG backup tree)? | Remove the enhancement |
| **Formula integrity** | For playgrounds: does the JS computation exactly implement the LaTeX formula? Verify with one numerical spot-check using values from the worked example. | Remove the enhancement |
| **Step fidelity** | For steppers: does every step label and narrative match the numbered walkthrough in the HTML? Is the step count identical? | Remove the enhancement |
| **Diagram fidelity** | For visual diagrams: do axis names, curve shapes, landmark markers, node labels, and directional arrows strictly match the HTML prose/ASCII? Does SVG stroke/fill use theme CSS custom properties (`var(--enh-*)`)? | Remove/fix the diagram |
| **Link verification** | For external resources: does every URL still return HTTP 200? Does the page title/content match the claimed topic? | Remove the dead/mismatched link |
| **No invented prose** | Does the enhancement contain ANY text not traceable to the HTML or to verified external source metadata (title, channel name)? | Remove the invented text |
| **CSS isolation** | Does `_enhancements.css` define any class without the `enh-` prefix? Does it override any existing class? | Fix the CSS |
| **JS isolation** | Does `_enhancements.js` manipulate any DOM element outside `.enh-widget` containers? | Fix the JS |
| **Graceful degradation** | With JS disabled, does every widget show a `<noscript>` fallback? Is the original content still fully visible? | Add missing fallback |

### Global Checks

- [ ] Modifications to `_notes.html` are purely additive (`.enh-widget` insertions, `<link>`, `<script>` tags) and preserve all existing HTML structure
- [ ] The modified HTML renders identically to the original when all `.enh-widget` elements are removed
- [ ] MathJax still renders correctly in the modified HTML (no broken delimiters)
- [ ] The `<script id="lecture-metadata">` block is preserved exactly
- [ ] All SEO meta tags are preserved exactly
- [ ] The enhancement CSS file uses only `enh-` prefixed classes
- [ ] The enhancement JS file is wrapped in an IIFE
- [ ] No `!important` appears in the enhancement CSS
- [ ] Every external resource link was verified within this session

---

## Output file structure

After a successful enhancement run:

```
outputs/<Subject>/<LecturePrefix>/
├── <LecturePrefix>_notes_dense.md          ← Agent 1 (untouched)
├── <LecturePrefix>_notes_enriched.md       ← Agent 2 (untouched)
├── <LecturePrefix>_extraction_manifest.json ← Agent 1 (untouched)
├── sections/                                ← Agents 2-3 (untouched)
└── <LecturePrefix>_notes/
    ├── <LecturePrefix>_notes.html           ← Modified in-place by Agent 4 (interactive elements and visual diagrams injected)
    ├── <LecturePrefix>_enhancements.css     ← Agent 4 output
    ├── <LecturePrefix>_enhancements.js      ← Agent 4 output
    └── <LecturePrefix>_enhancement_audit.json ← Agent 4 audit report
```

---

## Red-list (any one = automatic fail)

- Enhancement contains educational prose not present in the source HTML
- Formula in a playground does not match the LaTeX in the HTML
- Step-through has steps added, removed, or reworded vs. the HTML walkthrough
- Visual diagram contains nodes, axes, or curve shapes not supported by the HTML prose or ASCII art
- Visual diagram hardcodes hex colors (`#000000`/`#FFFFFF`) instead of using CSS custom properties (`var(--enh-*)`)
- External resource link is dead, paywalled, or from a blocked source
- Concept map contains a node that doesn't correspond to an `<h2>` heading
- Concept map contains an edge whose relationship text is not a direct quote from the prose
- Enhancement CSS overrides an existing `/lecture-notes.css` class
- Enhancement JS manipulates DOM outside `.enh-widget` containers
- Original lecture HTML content was deleted, reworded, or corrupted outside of `.enh-widget` insertions
- MathJax rendering is broken in the modified HTML
- Agent invented a Q&A question, an answer, or a self-check question not in the source
- Agent forced an enhancement on a section that does not meet the qualification criteria
- `<noscript>` fallback missing from any JS-dependent widget

---

## Ship checklist (all must be ✓ before finishing)

- [ ] Audit file saved and user-approved before implementation began
- [ ] Original lecture content in `_notes.html` is preserved without deletion or rewording
- [ ] Modified HTML file includes `<link>` to per-lecture CSS and `<script>` to per-lecture JS
- [ ] All `.enh-widget` containers have `data-type` and `data-source` attributes
- [ ] Every enhancement passed the per-enhancement verification checks
- [ ] Visual diagrams use theme-adaptive CSS custom properties and include accessible `<figcaption>` takeaways
- [ ] Every external resource link was verified (HTTP 200 + content match)
- [ ] CSS uses only `enh-` prefixed classes, no `!important`
- [ ] JS is wrapped in IIFE, only touches `.enh-widget` DOM
- [ ] MathJax renders correctly in the modified HTML
- [ ] SEO tags and metadata JSON are preserved exactly
- [ ] `<noscript>` fallback present on every JS-dependent widget
- [ ] No red-list items
- [ ] If no enhancements qualified, audit file documents this and `_notes.html` was not modified

