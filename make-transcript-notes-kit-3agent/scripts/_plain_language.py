#!/usr/bin/env python3
"""Shared plain-English word lists + readability helpers for lint.py.

Imported by `lint.py` so the language rules are defined once and enforced
consistently. Mirrors the same approach used in make-lecture-kit.

Pure standard library. No third-party deps, no network.
"""

import re

# Sentence-length policy for ordinary prose after math/code/table stripping.
# These are review hints, never blocking targets.
WORD_AIM = 20
WORD_CEILING = 28

# --------------------------------------------------------------------------- #
# 1. Hand-waving — BANNED OUTRIGHT (hard FAIL).
# --------------------------------------------------------------------------- #
HANDWAVE_PHRASES = [
    "it can be shown",
    "it can be proven",
    "it is easy to see",
    "it is clear that",
    "it should be apparent",
    "it should be obvious",
    "left to the reader",
    "left as an exercise",
    "the details are omitted",
    "after some algebra",
    "after simplification",
    "as is well known",
    "needless to say",
    "it goes without saying",
    "suffice it to say",
]
HANDWAVE_WORDS = [
    "clearly", "obviously", "evidently", "trivially", "plainly",
]

# --------------------------------------------------------------------------- #
# 2. Literary / filler flourishes — flagged (WARN; many => FAIL).
# --------------------------------------------------------------------------- #
FLOURISH_PHRASES = [
    "dissolve into", "dissolved into", "dissolves into",
    "the machinery of",
    "earns its keep", "earn its keep",
    "the chase for",
    "a tapestry of", "the dance of", "the symphony of",
    "lies at the intersection of", "at the intersection of",
    "in the realm of", "in the world of",
    "it is important to note that", "it is worth noting that",
    "it's important to note that", "it's worth noting that",
    "it is crucial to understand that", "it is crucial to note that",
    "as we can see",
    "in essence", "essentially,", "fundamentally,",
]

# --------------------------------------------------------------------------- #
# 3. Fancy words -> optional plain swaps (advisory WARN only).
# --------------------------------------------------------------------------- #
FANCY_WORDS = {
    "utilize": "use", "utilise": "use", "utilizes": "uses", "utilises": "uses",
    "utilizing": "using", "utilising": "using", "utilization": "use",
    "utilisation": "use",
    "leverage": "use", "leverages": "uses", "leveraging": "using",
    "facilitate": "help", "facilitates": "help", "facilitating": "helping",
    "demonstrate": "show", "demonstrates": "shows", "demonstrated": "showed",
    "obtain": "get", "obtains": "gets", "obtained": "got",
    "sufficient": "enough", "insufficient": "not enough",
    "numerous": "many", "myriad": "many", "plethora": "a lot",
    "additional": "more", "approximately": "about",
    "subsequently": "then", "commence": "start", "commences": "starts",
    "terminate": "end", "terminates": "ends",
    "furthermore": "also", "moreover": "also",
    "thus": "so", "hence": "so", "therefore": "so", "consequently": "so",
    "whence": "so",
    "nevertheless": "but", "nonetheless": "still",
    "regarding": "about", "pertaining": "about",
    "aforementioned": "this", "possess": "have", "possesses": "has",
    "comprise": "include", "comprises": "includes", "encompass": "include",
    "encompasses": "includes", "elucidate": "explain", "expound": "explain",
    "ascertain": "find out", "endeavour": "try", "endeavor": "try",
    "crucial": "key", "pivotal": "key", "paramount": "key",
    "robust": "strong", "ubiquitous": "everywhere", "salient": "main",
    "methodology": "method", "dichotomy": "split",
    "cognizant": "aware", "delineate": "describe",
}

# --------------------------------------------------------------------------- #
# 4. Clichés, Jargon, and AI/Marketing Hype — WARN only, never FAIL.
#
# This list targets phrases that are *unambiguously* AI-chatbot or corporate
# jargon. Words with legitimate technical meanings (transform, harness, may,
# could, etc.) are intentionally excluded — the agent prompt teaches judgment,
# and the lint gate only softly warns on these patterns.
# --------------------------------------------------------------------------- #
CLICHES_AND_JARGON = {
    # AI-chatbot clichés (almost never appropriate in educational notes)
    "dive into": "look at / explain",
    "dives into": "explains / covers",
    "diving into": "looking at / covering",
    "delve into": "look at / explain",
    "delves into": "explains / covers",
    "delving into": "looking at / covering",
    "unleash": "use / enable",
    "unleashes": "enables",
    "unleashing": "enabling",
    "unleash your potential": "improve your skills",
    "unleash the potential": "use / enable",
    "game-changing": "effective / important",
    "game changer": "useful improvement",
    "transformative": "helpful / significant",
    "explore the depth of": "understand",
    "explore the depths of": "understand",
    "groundbreaking": "new / significant",
    "cutting-edge": "modern / recent",
    # Corporate jargon (never appropriate in educational notes)
    "touch base": "meet / discuss",
    "touches base": "meets / discusses",
    "touching base": "meeting / discussing",
    "move the needle": "improve results",
    "moves the needle": "improves results",
    "moving the needle": "improving results",
    "mission-critical": "important / essential",
    "mission critical": "important / essential",
    "deliverable": "result / output",
    "deliverables": "results / outputs",
    "synergy": "cooperation / combination",
    "synergies": "combinations",
    "synergistic": "combined",
    "paradigm shift": "major change",
    "paradigm shifts": "major changes",
}

# --------------------------------------------------------------------------- #
# 5. PII / anonymisation — shared by the dense and HTML lint gates.
#
# Severity policy (single source of truth):
#   FAIL — direct transcript/recording references (notes must stand alone)
#   WARN — professor/university mention candidates (may be academic citations)
# --------------------------------------------------------------------------- #
PII_PATTERNS = [
    ("transcript reference", re.compile(
        r"\b(?:transcript|lecture\s+recording|audio\s+recording|this\s+recording)\b",
        re.IGNORECASE)),
    # Match the role word case-insensitively but keep the proper noun's case
    # requirement, so "professor Bishop" is caught without flagging
    # "professor of physics".
    ("professor/instructor mention", re.compile(
        r"\b(?:[Pp]rofessor|[Ii]nstructor|[Ll]ecturer)\s+[A-Z][a-z]+\b")),
    ("university mention candidate", re.compile(
        r"\b(?:[Uu]niversity|[Ii]nstitute|[Cc]ollege)\s+of\s+[A-Z]")),
]

_PII_FAIL_LABELS = {
    "transcript reference",
    "professor/instructor mention",
}


def find_pii(text):
    """Return [(label, matched_text)] for every PII candidate."""
    hits = []
    for label, pattern in PII_PATTERNS:
        for match in pattern.finditer(text):
            hits.append((label, match.group(0)))
    return hits


def pii_severity(label):
    """Return "FAIL" or "WARN" for a PII label using the shared policy."""
    return "FAIL" if label in _PII_FAIL_LABELS else "WARN"


# --------------------------------------------------------------------------- #
# 6. Shared pre-processing helpers
# --------------------------------------------------------------------------- #
VERIFY_MARKER_RE = re.compile(r"\*\[verify\b[^\]]*\]\*", re.IGNORECASE)


def strip_verify_markers(text):
    """Remove *[verify: ...]* markers before prose heuristics run.

    A marker's reason can legitimately contain a phrase like "after
    simplification"; that is metadata about the reconstruction, not
    hand-waving prose.
    """
    return VERIFY_MARKER_RE.sub(" ", text)


# Pre-compiled matchers ------------------------------------------------------ #
_FANCY_RE = {
    w: re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)
    for w in FANCY_WORDS
}
_HANDWORD_RE = {
    w: re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)
    for w in HANDWAVE_WORDS
}
_CLICHES_RE = {
    w: re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)
    for w in CLICHES_AND_JARGON
}
_VOWEL_GROUP = re.compile(r"[aeiouy]+")
_WORD = re.compile(r"[A-Za-z']+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def find_handwaving(text):
    """Return [(phrase, count)] for every banned hand-wave found (hard FAIL)."""
    low = text.lower()
    hits = []
    for p in HANDWAVE_PHRASES:
        c = low.count(p)
        if c:
            hits.append((p, c))
    for w, rx in _HANDWORD_RE.items():
        c = len(rx.findall(text))
        if c:
            hits.append((w, c))
    return hits


def find_flourishes(text):
    """Return [(phrase, count)] for literary/filler flourishes (WARN)."""
    low = text.lower()
    return [(p, low.count(p)) for p in FLOURISH_PHRASES if low.count(p)]


def find_fancy(text):
    """Return [(word, suggestion, count)] for fancy words (WARN)."""
    out = []
    for w, rx in _FANCY_RE.items():
        c = len(rx.findall(text))
        if c:
            out.append((w, FANCY_WORDS[w], c))
    out.sort(key=lambda t: (-t[2], t[0]))
    return out


def find_cliches_and_jargon(text):
    """Return [(word, suggestion, count)] for clichés, jargon, AI/marketing words (WARN)."""
    out = []
    for w, rx in _CLICHES_RE.items():
        c = len(rx.findall(text))
        if c:
            out.append((w, CLICHES_AND_JARGON[w], c))
    out.sort(key=lambda t: (-t[2], t[0]))
    return out


def count_syllables(word):
    """Cheap, robust syllable estimate (good enough for a readability score)."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = _VOWEL_GROUP.findall(w)
    n = len(groups)
    if w.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def flesch_reading_ease(text):
    """Return (score, n_words, n_sentences). Higher = easier.

    Flesch Reading Ease = 206.835 - 1.015*(words/sentences) - 84.6*(syll/words).
    ~90+ very easy, 60-70 plain English (≈ grade 8-9), <50 fairly hard,
    <30 very hard.
    """
    sentences = [s for s in _SENT_SPLIT.split(text) if s.strip()]
    words = _WORD.findall(text)
    n_sent = max(1, len(sentences))
    n_words = len(words)
    if n_words == 0:
        return (100.0, 0, n_sent)
    syll = sum(count_syllables(w) for w in words)
    score = (206.835
             - 1.015 * (n_words / n_sent)
             - 84.6 * (syll / n_words))
    return (round(score, 1), n_words, n_sent)
