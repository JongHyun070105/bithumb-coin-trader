# S3 archive missing-key remediation design (2026-09-10)

## Problem statement

`ArchivePipeline._upload_or_reuse()` currently probes `store.exists(key)` before calling `store.upload(...)`. For `S3ArchiveStore`, `exists()` is a `HeadObject` request. An absent key returns 403, rather than 404, when the caller has object `GetObject`/`PutObject` but lacks bucket `ListBucket`. The probe therefore blocks creation even though the caller is authorized to perform the required conditional `PutObject`.

The intended security contract is immutable create-or-reuse under the temporary validation object namespace, followed by authoritative metadata and byte-level restore verification.

## Option A: add prefix-conditioned ListBucket

This would allow S3 to reveal absence as 404 and would preserve the current pipeline control flow.

- Least privilege: broader than the data-plane contract because it adds bucket enumeration solely to support a preliminary probe.
- Race safety: unchanged but still subject to a time-of-check/time-of-use window between `HeadObject` and `PutObject`.
- Immutable-key semantics: ultimately provided by the later conditional `PutObject`, not by `ListBucket` or the probe.
- Existing-object reuse: works, but requires two `HeadObject` calls for existing objects and one preflight `HeadObject` plus one post-write `HeadObject` for new objects.
- Wrong-object rejection: remains in `_verify_remote()`.
- Error observability: distinguishes absence at the probe, but adds a permission whose only purpose is interpreting that probe.
- AWS permissions: requires `s3:ListBucket` on the bucket with an `s3:prefix` condition in both the role policy and permissions boundary.

## Option B: use conditional PutObject as create-or-reuse

`S3ArchiveStore.upload()` sends `IfNoneMatch="*"`. Only the exact response pair HTTP 412 plus `Error.Code=PreconditionFailed` establishes that a current object prevented the conditional write, so it proceeds to authoritative `HeadObject(ChecksumMode="ENABLED")` for normal verification/reuse. Only the exact pair HTTP 409 plus `Error.Code=ConditionalRequestConflict` is retried as another conditional `PutObject`. A status-only or code-only match is insufficient; every incomplete, malformed, or mismatched response pair is re-raised fail-closed without retry or `HeadObject`.

- Least privilege: preserves only object-level `s3:GetObject` and `s3:PutObject`.
- Race safety: stronger than the probe because S3 evaluates the write precondition atomically. There is no separate existence decision to race.
- Immutable-key semantics: a present object is never overwritten by the S3 adapter.
- Existing-object reuse: 412 is followed by authoritative `HeadObject`; the pipeline then accepts only exact size and SHA-256 equality.
- Conditional conflicts: 409 is retried with a newly opened local stream and the same content length, SHA-256 checksum, and `IfNoneMatch="*"`. The retry bound is three total conditional-PUT attempts. Exhaustion re-raises the conflict without using `HeadObject` to fabricate reuse.
- Wrong-object rejection: `_verify_remote()` fails closed on size mismatch, unavailable checksum, or checksum mismatch.
- Error observability: real authorization failures from `PutObject` or the required post-write/post-conflict `HeadObject` propagate and are recorded at the last successful receipt state.
- AWS permissions: `s3:PutObject` authorizes the conditional creation; `s3:GetObject` authorizes both `HeadObject` verification and streamed restore. `s3:ListBucket` is unnecessary.

## Decision

Choose option B.

The production change is limited to making `_upload_or_reuse()` invoke the store's create-or-reuse `upload()` operation directly. `exists()` remains available because it is public adapter surface and removing it is unrelated cleanup.

The `ArchiveStore.upload()` implementations must continue to honor create-or-reuse behavior:

- S3: immutable conditional `PutObject`; bounded retry after 409; authoritative `HeadObject` after success or 412 only.
- File: reuse an existing destination and return its metadata.
- Memory: must reuse an existing key rather than overwrite it so deterministic tests model the same archive-store contract.

No IAM or Terraform change is part of this remediation.

## Required regression proof

Before production changes, tests must fail against the current preflight behavior for an absent S3 object whose missing-key `HeadObject` would be 403 while conditional `PutObject` is allowed. Additional tests must prove identical-object reuse, wrong-object rejection, 409-to-success retry with a fresh stream, repeated-409 exhaustion, 409-to-412 convergence, non-retryable error propagation, and propagation of access denial from the authoritative post-write `HeadObject`.

## NO_EPOCH_MANIFEST classification

`epoch_manifest.json` is a post-run evidence-chain artifact produced by `build_epoch_manifest.py`; the collector and archive scheduler are not its producers. The authoritative sequence is actual-start evidence -> `compose_epoch_contract.py` -> `build_epoch_manifest.py --strict` -> `audit_72h_soak.py` with the resulting root.

The failed 45-minute run did not execute that root-build step. Its 76 FAILED receipts and missing fullscan also make a `SEALED_COMPLETE` root impossible: the builder requires a restore-verified receipt for every archive-eligible cohort and the official composed contract requires fullscan evidence. The strict builder would report an incomplete epoch rather than create a qualifying root.

Classification:

- EXPECTED GENERATION PHASE: POST-RUN
- WAS THE REQUIRED BUILD STEP EXECUTED: NO
- NO_EPOCH_MANIFEST: EXPECTED BECAUSE ARCHIVE FAILED

It is not a collector runtime bug and is not an independent cause of the archive failure. No manifest will be manufactured for the historical failed run. For a future potentially qualifying validation, the post-run sequence must build and verify the sealed root before invoking the authoritative audit.

## Verification record

TDD RED against the original production path:

- 4 failed, 1 passed across the five new S3 boundary cases.
- The absent-object, identical-object, conditional-race, and post-write-head cases failed because the preflight path made zero conditional `PutObject` calls; the wrong-object case already failed closed on size mismatch.

GREEN after the minimal change:

- New S3 boundary cases: 5 passed.
- Archive store and pipeline tests: 34 passed.
- Archive, scheduler, fullscan, policy, and evidence-chain target set: 165 passed.
- Full Python suite: 1007 passed, 2 skipped, 133 subtests passed.

Independent review then identified that 409 and 412 had been treated as equivalent. Follow-up strict TDD produced the expected RED result before the retry implementation:

- `409 -> success`, repeated 409, and `409 -> 412`: 3 failed because the first 409 was followed by `HeadObject` instead of another conditional `PutObject`.
- The test double created no object on 409, exposing the no-`ListBucket` failure mode rather than masking it.

GREEN after the review remediation:

- S3 adapter plus archive pipeline: 39 passed.
- `test_pre_soak_archive.py`: 31 passed.
- Archive, scheduler, fullscan, policy, and remediation target set: 204 passed, 5 subtests passed.
- Full Python suite: 1012 passed, 2 skipped, 133 subtests passed.

A second independent review identified that the implementation matched status and error code independently. Exact-pair regression tests produced the expected RED result before correction:

- 409/`AccessDenied` and 500/`ConditionalRequestConflict` each made three PUT attempts instead of one.
- 412/`AccessDenied` and 500/`PreconditionFailed` were incorrectly converted into verified reuse.
- An exact 409 followed by 500/`ConditionalRequestConflict` continued to a third PUT instead of stopping at the second response.

The minimal correction compares the complete `(HTTPStatusCode, Error.Code)` tuple. Only `(409, ConditionalRequestConflict)` retries and only `(412, PreconditionFailed)` verifies/reuses; every other pair propagates immediately.

GREEN after exact-pair correction:

- Exact-pair matrix and positive-path selection: 9 passed.
- S3 adapter boundary tests: 13 passed.
- `test_pre_soak_archive.py`: 31 passed.
- Archive, scheduler, fullscan, policy, and remediation target set: 209 passed, 5 subtests passed.
- Full Python suite: 1017 passed, 2 skipped, 133 subtests passed.
- `compileall` and `git diff --check`: PASS.

A third independent review identified that malformed exception-response containers could replace the original S3 error with `AttributeError` while the adapter inspected nested `.get()` values. Strict TDD covered `response=None`, an empty response mapping, null and non-mapping metadata/error payloads, missing status/code fields, and exact 409 followed by malformed response. The implementation now validates each response layer as a mapping and uses bare `raise` for every malformed or incomplete structure, preserving the original exception object and traceback.

GREEN after malformed-response correction:

- Malformed-response matrix: 9 passed, 6 subtests passed.
- Exact-pair and positive-path selection: 9 passed.
- S3 adapter boundary tests: 22 passed, 6 subtests passed.
- `test_pre_soak_archive.py`: 31 passed.
- Archive, scheduler, fullscan, policy, and remediation target set: 218 passed, 11 subtests passed.
- Full Python suite: 1026 passed, 2 skipped, 139 subtests passed.
- Pyright on the two changed test files: 0 errors, 0 warnings, 0 information messages. A combined production/test invocation still reports six pre-existing production diagnostics unrelated to this correction.
- `compileall` and `git diff --check`: PASS.

Static and live read-only checks:

- Terraform format and validation: PASS.
- IAM/Access Analyzer findings: 0.
- Live role inline policy and boundary v6 validation scopes: MATCH.
- Validation-prefix `GetObject`/`PutObject`: ALLOW.
- Canonical, old-72H, unrelated temporary, and other-bucket object access: DENY.
- `DeleteObject` and `ListBucket`: DENY.
- Private API disabled; public-data-only and cleanup-disabled runtime invariants preserved.
- Explicit authoritative-state Terraform plan: 0 add, 0 change, 0 destroy; lineage `5e8ff4b0-1d32-ed2e-9ed0-a9e68d3f3ccf`, serial 51, 27 state resource objects.

No AWS write, IAM change, Terraform apply, collector launch, archive retry, or historical evidence mutation was performed.
