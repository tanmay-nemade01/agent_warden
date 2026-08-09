#!/usr/bin/env python3
r"""Section splitter and strict assembler for section-by-section processing.

Splits a dense or enriched markdown draft into per-section files so an agent can process
one section at a time, avoiding attention degradation on large documents.
After conversion, reassembles the per-section HTML fragments into a single body.

Usage:
    # Split the enriched markdown into per-section files
    python scripts/section_splitter.py split <enriched.md> --output-dir <dir>

    # Reassemble per-section HTML files into a single body
    python scripts/section_splitter.py assemble <section_dir> --output <body.html>

Split output:
    <output_dir>/
        _inventory.json    ← section map: [{num, title, file, subsections}]
        section_01.md      ← first ## section
        section_02.md      ← second ## section
        ...

The _inventory.json locks heading numbering before conversion starts.
The agent converts each section_XX.md → section_XX.html, then the
assemble command concatenates them in order.
"""

import argparse
import hashlib
import json
import os
import re
import sys

from convert_md_to_html import convert_section_files

# Force UTF-8 on Windows
if sys.platform.startswith("win"):
    import io
    if hasattr(sys.stdout, "encoding") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "encoding") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
H2_PATTERN = re.compile(r"^##\s+(.+)$", re.MULTILINE)
H3_PATTERN = re.compile(r"^###\s+(.+)$", re.MULTILINE)
DOTTED_H2 = re.compile(r"^(\d+\.\d+)\s+(.+)$")
DOTTED_H3 = re.compile(r"^(\d+\.\d+\.\d+)\s+(.+)$")
GENERATED_SECTION_FILE = re.compile(
    r"^section_(?:\d+|00_preamble)\.(?:md|html)$",
    re.IGNORECASE,
)
SUMMARY_FILE = re.compile(r"^section_\d+_summary\.json$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------

def split_markdown(
    md_path: str,
    output_dir: str,
    invalidate_summaries: bool = False,
) -> str:
    """Split a markdown file into per-h2-section files.

    Reads the markdown, finds all ``## `` headings, assigns section numbers,
    and writes each section to a separate file. Also writes ``_inventory.json``
    with the locked heading numbering.

    Content BEFORE the first ``## `` heading is treated as preamble and written
    to ``section_00_preamble.md``.

    Args:
        md_path: Path to the enriched markdown file.
        output_dir: Directory to write section files into.
        invalidate_summaries: delete prior section summaries before Agent 2
            starts, forcing every summary to be regenerated.

    Returns:
        Path to the inventory JSON file.
    """
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    os.makedirs(output_dir, exist_ok=True)
    # A fresh split invalidates every prior generated fragment. Otherwise a
    # stale section_03.html can survive and be silently assembled after the
    # markdown or heading tree changes.
    for filename in os.listdir(output_dir):
        if GENERATED_SECTION_FILE.match(filename) or (
            invalidate_summaries and SUMMARY_FILE.match(filename)
        ):
            os.remove(os.path.join(output_dir, filename))

    # Find all h2 headings with their positions
    h2_matches = list(H2_PATTERN.finditer(text))

    if not h2_matches:
        # No h2 headings — write entire file as a single section
        section_file = os.path.join(output_dir, "section_01.md")
        with open(section_file, "w", encoding="utf-8") as f:
            f.write(text)
        inventory = [{
            "num": 1,
            "title": "(Full Document)",
            "file": "section_01.md",
            "subsections": _extract_h3s(text)
        }]
        inv_path = os.path.join(output_dir, "_inventory.json")
        with open(inv_path, "w", encoding="utf-8") as f:
            json.dump(inventory, f, indent=2, ensure_ascii=False)
        return inv_path

    inventory = []

    # Preamble (content before first h2)
    first_h2_start = h2_matches[0].start()
    if first_h2_start > 0:
        preamble = text[:first_h2_start].strip()
        if preamble:
            preamble_file = os.path.join(output_dir, "section_00_preamble.md")
            with open(preamble_file, "w", encoding="utf-8") as f:
                f.write(preamble)
            inventory.append({
                "num": 0,
                "title": "(Preamble)",
                "file": "section_00_preamble.md",
                "subsections": []
            })

    # Extract each h2 section
    for i, match in enumerate(h2_matches):
        section_num = i + 1
        title = match.group(1).strip()
        heading_match = DOTTED_H2.match(title)
        start = match.start()
        end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(text)
        body = text[start:end].strip()

        # Extract h3 subsections within this section
        subsections = _extract_h3s(body)

        # Write section file
        section_file = f"section_{section_num:02d}.md"
        section_path = os.path.join(output_dir, section_file)
        with open(section_path, "w", encoding="utf-8") as f:
            f.write(body)

        inventory.append({
            "num": section_num,
            "title": title,
            "heading_id": heading_match.group(1) if heading_match else None,
            "file": section_file,
            "subsections": subsections
        })

    # Write inventory
    inv_path = os.path.join(output_dir, "_inventory.json")
    with open(inv_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2, ensure_ascii=False)

    print(f"Split into {len(inventory)} section(s) in {output_dir}")
    print(f"Inventory: {inv_path}")
    return inv_path


def _extract_h3s(text: str) -> list[dict]:
    """Extract h3 headings from a section body, assigning subsection numbers."""
    h3s = []
    for j, m in enumerate(H3_PATTERN.finditer(text)):
        title = m.group(1).strip()
        heading_match = DOTTED_H3.match(title)
        h3s.append({
            "sub_num": j + 1,
            "title": title,
            "heading_id": heading_match.group(1) if heading_match else None,
        })
    return h3s


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bind_summaries(section_dir: str) -> None:
    """Bind each section summary to the exact markdown and heading ID."""
    inv_path = os.path.join(section_dir, "_inventory.json")
    with open(inv_path, "r", encoding="utf-8") as handle:
        inventory = json.load(handle)

    expected = set()
    for entry in inventory:
        if entry["num"] == 0:
            continue
        section_path = os.path.join(section_dir, entry["file"])
        summary_file = entry["file"].replace(".md", "_summary.json")
        summary_path = os.path.join(section_dir, summary_file)
        expected.add(summary_file)
        if not os.path.exists(summary_path):
            raise FileNotFoundError(f"Missing section summary: {summary_file}")
        with open(summary_path, "r", encoding="utf-8") as handle:
            summary = json.load(handle)
        expected_id = entry.get("heading_id") or entry.get("title")
        if str(summary.get("id", "")).strip() != str(expected_id).strip():
            raise ValueError(
                f"{summary_file} id {summary.get('id')!r} does not match "
                f"section id {expected_id!r}"
            )
        summary["source_heading_id"] = entry.get("heading_id")
        summary["source_sha256"] = _sha256_file(section_path)
        with open(summary_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, ensure_ascii=False)

    extras = {
        filename
        for filename in os.listdir(section_dir)
        if SUMMARY_FILE.match(filename) and filename not in expected
    }
    if extras:
        raise ValueError(f"Orphan section summaries found: {sorted(extras)}")


def validate_summaries(section_dir: str) -> None:
    """Fail when a summary is missing, orphaned, or bound to stale markdown."""
    inv_path = os.path.join(section_dir, "_inventory.json")
    with open(inv_path, "r", encoding="utf-8") as handle:
        inventory = json.load(handle)

    expected = set()
    failures = []
    for entry in inventory:
        if entry["num"] == 0:
            continue
        section_path = os.path.join(section_dir, entry["file"])
        summary_file = entry["file"].replace(".md", "_summary.json")
        summary_path = os.path.join(section_dir, summary_file)
        expected.add(summary_file)
        if not os.path.exists(summary_path):
            failures.append(f"missing {summary_file}")
            continue
        try:
            with open(summary_path, "r", encoding="utf-8") as handle:
                summary = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"invalid {summary_file}: {exc}")
            continue
        if summary.get("source_heading_id") != entry.get("heading_id"):
            failures.append(f"{summary_file} heading ID does not match inventory")
        if summary.get("source_sha256") != _sha256_file(section_path):
            failures.append(f"{summary_file} is stale for {entry['file']}")

    extras = {
        filename
        for filename in os.listdir(section_dir)
        if SUMMARY_FILE.match(filename) and filename not in expected
    }
    if extras:
        failures.append(f"orphan summaries: {sorted(extras)}")
    if failures:
        raise ValueError("; ".join(failures))


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------

def assemble_files(
    section_dir: str,
    output_path: str,
    format_type: str = "html",
    allow_missing: bool = False,
) -> str:
    """Reassemble per-section files into a single output file.

    Reads ``_inventory.json`` to get the section order, then concatenates
    all files in order.

    Args:
        section_dir: Directory containing section files and _inventory.json.
        output_path: Path to write the assembled file.
        format_type: "html" or "md".
        allow_missing: permit partial output. False by default because partial
            lecture assembly is a silent data-loss failure.

    Returns:
        Path to the assembled output file.
    """
    inv_path = os.path.join(section_dir, "_inventory.json")
    if not os.path.exists(inv_path):
        print(f"ERROR: _inventory.json not found in {section_dir}", file=sys.stderr)
        sys.exit(1)

    with open(inv_path, "r", encoding="utf-8") as f:
        inventory = json.load(f)

    parts = []
    missing = []
    ext = ".html" if format_type == "html" else ".md"

    for entry in inventory:
        if entry["num"] == 0:
            preamble_file = os.path.join(section_dir, "section_00_preamble" + ext)
            if os.path.exists(preamble_file):
                with open(preamble_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    parts.append(content)
                else:
                    missing.append(f"{os.path.basename(preamble_file)} (empty)")
            else:
                missing.append(os.path.basename(preamble_file))
            continue

        filename = entry["file"].replace(".md", ext)
        filepath = os.path.join(section_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                parts.append(content)
            else:
                missing.append(f"{filename} (empty)")
        else:
            missing.append(filename)

    if missing:
        message = (
            f"{len(missing)} section {format_type.upper()} file(s) missing: {missing}"
        )
        if not allow_missing:
            raise FileNotFoundError(message)
        print(f"WARNING: {message}", file=sys.stderr)

    body = "\n\n".join(parts)

    output_parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(body)

    print(f"Assembled {len(parts)} section(s) → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Split enriched markdown into per-section files, or reassemble HTML/MD sections."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # split
    sp = sub.add_parser("split", help="Split markdown into per-section files")
    sp.add_argument("markdown", help="Path to enriched markdown file")
    sp.add_argument("--output-dir", required=True, help="Directory for section files")
    sp.add_argument(
        "--invalidate-summaries",
        action="store_true",
        help="Delete existing summaries before Agent 2 regenerates them.",
    )

    # convert
    cp = sub.add_parser("convert", help="Convert markdown section files to HTML")
    cp.add_argument("section_dir", help="Directory containing section files")

    # assemble
    ap = sub.add_parser("assemble", help="Reassemble per-section files into a single output file")
    ap.add_argument("section_dir", help="Directory containing section files")
    ap.add_argument("--output", required=True, help="Path for assembled output")
    ap.add_argument("--format", default="html", choices=["html", "md"], help="Format of the sections to assemble")
    ap.add_argument(
        "--allow-missing",
        action="store_true",
        help="Allow partial assembly (unsafe; intended only for debugging).",
    )

    sub.add_parser(
        "bind-summaries",
        help="Bind section summaries to current markdown hashes and heading IDs",
    ).add_argument("section_dir", help="Directory containing sections and summaries")
    sub.add_parser(
        "validate-summaries",
        help="Validate summary hashes and inventory membership",
    ).add_argument("section_dir", help="Directory containing sections and summaries")

    args = parser.parse_args()

    if args.command == "split":
        split_markdown(
            args.markdown,
            args.output_dir,
            invalidate_summaries=args.invalidate_summaries,
        )
    elif args.command == "convert":
        convert_section_files(args.section_dir)
    elif args.command == "assemble":
        try:
            assemble_files(
                args.section_dir,
                args.output,
                args.format,
                allow_missing=args.allow_missing,
            )
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "bind-summaries":
        try:
            bind_summaries(args.section_dir)
            print(f"Bound summaries in {args.section_dir}")
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "validate-summaries":
        try:
            validate_summaries(args.section_dir)
            print(f"Validated summaries in {args.section_dir}")
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)



if __name__ == "__main__":
    main()