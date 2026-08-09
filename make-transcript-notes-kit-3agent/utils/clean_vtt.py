import re
import argparse
from pathlib import Path

def clean_vtt(input_path, output_path=None, keep_speakers=False, merge_paragraphs=True, delete_vtt=True):
    """
    Cleans a WebVTT file by removing timestamps, cue IDs, and tags,
    and returns a clean readable transcript.
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
    
    # Regex definitions
    tag_re = re.compile(r'<[^>]+>')  # Clean any HTML/XML like tags
    speaker_start_re = re.compile(r'<v\s+([^>]+)>')  # Match speaker tags: <v Speaker Name>
    
    while i < n:
        line = lines[i]
        
        # Skip WebVTT header and notes
        if line.startswith('WEBVTT') or line.startswith('NOTE'):
            i += 1
            continue
            
        # Skip empty lines
        if not line:
            i += 1
            continue
            
        # If next line has '-->', then the current line is a cue ID. Skip both.
        if i + 1 < n and '-->' in lines[i + 1]:
            i += 2
            continue
            
        # If current line is a timestamp, skip it
        if '-->' in line:
            i += 1
            continue
            
        # We have a content line. Check for speaker tag
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
            # Combine all text with spaces, clean multiple whitespaces
            full_text = " ".join(all_text_blocks)
            full_text = re.sub(r'\s+', ' ', full_text)
            
            # Segment into paragraphs of ~150 words for readability
            words = full_text.split()
            chunk_size = 150
            for k in range(0, len(words), chunk_size):
                paragraph = " ".join(words[k:k+chunk_size])
                output_lines.append(paragraph + "\n")
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
    parser.add_argument("input", nargs="?", help="Path to the input .vtt file (if omitted, processes all .vtt files in script folder)")
    parser.add_argument("output", nargs="?", help="Path to save the cleaned text file (optional)")
    parser.add_argument("--keep-speakers", action="store_true", help="Keep speaker names in the output (default: remove speakers)")
    parser.add_argument("--no-merge", action="store_true", help="Do not merge lines into paragraphs")
    parser.add_argument("--keep-vtt", action="store_true", help="Keep the .vtt file after conversion (by default it is deleted)")
    
    args = parser.parse_args()
    
    if args.input:
        # Single file mode
        if not args.output:
            input_path = Path(args.input)
            args.output = input_path.with_suffix('.txt')
        
        clean_vtt(
            input_path=args.input,
            output_path=args.output,
            keep_speakers=args.keep_speakers,
            merge_paragraphs=not args.no_merge,
            delete_vtt=not args.keep_vtt
        )
    else:
        # Batch mode: process all .vtt files in the script's directory
        script_dir = Path(__file__).resolve().parent
        vtt_files = list(script_dir.glob("*.vtt"))
        
        if not vtt_files:
            print(f"No .vtt files found in {script_dir}")
        else:
            print(f"Found {len(vtt_files)} .vtt file(s) in {script_dir}\n")
            for vtt_file in vtt_files:
                output_file = vtt_file.with_suffix('.txt')
                clean_vtt(
                    input_path=str(vtt_file),
                    output_path=str(output_file),
                    keep_speakers=args.keep_speakers,
                    merge_paragraphs=not args.no_merge,
                    delete_vtt=not args.keep_vtt
                )
                print()  # blank line between files
