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
    ev = dict(
        schema_version=2,
        evidence_kind="systemd-transient-actual-start-evidence",
        collector_epoch=prov['collector_epoch'],
        collector_run_id=prov['collector_run_id'],
        runtime_commit=prov['runtime_code_commit'],
        runtime_config_fingerprint=prov['runtime_config_fingerprint'],
        actual_start_time_utc='2020-01-01T00:00:00Z',
        source='synthetic-test',
        captured_at_utc='2020-01-01T00:00:01Z'
    )
    (tmp_path / 'actual.json').write_text(json.dumps(ev))
    return ev

def compose(p):
    return compose_epoch_contract(p/'runtime_seal.json', p/'launch-provenance.json', p/'epoch_contract.json', p/'actual.json')

@pytest.mark.parametrize('field,code', [('collector_epoch','COLLECTOR_EPOCH'),('collector_run_id','COLLECTOR_RUN_ID'),('runtime_commit','RUNTIME_COMMIT'),('runtime_config_fingerprint','RUNTIME_CONFIG_FINGERPRINT')])
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

@pytest.mark.parametrize('mutation', ['body','self'])
def test_tampered_contract(tmp_path, mutation):
    from scripts.evidence_contract import verify_contract
    bundle(tmp_path); c = compose(tmp_path)
    assert verify_contract(tmp_path/'epoch_contract.json') == c
    c['duration_seconds' if mutation == 'body' else 'contract_sha256'] = 1
    (tmp_path/'epoch_contract.json').write_text(json.dumps(c))
    with pytest.raises(ValueError, match='CONTRACT_HASH_MISMATCH'):
        verify_contract(tmp_path/'epoch_contract.json')

@pytest.mark.parametrize('value', ['2020-01-01T00:00:00', 'nonsense', None])
def test_invalid_start_time(tmp_path, value):
    ev = bundle(tmp_path); ev['actual_start_time_utc'] = value
    (tmp_path/'actual.json').write_text(json.dumps(ev))
    with pytest.raises(ValueError, match='ACTUAL_START_TIMESTAMP_NOT_UTC'):
        compose(tmp_path)

def test_contract_byte_whitespace(tmp_path):
    from scripts.evidence_contract import verify_root_contract
    bundle(tmp_path); c = compose(tmp_path)
    m = build_epoch_manifest(tmp_path, tmp_path/'epoch_contract.json', strict=False)
    verify_root_contract(m, tmp_path/'epoch_contract.json')
    (tmp_path/'epoch_contract.json').write_text(json.dumps(c, separators=(',',':')))
    with pytest.raises(ValueError, match='CONTRACT_FILE_HASH_MISMATCH'):
        verify_root_contract(m, tmp_path/'epoch_contract.json')

@pytest.mark.parametrize('field', ['contract_sha256','contract_file_sha256'])
def test_root_reference_mutation(tmp_path, field):
    from scripts.evidence_contract import verify_root_contract, canonical_sha256
    bundle(tmp_path); compose(tmp_path)
    m = build_epoch_manifest(tmp_path, tmp_path/'epoch_contract.json', strict=False)
    old = m['epoch_manifest_sha256']; m[field] = '0'*64
    assert canonical_sha256(m, ('epoch_manifest_sha256',)) != old
    with pytest.raises(ValueError, match='CONTRACT_.*HASH_MISMATCH'):
        verify_root_contract(m, tmp_path/'epoch_contract.json')

def execute_literal_runbook(work):
    """Execute the documented command bytes, with only interpreter/location substitution."""
    from tests.test_phase6_runbook_e2e import _populate_official_shaped_epoch
    import shutil
    epoch = work/'epoch'
    _populate_official_shaped_epoch(epoch)
    (epoch/'contracts').mkdir()
    for src, dst in [('runtime_seal.json','runtime_seal.json'),('launch-provenance.json','launch-provenance.json'),('actual_start.evidence.json','actual_start.evidence.json')]:
        shutil.copyfile(epoch/src, epoch/'contracts'/dst)
    (work/'reports').mkdir(exist_ok=True)
    blocks = re.findall(r'```bash\n(.*?)\n```', (ROOT/'docs/POST_72H_OFFLINE_IMPORT_RUNBOOK.md').read_text(), re.S)
    env = {**os.environ,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT), 'EPOCH_DIR':'epoch', 'GIT_DIR':subprocess.check_output(['git','rev-parse','--absolute-git-dir'],cwd=ROOT,text=True).strip()}
    for block in blocks:
        # Scripts are resolved to the repository; evidence paths remain relative and portable.
        text = block.replace('python scripts/', shlex.quote(sys.executable)+' '+shlex.quote(str(ROOT/'scripts'))+'/').replace('python -m', shlex.quote(sys.executable)+' -m')
        proc = subprocess.run(['bash','-eu','-c',text],cwd=work,env=env,capture_output=True,text=True)
        assert proc.returncode == 0, proc.stdout+proc.stderr
    return work


def test_literal_runbook_subprocess(tmp_path):
    execute_literal_runbook(tmp_path)
    assert (tmp_path/'data/datasets/krw_btc_72h_v1/manifest.json').is_file()

@pytest.mark.parametrize('field,code',[('contract_sha256','CONTRACT_HASH_MISMATCH'),('contract_file_sha256','CONTRACT_FILE_HASH_MISMATCH')])
def test_deep_audit_rejects_broken_contract_edge(tmp_path,field,code):
    from tests.test_phase6_runbook_e2e import _populate_official_shaped_epoch
    from scripts.audit_72h_soak import SoakAuditor72H
    from scripts.evidence_contract import canonical_sha256
    _populate_official_shaped_epoch(tmp_path)
    cp=tmp_path/'epoch_contract.json'; mp=tmp_path/'manifests/epoch_manifest.json'
    root=build_epoch_manifest(tmp_path,cp,mp)
    root[field]='0'*64
    root['epoch_manifest_sha256']=canonical_sha256(root,('epoch_manifest_sha256',))
    mp.write_text(json.dumps(root))
    report=SoakAuditor72H(tmp_path,contract_path=cp,epoch_manifest_path=mp).audit()
    assert report['status']=='FAIL'
    assert any(code in b for b in report['blockers'])


def test_compose_v3_contract(tmp_path):
    bundle(tmp_path)
    prov = json.loads((tmp_path / "launch-provenance.json").read_text())
    prov["contract_type"] = "OFFICIAL_30H_V3_COVERAGE_CONTRACT"
    prov["schema_version"] = 2
    prov["duration_seconds"] = 111600
    prov["maximum_collection_window_seconds"] = 111600
    prov["required_qualifying_full_hours"] = 30
    (tmp_path / "launch-provenance.json").write_text(json.dumps(prov))

    contract = compose_epoch_contract(
        tmp_path / "runtime_seal.json",
        tmp_path / "launch-provenance.json",
        tmp_path / "v3_contract.json",
        tmp_path / "actual.json",
        schema_version=2,
    )
    assert contract["schema_version"] == 2
    assert contract["contract_type"] == "OFFICIAL_30H_V3_COVERAGE_CONTRACT"
    assert contract["required_qualifying_full_hours"] == 30
    assert contract["maximum_collection_window_seconds"] == 111600
    assert len(contract["candidate_cohorts"]) == 30
    assert contract["expected_coverage_slots_per_cohort"] == 76
    assert contract["qualification_start_utc"] == "2020-01-01T01:00:00Z"
    assert contract["qualification_end_utc"] == "2020-01-02T07:00:00Z"
    assert contract["require_coverage_receipts"] is True
    assert contract["require_state_dependent_fullscan"] is True

    from scripts.evidence_contract import verify_contract
    verified = verify_contract(tmp_path / "v3_contract.json")
    assert verified["contract_sha256"] == contract["contract_sha256"]


def test_v3_contract_rejects_108000_derived_end(tmp_path):
    bundle(tmp_path)
    prov = json.loads((tmp_path / "launch-provenance.json").read_text())
    prov["duration_seconds"] = 108000
    (tmp_path / "launch-provenance.json").write_text(json.dumps(prov))

    with pytest.raises(ValueError, match="V3_DERIVED_108000_END_FORBIDDEN"):
        compose_epoch_contract(
            tmp_path / "runtime_seal.json",
            tmp_path / "launch-provenance.json",
            tmp_path / "v3_contract.json",
            tmp_path / "actual.json",
            schema_version=2,
        )


def test_v3_contract_rejects_invalid_qualifying_hours(tmp_path):
    bundle(tmp_path)
    prov = json.loads((tmp_path / "launch-provenance.json").read_text())
    prov["required_qualifying_full_hours"] = 29
    (tmp_path / "launch-provenance.json").write_text(json.dumps(prov))

    with pytest.raises(ValueError, match="V3_QUALIFICATION_HOURS_INVALID"):
        compose_epoch_contract(
            tmp_path / "runtime_seal.json",
            tmp_path / "launch-provenance.json",
            tmp_path / "v3_contract.json",
            tmp_path / "actual.json",
            schema_version=2,
        )


def test_v3_contract_rejects_invalid_heartbeat_policy(tmp_path):
    bundle(tmp_path)
    seal = json.loads((tmp_path / "runtime_seal.json").read_text())
    seal["heartbeat_policy"] = {"max_allowed_heartbeat_gap_seconds": {"bithumb": -1, "binance": 30, "upbit": 30}}
    (tmp_path / "runtime_seal.json").write_text(json.dumps(seal))
    prov = json.loads((tmp_path / "launch-provenance.json").read_text())
    prov["runtime_config_seal_sha256"] = hashlib.sha256((tmp_path / "runtime_seal.json").read_bytes()).hexdigest()
    (tmp_path / "launch-provenance.json").write_text(json.dumps(prov))

    with pytest.raises(ValueError, match="INVALID_HEARTBEAT_POLICY"):
        compose_epoch_contract(
            tmp_path / "runtime_seal.json",
            tmp_path / "launch-provenance.json",
            tmp_path / "v3_contract.json",
            tmp_path / "actual.json",
            schema_version=2,
        )


def test_v3_build_epoch_manifest(tmp_path):
    from tests.test_archive_audit_coverage import v3_bundle
    from scripts.build_epoch_manifest import build_epoch_manifest, verify_epoch_manifest
    bundle = v3_bundle(tmp_path, present=74, zero=2, failed=0)
    contract = bundle["contract"]
    contract_p = tmp_path / "epoch_contract.json"
    contract_p.write_text(json.dumps(contract, indent=2))

    manifest_p = tmp_path / "manifests/epoch_manifest.json"
    manifest = build_epoch_manifest(
        epoch_dir=tmp_path,
        contract_path=contract_p,
        output_path=manifest_p,
        strict=False,
        mode="lenient",
    )
    assert manifest["schema_version"] == "3.0.0"
    assert manifest["coverage_slots_count"] == 76
    assert manifest["data_present_count"] == 74
    assert manifest["verified_zero_count"] == 2
    assert manifest["failed_count"] == 0
    assert manifest["status"] == "SEALED_COMPLETE"
    assert manifest["sealed_complete"] is True
    assert len(manifest["missing_items"]) == 0

    verified = verify_epoch_manifest(manifest_p, contract_p)
    assert verified["epoch_manifest_sha256"] == manifest["epoch_manifest_sha256"]
