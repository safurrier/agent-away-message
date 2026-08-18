"""Structural validation for public hints, capsules, and model output."""

from __future__ import annotations

import re
from typing import Final

MAX_HINT_LENGTH: Final = 80
MAX_MESSAGE_LENGTH: Final = 96
_SAFE_TEXT: Final = re.compile(r"^[a-z][a-z0-9 ,.!?'/_-]*$", re.IGNORECASE)
_STRUCTURAL_LEAK: Final = re.compile(
    r"(?:https?://|[\\/][^ ]|\b[\w.+-]+@[\w.-]+|\.[a-z0-9]{1,8}\b|"
    # Unambiguous executable names are command syntax. Ambiguous words require a
    # recognizable subcommand so ordinary prose such as "ready to go" survives.
    r"\b(?:git|gh|curl|uv|mise|npm|npx|pytest|ruff|mypy|pyright|tox|rustc|"
    r"dotnet|gradle|mvn|javac|rm|sudo|pip(?:x)?|poetry|pnpm|yarn|docker|podman|"
    r"kubectl|helm|terraform|tofu|ansible|aws|gcloud|az|wrangler|bazel|cmake|"
    r"ninja)\b|"
    r"\b(?:make|cargo|go|ruby|just)\s+(?:build|checks?|clean|deploy|fmt|format|"
    r"install|lint|publish|run|test|verify)\b|"
    r"\bjava\s+(?:-jar|--?[a-z0-9_-]+|(?-i:[A-Z][A-Za-z0-9_$]*))|"
    r"\b(?:gem\s+install|bundle\s+exec|env\s+(?:--?|[A-Z_][A-Z0-9_]*=))|"
    r"\b(?:sh|bash|zsh|fish|python(?:3)?|node|deno|bun)\s+(?:-|[\w./])|"
    r"[`\"]|\b[a-f0-9]{12,}\b|"
    r"\b[a-z0-9_-]{24,}\b)",
    re.IGNORECASE,
)
_WORD: Final = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*")
_CAMEL_BOUNDARY: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


class PrivacyError(ValueError):
    """Raised when data is not safe for storage or public display."""


def _compact(value: str, maximum: int) -> str:
    compact = " ".join(value.split())
    if not compact or len(compact) > maximum or not _SAFE_TEXT.fullmatch(compact):
        raise PrivacyError("public text is not bounded plain language")
    if _STRUCTURAL_LEAK.search(compact):
        raise PrivacyError("public text contains a structural or identifying leak")
    return compact


def validate_public_hint(value: str) -> str:
    """Accept an already-public broad professional hint."""
    return _compact(value, MAX_HINT_LENGTH)


def validate_message(value: str) -> str:
    """Validate publication structure without claiming semantic authority."""
    return _compact(value, MAX_MESSAGE_LENGTH)


def validate_candid_status(value: str, source_context: str) -> str:
    """Validate candid prose structurally and reject only source-identifier overlap."""
    status = validate_message(value)
    if _distinctive_source_forms(source_context) & _identifier_forms(status):
        raise PrivacyError("candid status overlaps distinctive source identifiers")
    return status


def safe_error_name(error: Exception) -> str:
    """Return an exception class without exposing its message."""
    return type(error).__name__


def _identifier_forms(value: str) -> set[str]:
    words = _WORD.findall(value)
    forms: set[str] = set()
    for width in range(1, min(3, len(words)) + 1):
        for start in range(len(words) - width + 1):
            forms.add(_normalize_identifier(" ".join(words[start : start + width])))
    return {form for form in forms if form}


def _distinctive_source_forms(source_context: str) -> set[str]:
    """Find identifiers whose spelling itself signals private source context."""
    forms: set[str] = set()
    for word in _WORD.findall(source_context):
        parts = _split_identifier(word)
        is_distinctive = (
            len(parts) > 1
            or "_" in word
            or "-" in word
            or any(character.isdigit() for character in word)
            or (word.isupper() and len(word) > 2)
        )
        if is_distinctive:
            normalized = _normalize_identifier(word)
            if normalized:
                forms.add(normalized)
            for width in range(2, len(parts) + 1):
                for start in range(len(parts) - width + 1):
                    forms.add("".join(parts[start : start + width]).casefold())
    return forms


def _normalize_identifier(value: str) -> str:
    return "".join(_split_identifier(value)).casefold()


def _split_identifier(value: str) -> list[str]:
    return [
        part
        for part in re.split(r"[^A-Za-z0-9]+", _CAMEL_BOUNDARY.sub(" ", value))
        if part
    ]
