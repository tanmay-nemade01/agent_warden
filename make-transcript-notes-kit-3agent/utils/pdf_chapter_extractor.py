"""
Extract textbook PDFs chapter-by-chapter into companion_docs/<Subject>/.

Handles:
  - Text PDFs (PyMuPDF) with math post-processing from pdf_text_extractor
  - Scanned PDFs (EasyOCR) for image-only pages

Naming convention (ACI example):
  T1_Ch01_Introduction.txt
  R2_Ch03_GrowthOfFunctions.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import fitz

# Reuse math-aware page extraction when available.
_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

try:
    from pdf_text_extractor import extract_page as extract_text_page
except ImportError:
    def extract_text_page(page) -> str:
        return page.get_text("text").strip()


# ── Chapter metadata ──────────────────────────────────────────────────────────

T1_CHAPTERS = [
    (1, "Algorithm Analysis", 16),
    (2, "Basic Data Structures", 68),
    (3, "Search Trees and Skip Lists", 156),
    (4, "Sorting Sets and Selection", 230),
    (5, "Fundamental Techniques", 270),
    (6, "Graphs", 300),
    (7, "Weighted Graphs", 352),
    (8, "Network Flow and Matching", 394),
    (9, "Text Processing", 430),
    (10, "Number Theory and Cryptography", 464),
    (11, "Network Algorithms", 524),
    (12, "Computational Geometry", 560),
    (13, "NP-Completeness", 604),
    (14, "Algorithmic Frameworks", 656),
]

R2_CHAPTERS = [
    (1, "The Role of Algorithms in Computing", 26),
    (2, "Getting Started", 37),
    (3, "Growth of Functions", 64),
    (4, "Divide-and-Conquer", 86),
    (5, "Probabilistic Analysis and Randomized Algorithms", 135),
    (6, "Heapsort", 172),
    (7, "Quicksort", 191),
    (8, "Sorting in Linear Time", 212),
    (9, "Medians and Order Statistics", 234),
    (10, "Elementary Data Structures", 253),
    (11, "Hash Tables", 274),
    (12, "Binary Search Trees", 307),
    (13, "Red-Black Trees", 329),
    (14, "Augmenting Data Structures", 360),
    (15, "Dynamic Programming", 380),
    (16, "Greedy Algorithms", 435),
    (17, "Amortized Analysis", 472),
    (18, "B-Trees", 505),
    (19, "Fibonacci Heaps", 526),
    (20, "van Emde Boas Trees", 552),
    (21, "Data Structures for Disjoint Sets", 582),
    (22, "Elementary Graph Algorithms", 610),
    (23, "Minimum Spanning Trees", 645),
    (24, "Single-Source Shortest Paths", 664),
    (25, "All-Pairs Shortest Paths", 705),
    (26, "Maximum Flow", 729),
    (27, "Multithreaded Algorithms", 793),
    (28, "Matrix Operations", 834),
    (29, "Linear Programming", 864),
    (30, "Polynomials and the FFT", 919),
    (31, "Number-Theoretic Algorithms", 947),
    (32, "String Matching", 1006),
    (33, "Computational Geometry", 1035),
    (34, "NP-Completeness", 1069),
    (35, "Approximation Algorithms", 1127),
]

# Sahni — book page numbers from TOC; PDF page = book page + 22 (Ch1 at PDF 23).
R1_BOOK_OFFSET = 22
R1_CHAPTERS = [
    (1, "C++ Review", 1),
    (2, "Performance Analysis", 55),
    (3, "Asymptotic Notation", 95),
    (4, "Performance Measurement", 114),
    (5, "Linear Lists Array Representation", 139),
    (6, "Linear Lists Linked Representation", 170),
    (7, "Arrays and Matrices", 222),
    (8, "Stacks", 269),
    (9, "Queues", 317),
    (10, "Skip Lists and Hashing", 362),
    (11, "Binary and Other Trees", 418),
    (12, "Priority Queues", 464),
    (13, "Tournament Trees", 505),
    (14, "Binary Search Trees", 529),
    (15, "Balanced Search Trees", 563),
    (16, "Graphs", 615),
    (17, "The Greedy Method", 660),
    (18, "Divide and Conquer", 704),
    (19, "Dynamic Programming", 757),
    (20, "Backtracking", 793),
    (21, "Branch and Bound", 829),
]


def slugify(title: str, max_words: int = 6) -> str:
    """Convert chapter title to ACI-style filename segment."""
    title = re.sub(r"[^\w\s-]", "", title)
    words = title.split()
  # common shortenings
    replacements = {
        "and": "", "the": "", "of": "", "in": "", "for": "", "a": "", "an": "",
        "to": "", "with": "", "on": "", "from": "", "using": "",
    }
    cleaned = []
    for w in words:
        lw = w.lower()
        if lw in replacements and len(words) > 3:
            continue
        cleaned.append(w)
    words = cleaned[:max_words] or words[:max_words]
    out = "".join(w[:1].upper() + w[1:] for w in words if w)
    out = out.replace("-", "")
    return out or "Chapter"


def chapter_filename(prefix: str, num: int, title: str) -> str:
    return f"{prefix}_Ch{num:02d}_{slugify(title)}.txt"


def extract_text_pdf(doc: fitz.Document, start: int, end: int) -> str:
    """Extract pages [start, end) as plain text (1-based PDF page numbers)."""
    parts: list[str] = []
    for p in range(start - 1, min(end - 1, len(doc))):
        text = extract_text_page(doc[p])
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _ocr_page(page, reader, matrix) -> str:
    import numpy as np

    pix = page.get_pixmap(matrix=matrix)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    lines = reader.readtext(img, detail=0, paragraph=True)
    return "\n".join(lines).strip()


def extract_ocr_pdf(
    doc: fitz.Document,
    start: int,
    end: int,
    reader=None,
    dpi: float = 1.75,
) -> str:
    """OCR pages [start, end) for scanned PDFs."""
    import easyocr

    own_reader = reader is None
    if own_reader:
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    parts: list[str] = []
    matrix = fitz.Matrix(dpi, dpi)
    for p in range(start - 1, min(end - 1, len(doc))):
        text = _ocr_page(doc[p], reader, matrix)
        if text:
            parts.append(text)
        if (p - start + 2) % 10 == 0:
            print(f"    OCR page {p + 1}/{end - 1}", flush=True)
    return "\n\n".join(parts).strip()


def extract_book(
    pdf_path: Path,
    chapters: list[tuple[int, str, int]],
    prefix: str,
    out_dir: Path,
    *,
    ocr: bool = False,
    end_page: int | None = None,
) -> list[Path]:
    doc = fitz.open(str(pdf_path))
    total = end_page or len(doc)
    written: list[Path] = []

    ocr_reader = None
    if ocr:
        import easyocr
        print("  Initializing OCR engine...", flush=True)
        ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)

    for i, (num, title, start) in enumerate(chapters):
        next_start = chapters[i + 1][2] if i + 1 < len(chapters) else total + 1
        end = min(next_start, total + 1)
        if start >= end:
            print(f"  SKIP {prefix} Ch{num:02d} — bad range {start}-{end}")
            continue

        print(f"  {prefix} Ch{num:02d} ({title}): PDF pages {start}-{end - 1}", flush=True)
        if ocr:
            text = extract_ocr_pdf(doc, start, end, reader=ocr_reader)
        else:
            text = extract_text_pdf(doc, start, end)

        fname = chapter_filename(prefix, num, title)
        out_path = out_dir / fname
        out_path.write_text(text, encoding="utf-8")
        written.append(out_path)
        print(f"    -> {fname} ({len(text):,} chars)", flush=True)

    doc.close()
    return written


def r1_pdf_pages(chapters: list[tuple[int, str, int]], total_pages: int) -> list[tuple[int, str, int]]:
    return [(n, t, bp + R1_BOOK_OFFSET) for n, t, bp in chapters if bp + R1_BOOK_OFFSET <= total_pages]


def main():
    ap = argparse.ArgumentParser(description="Extract PDF chapters to companion_docs.")
    ap.add_argument("--workspace", default=".", help="Workspace root (default: cwd)")
    ap.add_argument("--subject", default="ACI", help="Subject folder under companion_docs")
    ap.add_argument("--books", nargs="+", choices=["T1", "R1", "R2"], default=["T1", "R1", "R2"])
    ap.add_argument("--ocr-dpi", type=float, default=1.75, help="OCR scale for scanned PDFs")
    args = ap.parse_args()

    root = Path(args.workspace).resolve()
    out_dir = root / "companion_docs" / args.subject
    out_dir.mkdir(parents=True, exist_ok=True)

    configs = {
        "T1": (root / "T1.pdf", T1_CHAPTERS, False),
        "R1": (root / "R1.pdf", R1_CHAPTERS, True),
        "R2": (root / "R2.pdf", R2_CHAPTERS, False),
    }

    for book in args.books:
        pdf_path, chapters, ocr = configs[book]
        if not pdf_path.is_file():
            print(f"Missing {pdf_path}")
            continue
        print(f"\n=== {book}: {pdf_path.name} ===")
        doc = fitz.open(str(pdf_path))
        total = len(doc)
        doc.close()

        ch = chapters
        if book == "R1":
            ch = r1_pdf_pages(chapters, total)

        extract_book(pdf_path, ch, book, out_dir, ocr=ocr, end_page=total)

    print(f"\nDone. Output -> {out_dir}")


if __name__ == "__main__":
    main()
