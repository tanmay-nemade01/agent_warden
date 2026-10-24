#!/usr/bin/env python3
r"""Shared utilities for reading topic_mapping YAML files.

Used by update_topic_mapping.py and imported by the lint gate to validate
topic_mapping updates. Uses PyYAML for reliable parsing.

Usage:
    from topic_mapping_utils import load_topic_map, find_previous_coverage
"""

import json
import os
import re
import sys
import html
from urllib.parse import quote

# Force UTF-8 encoding for standard output/error on Windows to prevent UnicodeEncodeError
if sys.platform.startswith("win"):
    import io
    if hasattr(sys.stdout, "encoding") and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "encoding") and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

TOPIC_MAPPINGS_DIR = os.path.abspath(
    os.environ.get(
        "BITSNOTES_TOPIC_MAPPINGS_DIR",
        os.path.join(os.path.dirname(__file__), "..", "..", "topic_mappings"),
    )
)

# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

SUBJECTS_JSON = os.environ.get(
    "BITSNOTES_SUBJECTS_JSON",
    os.path.abspath(os.path.join(TOPIC_MAPPINGS_DIR, "..", "subjects.json")),
)


class TopicMapParseError(ValueError):
    """Raised when an existing topic map file exists but cannot be parsed."""


def _parse_yaml(text, path=None, strict=False):
    """Parse a topic_mapping YAML string into a dict.

    Uses PyYAML (yaml.safe_load).

    Returns:
        dict with keys like subject_name, lectures (list of lecture dicts)
        or None if parse fails. When ``strict`` is True a parse failure raises
        :class:`TopicMapParseError` instead of returning None, so callers can
        distinguish "missing file" from "corrupt file".
    """
    import yaml
    try:
        return yaml.safe_load(text)
    except Exception as exc:
        if strict:
            label = path or "<string>"
            raise TopicMapParseError(
                f"Refusing to treat unparseable topic map as missing: {label}: {exc}"
            ) from exc
        return None


def _load_subjects_map():
    """Load the canonical subjects.json mapping (acronym -> full name)."""
    try:
        with open(SUBJECTS_JSON, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in data.items()
        if str(key).strip() and str(value).strip()
    }


def _normalize_name(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def resolve_subject_name(subject_name):
    """Resolve a subject name/acronym to its canonical display name.

    Uses ``subjects.json`` when it exists (it is the canonical map shared with
    the site). Returns None when the name is not listed, so callers can fall
    back to the legacy fuzzy matcher.
    """
    if not subject_name:
        return None
    clean = str(subject_name).strip()
    if clean.lower().endswith((".yaml", ".yml")):
        clean = clean.rsplit(".", 1)[0]

    subjects = _load_subjects_map()
    if not subjects:
        return None

    normalized = _normalize_name(clean)
    for acronym, full_name in subjects.items():
        if normalized in {_normalize_name(acronym), _normalize_name(full_name)}:
            return full_name
    return None


def find_topic_map_path(subject_name):
    """Return the existing YAML path for a subject, or None if absent.

    Unlike :func:`load_topic_map`, this never parses the file, so callers can
    detect a corrupt-but-present topic map before overwriting it.
    """
    if not subject_name or not os.path.isdir(TOPIC_MAPPINGS_DIR):
        return None

    clean = str(subject_name)
    if clean.lower().endswith((".yaml", ".yml")):
        clean = clean.rsplit(".", 1)[0]

    candidates = [clean]
    canonical = resolve_subject_name(clean)
    if canonical and canonical != clean:
        candidates.insert(0, canonical)

    for name in candidates:
        for ext in (".yaml", ".yml"):
            path = os.path.join(TOPIC_MAPPINGS_DIR, name + ext)
            if os.path.exists(path):
                return path

    for candidate in os.listdir(TOPIC_MAPPINGS_DIR):
        if not (candidate.endswith(".yaml") or candidate.endswith(".yml")):
            continue
        stem = candidate.rsplit(".", 1)[0]
        if any(is_subject_match(name, stem) for name in candidates):
            return os.path.join(TOPIC_MAPPINGS_DIR, candidate)
    return None


def clean_string_to_words(s):
    if not isinstance(s, str):
        return []
    s = s.lower().strip()
    # Replace underscores and hyphens with spaces
    s = s.replace("_", " ").replace("-", " ")
    words = s.split()
    stopwords = {"and", "for", "to", "of", "the", "in", "on", "at", "by", "with"}
    return [w for w in words if w not in stopwords]


def get_acronym(words):
    if not words:
        return ""
    return "".join(w[0] for w in words if w)


def is_subject_match(name1, name2):
    w1 = clean_string_to_words(name1)
    w2 = clean_string_to_words(name2)
    if not w1 or not w2:
        return False
    # Check exact match of words
    if w1 == w2:
        return True
    # Check acronym match
    acr1 = w1[0] if len(w1) == 1 else get_acronym(w1)
    acr2 = w2[0] if len(w2) == 1 else get_acronym(w2)
    joined1 = "".join(w1)
    joined2 = "".join(w2)
    
    if joined1 == acr2 or joined2 == acr1:
        return True
    if acr1 == acr2 and len(acr1) >= 2:
        return True
        
    # Strip digits for standard course codes/acronyms (e.g. SE4ML vs SEML)
    acr1_no_digits = "".join(c for c in acr1 if not c.isdigit())
    acr2_no_digits = "".join(c for c in acr2 if not c.isdigit())
    if acr1_no_digits == acr2_no_digits and len(acr1_no_digits) >= 2:
        return True
        
    return False


def load_topic_map(subject_name, strict=False):
    """Load a topic_mapping YAML file by subject name.

    Tries the canonical ``subjects.json`` name first, then a direct filename
    match (e.g. "Machine Learning" -> "Machine Learning.yaml"). If that fails,
    scans ALL YAML files in the directory and matches by the ``subject_name``
    field inside each file or by filename.

    Args:
        subject_name: e.g. "Artificial Computational Intelligence" or
                      "Artificial Computational Intelligence.yaml" or
                      "ACI" (acronym that matches a file like ACI.yaml)
        strict: when True, an existing-but-unparseable file raises
            :class:`TopicMapParseError` instead of being reported as missing.

    Returns:
        dict with keys: subject_name, lectures (list of lecture dicts)
        or None if no matching file found.
    """
    if not subject_name:
        return None

    clean_name = subject_name
    if clean_name.lower().endswith(".yaml") or clean_name.lower().endswith(".yml"):
        clean_name = clean_name.rsplit(".", 1)[0]

    canonical = resolve_subject_name(clean_name)
    names = [canonical, clean_name] if canonical and canonical != clean_name else [clean_name]

    # 1. Direct filename match (canonical name first when subjects.json exists)
    for name in names:
        for ext in (".yaml", ".yml"):
            path = os.path.join(TOPIC_MAPPINGS_DIR, name + ext)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8-sig") as f:
                    text = f.read()
                result = _parse_yaml(text, path=path, strict=strict)
                if result:
                    result["_file_path"] = path
                    return result

    # 2. Fallback: scan all YAMLs and match by subject_name field inside or filename
    if not os.path.isdir(TOPIC_MAPPINGS_DIR):
        return None

    for candidate in os.listdir(TOPIC_MAPPINGS_DIR):
        if not (candidate.endswith(".yaml") or candidate.endswith(".yml")):
            continue
        cpath = os.path.join(TOPIC_MAPPINGS_DIR, candidate)
        with open(cpath, "r", encoding="utf-8-sig") as f:
            text = f.read()
        cand_name = candidate.rsplit(".", 1)[0]
        # A corrupt file that *looks* like the requested subject must not be
        # silently skipped when strict mode is on.
        candidate_matches_subject = any(
            is_subject_match(name, cand_name) for name in names
        )
        parsed = _parse_yaml(
            text,
            path=cpath,
            strict=strict and candidate_matches_subject,
        )
        if parsed:
            cand_name = candidate.rsplit(".", 1)[0]
            inner_name = parsed.get("subject_name", "")
            if is_subject_match(clean_name, inner_name) or is_subject_match(clean_name, cand_name) \
                    or (canonical and (
                        _normalize_name(canonical) == _normalize_name(inner_name)
                        or _normalize_name(canonical) == _normalize_name(cand_name)
                    )):
                parsed["_file_path"] = cpath
                return parsed
    return None


def get_all_topic_maps():
    """Load all topic_mapping YAML files in the topic_mappings directory.

    Returns:
        dict mapping subject_name -> topic_map dict
    """
    maps = {}
    if not os.path.isdir(TOPIC_MAPPINGS_DIR):
        return maps
    for fname in os.listdir(TOPIC_MAPPINGS_DIR):
        if fname.endswith(".yaml") or fname.endswith(".yml"):
            topic_map = load_topic_map(fname)
            if topic_map and "subject_name" in topic_map:
                maps[topic_map["subject_name"]] = topic_map
    return maps


# ---------------------------------------------------------------------------
# Matching and parsing helper functions
# ---------------------------------------------------------------------------

def _is_sublist(a, b):
    """Check if list a is a contiguous sub-sequence of list b."""
    if not a:
        return True
    if not b:
        return False
    len_a, len_b = len(a), len(b)
    for i in range(len_b - len_a + 1):
        if b[i:i+len_a] == a:
            return True
    return False


def _clean_and_normalize(text):
    """Unescape HTML, strip leading numbers, normalize case and non-alphanumerics."""
    if not text:
        return []
    text = html.unescape(str(text))
    # Remove leading numbering (e.g. "3.2.1 ", "1.1-", etc.)
    text = re.sub(r"^[\d\.\s\-]+", "", text)
    text = text.lower()
    # Replace non-alphanumeric characters with spaces
    text = re.sub(r"[^\w\s]", " ", text)
    return text.split()


def _topics_match(qt, ct, min_word_overlap=3):
    """Check if query topic qt matches covered topic ct."""
    qw = _clean_and_normalize(qt)
    cw = _clean_and_normalize(ct)

    if not qw or not cw:
        return False

    if qw == cw:
        return True

    STOP_WORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "he", "in", "is", "it", "its", "of", "on", "that", "the",
        "to", "was", "were", "will", "with", "or"
    }

    # 1. Contiguous sub-phrase match
    # If one of the phrases is a single word, it must not be a common stop word
    if len(qw) == 1:
        if qw[0] not in STOP_WORDS and _is_sublist(qw, cw):
            return True
    elif len(cw) == 1:
        if cw[0] not in STOP_WORDS and _is_sublist(cw, qw):
            return True
    else:
        if _is_sublist(qw, cw) or _is_sublist(cw, qw):
            return True

    # 2. Word overlap of non-stop words (with adaptive/custom threshold)
    qw_set = set(qw) - STOP_WORDS
    cw_set = set(cw) - STOP_WORDS
    overlap = len(qw_set & cw_set)

    if overlap > 0:
        effective_threshold = min(min_word_overlap, len(qw_set))
        if effective_threshold >= 2 and overlap >= effective_threshold:
            return True

    return False


def _safe_parse_lecture_number(num_str):
    """Safely extract the first sequence of digits as an integer."""
    num_str = str(num_str)
    match = re.search(r'\d+', num_str)
    if match:
        return int(match.group())
    return 0


def find_coverage_by_topics(topics, subject_name=None, exclude_lecture=None,
                            all_maps=None, min_word_overlap=3):
    """Find lectures that cover any of the given topics.

    Args:
        topics: list of topic name strings to search for
        subject_name: optional, restrict to one subject
        exclude_lecture: optional (subject, lecture_number) tuple to exclude
        all_maps: pre-loaded dict from get_all_topic_maps(), or None to load
        min_word_overlap: minimum word overlap for partial matching (default 3)

    Returns:
        list of dicts:
            {subject, lecture_number, topic, matching_topics: [matched sub-topics]}
    """
    if all_maps is None:
        all_maps = get_all_topic_maps()

    results = []
    for subj, tmap in all_maps.items():
        if subject_name and not is_subject_match(subj, subject_name):
            continue
        for lec in tmap.get("lectures", []):
            if exclude_lecture and \
               is_subject_match(subj, exclude_lecture[0]) and \
               str(lec.get("lecture_number", "")) == str(exclude_lecture[1]):
                continue
            covered = lec.get("topics_covered", [])
            if not isinstance(covered, list):
                continue

            matched = []
            for ct in covered:
                for qt in topics:
                    if _topics_match(qt, ct, min_word_overlap):
                        if ct not in matched:
                            matched.append(ct)
                        break

            if matched:
                results.append({
                    "subject": subj,
                    "lecture_number": lec.get("lecture_number"),
                    "lecture_topic": lec.get("topic"),
                    "file_name": lec.get("file_name", ""),
                    "matching_topics": matched[:5],
                })
    return results


def find_previous_coverage_in_subject(subject_name, current_lecture_number):
    """Find all lectures before the current one in the same subject.

    Args:
        subject_name: the subject to search
        current_lecture_number: current lecture number (int or string)

    Returns:
        list of lecture dicts (from YAML) for earlier lectures
    """
    topic_map = load_topic_map(subject_name)
    if not topic_map:
        return []
    current = _safe_parse_lecture_number(current_lecture_number)
    return [
        lec for lec in topic_map.get("lectures", [])
        if _safe_parse_lecture_number(lec.get("lecture_number", "0")) < current
    ]


def format_coverage_summary(results):
    """Format coverage results into a human-readable summary string.

    Args:
        results: list from find_coverage_by_topics()

    Returns:
        markdown string with coverage summary
    """
    if not results:
        return "No previous coverage found across existing topic maps."

    lines = ["### Previously Covered in Existing Lectures",
             "",
             "The following lectures already cover topics related to this transcript."]

    by_subject = {}
    for r in results:
        by_subject.setdefault(r["subject"], []).append(r)

    for subj, lecs in by_subject.items():
        lines.append(f"\n**{subj}**")
        for lec in lecs:
            lec_num = lec["lecture_number"]
            lec_topic = lec["lecture_topic"]
            lines.append(f"- Lecture {lec_num}: {lec_topic}")
            for mt in lec["matching_topics"]:
                lines.append(f"  - `{mt}`")

    return "\n".join(lines)


def format_previous_coverage_in_subject(subject_name, current_lecture_number):
    """Get and format coverage from previous lectures in the same subject.

    Args:
        subject_name: subject to check
        current_lecture_number: current lecture number

    Returns:
        markdown string listing previous lectures and their topics
    """
    prev = find_previous_coverage_in_subject(subject_name, current_lecture_number)
    if not prev:
        return "No previous lectures found for this subject."

    lines = [
        "### Previous Lectures in This Subject",
        "",
        f"The following {len(prev)} lecture(s) precede this one. Topics they cover",
        "that overlap with the current transcript should be treated as **review**",
        "rather than introduced as new material.",
        "",
    ]

    for lec in prev:
        lec_num = lec.get("lecture_number", "?")
        lec_topic = lec.get("topic", "Unknown")
        topics = lec.get("topics_covered", [])
        if not isinstance(topics, list):
            topics = []
        lines.append(f"**Lecture {lec_num}: {lec_topic}**")
        for t in topics:
            lines.append(f"- {t}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prerequisite generation
# ---------------------------------------------------------------------------

# Administrative / non-technical topics never worth listing as prerequisites.
_NON_TECHNICAL_TOPIC = re.compile(
    r"\b(?:course\s+logistics|logistics|administrat\w*|housekeeping|"
    r"exam\s+guidance|exam\s+note|key\s+industry\s+applications|"
    r"further\s+reading|glossary|references|bibliography|"
    r"questions?\s*(?:and|&)\s*answers?|q\s*&\s*a|"
    r"summary|recap|conclusion|introduction\s+to\s+the\s+course)\b",
    re.IGNORECASE,
)

_GENERIC_TOPIC_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "concept", "concepts",
    "course", "data", "definition", "definitions", "domain", "example",
    "examples", "for", "from", "in", "intro", "introduction", "is", "it",
    "its", "lecture", "method", "methods", "model", "models", "of", "on",
    "overview", "part", "problem", "problems", "recap", "review", "section",
    "summary", "that", "the", "this", "to", "topic", "topics", "types",
    "use", "using", "what", "when", "where", "which", "why", "with",
}


def _topic_signature(value):
    """Return the distinctive lower-case tokens of a topic heading."""
    if not isinstance(value, str):
        return set()
    words = _clean_and_normalize(value)
    return {
        word for word in words
        if len(word) >= 3 and word not in _GENERIC_TOPIC_WORDS
    }


def _is_prerequisite_candidate(topic):
    text = str(topic).strip()
    if not text:
        return False
    # Keep major topics and first-level subsections; skip micro-subsections.
    if re.match(r"^\d+\.\d+\.\d+", text):
        return False
    return not _NON_TECHNICAL_TOPIC.search(text)


def _prerequisite_href(file_name):
    """Turn a YAML file_name into a link target (forward slashes)."""
    href = str(file_name or "").replace("\\", "/").strip()
    if not href:
        return ""
    # ``outputs/`` is the local build directory, not a published site path.
    if href.startswith("outputs/"):
        href = href[len("outputs/"):]
    return "/".join(quote(part) for part in href.split("/"))


def _current_lecture_topics(topic_map, current_lecture_number):
    current = _safe_parse_lecture_number(current_lecture_number)
    for lec in topic_map.get("lectures", []):
        if _safe_parse_lecture_number(lec.get("lecture_number", "0")) == current:
            topics = lec.get("topics_covered", [])
            return lec, topics if isinstance(topics, list) else []
    return None, []


def _select_prerequisites(current_topics, prev_lecs, limit=10):
    """Select prior topics with real token overlap against this lecture.

    Returns a list of (lecture_dict, topic_string, shared_token_count) in
    lecture order, capped at ``limit``. Low-confidence matches (one shared
    distinctive token) are only used to fill the list.
    """
    current_tokens = set()
    for topic in current_topics:
        if _is_prerequisite_candidate(topic):
            current_tokens |= _topic_signature(topic)
    if not current_tokens:
        return []

    strong, weak = [], []
    for lecture_index, lec in enumerate(prev_lecs):
        topics = lec.get("topics_covered", [])
        if not isinstance(topics, list):
            continue
        for topic_index, topic in enumerate(topics):
            if not _is_prerequisite_candidate(topic):
                continue
            shared = _topic_signature(topic) & current_tokens
            if not shared:
                continue
            record = (lec, str(topic).strip(), lecture_index, topic_index, len(shared))
            (strong if len(shared) >= 2 else weak).append(record)

    selected = sorted(
        strong,
        key=lambda item: (-item[4], item[2], item[3]),
    )[:limit]
    if len(selected) < limit:
        selected += sorted(
            weak,
            key=lambda item: (item[2], item[3]),
        )[: limit - len(selected)]

    # Restore chronological order and drop duplicate topic labels.
    selected.sort(key=lambda item: (item[2], item[3]))
    seen, deduped = set(), []
    for item in selected:
        key = item[1].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def generate_prerequisite_html(subject_name, current_lecture_number, limit=10):
    """Generate the prerequisite-knowledge HTML section for Agent 3.

    Matches this lecture's topics against prior lectures in the same subject by
    distinctive token overlap, excludes administrative/appendix topics, caps
    the list (default 10), groups the result by lecture, and links each topic
    to the earlier notes file recorded in the topic map.

    Args:
        subject_name: Course subject name (e.g. 'Machine Learning')
        current_lecture_number: Current lecture number (e.g. 5)
        limit: Maximum number of prerequisite topics to emit.

    Returns:
        Formatted HTML string to replace {{PREREQUISITE_KNOWLEDGE}}, or an
        empty string when there is no confident match.
    """
    topic_map = load_topic_map(subject_name)
    if not topic_map:
        return ""

    _, current_topics = _current_lecture_topics(topic_map, current_lecture_number)
    prev_lecs = find_previous_coverage_in_subject(subject_name, current_lecture_number)
    selected = _select_prerequisites(current_topics, prev_lecs, limit=limit)
    if not selected:
        return ""

    grouped = {}
    for lec, topic, lecture_index, topic_index, _ in selected:
        grouped.setdefault(lecture_index, (lec, []))[1].append(topic)

    lines = [
        '<section class="prerequisite-section">',
        '  <h2 class="section-title">Prerequisite Knowledge</h2>',
        '  <p class="prerequisite-intro">This lecture builds on the following concepts from earlier lectures. If any feel unfamiliar, review the linked notes before proceeding.</p>',
    ]
    for lecture_index in sorted(grouped):
        lec, topics = grouped[lecture_index]
        lec_num = lec.get("lecture_number", "?")
        lec_topic = lec.get("topic", "Lecture Notes")
        href = _prerequisite_href(lec.get("file_name", ""))
        lines.append('  <div class="prerequisite-entry">')
        lines.append(
            f'    <h3 class="subsection-title">Lecture {html.escape(str(lec_num))}: '
            f'{html.escape(str(lec_topic))}</h3>'
        )
        lines.append("    <ul>")
        for topic in topics:
            label = html.escape(topic)
            if href:
                label = f'<a href="{html.escape(href, quote=True)}">{label}</a>'
            lines.append(f"      <li><strong>{label}</strong></li>")
        lines.append("    </ul>")
        lines.append("  </div>")
    lines.append("</section>")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "prerequisites":
        subject_arg = sys.argv[2]
        lec_arg = sys.argv[3]
        prereq_html = generate_prerequisite_html(subject_arg, lec_arg)
        if prereq_html:
            print(prereq_html)
    else:
        maps = get_all_topic_maps()
        print(f"Loaded {len(maps)} topic maps:")
        for name, m in maps.items():
            lec_count = len(m.get("lectures", []))
            print(f"  {name}: {lec_count} lectures")
            for lec in m.get("lectures", []):
                tc = lec.get("topics_covered", [])
                tc_count = len(tc) if isinstance(tc, list) else 0
                print(f"    L{lec.get('lecture_number')}: \"{lec.get('topic', '?')}\" ({tc_count} topics)")