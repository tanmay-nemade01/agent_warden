#!/usr/bin/env python3
r"""Quality gate for dense markdown drafts (Agent 1's output).

Checks the dense draft notes_dense.md against the rules before Phase 2.
"""

import argparse
import io
import os
import re
import sys

# Force UTF-8 on Windows
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "encoding") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "encoding") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plain_language as PL

# Patterns
H2_PATTERN = re.compile(r"^##\s+(.+)$", re.MULTILINE)
H3_PATTERN = re.compile(r"^###\s+(.+)$", re.MULTILINE)
DOTTED_H2 = re.compile(r"^(\d+)\.(\d+)\s+(.+)$")
DOTTED_H3 = re.compile(r"^(\d+)\.(\d+)\.(\d+)\s+(.+)$")

PII_PATTERNS = [
    ("transcript reference", re.compile(r"\b(transcript|lecture recording|audio recording|this recording)\b", re.I)),
    ("professor/instructor mention", re.compile(r"\b(professor|instructor|lecturer)\s+[A-Z][a-z]+\b")),
    ("university mention candidate", re.compile(r"\b(University|Institute|College)\s+of\s+[A-Z]")),
]

class Report:
    def __init__(self):
        self.rows = []
        self.counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

    def add(self, level, name, message=""):
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


def detect_lecture_num(path, text):
    # Try filename first
    filename = os.path.basename(path)
    match = re.search(r"[Ll]ecture[_-]?(\d+)", filename)
    if match:
        return int(match.group(1))
    
    # Try first H2 that starts with digits
    h2_matches = H2_PATTERN.findall(text)
    for title in h2_matches:
        title = title.strip()
        m = DOTTED_H2.match(title)
        if m:
            return int(m.group(1))
    return None


def check_numbering(text, lecture_num, report):
    h2_matches = []
    # Find h2s and their indices
    for match in H2_PATTERN.finditer(text):
        h2_matches.append((match.group(1).strip(), match.start(), match.end()))

    if not h2_matches:
        report.failed("numbering", "No H2 (##) headings found in document.")
        return

    expected_t = 1
    for title, start, end in h2_matches:
        if title in ("Exam Guidance Summary", "Key Industry Applications"):
            continue
        
        m = DOTTED_H2.match(title)
        if not m:
            report.failed("numbering", f"H2 heading '{title}' does not match dotted L.T pattern (e.g. '## 5.1 Concept').")
            continue
        
        l_val = int(m.group(1))
        t_val = int(m.group(2))
        
        if lecture_num is not None and l_val != lecture_num:
            report.failed("numbering", f"H2 heading '{title}' has lecture number {l_val}, expected {lecture_num}.")
        
        if t_val != expected_t:
            report.failed("numbering", f"H2 heading '{title}' has topic number {t_val}, expected {expected_t} (non-sequential).")
        
        expected_t += 1

    # Extract sections to check H3 numbering
    for i, (title, start, end) in enumerate(h2_matches):
        if title in ("Exam Guidance Summary", "Key Industry Applications"):
            continue
        
        m = DOTTED_H2.match(title)
        if not m:
            continue
        
        l_val = int(m.group(1))
        t_val = int(m.group(2))
        
        section_end = h2_matches[i+1][1] if i + 1 < len(h2_matches) else len(text)
        section_text = text[start:section_end]
        
        h3_matches = H3_PATTERN.findall(section_text)
        if not h3_matches:
            report.failed("numbering", f"Section '{title}' has no H3 subsections. Every section must have at least one subsection.")
            continue
            
        expected_s = 1
        for h3_title in h3_matches:
            h3_title = h3_title.strip()
            m3 = DOTTED_H3.match(h3_title)
            if not m3:
                report.failed("numbering", f"H3 heading '{h3_title}' does not match dotted L.T.S pattern.")
                continue
            
            l3 = int(m3.group(1))
            t3 = int(m3.group(2))
            s3 = int(m3.group(3))
            
            if l3 != l_val or t3 != t_val:
                report.failed("numbering", f"H3 heading '{h3_title}' has L.T values {l3}.{t3}, expected {l_val}.{t_val} under section '{title}'.")
            
            if s3 != expected_s:
                report.failed("numbering", f"H3 heading '{h3_title}' has sub-topic number {s3}, expected {expected_s} (non-sequential).")
                
            expected_s += 1

    if report.counts["FAIL"] == 0:
        report.passed("numbering", "Strict dotted numbering sequence and subsection structure is correct.")


def check_math_delimiters(text, report):
    # Flag double backslashes in body
    double_inline = re.findall(r"\\\\\(", text)
    double_block = re.findall(r"\\\\\[", text)
    
    if double_inline or double_block:
        report.failed("math delimiters", f"Found double-backslash math delimiters: {len(double_inline)} inline \\\\(, {len(double_block)} block \\\\[. MathJax will not render these.")
    
    # Flag single or double dollars
    dollars = len(re.findall(r"(?<!\\)\$\$?", text))
    if dollars > 0:
        report.failed("math delimiters", f"Found {dollars} raw $ or $$ delimiters. Use only single-backslash \\( ... \\) and \\[ ... \\].")
        
    if report.counts["FAIL"] == 0:
        report.passed("math delimiters", "Single-backslash math delimiters correctly formatted, no raw dollars/double backslashes found.")





def check_pii_and_anonymization(text, report):
    pii_hits = []
    for label, pat in PII_PATTERNS:
        for m in pat.finditer(text):
            pii_hits.append(f"{label}: \"{m.group(0)}\"")
            
    if pii_hits:
        report.failed("PII / anonymization", f"PII or source-file references found (notes must stand alone without erasing anonymous Q&A):\n  " + "\n  ".join(pii_hits))
    else:
        report.passed("PII / anonymization", "No PII or transcript references detected.")


def check_verify_markers(text, report, strict=False):
    # Verify markers should have a description/reason
    markers = re.findall(r"\*\[verify\b(.*?)\]\*", text)
    if strict and len(markers) > 0:
        report.failed("verify markers", f"Found {len(markers)} unresolved verify marker(s). Resolve them or stop for human review before assembling the enriched draft.")
        return
        
    bad_markers = 0
    for m in markers:
        m = m.strip()
        if not m.startswith(":") or len(m) < 5:
            bad_markers += 1
            
    if bad_markers > 0:
        report.warned("verify markers", f"Found {bad_markers} verify marker(s) without a reason tag (e.g. use '*[verify: <reason>]*' instead of bare '*[verify]*').")
    elif len(markers) > 0:
        report.passed("verify markers", f"All {len(markers)} verify markers have description/reason tags.")
    else:
        report.passed("verify markers", "No verify markers found.")


def extract_readable_prose(text):
    """Remove math/code/table material before heuristic prose checks.

    Readability metrics are unreliable on LaTeX, symbol definitions, derivations,
    and markdown tables. It is safer to omit those regions than to encourage an
    agent to break correct mathematics while chasing a word-count score.
    """
    clean = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    clean = re.sub(
        r"\\begin\{([A-Za-z*]+)\}.*?\\end\{\1\}",
        " ",
        clean,
        flags=re.DOTALL,
    )
    clean = re.sub(r"\\\[.*?\\\]|\\\(.*?\\\)|\$\$.*?\$\$", " ", clean, flags=re.DOTALL)

    prose_lines = []
    in_symbol_registry = False
    for line in clean.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+.*symbol registry", stripped, re.I):
            in_symbol_registry = True
            continue
        if in_symbol_registry and stripped.startswith("#"):
            in_symbol_registry = False
        if in_symbol_registry:
            continue
        if (
            not stripped
            or stripped.startswith((":::", "#"))
            or stripped.count("|") >= 2
            or re.search(r"\\(?:frac|sum|prod|int|mathbb|mathbf|begin|end)\b", stripped)
            or "\\[" in stripped
            or "\\]" in stripped
        ):
            continue
        prose_lines.append(line)
    return "\n".join(prose_lines)


def check_writing_style(text, report):
    prose = extract_readable_prose(text)
    
    # 1. Banned hand-waving
    hand_waves = PL.find_handwaving(prose)
    if hand_waves:
        detail = "; ".join(f"'{p}' ({c}x)" for p, c in hand_waves[:8])
        report.failed("hand-waving", f"Banned hand-waving words/phrases found: {detail}. Replace each with the algebra/logic it hides.")
    else:
        report.passed("hand-waving", "No banned hand-waving detected.")

    # 2. Reading Ease
    score, n_words, n_sent = PL.flesch_reading_ease(prose)
    if n_words > 50:
        if score < 60:
            report.warned("Flesch reading ease", f"Score is {score}/100 (<60 is below plain English level). Aim to improve the readability of the surrounding prose as much as possible, but do not force changes on mathematical statements (leave math formulas and symbols as is).")
        else:
            report.passed("Flesch reading ease", f"Score is {score}/100.")

    # 3. Sentence lengths
    sentences = []
    _SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
    _WORD = re.compile(r"\S+")
    for line in prose.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            sentences.extend(s for s in _SENTENCE_SPLIT.split(line) if s.strip())
            
    long_sentences = 0
    for s in sentences:
        words_count = len(_WORD.findall(s))
        if words_count > PL.WORD_CEILING:
            long_sentences += 1
            
    total = len(sentences)
    ratio = long_sentences / total if total else 0
    if long_sentences > 0:
        msg = f"{long_sentences} sentences over {PL.WORD_CEILING} words ({ratio*100:.0f}% of {total} sentences)."
        report.warned(
            "sentence length",
            f"{msg} Advisory only after math/code/tables were excluded. "
            "Rewrite only prose that is genuinely hard to follow; never edit a formula to silence this warning.",
        )
    else:
        report.passed("sentence length", "All sentences are short and clear.")


def check_fancy_words(text, report):
    prose = extract_readable_prose(text)
    
    hits = PL.find_fancy(prose)
    if not hits:
        report.passed("fancy words", "No fancy-word offenders detected.")
        return
    total = sum(c for _, _, c in hits)
    detail = "; ".join(f"'{w}'→'{s}' ({c}x)" for w, s, c in hits[:6])
    report.warned(
        "fancy words",
        f"{total} possible plain-language substitution(s): {detail}. "
        "Advisory only; keep precise technical vocabulary when it is the correct term.",
    )


def lint_dense_file(path, lecture_num=None, phase="dense"):
    if not os.path.exists(path):
        print(f"Error: file not found at {path}", file=sys.stderr)
        sys.exit(2)
        
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    # Detect lecture num if none provided
    if lecture_num is None:
        lecture_num = detect_lecture_num(path, text)
        if lecture_num is not None:
            print(f"Detected Lecture Number: {lecture_num}")
        else:
            print("Warning: Could not detect lecture number. Skipping lecture-specific H2 check.")

    report = Report()
    
    check_numbering(text, lecture_num, report)
    check_math_delimiters(text, report)
    check_pii_and_anonymization(text, report)
    
    strict_verify = (phase == "enriched")
    check_verify_markers(text, report, strict=strict_verify)
    
    check_writing_style(text, report)
    check_fancy_words(text, report)
    
    return report


def main():
    parser = argparse.ArgumentParser(description="Lint dense/enriched markdown drafts for quality.")
    parser.add_argument("markdown", help="Path to the markdown file.")
    parser.add_argument("--lecture-num", type=int, default=None, help="Explicit lecture number to enforce.")
    parser.add_argument("--phase", choices=["dense", "enriched"], default="dense", help="Pipeline phase (dense or enriched).")
    args = parser.parse_args()
    
    print("=" * 68)
    print(f"Linting {args.phase.capitalize()} Draft: {args.markdown}")
    print("=" * 68)
    
    report = lint_dense_file(args.markdown, args.lecture_num, args.phase)
    report.print_all()
    
    print("-" * 68)
    c = report.counts
    print(f"Summary: {c['PASS']} PASS, {c['WARN']} WARN, {c['FAIL']} FAIL")
    if report.has_fail:
        print("Result: FAIL (one or more checks failed). Fix all FAIL items and re-run.")
        sys.exit(1)
    if c["WARN"]:
        print("Result: PASS WITH WARNINGS. Review warnings; no blocking issues.")
        sys.exit(0)
    print(f"Result: PASS. Ready for the next phase.")
    sys.exit(0)


if __name__ == "__main__":
    main()
