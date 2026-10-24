import re
import argparse
from pathlib import Path

# Match standard WebVTT timestamp line: 00:00:01.000 --> 00:00:04.000
TIMESTAMP_RE = re.compile(r"^\s*(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3}\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3}")
# Cue IDs are typically simple integers or alphanumeric slugs without
# whitespace, periods, or sentence punctuation. A spoken line such as "Okay."
# must never be mistaken for a cue ID.
CUE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
CUE_ID_FORBIDDEN_CHARS = re.compile(r"[.!?,;:'\"\u2026]")
METADATA_BLOCK_START_RE = re.compile(r"^(?:NOTE|STYLE|REGION)\b")


def _strip_vtt_timestamp(timestamp_line):
    """Return a compact HH:MM:SS (or MM:SS) label for a cue timestamp."""
    start = timestamp_line.split("-->")[0].strip()
    return start.split(".")[0].split(",")[0].strip()


def segment_into_paragraphs(sentences: list[str], target_words: int = 140) -> list[str]:
    """Combine sentence strings into readable paragraphs around target_words without splitting sentences."""
    paragraphs = []
    current_sentences = []
    current_word_count = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        words = sent.split()
        current_sentences.append(sent)
        current_word_count += len(words)

        if current_word_count >= target_words and sent.endswith((".", "!", "?", '."', '!"', '?"')):
            paragraphs.append(" ".join(current_sentences) + "\n")
            current_sentences = []
            current_word_count = 0

    if current_sentences:
        paragraphs.append(" ".join(current_sentences) + "\n")

    return paragraphs


def clean_vtt(input_path, output_path=None, keep_speakers=False,
              merge_paragraphs=True, delete_vtt=False, keep_timestamps=False):
    """
    Cleans a WebVTT file by removing timestamps, cue IDs, and tags,
    and returns a clean readable transcript without losing spoken content.

    NOTE/STYLE/REGION blocks are skipped in full (to the next blank line),
    never just their first line. With ``keep_timestamps`` each spoken line is
    prefixed with its cue start time, which keeps manifest anchors traceable.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        print(f"Error: File {input_path} does not exist.")
        return None

    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split into lines and strip carriage returns/whitespace
    lines = [line.strip() for line in content.splitlines()]
    
    cleaned_blocks = []
    i = 0
    n = len(lines)
    current_timestamp = None

    # Regex definitions
    tag_re = re.compile(r'<[^>]+>')  # Clean any HTML/XML like tags
    speaker_start_re = re.compile(r'<v\s+([^>]+)>')  # Match speaker tags: <v Speaker Name>

    def _skip_to_blank(index):
        while index < n and lines[index]:
            index += 1
        return index

    while i < n:
        line = lines[i]
        
        # Skip the WebVTT header and metadata blocks in full.
        if line.startswith('WEBVTT') or METADATA_BLOCK_START_RE.match(line):
            i = _skip_to_blank(i + 1)
            continue
            
        # Skip empty lines
        if not line:
            i += 1
            continue
            
        # Check if current line is a timestamp line
        timestamp_match = TIMESTAMP_RE.search(line)
        if timestamp_match:
            current_timestamp = _strip_vtt_timestamp(timestamp_match.group(0))
            i += 1
            continue

        # Check if next line is a timestamp line (in which case current line might be a cue ID)
        if i + 1 < n and TIMESTAMP_RE.search(lines[i + 1]):
            # A cue ID is a short alphanumeric slug with no sentence punctuation.
            if (
                CUE_ID_RE.match(line)
                and len(line) < 60
                and not CUE_ID_FORBIDDEN_CHARS.search(line)
            ):
                current_timestamp = _strip_vtt_timestamp(
                    TIMESTAMP_RE.search(lines[i + 1]).group(0)
                )
                i += 2  # skip cue ID and the timestamp line
                continue

        # We have a spoken content line. Check for speaker tag
        speaker = None
        m = speaker_start_re.search(line)
        if m:
            speaker = m.group(1).strip()
            # Clean trailing period often present in MS Teams/Zoom transcripts (e.g. "Name .")
            if speaker.endswith('.'):
                speaker = speaker[:-1].strip()
                
        # Strip all tags from the line (e.g., <v...>, </v>, <i>, etc.)
        cleaned_line = tag_re.sub('', line).strip()
        
        if cleaned_line:
            if keep_timestamps and current_timestamp:
                cleaned_line = f"[{current_timestamp}] {cleaned_line}"
            cleaned_blocks.append({
                'speaker': speaker,
                'text': cleaned_line
            })
            
        i += 1

    # Check if the file actually contained any speaker tags. If not, auto-disable keep_speakers
    has_any_speaker = any(b['speaker'] is not None for b in cleaned_blocks)
    if not has_any_speaker:
        keep_speakers = False

    output_lines = []
    
    if keep_speakers:
        current_speaker = None
        current_text = []
        
        for block in cleaned_blocks:
            speaker = block['speaker'] or "Speaker"
            text = block['text']
            
            if merge_paragraphs:
                if speaker == current_speaker:
                    current_text.append(text)
                else:
                    if current_speaker:
                        merged_text = " ".join(current_text)
                        merged_text = re.sub(r'\s+', ' ', merged_text)
                        output_lines.append(f"[{current_speaker}]: {merged_text}\n")
                    current_speaker = speaker
                    current_text = [text]
            else:
                output_lines.append(f"[{speaker}]: {text}")
                
        # Flush the final block
        if current_speaker and current_text:
            merged_text = " ".join(current_text)
            merged_text = re.sub(r'\s+', ' ', merged_text)
            output_lines.append(f"[{current_speaker}]: {merged_text}\n")
            
    else:
        # No speaker tags (or disabled). Combine sentences into continuous paragraphs
        all_text_blocks = [block['text'] for block in cleaned_blocks]
        
        if merge_paragraphs:
            # Reconstruct sentences and split paragraphs cleanly at sentence boundaries
            full_text = " ".join(all_text_blocks)
            full_text = re.sub(r'\s+', ' ', full_text)
            
            # Sentence splitter
            raw_sentences = re.split(r'(?<=[.!?])\s+', full_text)
            output_lines = segment_into_paragraphs(raw_sentences, target_words=140)
        else:
            output_lines.extend(all_text_blocks)
            
    result = "\n".join(output_lines)
    
    if output_path:
        output_path = Path(output_path)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(result)
        print(f"Successfully processed: {input_path.name} -> {output_path.name}")
        if delete_vtt:
            input_path.unlink()
            print(f"Deleted source: {input_path.name}")
    else:
        print(result)
        
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Clean WebVTT transcript files for LLMs.")
    parser.add_argument("input", nargs="?", help="Path to input .vtt file or directory (if omitted, processes all .vtt files in current directory)")
    parser.add_argument("output", nargs="?", help="Path to save the cleaned text file (optional)")
    parser.add_argument("--keep-speakers", action="store_true", help="Keep speaker names in output (default: false)")
    parser.add_argument("--no-merge", action="store_true", help="Do not merge lines into paragraphs")
    parser.add_argument("--keep-timestamps", action="store_true", help="Prefix each spoken line with its cue start time (for source anchors)")
    parser.add_argument("--delete-vtt", action="store_true", help="Delete source .vtt file after conversion (default: keep source file)")
    parser.add_argument("--keep-vtt", action="store_true", help="Keep source .vtt file (default behavior)")
    
    args = parser.parse_args()
    should_delete = args.delete_vtt and not args.keep_vtt
    
    if args.input and Path(args.input).is_file():
        # Single file mode
        input_path = Path(args.input)
        if not args.output:
            args.output = input_path.with_suffix('.txt')
        
        clean_vtt(
            input_path=input_path,
            output_path=args.output,
            keep_speakers=args.keep_speakers,
            merge_paragraphs=not args.no_merge,
            delete_vtt=should_delete,
            keep_timestamps=args.keep_timestamps,
        )
    else:
        # Batch mode: process all .vtt files in the specified directory or current working directory
        search_dir = Path(args.input) if args.input and Path(args.input).is_dir() else Path.cwd()
        vtt_files = sorted(list(search_dir.glob("*.vtt")))
        
        if not vtt_files:
            print(f"No .vtt files found in {search_dir}")
        else:
            print(f"Found {len(vtt_files)} .vtt file(s) in {search_dir}\n")
            for vtt_file in vtt_files:
                output_file = vtt_file.with_suffix('.txt')
                clean_vtt(
                    input_path=str(vtt_file),
                    output_path=str(output_file),
                    keep_speakers=args.keep_speakers,
                    merge_paragraphs=not args.no_merge,
                    delete_vtt=should_delete,
                    keep_timestamps=args.keep_timestamps,
                )
                print()
