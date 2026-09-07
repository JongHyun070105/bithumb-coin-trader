# Phase 6.3 — offline evidence contract and dashboard reconciliation

Base main: `4b46c9ed47afb67dd74e742ce1acf455508b2f6e`.
Gemini input: `6cdea3c996023312add142445832be168d7f45cf` (direct child of base).
Old aws-45m-remediation context is superseded. This change is repository/offline only.

## Scientific state (unchanged)

72H: final result pending; real audit and real DQ: NOT RUN; actual-start evidence:
not ingested. Alpha UNPROVEN, paper NOT STARTED, live/private API DISABLED.
The synthetic fixtures prove tooling behavior, not real feed completeness or alpha.
Next scientific event: natural completion followed by authoritative exported evidence.

## Reproduced before implementation

- Python: seven failures on Gemini input: root lacks contract digest, four foreign
  actual-start identities accepted, stale document identities, unsupported compose flag.
- Dashboard: thirteen failures on untouched Gemini: wrong root-contract reference,
  forged root self hash, FAIL with empty blockers, DQ_PASS degraded/unknown,
  wrong audit digest, three filename-only classifications, duplicate roots and
  qualifications, provenance run mismatch, presence-only COMPLETE.
- Additional regression: software SHA fallback failed all eleven tested lengths
  (0/1/3/55/56/63/64/65/119/120/1000); fixed SHA-256 padding.
- Additional regression: a supplied broken root-contract edge was only a warning
  in lenient audit mode and could produce DQ_PASS_ELIGIBLE. Both digest mutations
  now cause FAIL with the corresponding contract error token.

## Authoritative vocabulary and artifact matrix

Canonical self SHA means SHA256 over UTF-8 of Python `json.dumps` with sorted keys,
compact separators and default `ensure_ascii=True`, excluding the listed self fields.
File byte SHA means the exact file bytes, including whitespace/newlines. An upstream
reference explicitly identifies which of these two digest types it expects.

| Artifact | Producer / version | Self SHA | Upstream fields / digest type | Dashboard validation |
|---|---|---|---|---|
| runtime seal | tracked runtime JSON, schema 1 | none | none | field/schema validation; imported byte digest |
| launch provenance | tracked launch JSON, schema 1; synthetic helper | none | runtime_config_seal_sha256: seal bytes when present | commit/run/fingerprint and available seal reference |
| actual start | externally captured artifact; compose validates schema 1 | none | epoch/run/commit/fingerprint identity | exact normalized schema, aware timestamps, run identity |
| epoch contract | scripts/compose_epoch_contract.py / compose_epoch_contract, schema 1 | contract_sha256; exclude itself | runtime_seal_sha256, launch_provenance_sha256, actual_start_evidence_file_sha256: bytes | self SHA plus three file references and identity |
| epoch root | scripts/build_epoch_manifest.py / build_epoch_manifest, schema 2.1.0 | epoch_manifest_sha256; exclude itself | contract_sha256: canonical; contract_file_sha256: bytes; seal/launch: bytes | self SHA, both contract digests, identity, sealed status |
| deep DQ | scripts/audit_72h_soak.py / SoakAuditor72H.audit; audit_type authoritative_deep_dq, no schema_version | none | epoch_manifest_sha256: canonical | DQ_PASS_ELIGIBLE, blockers/errors/warnings, root reference |
| qualification | research_cli.py / cmd_dq_qualify; v9.1.0-offline / v1-strict / strict_v1 | qualification_sha256 == report_hash; exclude both | audit_report_sha256: bytes; epoch_manifest_sha256/source_manifest_hash: canonical; source_manifest_file_sha256: bytes | self SHA, policy/version, all three zero counters, upstream references |
| canonical root | research_cli.py / cmd_transform_canonical, schema 2.1.0 | canonical_manifest_sha256; exclude itself | source_epoch_manifest_sha256 and dq_qualification_sha256: canonical | self SHA and both references |
| dataset | research_cli.py / cmd_partition_dataset; no schema_version | no manifest self hash; dataset_id is not one | epoch/canonical/qualification: canonical; deep_dq_report_sha256: bytes; four source identity fields | metadata structure and upstream references; no partition content |
| archive receipt | pre_soak_archive.py / ArchiveReceipt.to_dict, schema 1 | none | partition-specific raw/compressed digests | ancillary schema only; no archive/restore success inference |
| fullscan | orchestrate_closed_hour_archive.py report envelope; audit_raw_integrity_offline.full_scan | none | integrity totals/failures | ancillary schema only; no re-scan claim |
| lifecycle/result | external post-soak evidence | unsupported | not inferred | no 72H completion promotion |

The core nine artifact types have exact Python-produced golden files in
`dashboard/tests/golden/`. Receipt/fullscan recognition does not qualify the chain.
No file name grants trust. Unsupported versions and malformed known schemas remain
RECOGNIZED_INVALID; unknown content remains UNKNOWN. Authoritative duplicates are
AMBIGUOUS_EVIDENCE; no last-one-wins behavior. Missing identity never invents a start.

## Deliberate verification limits

The strongest global result is STRUCTURALLY COMPLETE: supported metadata self hashes
and references match. This is not a signature/authenticity check, RAW verification,
full partition verification, dataset-content verification, real DQ rerun, or 72H verdict.
Unkeyed self hashes do not identify who produced an artifact. Official scientific
status stays unchanged even if an imported metadata chain matches.

The browser canonicalizer retains integer tokens (including integers beyond JS safe
precision), distinguishes int/float, preserves list order and uses Python Unicode
key ordering/ASCII escaping. Float support is deliberately limited to the exact
canonical Python spelling accepted by the verifier; alternate spellings such as
1.00 fail closed instead of guessing a digest. Python-generated Unicode/float/large
integer vectors exercise this boundary. No canonical digest is invented for byte-only
artifacts. UTF-8 BOM and malformed UTF-8 are rejected. The 10 MiB guard runs before
file reads; nested JSON over 64 levels and duplicate JSON keys are rejected.

## Runbook and reproducibility

`tests/test_phase6_3_contract.py` extracts all seven documented commands, validates
literal flags, and executes the entire sequence in a temporary synthetic bundle.
Only the Python executable and repository script location are substituted; evidence
paths and commands are those in the document. GIT_DIR supplies the actual repository
commit for producer provenance. No RAW/holdout output is exported to the dashboard.

Regenerate: `PYTHONPATH=src:. python3 dashboard/tests/generate_golden.py`.
Fixture timestamps are generation time; regeneration intentionally updates file and
self digests. `hashes.json` freezes the exact producer file-byte digests. No post-hash
path sanitization occurs. Test-only vectors are separate from the artifact bundle.

Mutation sensitivity: `python3 dashboard/tests/check_mutations.py` temporarily disables
self-hash comparison, degraded gate, duplicate detection, actual-start run identity,
root-contract reference, canonical-root reference. All six must cause assertion failures;
every source file is restored byte-for-byte in a finally block.

## Scope and safety

Changes are offline evidence scripts/shared digest helper, synthetic tests, authoritative
and dashboard documentation, and dashboard source/fixtures. No collector, archive runtime,
strategy, risk engine, order transport, Terraform, IAM, SG or storage configuration changes.
No AWS/runtime/market/private API calls. No tracked GitHub workflow/auto-deploy configuration
exists in the reviewed tree. Git operations and npm development tooling are the only
external development-service access; the built dashboard has no external runtime client.

## Review-branch verification

- Full Python: **942 passed, 2 skipped, 128 subtests passed**. Skips are existing tests.
- Targeted Phase 6.3 plus stepwise coverage: **19 passed**; literal runbook subprocess included.
- `compileall` for src/scripts/tests/dashboard fixture scripts: PASS.
- Dashboard typecheck, oxlint, production build: PASS; Vitest: **84 passed**.
- Six temporary protection mutations: all killed by assertion failures and restored.
- Changed-file scan: no credential pattern, personal absolute path, unexpected binary,
  external dashboard runtime client, or unauthorized runtime/infra modification found.
  This is a pattern/scope scan, not a claim of a comprehensive secret-history audit.
- Local browser: nine-file import and same-type duplicate rejection PASS. Desktop width
  1440 has no document overflow; mobile width 390 has no document overflow. Narrow-screen
  chain rows and banner wrapping were repaired. Mobile navigation was exercised.
- Main integration requires rerunning both suites on the actual no-ff merged tree.
