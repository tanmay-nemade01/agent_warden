"""Confine user-supplied path segments so they cannot escape a root.

Windows pathlib replaces the left-hand side when a component is absolute
(drive letter, UNC, or POSIX ``/``), so every join goes through ``confine``
rather than ``root / user_string``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SEP = re.compile(r"[/\\]")
_DRIVE = re.compile(r"^[A-Za-z]:")
_UNC = re.compile(r"^[/\\]{2}")

ABBR_RE = re.compile(r"^[A-Z0-9_]{1,8}$")
SUBJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-()]{0,120}$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}$")


def is_safe_component(part: object) -> bool:
    """True if *part* is a single relative path segment that cannot escape."""
    if not isinstance(part, str):
        return False
    s = part.strip()
    if not s or s in {".", ".."}:
        return False
    if "\x00" in s:
        return False
    if _SEP.search(s):
        return False
    if _DRIVE.match(s) or _UNC.match(s):
        return False
    if s.startswith("/") or s.startswith("\\"):
        return False
    if os.path.isabs(s):
        return False
    return True


def confine(root: Path | str, *parts: str) -> Path | None:
    """Join *parts* under *root*, or ``None`` if any component is unsafe.

    Rejects empty segments, ``..``, separators, drive letters, UNC, and
    absolute components. After join, the resolved path must stay inside
    the resolved root (``relative_to``).
    """
    if root is None or not parts:
        return None
    try:
        base = Path(root).resolve()
    except (OSError, RuntimeError, TypeError):
        return None
    cleaned: list[str] = []
    for part in parts:
        if not is_safe_component(part):
            return None
        cleaned.append(part.strip())
    joined = base.joinpath(*cleaned)
    try:
        resolved = joined.resolve()
        resolved.relative_to(base)
    except (ValueError, OSError, RuntimeError):
        return None
    return resolved


def resolve_under(root: Path | str, path: str | Path | None) -> Path | None:
    """Resolve *path* and require it to sit under *root*.

    Used for transcript and companion-docs paths that may already be
    absolute (as listed by the UI) rather than single components.
    """
    if path is None:
        return None
    raw = str(path).strip()
    if not raw:
        return None
    try:
        base = Path(root).resolve()
        p = Path(raw)
        if not p.is_absolute():
            for seg in p.parts:
                if seg in {".", ""}:
                    continue
                if not is_safe_component(seg):
                    return None
            p = base.joinpath(*[s for s in p.parts if s not in {".", ""}])
        resolved = p.resolve()
        resolved.relative_to(base)
    except (ValueError, OSError, RuntimeError, TypeError):
        return None
    return resolved
