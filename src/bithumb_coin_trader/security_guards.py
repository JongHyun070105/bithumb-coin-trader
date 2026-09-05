"""Security Sanitization, Path Traversal Defense, and Secret Leak Scanners (P21).

Provides:
- Path traversal defense: validates and bounds all file accesses strictly within base directory.
- Secret masking: automatically redacts AWS, exchange API keys, and private credentials.
- Secret scanning: detects accidental inclusions of credentials in text or files.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Sequence


class SecurityViolationError(ValueError):
    """Raised when a path traversal, injection, or security boundary violation occurs."""


SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_ACCESS_KEY]"),
    (re.compile(r"(?i)(bithumb|binance|upbit)_(api_key|secret|token)\s*[:=]\s*['\"][^'\"]+['\"]"), "[REDACTED_EXCHANGE_KEY]"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC )?PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(?i)aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{40}"), "aws_secret_access_key=[REDACTED]"),
]


def sanitize_path(target_path: Path | str, base_dir: Path | str) -> Path:
    """Sanitizes and bounds a path strictly within base_dir, preventing traversal attacks."""
    str_path = str(target_path)
    if "\0" in str_path:
        raise SecurityViolationError("Null byte injection detected in path")

    base = Path(base_dir).resolve()
    resolved = (base / target_path).resolve()

    # Check that resolved path is inside base
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise SecurityViolationError(
            f"Path traversal detected: {target_path} resolves outside {base}"
        ) from exc

    return resolved


def mask_secrets(text: str) -> str:
    """Redacts secrets from strings (e.g. before logging or writing to reports)."""
    result = text
    for pattern, replacement in SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def scan_for_secrets(text: str) -> list[str]:
    """Scans text and returns descriptions of any detected secrets."""
    detected = []
    for pattern, _ in SECRET_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            detected.append(f"Detected {len(matches)} match(es) for pattern {pattern.pattern}")
    return detected
