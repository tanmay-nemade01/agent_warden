#!/usr/bin/env python3
r"""Quality gate for transcript-based notes HTML pages.

Checks a generated lecture HTML file against the shipping standard.
Pure standard library (html.parser + re). No third-party deps.

Usage:
    python3 scripts/lint.py <lecture_html_file>

Checks (each reported as PASS / WARN / FAIL):
  * Template hygiene: no surviving {{PLACEHOLDER}} tokens, no broken comments.
  * <meta viewport> present.
  * Metadata JSON is valid and complete (title, subject, date, sections, examRevisionNotes).
  * SEO: Open Graph tags, Twitter Card tags, description length (100–155 chars),
    keywords meta, canonical URL, robots meta, structured data (JSON-LD), author meta.
  * Callout box usage: reports which supported types are used; never requires
    filler boxes merely to include all five types.
  * Math delimiters: flags double-backslash delimiters (\\( \\[) in the visible
    body, which MathJax will NOT render. Use single-backslash \( ... \) and
    \[ ... \] (MathJax is loaded by the platform — no script tag needed).
  * No leaked secrets or PII patterns.
  * Readability heuristic: math-stripped prose is reviewed with advisory WARNs.
  * Banned hand-waving phrases: "clearly", "obviously", "it can be shown",
    "left to the reader", etc. => WARN in the HTML phase.
  * Fancy/academic word detection: advisory WARN.
  * Flesch Reading Ease estimate: scores prose readability (WARN if hard).
  * Suspiciously long unbroken tokens flagged as overflow risk.
  * Exam revision notes: at least one .exam-revision-entry present.
  * No <style> tags, no inline style="" attributes, no Google Fonts links.
  * Leftover *[verify]* math-reconstruction markers (FAIL) — unresolved
    uncertainty stops shipping and requires Agent 2 or human review.

Exit code is non-zero if any check FAILs.
"""

import io
import json
import os
import re
import sys
from html.parser import HTMLParser

if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "encoding") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "encoding") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plain_language as PL  # shared word lists + readability helpers

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SENTENCE_WORD_WARN = PL.WORD_CEILING
SENTENCE_MANY_RATIO = 0.15
SENTENCE_MANY_ABS = 12
LONG_TOKEN_CHARS = 40

SECRET_PATTERNS = [
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("openai-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api_key literal", re.compile(r"""api[_-]?key\s*[:=]\s*['\"][^'\"]+['\"]""", re.I)),
    ("bearer token", re.compile(r"Authorization\s*:\s*Bearer\s+\S+", re.I)),
]

PII_PATTERNS = [
    ("transcript reference", re.compile(r"\b(transcript|lecture recording|audio recording|this recording)\b", re.I)),
    ("professor/instructor mention", re.compile(r"\b(professor|instructor|lecturer)\s+[A-Z][a-z]+\b")),
    ("university mention candidate", re.compile(r"\b(University|Institute|College)\s+of\s+[A-Z]")),
]

NON_PROSE_TAGS = {"script", "style", "noscript", "template", "code", "pre", "svg"}

REQUIRED_CALLOUTS = [
    "key-concept",
    "important-note",
    "example-box",
    "warning-box",
    "key-takeaway",
]

# ---------------------------------------------------------------------------
# Result accounting
# ---------------------------------------------------------------------------

class Report:
    LEVELS = ("PASS", "WARN", "FAIL")

    def __init__(self):
        self.rows = []
        self.counts = {lvl: 0 for lvl in self.LEVELS}

    def add(self, level, name, message=""):
        assert level in self.LEVELS, level
        self.rows.append((level, name, message))
        self.counts[level] += 1

    def passed(self, name, message=""):
        self.add("PASS", name, message)

    def warned(self, name, message=""):
        self.add("WARN", name, message)

    def failed(self, name, message=""):
        self.add("FAIL", name, message)

    def print_all(self):
        glyph = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
        for level, name, message in self.rows:
            print(f"{glyph[level]} {name}")
            if message:
                for sub in message.splitlines():
                    print(f"         {sub}")

    @property
    def has_fail(self):
        return self.counts["FAIL"] > 0


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

VOID_TAGS = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col", "embed", "param", "track", "wbr"}
CALLOUT_CLASSES = {"key-concept", "important-note", "example-box", "warning-box", "key-takeaway"}


class DocParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.has_viewport = False
        self._suppress_depth = 0
        self._prose_chunks = []
        self.classes_used = set()
        self.exam_revision_entries = 0
        self.has_style_tag = False
        self.has_inline_style = False
        self.inline_style_tags = []
        self.has_google_fonts = False
        self.has_chapter_title = False
        self.has_metadata_script = False
        self.metadata_json = None
        self.tag_stack = []
        self.html_errors = []
        # SEO tracking

        self.has_og_title = False
        self.has_og_description = False
        self.has_og_type = False
        self.has_og_url = False
        self.has_og_site_name = False
        self.has_twitter_card = False
        self.has_twitter_title = False
        self.has_twitter_description = False
        self.has_keywords = False
        self.has_canonical = False
        self.has_robots = False
        self.has_author = False
        self.has_structured_data = False
        self.description_content = ""
        self._num_meta_tags = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrd = dict(attrs)

        if tag == "meta":
            self._num_meta_tags += 1
            name = (attrd.get("name") or "").lower()
            prop = (attrd.get("property") or "").lower()
            content = attrd.get("content", "")

            if name == "viewport":
                self.has_viewport = True
            elif name == "description":
                self.description_content = content
            elif name == "keywords":
                self.has_keywords = True
            elif name == "author":
                self.has_author = True
            elif name == "robots":
                self.has_robots = True

            if name == "twitter:card":
                self.has_twitter_card = True
            elif name == "twitter:title":
                self.has_twitter_title = True
            elif name == "twitter:description":
                self.has_twitter_description = True

            if prop == "og:title":
                self.has_og_title = True
            elif prop == "og:description":
                self.has_og_description = True
                if not self.description_content:
                    self.description_content = content
            elif prop == "og:type":
                self.has_og_type = True
            elif prop == "og:url":
                self.has_og_url = True
            elif prop == "og:site_name":
                self.has_og_site_name = True

        if tag == "style":
            self.has_style_tag = True

        if "style" in attrd and (attrd.get("style") or "").strip():
            self.has_inline_style = True
            if tag not in self.inline_style_tags:
                self.inline_style_tags.append(tag)

        if tag == "link":
            href = attrd.get("href", "")
            rel = (attrd.get("rel") or "").lower()
            if "googleapis.com" in href or "fonts.googleapis" in href:
                self.has_google_fonts = True
            if rel == "canonical":
                self.has_canonical = True

        if tag == "script":
            stype = (attrd.get("type") or "").lower()
            if attrd.get("id") == "lecture-metadata":
                self.has_metadata_script = True
            if stype == "application/ld+json":
                self.has_structured_data = True

        if tag == "h1":
            self.has_chapter_title = True

        # Track CSS classes
        cls = attrd.get("class", "")
        if cls:
            for c in cls.split():
                self.classes_used.add(c)

        # Track exam revision entries
        if tag == "div" and "exam-revision-entry" in (attrd.get("class") or ""):
            self.exam_revision_entries += 1

        if tag in NON_PROSE_TAGS:
            self._suppress_depth += 1

        if tag not in VOID_TAGS:
            callout_class = None
            if tag == "div":
                cls_list = (attrd.get("class") or "").split()
                callout_class = next((c for c in cls_list if c in CALLOUT_CLASSES), None)
                if callout_class:
                    parent_callout = next((item["callout"] for item in reversed(self.tag_stack) if item.get("callout")), None)
                    if parent_callout:
                        line_num = self.getpos()[0]
                        self.html_errors.append(
                            f"Line {line_num}: Nested callout box detected: '{callout_class}' is inside '{parent_callout}'"
                        )
            self.tag_stack.append({"tag": tag, "callout": callout_class, "line": self.getpos()[0]})

    BLOCK_TAGS = {
        "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th",
        "figcaption", "button", "label", "a", "div", "section", "blockquote",
    }

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag not in VOID_TAGS:
            if not self.tag_stack:
                line_num = self.getpos()[0]
                self.html_errors.append(f"Line {line_num}: Orphan closing tag </{tag}> with no matching opening tag")
            else:
                top = self.tag_stack[-1]
                if top["tag"] == tag:
                    self.tag_stack.pop()
                else:
                    match_idx = None
                    for idx in range(len(self.tag_stack) - 1, -1, -1):
                        if self.tag_stack[idx]["tag"] == tag:
                            match_idx = idx
                            break
                    if match_idx is not None:
                        unclosed = self.tag_stack[match_idx + 1:]
                        line_num = self.getpos()[0]
                        for item in unclosed:
                            self.html_errors.append(
                                f"Line {line_num}: Unclosed tag <{item['tag']}> (opened on line {item['line']}) before </{tag}>"
                            )
                        self.tag_stack = self.tag_stack[:match_idx]
                    else:
                        line_num = self.getpos()[0]
                        self.html_errors.append(f"Line {line_num}: Orphan closing tag </{tag}> with no matching opening tag")

        if tag in NON_PROSE_TAGS and self._suppress_depth > 0:
            self._suppress_depth -= 1
        if tag in self.BLOCK_TAGS and self._prose_chunks \
                and self._prose_chunks[-1] != "\n":
            self._prose_chunks.append("\n")

    def close(self):
        super().close()
        for item in self.tag_stack:
            self.html_errors.append(
                f"Unclosed tag <{item['tag']}> (opened on line {item['line']}) at end of file"
            )
        self.tag_stack.clear()

    def handle_data(self, data):
        if self._suppress_depth == 0:
            stripped = data.strip()
            if stripped:
                self._prose_chunks.append(stripped)

    @property
    def prose(self):
        return " ".join(self._prose_chunks)



# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_template_hygiene(raw, report):
    problems = []

    pos, n_comments = 0, 0
    while True:
        start = raw.find("<!--", pos)
        if start < 0:
            break
        end = raw.find("-->", start + 4)
        if end < 0:
            problems.append(f"comment opened at offset {start} never closed")
            break
        body = raw[start + 4:end]
        if "<!--" in body:
            line = raw[:start].count("\n") + 1
            problems.append(
                f"comment at line {line} contains nested '<!--' — will end early and leak text")
        pos = end + 3
        n_comments += 1

    visible = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    visible = re.sub(r"<script\b.*?</script>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r"<style\b.*?</style>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)
    leftovers = re.findall(r"\{\{[^}]{0,80}\}?\}?", visible)
    if leftovers:
        sample = "; ".join(t.strip()[:40] for t in leftovers[:5])
        problems.append(
            f"{len(leftovers)} visible {{{{placeholder}}}} token(s) would "
            f"render as literal text: {sample}")

    if problems:
        report.failed("template hygiene", "\n".join(problems))
    else:
        report.passed("template hygiene",
                      f"{n_comments} comment(s) well-formed; no visible placeholders.")


def check_viewport(parser, report):
    if parser.has_viewport:
        report.passed("meta viewport", "Responsive viewport meta tag present.")
    else:
        report.failed("meta viewport",
                      "Missing <meta name=\"viewport\"> tag. Page won't be mobile-responsive.")


def check_seo(parser, report):
    """Validate search-engine optimisation metadata.

    Checks: description length (140–155 chars), Open Graph tags, Twitter Card
    tags, keywords meta, canonical URL, robots meta, author meta, and JSON-LD
    structured data.
    """
    issues = []
    warnings = []

    # --- Description (the most important SEO element) ---
    desc = parser.description_content.strip()
    if not desc:
        issues.append("Missing <meta name=\"description\">. Every page must have a unique description.")
    else:
        clean = re.sub(r"<[^>]+>", "", desc).strip()
        clean = re.sub(r"\s+", " ", clean)
        if len(clean) < 100:
            issues.append(
                f"Description is too short ({len(clean)} chars); must be 100-155 "
                "characters and packed with keywords for search engines.")
        elif len(clean) > 155:
            warnings.append(
                f"Description is {len(clean)} chars; search engines typically "
                "truncate descriptions longer than 155 characters.")
        else:
            warnings.append(f"Description length ({len(clean)} chars) — good.")

    # --- Open Graph tags (required by Facebook, LinkedIn, etc.) ---
    og_required = {
        "og:title": parser.has_og_title,
        "og:description": parser.has_og_description,
        "og:type": parser.has_og_type,
        "og:url": parser.has_og_url,
        "og:site_name": parser.has_og_site_name,
    }
    og_missing = [k for k, v in og_required.items() if not v]
    if og_missing:
        issues.append("Missing Open Graph tag(s): " + ", ".join(og_missing)
                      + ". OG tags are required for Facebook/LinkedIn link previews.")

    # --- Twitter Card tags ---
    tw_required = {
        "twitter:card": parser.has_twitter_card,
        "twitter:title": parser.has_twitter_title,
        "twitter:description": parser.has_twitter_description,
    }
    tw_missing = [k for k, v in tw_required.items() if not v]
    if tw_missing:
        issues.append("Missing Twitter Card tag(s): " + ", ".join(tw_missing)
                      + ". Twitter Cards control how links appear when shared on X/Twitter.")

    # --- Other meta tags ---
    if not parser.has_keywords:
        issues.append("Missing <meta name=\"keywords\">. Populate with keyConcepts + subject name for search engines.")
    if not parser.has_canonical:
        issues.append("Missing <link rel=\"canonical\">. Canonical URLs prevent duplicate-content penalties.")
    if not parser.has_robots:
        issues.append("Missing <meta name=\"robots\">. Explicitly set to 'index, follow' for public pages.")
    if not parser.has_author:
        warnings.append("Missing <meta name=\"author\">. Recommended for content attribution signals.")

    # --- Structured Data (JSON-LD) ---
    if not parser.has_structured_data:
        issues.append(
            "Missing <script type=\"application/ld+json\"> structured data. "
            "JSON-LD helps search engines understand the page as educational content "
            "(Article or Course schema recommended).")

    if issues:
        report.failed("SEO", "\n".join(issues))
    elif warnings:
        report.warned("SEO", "\n".join(warnings))
    else:
        report.passed("SEO", "All SEO tags present and description length optimal.")


def check_metadata(raw, parser, report):
    if not parser.has_metadata_script:
        report.failed("metadata", "No <script id=\"lecture-metadata\"> tag found.")
        return

    m = re.search(
        r'<script[^>]*id\s*=\s*"lecture-metadata"[^>]*>\s*(.*?)\s*</script>',
        raw, re.DOTALL)
    if not m:
        report.failed("metadata", "Could not extract metadata JSON from script tag.")
        return

    json_str = m.group(1)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        report.failed("metadata", f"Invalid JSON in lecture-metadata: {e}")
        return

    checks = []
    required = {
        "title": str, "subject": str, "gradeLevel": str,
        "datePublished": str, "targetAudience": str,
        "sections": list, "examRevisionNotes": list,
    }

    for field, ftype in required.items():
        if field not in data:
            checks.append(f"missing required field: '{field}'")
        elif not isinstance(data[field], ftype):
            checks.append(f"'{field}' must be {ftype.__name__}, got {type(data[field]).__name__}")
        elif ftype == str and not data[field].strip():
            checks.append(f"'{field}' is empty")
        elif ftype == list and len(data[field]) == 0:
            checks.append(f"'{field}' is an empty list")

    if checks:
        report.failed("metadata", "\n".join(checks))
        return

    # Validate examRevisionNotes structure
    exam = data["examRevisionNotes"]
    if len(exam) < 2:
        report.warned("metadata", f"examRevisionNotes has only {len(exam)} entries (recommend one per major concept).")
    exam_ok = True
    for i, entry in enumerate(exam):
        echecks = []
        if "topic" not in entry or not entry["topic"]:
            echecks.append("missing 'topic'")
        if "mustKnow" not in entry or not entry["mustKnow"]:
            echecks.append("missing 'mustKnow'")
        if "keyFormula" not in entry or not entry["keyFormula"]:
            echecks.append("missing 'keyFormula'")
        if "commonPitfall" not in entry or not entry["commonPitfall"]:
            echecks.append("missing 'commonPitfall'")
        if "quickCheck" not in entry or not entry["quickCheck"]:
            echecks.append("missing 'quickCheck'")
        if "connections" not in entry:
            echecks.append("missing 'connections'")
        if echecks:
            exam_ok = False
            report.warned("metadata", f"examRevisionNotes entry {i} ('{entry.get('topic', 'unknown')}'): {'; '.join(echecks)}")
    if exam_ok and len(exam) >= 2:
        report.passed("metadata", f"Valid metadata JSON with {len(data['sections'])} sections, {len(exam)} exam revision entries.")
    else:
        report.warned("metadata", "Metadata JSON present but has issues (see above).")

    # Warn if removed fields are still present
    removed_fields = [f for f in ("summary", "keyConcepts", "quiz") if f in data]
    if removed_fields:
        report.warned("metadata",
                      f"Metadata contains removed field(s): {', '.join(removed_fields)}. "
                      "These sections are no longer part of the output. Remove them.")


def check_callout_boxes(parser, report):
    found = []
    for c in REQUIRED_CALLOUTS:
        if c in parser.classes_used:
            found.append(c)

    if found:
        report.passed(
            "callout boxes",
            f"Used {len(found)} appropriate callout type(s): {', '.join(found)}. "
            "Not every lecture needs all five.",
        )
    else:
        report.warned(
            "callout boxes",
            "No callout boxes found. Add one only where it materially helps; "
            "do not create filler to satisfy the linter.",
        )


def check_style_separation(parser, report):
    issues = []
    if parser.has_style_tag:
        issues.append("Found <style> tag — must rely on /lecture-notes.css only.")
    if parser.has_inline_style:
        issues.append(
            "Found inline style=\"\" attribute(s) on: "
            + ", ".join(f"<{t}>" for t in parser.inline_style_tags)
            + " - styling must come from /lecture-notes.css, not inline attributes.")
    if parser.has_google_fonts:
        issues.append("Found Google Fonts link — must rely on platform fonts only.")
    if issues:
        report.failed("style separation", "\n".join(issues))
    else:
        report.passed("style separation",
                      "No <style> tags, inline styles, or Google Fonts links.")


_DOUBLE_BACKSLASH_MATH = re.compile(r"\\\\[(\[)\]]")


def check_math(raw, report):
    """Reject invalid math formatting, delimiters, and HTML-incompatible syntax in the visible body.

    In the final HTML, MathJax delimiters must be single-backslash: \\( \\)
    and \\[ \\]. A literal '\\\\(' (two backslashes) renders as an escaped
    backslash plus a paren, so the math will not typeset. Note: the metadata
    JSON legitimately JSON-escapes backslashes, so scripts/styles/comments are
    stripped before scanning.
    """
    visible = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    visible = re.sub(r"<script\b.*?</script>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r"<style\b.*?</style>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)

    failed = False

    # 1. Double-backslash check
    hits = _DOUBLE_BACKSLASH_MATH.findall(visible)
    if hits:
        report.failed(
            "math delimiters",
            f"{len(hits)} double-backslash math delimiter(s) found in the body "
            "(e.g. '\\\\(' or '\\\\['). MathJax will not render these. "
            "Use single-backslash \\( ... \\) for inline and \\[ ... \\] for blocks.")
        failed = True

    # 2. Split delimiter check (delimiters alone inside HTML tags)
    # matching things like <p>\[</p> or <li>\)</li>
    split_hits = re.findall(r"<([a-zA-Z0-9]+)\b[^>]*>\s*\\(?:\[|\]|\(|\))\s*</\1>", visible, re.IGNORECASE)
    if split_hits:
        report.failed(
            "math delimiters",
            f"Found {len(split_hits)} math delimiter(s) isolated in separate HTML tags (e.g. <{split_hits[0]}>\[</{split_hits[0]}>). "
            "The math expression and its delimiters must be contained within the same HTML element for MathJax to render it."
        )
        failed = True

    # 3. Nested/double delimiter check (e.g., \[\[ or \]\] or \(\( or \)\))
    nested_hits = re.findall(r"\\\[\s*\\\[|\\\]\s*\\\]|\\\(\s*\\\(|\\\)\s*\\\)", visible)
    if nested_hits:
        report.failed(
            "math delimiters",
            f"Found {len(nested_hits)} nested or double math delimiters in the body (e.g. '\\\\[\\\\[' or '\\\\]\\\\]'). "
            "Use single delimiters like '\\[' and '\\]' instead."
        )
        failed = True

    # 4. Raw '<' or '<' followed by letters check inside math blocks
    math_blocks = re.findall(r"\\\(.*?\\\)|\\\[.*?\\\]", visible, re.DOTALL)
    raw_char_hits = []
    for block in math_blocks:
        if re.search(r"<[a-zA-Z/]", block):
            raw_char_hits.append(block)
    if raw_char_hits:
        sample = raw_char_hits[0].strip().replace("\n", " ")
        if len(sample) > 80:
            sample = sample[:77] + "..."
        report.failed(
            "math delimiters",
            f"Found {len(raw_char_hits)} math block(s) containing raw '<' or '<' followed by letters (e.g., '{sample}'). "
            "Browsers will parse these as HTML tags and break rendering. Use '\\lt' or '&lt;' instead."
        )
        failed = True

    if not failed:
        report.passed("math delimiters",
                      "No invalid math delimiters, split tags, nested delimiters, or raw HTML-breaking character issues found.")


def check_pii_and_secrets(raw, report):
    # Secrets
    secret_hits = []
    for label, pat in SECRET_PATTERNS:
        for m in pat.finditer(raw):
            snippet = m.group(0)
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            secret_hits.append(f"{label}: {snippet}")
    if secret_hits:
        seen, uniq = set(), []
        for h in secret_hits:
            if h not in seen:
                seen.add(h)
                uniq.append(h)
        report.failed("secrets", "Possible secret(s) detected:\n  " + "\n  ".join(uniq))
    else:
        report.passed("secrets", "No secret-like patterns found.")

    # PII / transcript references
    pii_hits = []
    for label, pat in PII_PATTERNS:
        for m in pat.finditer(raw):
            snippet = m.group(0)
            pii_hits.append(f"{label}: \"{snippet}\"")
    if pii_hits:
        report.failed("PII / anonymization",
                      "Possible PII or source-file references found (notes must stand alone "
                      "without person/institute names while preserving anonymous Q&A):\n  "
                      + "\n  ".join(pii_hits))
    else:
        report.passed("PII / anonymization",
                      "No obvious PII or transcript references detected.")


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"\u201d])\s+")
_WORD = re.compile(r"\S+")


def _prose_for_readability(prose):
    """Remove mathematical regions before applying language heuristics."""
    clean = re.sub(
        r"\\begin\{([A-Za-z*]+)\}.*?\\end\{\1\}",
        " ",
        prose,
        flags=re.DOTALL,
    )
    clean = re.sub(r"\\\(.*?\\\)|\\\[.*?\\\]|\$\$.*?\$\$", " ", clean,
                   flags=re.DOTALL)
    lines = []
    for line in clean.splitlines():
        if (
            re.search(r"\\(?:frac|sum|prod|int|mathbb|mathbf|begin|end)\b", line)
            or "\\[" in line
            or "\\]" in line
        ):
            continue
        lines.append(line)
    return "\n".join(lines)


def check_readability(parser, report):
    prose = _prose_for_readability(parser.prose)
    if not prose.strip():
        report.warned("readability", "No visible prose extracted; cannot assess.")
        return

    sentences = []
    for line in prose.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            sentences.extend(s for s in _SENTENCE_SPLIT.split(line) if s.strip())
    if not sentences:
        sentences = [re.sub(r"\s+", " ", prose).strip()]

    long_ones = []
    for s in sentences:
        wc = len(_WORD.findall(s))
        if wc > SENTENCE_WORD_WARN:
            long_ones.append((wc, s))

    total = len(sentences)
    n_long = len(long_ones)
    ratio = n_long / total if total else 0.0
    long_ones.sort(reverse=True)
    longest_preview = "\n".join(
        f"{wc}w: " + (s[:120] + ("..." if len(s) > 120 else ""))
        for wc, s in long_ones[:3]
    )

    base = (f"{total} sentences; {n_long} over {SENTENCE_WORD_WARN} words "
            f"({ratio*100:.0f}%).")
    if n_long == 0:
        report.passed("readability", base + " Sentences are short and clear.")
    elif n_long >= SENTENCE_MANY_ABS or ratio > SENTENCE_MANY_RATIO:
        report.warned(
            "readability",
            base + " Review only genuinely difficult prose; math has been excluded and this is not blocking.\n"
            "Longest prose samples:\n" + longest_preview,
        )
    else:
        report.warned(
            "readability",
            base + " Advisory only; do not alter mathematical content to shorten text.\n"
            "Longest prose samples:\n" + longest_preview,
        )


def check_handwaving(parser, report):
    """Detect banned hand-waving phrases/words in the prose (WARN)."""
    prose = parser.prose
    hits = PL.find_handwaving(prose)
    if hits:
        detail = "; ".join(f"'{p}' ({c}x)" for p, c in hits[:8])
        report.warned("hand-waving",
                      f"Banned hand-waving found: {detail}. "
                      "These are discouraged in easy-language notes — "
                      "consider replacing each with the algebra/logic it hides.")
    else:
        report.passed("hand-waving", "No banned hand-waving phrases detected.")


def check_fancy_words(parser, report):
    """Flag fancy/academic words that have plain alternatives (WARN)."""
    prose = _prose_for_readability(parser.prose)
    hits = PL.find_fancy(prose)
    if not hits:
        report.passed("fancy words", "No fancy-word offenders detected.")
        return
    total = sum(c for _, _, c in hits)
    detail = "; ".join(f"'{w}'→'{s}' ({c}x)" for w, s, c in hits[:6])
    if total >= 10:
        report.warned("fancy words",
                      f"{total} fancy-word hit(s): {detail}. "
                      "Too many academic words — consider plain alternatives.")
    else:
        report.warned("fancy words",
                      f"{total} fancy-word hit(s): {detail}. "
                      "Consider plain alternatives for a friendlier read.")


def check_flesch(parser, report):
    """Estimate Flesch Reading Ease of the prose (WARN if too hard)."""
    prose = _prose_for_readability(parser.prose)
    score, n_words, n_sent = PL.flesch_reading_ease(prose)
    if n_words < 50:
        report.passed("Flesch reading ease",
                      f"Too few words ({n_words}) for a reliable score.")
    elif score >= 60:
        report.passed("Flesch reading ease",
                      f"Score {score}/100 (≥60 = plain English, ~grade 9).")
    elif score >= 45:
        report.warned("Flesch reading ease",
                      f"Score {score}/100 (45-59 = fairly hard, ~grade 10-12). "
                      "Aim to improve the readability of the surrounding prose, but do not force changes on mathematical statements.")
    else:
        report.warned("Flesch reading ease",
                      f"Score {score}/100 (<45 = hard, ~college level). "
                      "Aim to improve the readability of the surrounding prose as much as possible, but do not force changes on mathematical statements (leave math formulas and symbols as is).")


def check_long_tokens(parser, report):
    prose = parser.prose
    risky = []
    for tok in _WORD.findall(prose):
        if len(tok) > LONG_TOKEN_CHARS and not tok.startswith(("http://", "https://")):
            risky.append(tok)
    risky = sorted(set(risky), key=len, reverse=True)
    if not risky:
        report.passed("long tokens",
                      f"No unbroken tokens longer than {LONG_TOKEN_CHARS} chars in prose.")
    else:
        preview = "\n".join(
            (t[:60] + "...") + f" ({len(t)} chars)" for t in risky[:3]
        )
        report.warned("long tokens",
                      f"{len(risky)} long unbroken token(s) may cause overflow:\n" + preview)


def check_exam_revision(parser, report):
    if parser.exam_revision_entries == 0:
        report.warned("exam revision", "No .exam-revision-entry divs found in HTML. Every lecture should have one entry per major concept.")
    elif parser.exam_revision_entries < 2:
        report.warned("exam revision", f"Only {parser.exam_revision_entries} exam revision entry found (recommend one per major concept).")
    else:
        report.passed("exam revision", f"{parser.exam_revision_entries} exam revision entries present.")


def check_content_structure(parser, report):
    checks = []
    if not parser.has_chapter_title:
        checks.append("No h1 tag found — use h1.chapter-title for chapter headings.")
    if not parser.has_metadata_script:
        checks.append("No lecture-metadata script tag.")

    if checks:
        report.warned("content structure", "\n".join(checks))
    else:
        report.passed("content structure", "Has metadata script and chapter headings.")


# ---------------------------------------------------------------------------
# Leftover math-verification marker check
# ---------------------------------------------------------------------------

_VERIFY_MARKER = re.compile(r"\*\[verify(?::[^\]]*)?\]\*", re.IGNORECASE)


def check_verify_markers(raw, report):
    """Detect leftover *[verify]* markers from Agent 1/Agent 2.

    Agent 1 marks uncertain math reconstructions with *[verify: reason]*.
    Agent 2 must resolve every one (confirm/correct/fill) or escalate it
    inside a :::warning-box callout and remove the raw marker token.
    A surviving *[verify]* token in the shipped HTML means a math
    reconstruction was never reconciled — a real correctness risk.

    Scripts/styles/comments are stripped so JSON-escaped text in the
    metadata script does not trip the check.
    """
    visible = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    visible = re.sub(r"<script\b.*?</script>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r"<style\b.*?</style>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)

    hits = _VERIFY_MARKER.findall(visible)
    if hits:
        sample = "; ".join(h[:60] for h in hits[:5])
        report.failed(
            "math verify markers",
            f"{len(hits)} leftover *[verify]* marker(s) found in the visible "
            f"body. Uncertain math must be resolved or the pipeline must stop "
            f"for human review before HTML conversion. Sample: {sample}")
    else:
        report.passed("math verify markers",
                      "No leftover *[verify]* markers in the visible body.")


# ---------------------------------------------------------------------------
# Intermediate metadata leakage check
# ---------------------------------------------------------------------------

_INTERMEDIATE_HEADING = re.compile(
    r"<h[1-6][^>]*class\s*=\s*\"[^\"]*section-title[^\"]*\"[^>]*>\s*"
    r"(?:Extraction\s*Checklist|Quality\s*[Ss]elf[-\s]*[Cc]heck)",
    re.IGNORECASE
)
_CHECKBOX_LIST = re.compile(r"\[x\]", re.IGNORECASE)


def check_intermediate_metadata(raw, report):
    """Detect extraction checklists, quality self-checks, or other intermediate
    process metadata that has leaked into the final HTML.

    These are Agent 1's and Agent 2's internal verification artifacts and
    must never appear in the shipped notes.
    """
    # Strip scripts and comments so we only check visible body
    visible = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    visible = re.sub(r"<script\b.*?</script>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r"<style\b.*?</style>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)

    hits = []

    # Check for intermediate section headings
    heading_matches = _INTERMEDIATE_HEADING.findall(visible)
    if heading_matches:
        hits.append(
            f"Found {len(heading_matches)} intermediate metadata heading(s) in the "
            f"visible body (e.g. 'Extraction Checklist', 'Quality self-check'). "
            f"These are Agent 1/Agent 2 internal artifacts — strip them before shipping."
        )

    # Check for extraction checklist pattern: a section followed by many [x] items
    # Find all [x] markers in visible content
    checkbox_hits = _CHECKBOX_LIST.findall(visible)
    if len(checkbox_hits) >= 5:
        hits.append(
            f"Found {len(checkbox_hits)} '[x]' checkbox markers in the visible body. "
            f"This looks like a leaked extraction checklist. "
            f"Remove the entire checklist section — it is intermediate process data, "
            f"not educational content."
        )

    if hits:
        report.failed("intermediate metadata leakage", "\n".join(hits))
    else:
        report.passed("intermediate metadata leakage",
                      "No extraction checklists or quality self-checks leaked into the HTML.")


# -- Pipeline jargon patterns (student-facing guardrail) --
_PIPELINE_JARGON_PATTERNS = [
    ("'Enriched' in title/heading/body",
     re.compile(r"\bEnriched\b", re.IGNORECASE)),
    ("'Dense Draft' reference",
     re.compile(r"\bDense\s+Draft\b", re.IGNORECASE)),
    ("Agent name/phase label",
     re.compile(r"\b(?:Agent\s*[123]|Extractor|Enricher|Formatter)\b", re.IGNORECASE)),
    ("'enriched by' or 'created by agent'",
     re.compile(r"(?:enriched\s+by|created\s+by\s+agent|based\s+on\s+agent)", re.IGNORECASE)),
    ("Callout legend (CSS class names)",
     re.compile(r"(?:key-concept|important-note|example-box|warning-box|key-takeaway)\s*=\s*")),
    ("Enrichment source attribution",
     re.compile(r"Enrichment\s+sources?\s*:", re.IGNORECASE)),
]


def check_pipeline_jargon(raw, parser, report):
    """Detect pipeline-internal language that should never be visible to students.

    Checks both the <title> tag and the visible body text for words like
    'Enriched', agent names, callout legends, and enrichment source
    attribution. Any hit is a FAIL.
    """
    # Extract visible text: strip scripts, styles, comments
    visible = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    visible = re.sub(r"<script\b.*?</script>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r"<style\b.*?</style>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)

    # Also check <title> tag content specifically
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw,
                            re.DOTALL | re.IGNORECASE)
    title_text = title_match.group(1).strip() if title_match else ""

    hits = []

    for desc, pattern in _PIPELINE_JARGON_PATTERNS:
        # Check title
        if title_text and pattern.search(title_text):
            hits.append(f"{desc} found in <title> tag: '{title_text[:80]}'")
        # Check visible body
        body_matches = pattern.findall(visible)
        if body_matches:
            sample = body_matches[0] if isinstance(body_matches[0], str) else str(body_matches[0])
            hits.append(
                f"{desc} found in visible body ({len(body_matches)} occurrence(s), "
                f"e.g. '{sample[:60]}'). Pipeline-internal language must not be "
                f"visible to students.")

    # Also check metadata title for jargon
    meta_match = re.search(
        r'<script[^>]*id\s*=\s*"lecture-metadata"[^>]*>\s*(.*?)\s*</script>',
        raw, re.DOTALL)
    if meta_match:
        try:
            meta_data = json.loads(meta_match.group(1))
            meta_title = meta_data.get("title", "")
            for desc, pattern in _PIPELINE_JARGON_PATTERNS:
                if pattern.search(meta_title):
                    hits.append(
                        f"{desc} found in metadata title: '{meta_title[:80]}'. "
                        "The metadata title must be a clean topic title.")
                    break
        except (json.JSONDecodeError, AttributeError):
            pass  # metadata JSON issues are caught by check_metadata

    if hits:
        report.failed("pipeline jargon (student-facing)", "\n".join(hits))
    else:
        report.passed("pipeline jargon (student-facing)",
                      "No pipeline-internal language found in visible content.")


# -- Placeholder and task patterns (student-facing guardrail) --
_PLACEHOLDER_TASK_PATTERNS = [
    ("TODO marker", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("placeholder marker", re.compile(r"\bplaceholder\b", re.IGNORECASE)),
    ("Agent 2 task instruction", re.compile(r"\bdefine\s+[\w\s-]{2,50}\s+in\s+(?:[\w\s-]+\s+)?notes?\b", re.IGNORECASE)),
    ("bracketed placeholder", re.compile(r"\[\s*(?:insert|add|fill|define|todo|placeholder)[^\]]{0,100}\]", re.IGNORECASE)),
    ("Agent 3 delegation", re.compile(r"\b(?:task|instruction)\s+for\s+Agent\s*3\b", re.IGNORECASE)),
]


def check_placeholder_tasks(raw, parser, report):
    """Detect leftover placeholder text, TODOs, or task instructions.

    These are Agent 2 process instructions for Agent 3 (e.g., 'Define Logistic
    Regression in revision notes' or 'TODO: ...') and must be resolved
    content-aware by Agent 3 during HTML conversion.
    """
    # Extract visible text: strip scripts, styles, comments
    visible = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    visible = re.sub(r"<script\b.*?</script>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r"<style\b.*?</style>", "", visible,
                     flags=re.DOTALL | re.IGNORECASE)

    hits = []

    # Check visible text
    for desc, pattern in _PLACEHOLDER_TASK_PATTERNS:
        matches = pattern.findall(visible)
        if matches:
            sample = matches[0] if isinstance(matches[0], str) else str(matches[0])
            hits.append(
                f"{desc} found in visible body: '{sample[:60]}'."
            )

    # Check metadata JSON fields
    meta_match = re.search(
        r'<script[^>]*id\s*=\s*"lecture-metadata"[^>]*>\s*(.*?)\s*</script>',
        raw, re.DOTALL)
    if meta_match:
        try:
            meta_data = json.loads(meta_match.group(1))
            def _has_placeholder(val, pattern):
                if isinstance(val, str):
                    return bool(pattern.search(val))
                if isinstance(val, list):
                    return any(_has_placeholder(item, pattern) for item in val)
                if isinstance(val, dict):
                    return any(_has_placeholder(item, pattern) for item in val.values())
                return False

            for desc, pattern in _PLACEHOLDER_TASK_PATTERNS:
                if _has_placeholder(meta_data, pattern):
                    # Find a sample from metadata to show in the report
                    sample = ""
                    def _find_sample(val, pattern):
                        if isinstance(val, str) and pattern.search(val):
                            return val
                        if isinstance(val, list):
                            for item in val:
                                s = _find_sample(item, pattern)
                                if s: return s
                        if isinstance(val, dict):
                            for item in val.values():
                                s = _find_sample(item, pattern)
                                if s: return s
                        return ""
                    sample = _find_sample(meta_data, pattern)
                    hits.append(
                        f"{desc} found in metadata JSON: '{sample[:60]}'."
                    )
        except (json.JSONDecodeError, AttributeError):
            pass

    if hits:
        report.failed("placeholder tasks", "\n".join(hits))
    else:
        report.passed("placeholder tasks",
                      "No leftover placeholder tasks or TODOs found.")


_EMOJI_RE = re.compile(r"[\U00010000-\U0010ffff]", flags=re.UNICODE)


def check_writing_style(parser, report):
    """Soft writing-style check — WARN only, never FAIL.

    The agent prompts teach principles (clarity, active voice, conversational
    tone). This lint check catches surface-level patterns the agent missed.
    It never hard-fails because many flagged words have legitimate technical
    uses (e.g., "transform" in Fourier transform). The goal is to nudge, not
    to gatekeep.

    Checks:
      - Semicolons in visible prose (suggest splitting).
      - Emojis in visible prose (educational notes should not have emojis).
      - Hashtags (#word) in visible prose.
      - Raw asterisks (*) in visible prose (likely leaked markdown).
      - AI-chatbot clichés and corporate jargon (from _plain_language.py).
    """
    prose = parser.prose
    if not prose.strip():
        report.warned("writing style", "No visible prose extracted; cannot assess style.")
        return

    warnings = []

    # 1. Semicolons — suggest shorter sentences
    semicolons = prose.count(";")
    if semicolons > 0:
        warnings.append(
            f"Found {semicolons} semicolon(s) in prose. "
            "Consider splitting into shorter sentences."
        )

    # 2. Emojis — should not appear in textbook notes
    emoji_hits = _EMOJI_RE.findall(prose)
    if emoji_hits:
        warnings.append(
            f"Found {len(emoji_hits)} emoji(s) in prose: " + "".join(emoji_hits[:10]) + ". "
            "Educational notes should not contain emojis."
        )

    # 3. Hashtags — social media artifact
    hashtag_hits = re.findall(r"#\w+", prose)
    if hashtag_hits:
        warnings.append(
            f"Found {len(hashtag_hits)} hashtag(s) in prose: " + ", ".join(hashtag_hits[:5]) + ". "
            "Hashtags are not appropriate in educational notes."
        )

    # 4. Raw asterisks (likely leaked markdown bold/italic markers)
    clean_prose = re.sub(r"\\\(.*?\\\)|\\\[.*?\\\]|\$\$.*?\$\$", " ", prose, flags=re.DOTALL)
    asterisk_count = clean_prose.count("*")
    if asterisk_count > 0:
        warnings.append(
            f"Found {asterisk_count} raw asterisk(s) ('*') in prose. "
            "These may be leaked markdown markers."
        )

    # 5. AI-chatbot clichés and corporate jargon (soft signal only)
    cliche_hits = PL.find_cliches_and_jargon(clean_prose)
    if cliche_hits:
        total = sum(c for _, _, c in cliche_hits)
        detail = "; ".join(f"'{w}'→'{s}' ({c}x)" for w, s, c in cliche_hits[:6])
        warnings.append(
            f"{total} AI-cliché / jargon hit(s): {detail}. "
            "Review — if the word is technically accurate in context, keep it."
        )

    # Always WARN, never FAIL — the agent's judgment is the primary defense
    if warnings:
        report.warned("writing style", "\n".join(warnings))
    else:
        report.passed("writing style",
                      "No obvious AI-cliché or formatting issues in prose.")


def check_html_formatting(raw, report):
    """Detect if the HTML code is minified or written on a single line (or very few lines).
    Well-formatted HTML must have line breaks and proper indentation.
    """
    # Extract the textbook content and exam revision content
    main_match = re.search(r'<main[^>]*>(.*?)</main>', raw, re.DOTALL | re.IGNORECASE)
    exam_match = re.search(r'<section\s+class\s*=\s*["\']exam-revision-section["\'][^>]*>(.*?)</section>', raw, re.DOTALL | re.IGNORECASE)
    
    parts_to_check = []
    if main_match:
        parts_to_check.append(("main textbook content (<main>)", main_match.group(1)))
    if exam_match:
        parts_to_check.append(("exam revision notes (<section class=\"exam-revision-section\">)", exam_match.group(1)))
        
    failures = []
    
    for name, content in parts_to_check:
        stripped_content = content.strip()
        if not stripped_content:
            continue
            
        lines = stripped_content.splitlines()
        num_lines = len(lines)
        total_len = len(stripped_content)
        
        # 1. Check if a non-empty section is written in just 1 or 2 lines
        if num_lines <= 2 and total_len > 400:
            failures.append(
                f"The {name} section is written in only {num_lines} line(s) despite being {total_len} "
                "characters long. HTML must be pretty-printed with line breaks and proper indentation, "
                "never minified or compressed into a single line."
            )
            continue
            
        # 2. Check if individual lines are excessively long and contain multiple block or formatting tags.
        # This catches when a single topic/section is compressed onto a single line.
        block_tag_re = re.compile(r'</?(?:div|p|li|ul|ol|h[1-6]|section|blockquote)\b(?:\s+[^>]*)?>', re.IGNORECASE)
        for i, line in enumerate(lines):
            # If a line is longer than 1000 characters and contains tag boundaries
            if len(line) > 1000 and ('<' in line and '>' in line):
                # Count block HTML tags on this line
                block_tags = block_tag_re.findall(line)
                if len(block_tags) > 2:
                    failures.append(
                        f"Line {i+1} in {name} is extremely long ({len(line)} characters) and contains "
                        f"{len(block_tags)} block-level HTML tags. Block-level HTML elements must start on new lines for readability."
                    )
                    break
                    
    if failures:
        report.failed("HTML formatting consistency", "\n".join(failures))
    else:
        report.passed("HTML formatting consistency", "HTML is pretty-printed with appropriate line breaks and nesting.")


def check_html_structure(parser, report):
    """Verify that HTML tags are properly balanced and callout boxes are not nested."""
    if parser.html_errors:
        report.failed("HTML tag balance and callout structure", "\n".join(parser.html_errors))
    else:
        report.passed("HTML tag balance and callout structure", "No unclosed tags, nested callouts, or tag imbalances found.")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def lint_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    parser = DocParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception as exc:
        print(f"[WARN] HTML parser raised {exc!r}; continuing with partial data.")

    report = Report()
    check_html_structure(parser, report)
    check_template_hygiene(raw, report)

    check_viewport(parser, report)
    check_metadata(raw, parser, report)
    check_seo(parser, report)
    check_callout_boxes(parser, report)
    check_style_separation(parser, report)
    check_math(raw, report)
    check_pii_and_secrets(raw, report)
    check_readability(parser, report)
    check_handwaving(parser, report)
    check_fancy_words(parser, report)
    check_flesch(parser, report)
    check_long_tokens(parser, report)
    check_exam_revision(parser, report)
    check_content_structure(parser, report)
    check_intermediate_metadata(raw, report)
    check_verify_markers(raw, report)
    check_pipeline_jargon(raw, parser, report)
    check_placeholder_tasks(raw, parser, report)
    check_writing_style(parser, report)
    check_html_formatting(raw, report)
    return report


def main(argv):
    if len(argv) != 2:
        print("Usage: python3 scripts/lint.py <lecture_html_file>", file=sys.stderr)
        return 2

    path = argv[1]
    if not os.path.isfile(path):
        print(f"Error: no such file: {path}", file=sys.stderr)
        return 2

    print("=" * 68)
    print(f"Linting: {path}")
    print("=" * 68)

    report = lint_file(path)
    report.print_all()

    print("-" * 68)
    c = report.counts
    print(f"Summary: {c['PASS']} PASS, {c['WARN']} WARN, {c['FAIL']} FAIL")
    if report.has_fail:
        print("Result: FAIL (one or more checks failed). Fix all FAIL items and re-run.")
        return 1
    if c["WARN"]:
        print("Result: PASS WITH WARNINGS. Review warnings; no blocking issues.")
        return 0
    print("Result: PASS. Ready to ship.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
