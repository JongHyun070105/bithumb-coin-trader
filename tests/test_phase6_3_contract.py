"""Synthetic regressions for the Phase 6.3 authoritative contract."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest
from scripts.compose_epoch_contract import compose_epoch_contract
from scripts.build_epoch_manifest import build_epoch_manifest

ROOT = Path(__file__).resolve().parents[1]

def bundle(tmp_path):
    for name, target in [('runtime.json', 'runtime_seal.json'), ('launch-provenance.json', 'launch-provenance.json')]:
        src = ROOT / 'infra/aws/seals' / ('aws-72h-soak-20260905.' + name)
        (tmp_path / target).write_bytes(src.read_bytes())
    prov = json.loads((tmp_path / 'launch-provenance.json').read_text())
    ev = dict(schema_version=1, collector_epoch=prov['collector_epoch'], collector_run_id=prov['collector_run_id'], runtime_commit=prov['runtime_code_commit'], runtime_fingerprint=prov['runtime_config_fingerprint'], actual_start_time_utc='2020-01-01T00:00:00Z', start_evidence_type='PROCESS_EXEC_START', source='synthetic-test', captured_at_utc='2020-01-01T00:00:01Z')
    (tmp_path / 'actual.json').write_text(json.dumps(ev))
    return ev

def compose(p):
    return compose_epoch_contract(p/'runtime_seal.json', p/'launch-provenance.json', p/'epoch_contract.json', p/'actual.json')

@pytest.mark.parametrize('field,code', [('collector_epoch','EPOCH'),('collector_run_id','RUN_ID'),('runtime_commit','RUNTIME_COMMIT'),('runtime_fingerprint','RUNTIME_FINGERPRINT')])
def test_wrong_actual_identity(tmp_path, field, code):
    ev = bundle(tmp_path); ev[field] = 'wrong'
    (tmp_path/'actual.json').write_text(json.dumps(ev))
    with pytest.raises(ValueError, match='ACTUAL_START_'+code+'_MISMATCH'):
        compose(tmp_path)

def test_root_binds_contract(tmp_path):
    bundle(tmp_path); c = compose(tmp_path)
    m = build_epoch_manifest(tmp_path, tmp_path/'epoch_contract.json', strict=False)
    assert m['contract_sha256'] == c['contract_sha256']
    assert m['contract_file_sha256'] == hashlib.sha256((tmp_path/'epoch_contract.json').read_bytes()).hexdigest()

def test_stale_document_identity():
    doc = (ROOT/'docs/PROJECT_STATUS.md').read_text().split('## 3.')[1].split('## 4.')[0]
    assert 'e9e4be4db086706e57ba51c14a2432a106526fc8' not in doc
    assert 'epoch-aws-72h-soak-20260905' not in doc

def test_literal_runbook_flags():
    blocks = re.findall(r'```bash\n(.*?)\n```', (ROOT/'docs/POST_72H_OFFLINE_IMPORT_RUNBOOK.md').read_text(), re.S)
    checked = 0
    for block in blocks:
        for line in block.replace('\\\n', ' ').splitlines():
            if not line.startswith('python '): continue
            argv = shlex.split(line)[1:]
            prefix = argv[:3] if argv[0] == '-m' else argv[:1]
            result = subprocess.run([sys.executable, *prefix, '--help'], capture_output=True, text=True, env={**os.environ, 'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT)}, cwd=ROOT)
            assert result.returncode == 0, result.stderr
            for flag in re.findall(r'(?<!\w)--[\w-]+', line):
                assert re.search(re.escape(flag)+r'(?=[\s,=\]])', result.stdout), (prefix, flag)
            checked += 1
    assert checked == 7
