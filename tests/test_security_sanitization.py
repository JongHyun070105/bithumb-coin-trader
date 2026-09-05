import pytest
from pathlib import Path
from bithumb_coin_trader.security_guards import (
    sanitize_path,
    mask_secrets,
    scan_for_secrets,
    SecurityViolationError,
)


def test_path_sanitization_valid(tmp_path):
    safe = sanitize_path("data/sub/file.txt", tmp_path)
    assert safe == (tmp_path / "data/sub/file.txt").resolve()


def test_path_sanitization_traversal_blocked(tmp_path):
    with pytest.raises(SecurityViolationError, match="Path traversal detected"):
        sanitize_path("../../etc/passwd", tmp_path)

    with pytest.raises(SecurityViolationError, match="Null byte"):
        sanitize_path("file.txt\0.png", tmp_path)


def test_secret_masking():
    raw_log = "Error connecting with AKIAIOSFODNN7EXAMPLE and bithumb_secret='super_secret_key'"
    masked = mask_secrets(raw_log)
    assert "AKIAIOSFODNN7EXAMPLE" not in masked
    assert "[REDACTED_AWS_ACCESS_KEY]" in masked
    assert "super_secret_key" not in masked


def test_scan_for_secrets():
    clean_text = "Standard log message: all services running normally."
    assert len(scan_for_secrets(clean_text)) == 0

    dirty_text = "Found AKIA1111222233334444 in config"
    findings = scan_for_secrets(dirty_text)
    assert len(findings) > 0
