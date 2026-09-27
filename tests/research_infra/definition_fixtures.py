from __future__ import annotations

import hashlib
import json
from typing import Any


def definition_record(kind: str, definition_id: str) -> dict[str, Any]:
    unsigned = {
        "schema_version": 1,
        "kind": kind,
        "definition_id": definition_id,
        "version": "1.0.0",
        "implementation_sha256": hashlib.sha256(f"test:{kind}:{definition_id}".encode()).hexdigest(),
        "config_schema": {"type": "object"},
        "description": "synthetic test definition",
    }
    unsigned["definition_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return unsigned
