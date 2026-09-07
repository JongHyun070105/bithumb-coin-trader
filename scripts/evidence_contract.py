"""Shared offline evidence digests. Canonical JSON uses Python ensure_ascii=True."""
import hashlib
import json
from pathlib import Path


def canonical_sha256(data, excluded=()):
    body = {k: v for k, v in data.items() if k not in excluded}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


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
