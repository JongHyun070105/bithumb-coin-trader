"""Generate metadata only from synthetic raw using the literal offline runbook.
Run: PYTHONPATH=src:. python3 dashboard/tests/generate_golden.py
No real data or holdout files are exported.
"""
import hashlib
import json
from pathlib import Path
import tempfile
from tests.test_phase6_3_contract import execute_literal_runbook

OUT = Path(__file__).resolve().parent/'golden'
FILES = {
 'runtime_seal':'epoch/contracts/runtime_seal.json',
 'launch_provenance':'epoch/contracts/launch-provenance.json',
 'actual_start_evidence':'epoch/contracts/actual_start.evidence.json',
 'epoch_contract':'epoch/contracts/epoch_contract.json',
 'epoch_manifest':'epoch/manifests/epoch_manifest.json',
 'deep_dq_report':'reports/deep_dq_audit_72h.json',
 'dq_qualification':'evidence/research/dq_qualification_72h.json',
 'canonical_manifest':'data/canonical_72h/canonical_manifest.json',
 'dataset_manifest':'data/datasets/krw_btc_72h_v1/manifest.json',
}
if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='phase63-synthetic-') as d:
        work = execute_literal_runbook(Path(d))
        hashes = {}
        for kind, source in FILES.items():
            data = (work/source).read_bytes()
            # Metadata must already be portable; never sanitize after hashing.
            assert d.encode() not in data, (kind, 'producer emitted temporary absolute path')
            (OUT/(kind+'.json')).write_bytes(data)
            hashes[kind] = hashlib.sha256(data).hexdigest()
        (OUT/'hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
