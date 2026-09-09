# AWS 45M validation addendum (2026-09-09)

This addendum preserves the original failed run and corrects reporting errors without editing its raw evidence, receipts, manifests, or original report.

## Corrections

1. The authoritative feed universe was **Bithumb 60, Binance 8, Upbit 8; total 76**. The later Gemini summary's `Binance 36 / Bithumb 32 / Upbit 8` statement was a documentation error. The runtime seal declares 20 Bithumb markets, 4 Binance symbols, and 4 Upbit markets; the 76 receipts and both sets of 76 manifests have the authoritative `60 / 8 / 8` distribution.
2. No S3 object was uploaded for the new epoch. `ArchivePipeline._upload_or_reuse()` calls `S3ArchiveStore.exists()`, which performs `HeadObject`, before `PutObject`. The new-prefix `HeadObject` was denied, so the upload was never attempted. `failure_stage=COMPRESSED_VERIFIED` records the last completed local state, not a successful remote write.
3. The full evidence commit previously reported as `270dbe38bfd2c0ec222aaaeeb5e921d74659b85c` does not exist. The validation branch's actual evidence commit is `270dbe3fad7561455b5210c15d77d5aa92764813`.

## Verdict preserved

- 45M PROCESS: PASS
- 45M ARCHIVE: FAIL
- 45M EVIDENCE CONTRACT: FAIL
- AWS 45M OVERALL: FAIL

The failed epoch and run ID remain immutable and are not eligible for repair or reuse.
