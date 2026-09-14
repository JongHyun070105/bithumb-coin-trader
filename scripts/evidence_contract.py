"""Shared offline evidence digests. Delegates to evidence_hashing for canonical JSON."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from bithumb_coin_trader.evidence_hashing import (
        canonical_json_bytes,
        canonical_sha256,
        file_sha256,
    )
except ModuleNotFoundError:
    # fallback for direct script invocation without installed package
    import hashlib
    from collections.abc import Collection, Mapping

    def canonical_json_bytes(value: Mapping, excluded: Collection = ()) -> bytes:
        blocked = set(excluded)
        body = {k: v for k, v in value.items() if k not in blocked}
        return json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

    def canonical_sha256(value: Mapping, excluded: Collection = ()) -> str:
        import hashlib
        return hashlib.sha256(canonical_json_bytes(value, excluded)).hexdigest()

    def file_sha256(path) -> str:
        import hashlib
        h = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()

__all__ = ["canonical_json_bytes", "canonical_sha256", "file_sha256", "verify_contract", "verify_root_contract"]


def verify_contract(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('contract_sha256') != canonical_sha256(data, ('contract_sha256',)):
            raise ValueError('digest differs')
        return data
    except (ValueError, TypeError, OSError) as exc:
        raise ValueError(f'CONTRACT_HASH_MISMATCH: {exc}') from exc


def verify_root_contract(root, contract_path):
    contract = verify_contract(contract_path)
    if root.get('contract_sha256') != contract['contract_sha256']:
        raise ValueError('CONTRACT_HASH_MISMATCH: root upstream canonical digest differs')
    if root.get('contract_file_sha256') != file_sha256(contract_path):
        raise ValueError('CONTRACT_FILE_HASH_MISMATCH: root upstream file bytes differ')
    return contract
