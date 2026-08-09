"""
pdf_math_extractor.py — Reusable math-aware PDF text extractor.

Extracts text from all PDFs in a given folder, applying post-processing
that correctly reconstructs mathematical notation from LaTeX-generated PDFs.

Handles:
  • Matrices — PUA bracket chars ⎡⎢⎣/⎤⎥⎦ and ⎡⎣/⎤⎦ → [ ... ] with proper grid layout
  • Determinants — form-feed bars (≥3 U+FFFD) → | ... |
  • Fractions — short bars (1–2 U+FFFD) or ─── lines → (num) / (den)
  • Square roots — √ split across lines → √arg
  • Greek letters — λ, σ, ε, etc. preserved
  • Unicode math — minus sign, ±, ⇒, ×, ∈, ≤, etc.
  • Trailing text on bracket/bar lines properly separated

Usage:
  python pdf_math_extractor.py <input_folder> [--output-folder <path>]

If --output-folder is omitted, output is written to <input_folder>/extracted_text/

Requires: PyMuPDF  (pip install pymupdf)
"""

import fitz
import math
import json
import re
import argparse
from pathlib import Path

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Unicode bracket part characters (standard)
LTOP, LMID, LBOT = "\u23a1", "\u23a2", "\u23a3"
RTOP, RMID, RBOT = "\u23a4", "\u23a5", "\u23a6"
L_SET = {LTOP, LMID, LBOT}
R_SET = {RTOP, RMID, RBOT}
ALL_BRACKETS = L_SET | R_SET

FFFD = "\ufffd"

# Private-Use-Area bracket families found in LaTeX-generated PDFs.
# Family 1 uses 3 segments per side (top, mid, bottom).
# Family 2 uses 2 segments per side (top, bottom only).
PUA_BRACKET_MAP = {
    # Family 1 — 3-segment (e.g. Lecture_5, Lecture_1)
    "\uf8eb": LTOP,  # left top
    "\uf8ec": LMID,  # left middle
    "\uf8ed": LBOT,  # left bottom
    "\uf8f6": RTOP,  # right top
    "\uf8f7": RMID,  # right middle
    "\uf8f8": RBOT,  # right bottom
    # Family 2 — 2-segment (e.g. Cholesky_Decomp)
    "\uf8ee": LTOP,  # left top
    "\uf8f0": LBOT,  # left bottom
    "\uf8f9": RTOP,  # right top
    "\uf8fb": RBOT,  # right bottom
}

UNICODE_REPLACEMENTS = {
    "\u2212": "-",    # MINUS SIGN → hyphen-minus
    "\u00d7": "\u00d7",  # MULTIPLICATION SIGN (keep)
    "\u00b1": "\u00b1",  # PLUS-MINUS (keep)
    "\u21d2": "\u21d2",  # RIGHTWARDS DOUBLE ARROW (keep)
    "\u221a": "\u221a",  # SQUARE ROOT (keep)
    "\u221e": "\u221e",  # INFINITY (keep)
    "\u2264": "\u2264",  # ≤ (keep)
    "\u2265": "\u2265",  # ≥ (keep)
    "\u2260": "\u2260",  # ≠ (keep)
    "\u2248": "\u2248",  # ≈ (keep)
    "\u2261": "\u2261",  # ≡ (keep)
    "\u2208": "\u2208",  # ∈ (keep)
    "\u2282": "\u2282",  # ⊂ (keep)
    "\u2200": "\u2200",  # ∀ (keep)
    "\u2203": "\u2203",  # ∃ (keep)
    "\u2227": "\u2227",  # ∧ (keep)
    "\u2228": "\u2228",  # ∨ (keep)
    "\u00ac": "\u00ac",  # ¬ (keep)
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STEP 1 — UNICODE CLEANUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def clean_text(text: str) -> str:
    """Replace PUA bracket chars with standard Unicode bracket parts, normalize minus sign."""
    for pua, std in PUA_BRACKET_MAP.items():
        text = text.replace(pua, std)
    text = text.replace("\u2212", "-")
    return text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STEP 2 — MATRIX RECONSTRUCTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _split_bracket(line: str):
    """Return (bracket_char_or_None, trailing_text) if line starts with a bracket part."""
    s = line.strip()
    if not s:
        return None, s
    for b in ALL_BRACKETS:
        if s.startswith(b):
            return b, s[len(b):].strip()
    return None, s


def _try_matrix(lines: list[str], start: int):
    """
    Attempt to reconstruct a matrix starting at lines[start].
    Returns (matrix_string, trailing_text, end_index) or None.
    """
    j = start

    # ── collect left brackets ──────────────────────────────────────
    lefts: list[str] = []
    while j < len(lines):
        s = lines[j].strip()
        if s == LTOP:
            lefts.append(LTOP)
            j += 1
        elif s == LMID:
            lefts.append(LMID)
            j += 1
        elif s == LBOT:
            lefts.append(LBOT)
            j += 1
            break
        else:
            return None  # not a matrix

    if len(lefts) < 2:
        return None

    has_mid = LMID in lefts
    num_rows = len(lefts) - 1 if has_mid else 0  # determined later for 2-segment

    # ── collect entry lines (between left and right brackets) ──────
    entries: list[str] = []
    while j < len(lines):
        brk, _ = _split_bracket(lines[j])
        if brk in R_SET:
            break
        s = lines[j].strip()
        if s:
            entries.append(s)
        j += 1

    if not entries or (has_mid and num_rows <= 0):
        return None

    # ── consume right brackets + trailing text ─────────────────────
    trailing = ""
    while j < len(lines):
        brk, rest = _split_bracket(lines[j])
        if brk in R_SET:
            if rest:
                trailing = rest
            j += 1
            if brk == RBOT:
                break
        else:
            break

    # ── determine grid dimensions ──────────────────────────────────
    ne = len(entries)
    if num_rows == 0:
        # 2-segment brackets: infer from entry count
        root = int(math.isqrt(ne))
        if root * root == ne:
            num_rows = num_cols = root
        else:
            num_rows, num_cols = ne, 1
            for r in range(2, ne):
                if ne % r == 0:
                    num_rows, num_cols = r, ne // r
                    break
    else:
        num_cols = ne // num_rows if num_rows else 0

    if num_cols <= 0 or ne != num_rows * num_cols:
        return ("[ " + "  ".join(entries) + " ]", trailing, j)

    # ── format aligned matrix ──────────────────────────────────────
    widths = [0] * num_cols
    for r in range(num_rows):
        for c in range(num_cols):
            widths[c] = max(widths[c], len(entries[r * num_cols + c]))

    row_strs = []
    for r in range(num_rows):
        cells = [entries[r * num_cols + c].rjust(widths[c]) for c in range(num_cols)]
        row_strs.append(" ".join(cells))

    mat = "[ " + row_strs[0]
    for row in row_strs[1:]:
        mat += "\n  " + row
    mat += " ]"
    return (mat, trailing, j)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STEP 3 — DETERMINANT RECONSTRUCTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _split_fffd(line: str):
    """Return (count_of_leading_FFFD, trailing_text) for a line."""
    s = line.strip()
    if not s or s[0] != FFFD:
        return 0, s
    count = 0
    for c in s:
        if c == FFFD:
            count += 1
        else:
            break
    return count, s[count:].strip()


def _try_determinant(lines: list[str], start: int):
    """
    Reconstruct a determinant delimited by ≥3 U+FFFD bar lines.
    Returns (det_string, "", end_index) or None.
    """
    entries: list[str] = []
    j = start + 1
    while j < len(lines):
        ns = lines[j].strip()
        if not ns:
            j += 1
            continue
        nc, rest = _split_fffd(lines[j])
        if nc >= 3:
            if not entries:
                return None
            j += 1
            trailing = rest
            # grab "= 0" etc. from next non-empty line
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines) and lines[k].strip().startswith("="):
                trailing = (trailing + " " if trailing else "") + lines[k].strip()
                j = k + 1
            det = "| " + entries[0]
            for e in entries[1:]:
                det += "\n  " + e
            det += " |"
            if trailing:
                det += " " + trailing
            return (det, "", j)
        elif ns:
            entries.append(ns)
            j += 1
        else:
            j += 1
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STEP 4 — FRACTION BAR HANDLING  (1-2 FFFD chars or ─── lines)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _handle_fraction_bar(lines: list[str], i: int, result: list[str]):
    """
    Try to build a fraction from result[-1] (numerator), the bar at lines[i],
    and the next non-empty line (denominator).
    Returns (consumed: bool, next_index: int).
    """
    _, rest = _split_fffd(lines[i])
    if rest:
        # bar has trailing content — not a pure fraction bar
        return False, i + 1
    if not result or not result[-1].strip():
        return False, i + 1
    numerator = result.pop().strip()
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j < len(lines):
        denom = lines[j].strip()
        if denom:
            result.append(f"({numerator}) / ({denom})")
            return True, j + 1
    # couldn't form fraction — restore
    result.append(numerator)
    return False, i + 1


_HORIZ_BAR_RE = re.compile(r"^[\s\u2500\u2501\u2550_\u2014\u2013-]+$")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STEP 5 — SQUARE ROOT FIX
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_SQRT = "\u221a"
_SQRT_NUM_RE = re.compile(r"\u221a\s*\n\s*(\d+)")
_SQRT_ALPHA_RE = re.compile(r"\u221a\s*\n\s*([a-zA-Z\u03b1-\u03c9\u0391-\u03a9])")


def _fix_sqrt(text: str) -> str:
    text = _SQRT_NUM_RE.sub(_SQRT + r"\1", text)
    text = _SQRT_ALPHA_RE.sub(_SQRT + r"\1", text)
    return text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FULL PAGE PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_page(page) -> str:
    """Extract and post-process a single PDF page."""
    raw = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    text = clean_text(raw)

    # ── reconstruct math structures ────────────────────────────────
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # --- FFFD bars ---
        nc, rest = _split_fffd(lines[i])
        if nc >= 3:
            det = _try_determinant(lines, i)
            if det:
                result.append(det[0])
                i = det[2]
                continue
            result.append(rest if rest else "")
            i += 1
            continue
        if nc >= 1:
            frac = _handle_fraction_bar(lines, i, result)
            if frac[0]:
                i = frac[1]
                continue
            if rest:
                result.append(rest)
            i += 1
            continue

        # --- matrix brackets ---
        if stripped == LTOP:
            mat = _try_matrix(lines, i)
            if mat:
                result.append(mat[0])
                if mat[1]:
                    result.append(mat[1])
                i = mat[2]
                continue

        result.append(lines[i])
        i += 1

    text = "\n".join(result)

    # ── post-fixes ─────────────────────────────────────────────────
    text = _fix_sqrt(text)

    # fix horizontal fraction bars (─── or ===)
    out_lines = text.split("\n")
    final: list[str] = []
    li = 0
    while li < len(out_lines):
        s = out_lines[li].strip()
        if (_HORIZ_BAR_RE.match(s) and len(s) > 2
                and li > 0 and li + 1 < len(out_lines)):
            num = final.pop().strip() if final else ""
            den = out_lines[li + 1].strip()
            if num and den:
                final.append(f"({num}) / ({den})")
                li += 2
                continue
        final.append(out_lines[li])
        li += 1
    text = "\n".join(final)

    # collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PDF-LEVEL API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_pdf(pdf_path: Path, output_path: Path | None = None) -> str:
    """Extract all pages from one PDF. Optionally write to *output_path*."""
    doc = fitz.open(str(pdf_path))
    pages = []
    for idx in range(len(doc)):
        pages.append(f"===== PDF page {idx + 1} =====\n\n{extract_page(doc[idx])}")
    doc.close()
    full = "\n\n".join(pages)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(full, encoding="utf-8")
    return full


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FOLDER-LEVEL CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_folder(input_dir: Path, output_dir: Path) -> dict:
    """Extract every .pdf in *input_dir*, write .txt files to *output_dir*."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDF files found in {input_dir}")
        return {}

    print(f"Found {len(pdfs)} PDF(s) in {input_dir}\n")
    results = {}
    for pdf in pdfs:
        txt_path = output_dir / (pdf.stem + ".txt")
        try:
            text = extract_pdf(pdf, txt_path)
            info = {"chars": len(text), "output": str(txt_path)}
            print(f"  OK  {pdf.name:<45s}  {len(text):>8,} chars  ->  {txt_path.name}")
        except Exception as exc:
            info = {"error": str(exc)}
            print(f"  ERR {pdf.name:<45s}  {exc}")
        results[pdf.name] = info

    # summary
    summary_path = output_dir / "_extraction_summary.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSummary -> {summary_path}")

    ok = sum(1 for v in results.values() if "error" not in v)
    print(f"\nDone: {ok}/{len(pdfs)} succeeded, {len(pdfs) - ok} failed")
    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CLI ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    ap = argparse.ArgumentParser(
        description="Extract text from math-heavy PDFs with matrix/determinant/fraction reconstruction.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python pdf_math_extractor.py ./pdfs
  python pdf_math_extractor.py ./pdfs --output-folder ./output
  python pdf_math_extractor.py single.pdf            # single file also works
""",
    )
    ap.add_argument("input", help="PDF file or folder containing PDFs")
    ap.add_argument("--output-folder", "-o", help="Output folder (default: <input>/extracted_text)")
    args = ap.parse_args()

    input_path = Path(args.input).resolve()

    # single file mode
    if input_path.is_file() and input_path.suffix.lower() == ".pdf":
        out_dir = Path(args.output_folder).resolve() if args.output_folder else input_path.parent / "extracted_text"
        out_path = out_dir / (input_path.stem + ".txt")
        text = extract_pdf(input_path, out_path)
        print(f"Extracted {len(text):,} chars -> {out_path}")
        return

    # folder mode
    if not input_path.is_dir():
        ap.error(f"Input must be a PDF file or folder: {input_path}")

    out_dir = Path(args.output_folder).resolve() if args.output_folder else input_path / "extracted_text"
    extract_folder(input_path, out_dir)


if __name__ == "__main__":
    main()
