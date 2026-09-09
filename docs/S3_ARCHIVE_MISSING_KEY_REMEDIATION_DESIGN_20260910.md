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

`S3ArchiveStore.upload()` already sends `IfNoneMatch="*"`. It accepts only the documented conflict outcomes (HTTP 409/412 or `ConditionalRequestConflict`/`PreconditionFailed`) as evidence that another immutable writer won; every other error is re-raised. It always follows the write or accepted conflict with `HeadObject(ChecksumMode="ENABLED")`.

- Least privilege: preserves only object-level `s3:GetObject` and `s3:PutObject`.
- Race safety: stronger than the probe because S3 evaluates the write precondition atomically. There is no separate existence decision to race.
- Immutable-key semantics: a present object is never overwritten by the S3 adapter.
- Existing-object reuse: 409/412 is followed by authoritative `HeadObject`; the pipeline then accepts only exact size and SHA-256 equality.
- Wrong-object rejection: `_verify_remote()` fails closed on size mismatch, unavailable checksum, or checksum mismatch.
- Error observability: real authorization failures from `PutObject` or the required post-write/post-conflict `HeadObject` propagate and are recorded at the last successful receipt state.
- AWS permissions: `s3:PutObject` authorizes the conditional creation; `s3:GetObject` authorizes both `HeadObject` verification and streamed restore. `s3:ListBucket` is unnecessary.

## Decision

Choose option B.

The production change is limited to making `_upload_or_reuse()` invoke the store's create-or-reuse `upload()` operation directly. `exists()` remains available because it is public adapter surface and removing it is unrelated cleanup.

The `ArchiveStore.upload()` implementations must continue to honor create-or-reuse behavior:

- S3: immutable conditional `PutObject`, accepted race conflict, authoritative `HeadObject`.
- File: reuse an existing destination and return its metadata.
- Memory: must reuse an existing key rather than overwrite it so deterministic tests model the same archive-store contract.

No IAM or Terraform change is part of this remediation.

## Required regression proof

Before production changes, tests must fail against the current preflight behavior for an absent S3 object whose missing-key `HeadObject` would be 403 while conditional `PutObject` is allowed. Additional tests must prove identical-object reuse, wrong-object rejection, conditional-write race handling, and propagation of access denial from the authoritative post-write `HeadObject`.

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
