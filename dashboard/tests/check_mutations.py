"""Temporary source mutations; restore every byte in finally, require tests to fail."""
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PARSER=ROOT/'src/evidence/artifactClassifier.ts'
CHAIN=ROOT/'src/evidence/chainEvaluator.ts'
mutations=[
 ('self_hash',PARSER,lambda s:s.replace('a.calculatedSelfSha256 !== j[self]','false')),
 ('degraded_gate',CHAIN,lambda s:s.replace("['hard_fail_count', 'unknown_count', 'degraded_count']", "['hard_fail_count', 'unknown_count']")),
 ('duplicate_detection',CHAIN,lambda s:s.replace('.filter(([, v]) => v.length > 1)', '.filter(() => false)')),
 ('actual_start_run_identity',CHAIN,lambda s:s.replace("'collector_run_id'", "'collector_epoch'").replace('j(start).collector_run_id','j(start).collector_epoch').replace('j(launch).collector_run_id','j(launch).collector_epoch')),
 ('root_contract_reference',CHAIN,lambda s:re.sub(r'differs\(\s*j\(root\).contract_sha256,\s*j\(contract\).contract_sha256,?\s*\)', 'false',s)),
 ('canonical_root_reference',CHAIN,lambda s:re.sub(r'differs\(\s*j\(canonical\).source_epoch_manifest_sha256,\s*j\(root\).epoch_manifest_sha256,?\s*\)', 'false',s)),
]
for name,path,mutate in mutations:
    old=path.read_text();new=mutate(old)
    assert new!=old, (name,'mutation did not apply')
    try:
        path.write_text(new)
        p=subprocess.run(['npm','test','--','src/evidence/golden.test.ts'],cwd=ROOT,capture_output=True,text=True)
        assert p.returncode!=0 and 'AssertionError' in p.stdout+p.stderr, (name,p.stdout,p.stderr)
        print(name+': KILLED (assertion failure)')
    finally:
        path.write_text(old)
