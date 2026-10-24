#!/usr/bin/env python3
r"""Convert enriched markdown sections to pretty-printed HTML fragments.

Supports:
- Callout directives :::key-concept ... ::: -> <div class="key-concept">...</div>
- Headings ## and ### with generated id attributes
- Paragraphs, Lists (ul/ol), Blockquotes (>), Code blocks (```), Tables (|)
- Inline formatting (**bold**, *italic*, `code`, [link](url))
- Preserves single-backslash MathJax delimiters \( ... \) and \[ ... \]
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


import html
import uuid

CALLOUT_CLASSES = {
    "key-concept",
    "important-note",
    "example-box",
    "warning-box",
    "key-takeaway",
}


def sanitize_math_block(tok: str) -> str:
    """Ensure block math \\[ ... \\] does not contain nested \\( or \\) inline delimiters,
    and prevent raw '<' followed by letters from breaking browser HTML parsing."""
    if tok.startswith(r"\[") and tok.endswith(r"\]"):
        inner = tok[2:-2]
        # Strip nested inline math delimiters inside display math blocks
        inner_clean = inner.replace(r"\(", "").replace(r"\)", "")
        # Protect '<' followed by letters to avoid tag parsing in browser
        inner_clean = re.sub(r"<([a-zA-Z])", r"\\lt \1", inner_clean)
        return rf"\[{inner_clean}\]"
    elif tok.startswith(r"\(") and tok.endswith(r"\)"):
        inner = tok[2:-2]
        inner_clean = re.sub(r"<([a-zA-Z])", r"\\lt \1", inner)
        return rf"\({inner_clean}\)"
    return tok


def _escape_ampersands(text: str) -> str:
    """Escape bare ampersands without double-escaping existing entities."""
    return re.sub(
        r"&(?!(?:[A-Za-z][A-Za-z0-9]*|#\d+|#x[0-9A-Fa-f]+);)",
        "&amp;",
        text,
    )


def process_inline(text: str) -> str:
    """Process inline markdown formatting while preserving MathJax delimiters intact."""
    if not text:
        return ""

    token_map = {}

    def save_token(match):
        tok = match.group(0)
        token_key = f"@@BNMATH{uuid.uuid4().hex}@@"
        token_map[token_key] = tok
        return token_key

    # 1. Protect block math \[ ... \] and inline math \( ... \)
    pattern_math = re.compile(r"(\\\(.*?\\\)|\\\[.*?\\\])", re.DOTALL)
    text = pattern_math.sub(save_token, text)

    # 2. Protect inline code `...`
    pattern_code = re.compile(r"(`[^`]+`)")
    text = pattern_code.sub(save_token, text)

    # 3. Protect escaped markdown punctuation (\*, \_, etc.) so it renders
    #    literally instead of becoming emphasis.
    def save_escape(match):
        token_key = f"@@BNESC{uuid.uuid4().hex}@@"
        token_map[token_key] = match.group(1)
        return token_key

    text = re.sub(r"\\([*_`#\[\]\\])", save_escape, text)

    # 4. Links [text](url) with balanced parentheses inside the URL.
    def replace_link(match):
        label = match.group(1)
        url = html.escape(match.group(2), quote=True)
        return f'<a href="{url}">{label}</a>'

    text = re.sub(
        r"\[([^\]]+)\]\(((?:[^()\\]|\\.|\([^()]*\))+)\)",
        replace_link,
        text,
    )

    # 5. Escape bare ampersands, then raw '<' that is not an allowed tag.
    text = _escape_ampersands(text)
    text = re.sub(
        r"<(?!(?:strong|/strong|em|/em|code|/code|a\b|/a|span\b|/span|br\s*/?>|b\b|/b|i\b|/i))",
        "&lt;",
        text,
    )

    # 6. Bold **text** or __text__
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)

    # 7. Italic *text* or _text_
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<em>\1</em>", text)

    # 8. Restore tokens
    for token_key, tok in token_map.items():
        if tok.startswith("`") and tok.endswith("`"):
            tok_html = f"<code>{html.escape(tok[1:-1])}</code>"
        elif (tok.startswith(r"\[") and tok.endswith(r"\]")) or (tok.startswith(r"\(") and tok.endswith(r"\)")):
            tok_html = sanitize_math_block(tok)
        else:
            tok_html = tok
        text = text.replace(token_key, tok_html)

    return text


def make_slug(title: str) -> str:
    """Generate a stable id for a heading.

    Dotted headings use their inventory ID (``5.1`` / ``5.1.2``) so Agent 3's
    "IDs match _inventory.json" check can actually pass. Unnumbered headings
    fall back to a readable slug.
    """
    dotted = re.match(r"^(\d+(?:\.\d+)+)\s+", str(title).strip())
    if dotted:
        return dotted.group(1)
    clean = re.sub(r"<[^>]+>", "", title)
    clean = re.sub(r"[^a-zA-Z0-9.]+", "-", clean).strip("-").lower()
    return clean or "heading"


def split_table_row(line: str) -> list[str]:
    """Split a markdown table row on unescaped pipes outside math spans.

    ``P(A|B)``, ``\\(a|b\\)``, and escaped ``\\|`` stay inside one cell.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]

    cells = []
    buffer = []
    math_close = None
    index = 0
    while index < len(stripped):
        char = stripped[index]
        pair = stripped[index:index + 2]
        if char == "\\" and index + 1 < len(stripped):
            if pair in (r"\(", r"\[") and not math_close:
                math_close = r"\)" if pair == r"\(" else r"\]"
                buffer.append(pair)
                index += 2
                continue
            if math_close and pair == math_close:
                math_close = None
                buffer.append(pair)
                index += 2
                continue
            if pair == r"\|":
                buffer.append("|")
                index += 2
                continue
            buffer.append(char)
            index += 1
            continue
        if char == "|" and not math_close:
            cells.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    cells.append("".join(buffer).strip())
    return cells


def convert_table(lines: list[str], indent: str) -> list[str]:
    out = [f"{indent}<table>"]
    rows = [split_table_row(line) for line in lines]

    if not rows:
        out.append(f"{indent}</table>")
        return out

    has_header = False
    if len(rows) > 1:
        sep_row = rows[1]
        if all(re.match(r"^:?-+:?$", cell) for cell in sep_row if cell):
            has_header = True

    if has_header:
        header_cells = rows[0]
        out.append(f"{indent}  <thead>")
        out.append(f"{indent}    <tr>")
        for c in header_cells:
            out.append(f"{indent}      <th>{process_inline(c)}</th>")
        out.append(f"{indent}    </tr>")
        out.append(f"{indent}  </thead>")
        body_rows = rows[2:]
    else:
        body_rows = rows

    out.append(f"{indent}  <tbody>")
    for r in body_rows:
        out.append(f"{indent}    <tr>")
        for c in r:
            out.append(f"{indent}      <td>{process_inline(c)}</td>")
        out.append(f"{indent}    </tr>")
    out.append(f"{indent}  </tbody>")
    out.append(f"{indent}</table>")
    return out


def convert_markdown_to_html(md_text: str, drop_leading_h1: bool = False) -> str:
    """Convert markdown section text into pretty-printed HTML.

    Args:
        md_text: markdown source for one section.
        drop_leading_h1: drop a single leading ``# Title`` line. The HTML
            template already renders ``{{TITLE}}`` as ``h1.lecture-title``, so
            keeping the preamble heading would create a duplicate H1.
    """
    lines = md_text.splitlines()
    if drop_leading_h1:
        cursor = 0
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        if cursor < len(lines) and lines[cursor].startswith("# "):
            lines = lines[cursor + 1:]
    html_lines = []
    indent = ""
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Callout start :::class-name
        if stripped.startswith(":::") and len(stripped) > 3:
            cname = stripped[3:].strip()
            if cname in CALLOUT_CLASSES:
                html_lines.append(f'{indent}<div class="{cname}">')
                indent += "  "
                i += 1
                continue

        # Callout end :::
        if stripped == ":::":
            if len(indent) >= 2:
                indent = indent[:-2]
            html_lines.append(f"{indent}</div>")
            i += 1
            continue

        # Headings
        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            if stripped.startswith("### "):
                tag = "h3"
                css_class = "subsection-title"
                h_text = stripped[4:].strip()
            elif stripped.startswith("## "):
                tag = "h2"
                css_class = "section-title"
                h_text = stripped[3:].strip()
            else:
                tag = "h1"
                css_class = "lecture-title"
                h_text = stripped[2:].strip()
            processed_text = process_inline(h_text)
            slug = make_slug(h_text)
            html_lines.append(
                f'{indent}<{tag} id="{slug}" class="{css_class}">{processed_text}</{tag}>'
            )
            i += 1
            continue

        # Raw HTML block: <figure> or <svg>
        if stripped.startswith("<figure") or stripped.startswith("<svg"):
            closing_tag = "</figure>" if stripped.startswith("<figure") else "</svg>"
            raw_block = []
            while i < n:
                curr_line = lines[i]
                raw_block.append(f"{indent}{curr_line.rstrip()}")
                if closing_tag in curr_line:
                    i += 1
                    break
                i += 1
            html_lines.extend(raw_block)
            continue

        # Display math block \[ ... \]
        if stripped.startswith(r"\["):
            math_lines = []
            while i < n:
                math_lines.append(lines[i].strip())
                if r"\]" in lines[i]:
                    i += 1
                    break
                i += 1
            full_math = " ".join(math_lines)
            full_math = sanitize_math_block(full_math)
            html_lines.append(f"{indent}<p>{full_math}</p>")
            continue

        # Code block ```
        if stripped.startswith("```"):
            code_lines = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1  # skip closing ```
            code_content = "\n".join(code_lines)
            code_content = (
                code_content.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            html_lines.append(f"{indent}<pre><code>{code_content}</code></pre>")
            continue

        # Blockquote >
        if stripped.startswith(">"):
            paragraphs = []
            current = []
            while i < n and lines[i].strip().startswith(">"):
                content = lines[i].strip()[1:].strip()
                if not content:
                    if current:
                        paragraphs.append(" ".join(current))
                        current = []
                else:
                    is_dialog_turn = bool(
                        re.match(
                            r"^(\*\*(?:Q|A|Question|Answer):?\*\*|[QA]:)\s+",
                            content,
                            re.IGNORECASE,
                        )
                    )
                    if is_dialog_turn and current:
                        paragraphs.append(" ".join(current))
                        current = []
                    current.append(content)
                i += 1
            if current:
                paragraphs.append(" ".join(current))
            html_lines.append(f"{indent}<blockquote>")
            for paragraph in paragraphs:
                html_lines.append(f"{indent}  <p>{process_inline(paragraph)}</p>")
            html_lines.append(f"{indent}</blockquote>")
            continue

        # Table |
        if "|" in stripped and (i + 1 < n and "|" in lines[i + 1]):
            table_lines = []
            while i < n and "|" in lines[i].strip():
                table_lines.append(lines[i].strip())
                i += 1
            table_html = convert_table(table_lines, indent)
            html_lines.extend(table_html)
            continue

        # Bullet list - or *
        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                item_text = re.sub(r"^[-*]\s+", "", lines[i].strip())
                items.append(item_text)
                i += 1
            html_lines.append(f"{indent}<ul>")
            for it in items:
                html_lines.append(f"{indent}  <li>{process_inline(it)}</li>")
            html_lines.append(f"{indent}</ul>")
            continue

        # Numbered list 1.
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                item_text = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                items.append(item_text)
                i += 1
            html_lines.append(f"{indent}<ol>")
            for it in items:
                html_lines.append(f"{indent}  <li>{process_inline(it)}</li>")
            html_lines.append(f"{indent}</ol>")
            continue

        # Paragraph (collect multiline text block)
        p_lines = []
        in_display_math = False
        while i < n:
            curr_stripped = lines[i].strip()
            if not in_display_math:
                if not curr_stripped:
                    break
                if (
                    curr_stripped.startswith(":::")
                    or curr_stripped.startswith("# ")
                    or curr_stripped.startswith("## ")
                    or curr_stripped.startswith("### ")
                    or curr_stripped.startswith("<figure")
                    or curr_stripped.startswith("<svg")
                    or curr_stripped.startswith(r"\[")
                    or curr_stripped.startswith("```")
                    or curr_stripped.startswith(">")
                    or re.match(r"^[-*]\s+", curr_stripped)
                    or re.match(r"^\d+\.\s+", curr_stripped)
                ):
                    break
            p_lines.append(curr_stripped)
            if r"\[" in curr_stripped and r"\]" not in curr_stripped:
                in_display_math = True
            elif in_display_math and r"\]" in curr_stripped:
                in_display_math = False
            i += 1

        if p_lines:
            p_text = " ".join(p_lines)
            html_lines.append(f"{indent}<p>{process_inline(p_text)}</p>")

    return "\n".join(html_lines)


def convert_section_files(section_dir: str) -> None:
    """Batch convert all section_XX.md files in section_dir to section_XX.html."""
    for filename in sorted(os.listdir(section_dir)):
        if filename.endswith(".md") and (
            filename.startswith("section_") or filename == "section_00_preamble.md"
        ):
            md_path = os.path.join(section_dir, filename)
            html_filename = filename[:-3] + ".html"
            html_path = os.path.join(section_dir, html_filename)

            with open(md_path, "r", encoding="utf-8") as f:
                md_content = f.read()

            html_content = convert_markdown_to_html(
                md_content,
                drop_leading_h1=filename == "section_00_preamble.md",
            )

            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content + "\n")

            print(f"Converted {filename} -> {html_filename}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert markdown section files to pretty-printed HTML."
    )
    parser.add_argument(
        "input_path",
        help="Path to a section_XX.md file or a section directory containing .md files",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output HTML file path (only when input_path is a single file)",
    )

    args = parser.parse_args()

    if os.path.isdir(args.input_path):
        convert_section_files(args.input_path)
    elif os.path.isfile(args.input_path):
        with open(args.input_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        html_content = convert_markdown_to_html(
            md_content,
            drop_leading_h1=os.path.basename(args.input_path) == "section_00_preamble.md",
        )
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(html_content + "\n")
            print(f"Converted {args.input_path} -> {args.output}")
        else:
            print(html_content)
    else:
        print(f"ERROR: {args.input_path} does not exist", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
