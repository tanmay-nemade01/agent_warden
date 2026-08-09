# Prompt cookbook — copy, paste, go

## Full conversion (with topic mapping)
```text
Use make-transcript-notes-kit to convert the transcript for <Lecture Name> in
<Subject>. Run all three agents. Preserve source-anchored Q&A, misconceptions,
terminology corrections, professor intuition, worked reasoning, and lecture
flow. Agent 2 updates topic_mappings/<Subject>.yaml after enrichment; Agent 3
reads it for prerequisites and never writes it. Run manifest verification on
both dense and enriched drafts, then run the final HTML lint gate.
```

## Full conversion (no topic mapping)
```text
Use make-transcript-notes-kit to convert the transcript for <Lecture Name> in
<Subject>. Go through exhaustive source-anchored extraction, lecture-faithful
teaching edits, and format-only HTML conversion. Embed metadata in the HTML;
do not create a companion JSON file. Use callouts only where they help. Run
manifest verification and the final lint gate.
```

## Faster notes (lighter enrichment, all gates retained)
```text
Use all three agents for <Lecture Name>, but keep Agent 2 supplementation light.
Do not skip manifest closure, Q&A/correction preservation, math reconciliation,
or strict section assembly. Agent 3 remains format-only.
```

## Enrich existing notes (Phase 2 pass)
```text
Take the existing enriched markdown at <path/to/LectureName_notes_enriched.md>
and its extraction manifest. Run Agent 2 section-by-section. Preserve every
essential teaching moment and fill only useful spine gaps. Do not force all
spine steps or callouts. Verify the result with verify_manifest.py.
```

## One concept deep-dive
```text
In the notes for <Lecture Name>, improve <specific concept> without replacing
its original explanation flow. Preserve source Q&A, corrections, analogies,
and terminology. Add only the missing elements that materially improve
understanding, such as a worked example or scope boundary.
```

## Easy-language & Writing Style Pass
```text
Run an easy-language and writing style pass on the notes at <path>.
The notes should sound like a knowledgeable person explaining things
to a friend — not like an AI chatbot or a corporate press release.
Focus on:
- Fixing ordinary prose only when it is genuinely hard to follow.
- Defining terms on first use inline.
- Using active voice and conversational tone.
- Cutting filler phrases ("It's important to note that" → just say the thing).
- Cutting marketing-speak and AI clichés (the lint gate will flag any remaining).
- Ensuring every formula has a plain-English intuition explanation.
Ignore LaTeX, derivations, symbol definitions, tables, and code when judging
sentence length or Flesch score. Never rewrite correct math merely to satisfy
a readability heuristic.
Technical terms are fine when they are the right word (e.g., "Fourier transform"
is not marketing-speak). Words like "could" and "may" are appropriate when
expressing genuine uncertainty.
```

## Add more worked examples
```text
In the notes for <Lecture Name>, add 2-3 fully-worked examples for
<concept X> — different numbers each time, every step shown, in .example-box
callouts. Each must end with a one-sentence sense-check.
```

## Exam-prep edition
```text
Regenerate the exam revision notes from the core content of <Lecture Name>.
One .exam-revision-entry per major concept with must-know, key formula,
#1 pitfall, self-check, and connections. Built from the core — never invent.
```

## Fix lint failures
```text
lint.py reported FAILs on <path>. Fix every FAIL and re-run until it passes.
```

## Re-score against rubric
```text
Self-score the notes at <path> against the quality rubric. Give breakdown
per category and list every gap keeping it under 85.
```

## Math-focused extraction (Phase 1, math-heavy lecture)
```text
Use Agent 1 (extractor) to process transcript.txt into <LecturePrefix>_notes_dense.md.
This lecture is math-heavy and the transcript describes formulas and
derivations in plain language. Follow the Math extraction protocol strictly:
reconstruct real LaTeX for every formula, keep the professor's verbal
description next to each equation as the audit trail, define every symbol
on first use in surrounding prose (name, meaning, type, units/domain), capture every
derivation step and mark skipped steps with *[verify]*, dimension/shape-check
every vector/matrix formula, and tag any uncertain reconstruction with
*[verify: <reason>]* instead of silently guessing. Output the math map as
part of the Step 0.5 inventory.
```

## Math reconciliation (Phase 2, with enrichment docs)
```text
Use Agent 2 (enricher) to process <LecturePrefix>_notes_dense.md using the attached enrichment docs
<doc1.pdf, doc2.md, ...> as MATH GROUND TRUTH. You must split <LecturePrefix>_notes_dense.md into sections
under sections/, apply the 9-step spine section-by-section (resolving all *[verify]* markers,
annotating all algebra steps, and performing factuality checks), and then reassemble the
enriched sections back into <LecturePrefix>_notes_enriched.md.
```

## Verify just the formulas in existing notes
```text
Scan <path/to/LectureName.html> for every formula. For each, run the Step R4
factuality checks from the enricher skill: dimensional/shape consistency,
limiting/special-case reduction, output domain, and a tiny numerical
spot-check. List any formula that fails a check, with the failing check
and a proposed correction. Do not edit the file — just report.
```

## Complete a skipped derivation
```text
In the notes for <Lecture Name>, the section on <concept> shows a
derivation that skips from <line A> to <line B> with "after simplification".
Fill in every missing algebraic step in a \begin{aligned} block, each line
annotated with the move (distribute, substitute, apply Bayes, take log,
etc.). Source each non-trivial step from <enrichment doc> or a standard
identity. Remove any *[verify]* marker once the gap is filled.
```


