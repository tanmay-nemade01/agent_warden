#!/usr/bin/env python3
r"""Manifest verification for the 3-Agent Toolkit.

Checks whether source-anchored concepts, examples, Q&A, and teaching moments
survive in the dense or enriched markdown. Essential-item misses are blocking.
"""

import argparse
import io
import json
import os
import re
import sys
from html.parser import HTMLParser

# Force UTF-8 on Windows
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "encoding") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "encoding") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Heading patterns
DOTTED_H2 = re.compile(r"^(\d+)\.(\d+)\s+(.+)$")


class ManifestHTMLExtractor(HTMLParser):
    """Convert final HTML into verifier-friendly markdown-like text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.heading = None
        self.heading_text = []
        self.skip_depth = 0
        self.div_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"h2", "h3"}:
            self.heading = tag
            self.heading_text = []
        if tag == "div":
            classes = dict(attrs).get("class", "").split()
            marker = next(
                (
                    name
                    for name in (
                        "important-note",
                        "example-box",
                        "key-concept",
                        "warning-box",
                        "key-takeaway",
                    )
                    if name in classes
                ),
                None,
            )
            self.div_stack.append(marker)
            if marker:
                self.parts.append(f"\n:::{marker}\n")
        if tag in {"p", "li", "br", "blockquote"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == self.heading:
            prefix = "##" if tag == "h2" else "###"
            self.parts.append(f"\n{prefix} {''.join(self.heading_text).strip()}\n")
            self.heading = None
            self.heading_text = []
        if tag == "div" and self.div_stack:
            if self.div_stack.pop():
                self.parts.append("\n:::\n")
        if tag in {"p", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.heading:
            self.heading_text.append(data)
        else:
            self.parts.append(data)

    def text(self):
        return "".join(self.parts)


def html_to_verifier_text(html):
    parser = ManifestHTMLExtractor()
    parser.feed(html)
    return parser.text()


class VerifyReport:
    def __init__(self):
        self.failures = []
        self.passes = []
        self.warnings = []

    def fail(self, name, message):
        self.failures.append((name, message))

    def warn(self, name, message):
        self.warnings.append((name, message))

    def passed(self, name, message):
        self.passes.append((name, message))

    def print_summary(self):
        print(f"=== Verification Passes: {len(self.passes)} ===")
        for name, msg in self.passes[:15]:
            print(f"[PASS] {name}: {msg}")
        if len(self.passes) > 15:
            print(f"... and {len(self.passes) - 15} more passes.")

        if self.warnings:
            print(f"\n=== Verification Warnings: {len(self.warnings)} ===")
            for name, msg in self.warnings:
                print(f"[WARN] {name}: {msg}")

        if self.failures:
            print(f"\n=== Verification Failures: {len(self.failures)} ===")
            for name, msg in self.failures:
                print(f"[FAIL] {name}: {msg}")
        else:
            print("\nResult: ALL MANIFEST VERIFICATIONS PASSED")


def extract_keywords(text, min_len=4):
    """Tokenize text into lowercase keywords.

    Keeps ordinary words of at least ``min_len`` characters plus upper-case
    acronyms (PPO, MDP, GAN) regardless of length, so abbreviations are not
    silently ignored by the evidence checks.
    """
    # Strip markdown symbols
    cleaned = re.sub(r"[\#\*_`\[\]\(\):-]", " ", str(text))
    words = re.findall(r"\b[a-zA-Z]{" + str(min_len) + r",}\b", cleaned)
    acronyms = re.findall(r"\b[A-Z]{2,}\b", str(text))
    # Filter out common stop words
    stops = {
        "with", "that", "this", "from", "have", "they", "more", "about",
        "some", "were", "what", "their", "them", "then", "there", "when"
    }
    keywords = {w.lower() for w in words if w.lower() not in stops}
    keywords |= {a.lower() for a in acronyms if a.lower() not in stops}
    return keywords


def _keyword_pattern(keyword):
    """Match a keyword on token boundaries (avoids 'act' inside 'practice')."""
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(keyword) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _normalize_whitespace(value):
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def keyword_match(pattern_text, target_text, threshold=2):
    """Check if at least 'threshold' keywords from pattern_text are present in target_text."""
    pat_keywords = extract_keywords(pattern_text)
    if not pat_keywords:
        # Fallback to token-boundary containment for short terms/acronyms.
        return bool(
            _keyword_pattern(_normalize_whitespace(pattern_text)).search(
                _normalize_whitespace(target_text)
            )
        )

    matched = [kw for kw in pat_keywords if _keyword_pattern(kw).search(target_text)]

    req_match = min(threshold, len(pat_keywords))
    return len(matched) >= req_match


def keyword_match_position(pattern_text, target_text, threshold=2, start=0):
    """Return the earliest evidence position at or after ``start``, else -1.

    Searching from the previous event's position makes the flow check
    sequential: two events that share generic words ("reward", "update") are
    still matched in narrative order instead of collapsing onto the same
    offset.
    """
    pat_keywords = extract_keywords(pattern_text)
    if not pat_keywords:
        match = _keyword_pattern(_normalize_whitespace(pattern_text)).search(
            _normalize_whitespace(target_text), start
        )
        return match.start() if match else -1

    positions = []
    for keyword in pat_keywords:
        match = _keyword_pattern(keyword).search(target_text, start)
        if match:
            positions.append(match.start())

    required = min(threshold, len(pat_keywords))
    if len(positions) < required:
        return -1
    positions.sort()
    # The last required keyword bounds the local evidence span.
    return positions[required - 1]


def item_is_essential(item, default=True):
    """Return whether a manifest item is blocking when it cannot be matched."""
    salience = str(item.get("salience", "")).strip().lower()
    if not salience:
        return default
    return salience in {"essential", "core", "exam-critical", "exam_critical"}


def report_item_miss(report, name, message, item, default_essential=True):
    if item_is_essential(item, default=default_essential):
        report.fail(name, message)
    else:
        report.warn(name, message)


def has_math_markup(text):
    """Detect supported inline, display, or environment-based LaTeX."""
    return bool(re.search(
        r"\\\(.*?\\\)|\\\[.*?\\\]|\\begin\{(?:aligned|align\*?|equation\*?)\}",
        text,
        flags=re.DOTALL,
    ))


def has_qna_markup(text, phase):
    return bool(extract_qna_blocks(text, phase))


def extract_qna_blocks(text, phase):
    """Return individual Q&A exchanges rather than searching a whole section."""
    if phase == "enriched":
        candidates = re.findall(
            r":::important-note[^\n]*\n(.*?)\n:::",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    else:
        candidates = re.findall(r"(?:^>\s?.*(?:\n|$))+", text, flags=re.MULTILINE)

    return [
        block
        for block in candidates
        if re.search(r"(?:\*\*)?Q:(?:\*\*)?", block, re.I)
        and re.search(r"(?:\*\*)?A:(?:\*\*)?", block, re.I)
    ]


def validate_trace_fields(report, item, name, schema_version):
    """Require provenance fields for schema v2 manifests.

    The manifest is a condensed checksum of the dense draft, so only a
    source anchor is required — the prose itself carries the detail.
    """
    if schema_version < 2:
        return
    if not str(item.get("source_anchor", "")).strip():
        report.fail(name, "Schema v2 item is missing required 'source_anchor' provenance.")


def item_evidence_matches(item, text, fields):
    """Match at least one useful evidence field, preferring anchor_quote.

    ``anchor_quote`` is the strongest provenance field: a short span copied
    from the source. If it is present and found, the item is proven; keywords
    and summary fields are only fallbacks.
    """
    anchor_quote = _normalize_whitespace(item.get("anchor_quote", ""))
    if anchor_quote:
        if anchor_quote in _normalize_whitespace(text):
            return True
        anchor_keywords = extract_keywords(anchor_quote)
        if anchor_keywords and keyword_match(
            anchor_quote, text, threshold=min(2, len(anchor_keywords))
        ):
            return True

    keywords = item.get("keywords")
    if isinstance(keywords, list) and keywords:
        phrase = " ".join(str(value) for value in keywords)
        if keyword_match(phrase, text, threshold=min(2, len(keywords))):
            return True

    evidence = [str(item.get(field, "")).strip() for field in fields]
    evidence = [value for value in evidence if value]
    return any(keyword_match(value, text, threshold=2) for value in evidence)


def _find_h2_headings(text):
    """Find real ``## `` headings, ignoring headings inside fenced code."""
    headings = []
    offset = 0
    fence_char = None
    fence_len = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        fence = re.match(r"^(```+|~~~+)", stripped)
        if fence:
            marker = fence.group(1)
            if fence_char is None:
                fence_char, fence_len = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                fence_char, fence_len = None, 0
        elif fence_char is None:
            match = re.match(r"^##\s+(.+?)\s*$", line)
            if match:
                headings.append((offset, match.group(1).strip()))
        offset += len(line)
    return headings


def _extract_prose_blocks(text):
    """Split verifier text into paragraph blocks (callout fences removed)."""
    blocks = []
    current = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(":::") or stripped.startswith("#"):
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        if not stripped:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [block for block in blocks if block.strip()]


_LINE_RANGE_RE = re.compile(
    r"(?:^|\b)(?:lines?\s*)(\d+)\s*(?:-|–|—|\.\.|to)\s*(\d+)\b",
    re.IGNORECASE,
)


def _has_explicit_line_range(source_anchor):
    """True when the anchor is a source line range, not a stripped timestamp."""
    return bool(_LINE_RANGE_RE.search(str(source_anchor)))


def verify_manifest(manifest_path, markdown_path, phase="enriched"):
    report = VerifyReport()
    
    if not os.path.exists(manifest_path):
        report.fail("Manifest file", f"Manifest file does not exist: {manifest_path}")
        return report
    
    if not os.path.exists(markdown_path):
        report.fail("Markdown file", f"Markdown file does not exist: {markdown_path}")
        return report

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        report.fail("Manifest file", f"Failed to parse manifest JSON: {e}")
        return report

    with open(markdown_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    content_phase = "enriched" if phase == "html" else phase
    if phase == "html":
        md_text = html_to_verifier_text(md_text)
    try:
        schema_version = int(manifest.get("schema_version", 1) or 1)
    except (TypeError, ValueError):
        report.fail(
            "Manifest schema",
            f"Invalid schema_version: {manifest.get('schema_version')!r}.",
        )
        return report

    # Split markdown into sections (fence-aware: "## " inside code is not a heading)
    h2_matches = _find_h2_headings(md_text)
    sections = {}

    for i, (start, title) in enumerate(h2_matches):
        end = h2_matches[i + 1][0] if i + 1 < len(h2_matches) else len(md_text)
        sect_text = md_text[start:end]
        
        # Try to parse dotted ID
        m = DOTTED_H2.match(title)
        if m:
            dotted_id = f"{m.group(1)}.{m.group(2)}"
            sections[dotted_id] = {
                "title": title,
                "text": sect_text,
                "clean_title": m.group(3).strip()
            }
        else:
            # Fallback using title itself for special sections
            sections[title] = {
                "title": title,
                "text": sect_text,
                "clean_title": title
            }

    # Verify Concepts
    manifest_concepts = manifest.get("concepts", [])
    for concept in manifest_concepts:
        cid = concept.get("id")
        title = concept.get("title", "")
        
        if cid not in sections:
            # Try to match by title
            matched_sect = None
            for sect_id, sect_info in sections.items():
                if title.lower() in sect_info["title"].lower():
                    matched_sect = sect_id
                    break
            if matched_sect:
                cid = matched_sect
            else:
                report.fail(f"Concept {cid}", f"Concept '{title}' (ID {cid}) is missing from enriched markdown.")
                continue
                
        sect = sections[cid]
        sect_text = sect["text"]
        report.passed(f"Concept {cid}", f"Section found: '{sect['title']}'")
        
        # Verify Formula Requirement
        if concept.get("has_formula"):
            has_math = has_math_markup(sect_text)
            if not has_math:
                report.fail(f"Concept {cid} Formula", f"Concept '{title}' is marked as having a formula, but no math block \\( or \\[ was found.")
            else:
                report.passed(f"Concept {cid} Formula", "Math blocks found.")
                
        # Verify Worked Example Requirement
        if concept.get("has_worked_example"):
            has_example = phase == "dense" or ":::example-box" in sect_text
            if not has_example:
                report.fail(f"Concept {cid} Example", f"Concept '{title}' is marked as having a worked example, but no :::example-box was found.")
            else:
                report.passed(f"Concept {cid} Example", "Example box found.")
                
        # Verify Q&A Requirement
        if concept.get("has_qna"):
            has_qna = has_qna_markup(sect_text, content_phase)
            if not has_qna:
                report.fail(f"Concept {cid} Q&A", f"Concept '{title}' is marked as having Q&A, but no Q&A exchange (:::important-note with **Q:**) was found.")
            else:
                report.passed(f"Concept {cid} Q&A", "Q&A exchange found.")

    # Verify Worked Examples by keywords
    for example in manifest.get("worked_examples", []):
        cid = example.get("concept")
        desc = example.get("description", "")
        validate_trace_fields(report, example, f"Worked Example ({cid})", schema_version)
        
        if cid in sections:
            sect_text = sections[cid]["text"]
            if not item_evidence_matches(example, sect_text, ("description",)):
                report_item_miss(
                    report,
                    f"Worked Example ({cid})",
                    f"Worked example '{desc}' was not matched in the concept section.",
                    example,
                    default_essential=schema_version >= 2,
                )
            else:
                report.passed(f"Worked Example ({cid})", f"Example matches description: '{desc}'")
        else:
            report_item_miss(
                report,
                f"Worked Example ({cid})",
                f"Manifest item references missing concept section '{cid}'.",
                example,
                default_essential=schema_version >= 2,
            )

    # Verify Formulas by keywords
    for formula in manifest.get("formulas", []):
        cid = formula.get("concept")
        desc = formula.get("description", "")
        validate_trace_fields(report, formula, f"Formula ({cid})", schema_version)
        
        if cid in sections:
            sect_text = sections[cid]["text"]
            if not has_math_markup(sect_text) or not item_evidence_matches(
                formula, sect_text, ("description",)
            ):
                report_item_miss(
                    report,
                    f"Formula ({cid})",
                    f"Formula '{desc}' was not matched with supported math markup in the concept section.",
                    formula,
                    default_essential=schema_version >= 2,
                )
            else:
                report.passed(f"Formula ({cid})", f"Formula description matched: '{desc}'")
        else:
            report_item_miss(
                report,
                f"Formula ({cid})",
                f"Manifest item references missing concept section '{cid}'.",
                formula,
                default_essential=schema_version >= 2,
            )

    # Verify Q&As by keywords
    for qna in manifest.get("qna_exchanges", []):
        cid = qna.get("concept")
        q_summary = qna.get("question_summary", "")
        validate_trace_fields(report, qna, f"Q&A ({cid})", schema_version)
        
        if cid in sections:
            sect_text = sections[cid]["text"]
            qna_blocks = extract_qna_blocks(sect_text, content_phase)
            if schema_version >= 2:
                question = str(qna.get("question_summary", "")).strip()
                answer = str(qna.get("answer_summary", "")).strip()
                qna_match = bool(question and answer) and any(
                    keyword_match(question, block, threshold=2)
                    and keyword_match(answer, block, threshold=2)
                    for block in qna_blocks
                )
            else:
                qna_match = any(
                    item_evidence_matches(
                        qna,
                        block,
                        ("question_summary", "answer_summary"),
                    )
                    for block in qna_blocks
                )
            if not qna_match:
                report_item_miss(
                    report,
                    f"Q&A ({cid})",
                    f"Q&A '{q_summary}' did not retain both Q/A structure and matching content.",
                    qna,
                    default_essential=schema_version >= 2,
                )
            else:
                report.passed(f"Q&A ({cid})", f"Q&A matches summary: '{q_summary}'")
        else:
            report_item_miss(
                report,
                f"Q&A ({cid})",
                f"Manifest item references missing concept section '{cid}'.",
                qna,
                default_essential=schema_version >= 2,
            )

    # Verify human teaching moments: misconceptions, terminology corrections,
    # analogies, warnings, and re-explanations.
    for moment in manifest.get("teaching_moments", []):
        cid = moment.get("concept")
        moment_id = moment.get("id", f"{cid}.moment")
        moment_type = moment.get("type", "teaching_moment")
        validate_trace_fields(report, moment, f"Teaching moment {moment_id}", max(schema_version, 2))

        if cid not in sections:
            report_item_miss(
                report,
                f"Teaching moment {moment_id}",
                f"No concept section '{cid}' exists for {moment_type}.",
                moment,
            )
            continue

        sect_text = sections[cid]["text"]
        correction_types = {
            "misconception_correction",
            "vocabulary_correction",
            "rejected_analogy",
            "re_explanation",
            "terminology_contrast",
        }
        if moment_type in correction_types:
            required_parts = [
                str(moment.get(field, "")).strip()
                for field in ("trigger", "resolution", "preferred_term")
                if str(moment.get(field, "")).strip()
            ]
            if not required_parts and moment.get("summary"):
                summary_val = str(moment["summary"]).strip()
                if "->" in summary_val:
                    required_parts = [p.strip() for p in summary_val.split("->") if p.strip()]
                elif "→" in summary_val:
                    required_parts = [p.strip() for p in summary_val.split("→") if p.strip()]
                else:
                    required_parts = [summary_val]

            # Corrections may live in a Q&A exchange or in ordinary prose.
            # Either way, all parts must co-occur in the SAME block so two
            # unrelated Q&A entries cannot be stitched together.
            evidence_blocks = extract_qna_blocks(sect_text, content_phase)
            evidence_blocks += _extract_prose_blocks(sect_text)
            matched = any(
                all(
                    keyword_match(
                        part,
                        block,
                        threshold=min(2, max(1, len(extract_keywords(part)))),
                    )
                    for part in required_parts
                )
                for block in evidence_blocks
            ) if required_parts else False
        else:
            matched = item_evidence_matches(
                moment,
                sect_text,
                ("resolution", "summary"),
            )

        if not matched:
            report_item_miss(
                report,
                f"Teaching moment {moment_id}",
                f"Essential {moment_type} did not retain its trigger, resolution, or Q&A structure.",
                moment,
            )
        else:
            report.passed(
                f"Teaching moment {moment_id}",
                f"{moment_type} preserved in section {cid}.",
            )

    # Validate the chronological teaching ledger and preserve relative event
    # order inside each concept, where questions and corrections are causal.
    flow = manifest.get("lecture_flow", [])
    if schema_version >= 2 and not isinstance(flow, list):
        report.fail("Lecture flow", "schema v2 requires lecture_flow to be a list.")
        flow = []
    if schema_version >= 2 and not flow:
        report.fail("Lecture flow", "schema v2 requires a non-empty lecture_flow ledger.")

    previous_order = 0
    positions_by_concept = {}
    order_violations = {}
    for entry in flow:
        order = entry.get("order")
        cid = entry.get("concept")
        summary = str(entry.get("summary", "")).strip()
        source_anchor = str(entry.get("source_anchor", "")).strip()
        anchor_quote = str(entry.get("anchor_quote", "")).strip()
        keywords = entry.get("keywords", [])
        name = f"Lecture flow {order!r}"

        if not isinstance(order, int) or order <= previous_order:
            report.fail(name, "Flow order values must be strictly increasing integers.")
        elif isinstance(order, int):
            previous_order = order
        if not summary or not source_anchor:
            report.fail(
                name,
                "Flow entries require a summary and a source_anchor.",
            )
            continue
        if cid not in sections:
            report.fail(name, f"Flow entry references missing concept section '{cid}'.")
            continue

        sect_text = sections[cid]["text"]
        # anchor_quote is the strongest evidence; keywords and the summary are
        # fallbacks for compact manifests.
        has_explicit_keywords = isinstance(keywords, list) and len(keywords) > 0
        if anchor_quote:
            evidence = anchor_quote
            req_threshold = min(2, max(1, len(extract_keywords(evidence))))
        elif has_explicit_keywords:
            evidence = " ".join(str(keyword) for keyword in keywords)
            req_threshold = max(1, len(extract_keywords(evidence)))
        else:
            evidence = summary
            req_threshold = min(3, max(1, len(extract_keywords(evidence)) // 2))

        previous_position = positions_by_concept.get(cid, [])
        start = previous_position[-1][1] if previous_position else 0
        position = keyword_match_position(
            evidence,
            sect_text,
            threshold=req_threshold,
            start=start,
        )
        if position < 0:
            # Distinguish "content was lost" from "content moved earlier".
            earlier = keyword_match_position(
                evidence,
                sect_text,
                threshold=req_threshold,
                start=0,
            )
            if earlier >= 0:
                order_violations.setdefault(cid, []).append(
                    (order, source_anchor, summary)
                )
                position = earlier
            else:
                report.fail(
                    name,
                    f"Flow event '{summary}' was not matched in concept section {cid}.",
                )
                continue
        positions_by_concept.setdefault(cid, []).append((order, position))
        report.passed(name, f"Flow event preserved in section {cid}.")

    # Order violations are only blocking when the manifest carries explicit
    # source line ranges. Timestamp anchors (or no anchor) mean the manifest
    # cannot prove narrative order, so report a warning instead of failing
    # otherwise-correct lectures.
    for cid, violations in order_violations.items():
        has_line_ranges = all(
            _has_explicit_line_range(anchor) for _, anchor, _ in violations
        )
        if has_line_ranges:
            report.fail(
                f"Lecture flow order ({cid})",
                "Teaching events appear out of source order inside the concept "
                "section, and the manifest carries explicit source line ranges.",
            )
        else:
            report.warn(
                f"Lecture flow order ({cid})",
                "Teaching events appear out of source order, but the manifest "
                "has no explicit source line ranges. Review the section; this "
                "is not blocking.",
            )

    # Verify Exam Guidance by keywords
    exam_sections = {
        sect_id: info
        for sect_id, info in sections.items()
        if "exam" in str(info.get("title", "")).lower()
    }
    for exam in manifest.get("exam_guidance", []):
        cid = exam.get("concept")
        guidance = str(exam.get("guidance", "")).strip()
        if not guidance:
            continue

        matched = False
        if cid in sections:
            matched = keyword_match(guidance, sections[cid]["text"], threshold=2)
        if not matched:
            # Accept any exam-focused section, not only the legacy
            # "Exam Guidance Summary" heading.
            matched = any(
                keyword_match(guidance, info["text"], threshold=2)
                for info in exam_sections.values()
            )
        if matched:
            report.passed(f"Exam Guidance ({cid})", f"Exam guidance matched: '{guidance}'")
        else:
            report.warn(
                f"Exam Guidance ({cid})",
                f"Exam guidance '{guidance}' was not matched in the concept "
                "section or an exam-focused section.",
            )

    # Verify Named References
    normalized_text = _normalize_whitespace(md_text)
    for ref in manifest.get("named_references", []):
        name = str(ref.get("name", "")).strip()
        if not name:
            continue
        found = _normalize_whitespace(name) in normalized_text
        if not found:
            # Fall back to distinctive-token matching for references recorded
            # with section signs or edition noise (e.g. "Bishop §4.3").
            core = re.sub(r"[§#].*$", "", name).strip()
            found = bool(core) and keyword_match(
                core, md_text, threshold=min(2, max(1, len(extract_keywords(core))))
            )
        if found:
            report.passed("Reference", f"Named reference '{name}' found.")
        elif item_is_essential(ref, default=False):
            report.fail("Reference", f"Essential named reference '{name}' is not found in the text.")
        else:
            report.warn("Reference", f"Named reference '{name}' is not found in the text.")

    return report


def main():
    parser = argparse.ArgumentParser(description="Verify dense or enriched markdown against the extraction manifest.")
    parser.add_argument("manifest", help="Path to _extraction_manifest.json")
    parser.add_argument("markdown", help="Path to dense or enriched markdown")
    parser.add_argument(
        "--phase",
        choices=["dense", "enriched", "html"],
        default="enriched",
        help="Dense accepts blockquote Q&A; enriched/HTML require Q&A callouts.",
    )
    args = parser.parse_args()
    
    print("=" * 68)
    print(f"Verifying {args.phase.capitalize()} Content against Manifest")
    print(f"Manifest: {args.manifest}")
    print(f"Markdown: {args.markdown}")
    print("=" * 68)
    
    report = verify_manifest(args.manifest, args.markdown, phase=args.phase)
    report.print_summary()
    
    if report.failures:
        print("-" * 68)
        print(f"Result: FAIL ({len(report.failures)} core checklist failure(s) found). Verify and correct enriched content.")
        sys.exit(1)
        
    sys.exit(0)


if __name__ == "__main__":
    main()
