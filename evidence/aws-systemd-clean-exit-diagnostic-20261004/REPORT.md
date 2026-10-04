# Systemd clean-exit diagnostic — 2026-10-04

Host: EC2 i-008bc503c1136349f (existing approved host), Amazon Linux 2023, kernel 6.12.103, **systemd 252 (252.23-12.amzn2023)**.
Method: four transient units, production-like properties (`--collect` except case C, `--service-type=notify`, `--uid=bitcoin-trader`,
`Restart=no`, `KillMode=mixed`, `NotifyAccess=main`), name pattern `bitcoin-trader-30h-diag-sysd-<TS>-<case>.service`
(TS=20261004T035255Z). Not touching historical 30H evidence; the only other active unit was the completed run's observer (untouched).
Raw output (verbatim, captured before interpretation): `raw/<case>/`; sha256 index `RAW_SHA256SUMS.txt`.

| case | exit | unit_name suffix | InvocationID |
|---|---|---|---|
| A | 0, --collect | A_clean_collect | 9de836506acf4d9b85346559da9cb74e |
| B | 3, --collect | B_exit3_collect | 44a11a9960a74ce08defcea0a007ffbc |
| C | 0, no --collect | C_clean_nocollect | 75ff72b0fe204d3cb2fbc60b08a8a843 |
| E | 0, --collect, ExecStartPre echo | E_clean_wrapexit | fc14c8d2dfd141859923b6ca2576c77a |

## Observed facts
1. **Clean exit (A, C, E): systemd 252 emits NO `Main process exited, code=..., status=...` record.** The only terminal manager
   row is `<unit>: Deactivated successfully.` (CODE_FUNC unit_log_success).
2. **Nonzero exit (B)** emits `<unit>: Main process exited, code=exited, status=3/NOTIMPLEMENTED` then `Failed with result 'exit-code'.`.
3. **Manager rows** (Starting/Started/Deactivated/Main process exited): `_PID=1`, `_SYSTEMD_UNIT=init.scope`, target unit only in `UNIT=`,
   invocation only in `INVOCATION_ID=` (NOT `_SYSTEMD_INVOCATION_ID`). No `OBJECT_SYSTEMD_UNIT`/`USER_UNIT` rows exist.
4. **`journalctl _SYSTEMD_INVOCATION_ID=<id>` returns only unit-native rows** (stdout/stderr of ExecStartPre/main/ExecStopPost; 4-5 rows) and
   **zero manager rows**. `INVOCATION_ID=<id>` / `UNIT=<unit>` return only manager rows. `-u <unit>` returns the union.
5. **Unprivileged `journalctl` (ssm-user, no sudo) returns 0 rows** for every case ("not seeing messages from other users").
6. **Transient unit is garbage-collected after ExecStopPost** in every case (even failed B with `--collect`, and clean C without it):
   post-run `systemctl show` => `LoadState=not-found, InvocationID=<empty>, ExecMainCode=0, ExecMainStatus=0, Result=success` (defaults,
   which would be a false "clean" if trusted). Real values exist only **inside ExecStopPost**
   (`ActiveState=deactivating SubState=stop-post`, `ExecMainCode=1` numeric CLD_EXITED, `ExecMainStatus`, `Result`, `InvocationID`,
   `ExecMainStart/ExitTimestamp` at 1 s granularity, `MainPID=0`, `ExecMainPID=<pid>`).
7. ExecStopPost receives systemd-provided `EXIT_CODE=exited EXIT_STATUS=<n> SERVICE_RESULT=<result> INVOCATION_ID=<id>` (A: exited/0/success; B: exited/3/exit-code).
8. ExecStartPre stdout is journaled with `_SYSTEMD_INVOCATION_ID` and `_SYSTEMD_UNIT=<unit>` (case E).

## Frozen V2 `_audit_systemd` (scripts/audit_fresh_30h_terminal_v2.py:665-771) requirements that follow
* every row's `_SYSTEMD_UNIT` (preferred over `UNIT`) == run unit and invocation id == terminal InvocationID  => **no manager row can ever appear in `systemd-invocation.jsonl`** (L681-686);
* exactly one row whose MESSAGE matches `\bStarting\b` (L727-745) and exactly one matching `main process exited, code=X, status=N` (L732, L746-751), within 5 s / 2 s of the parsed start/stop times;
* parsed terminal fields (Result/ExecMainCode/ExecMainStatus/MainPID/NRestarts/times) must be real, not defaults.

## Classification
**SYSTEMD_TERMINAL_ROOT_CAUSE = MULTIPLE**
* AUDITOR_SYSTEMD_ASSUMPTION_MISMATCH: on this host a clean exit never produces the literal `main process exited` line, and the only place systemd would produce it (failure only) is a manager row that the same auditor forbids (`_SYSTEMD_UNIT=init.scope`). The literal fact can never come from the systemd manager for a clean run. Documented, not weakened; auditor untouched.
* CAPTURE_EXPORT_BUG: the historical 14.7 MB `systemd-invocation.jsonl` (10,974 rows, 10,793 `init.scope`) was a `-u`-style union, contradicting its own invocation/unit scoping => FAIL "not bound to the exact run-specific collector unit". The fe0183f capture script assumes manager rows are retained under `_SYSTEMD_INVOCATION_ID=` (false, fact 4) and reads `systemctl show` post-run (impossible after GC, fact 6).
* RUNTIME_EVIDENCE_MISSING: no runtime-time snapshot of systemd properties, no unit-native start/terminal markers existed in the previous run.

## What future-runtime native evidence can satisfy the intended terminal fact (without editing the auditor)
Intended fact = "this exact unit invocation started once and its main process exited with code=exited status=0, Result=success, 0 restarts".
Systemd itself supplies that fact to the unit during ExecStopPost (`EXIT_CODE`, `EXIT_STATUS`, `SERVICE_RESULT`, and a live `systemctl show` while the unit still exists).
An ExecStartPre / first ExecStopPost step of the unit may therefore journal, **from inside the unit's own cgroup and invocation** (unit-native rows),
exactly one `Starting <unit>` marker and exactly one `main process exited, code=<EXIT_CODE>, status=<EXIT_STATUS>` marker built only from systemd-provided values and cross-checked against the live `systemctl show` snapshot. This is a unit-side attestation of systemd-supplied values, not a manager log line; it is declared as such and never rewrites historical runs (f5edeed0 remains FAIL / NOT_VERIFIABLE).

## Addendum: native-evidence implementation verified on the host (2026-10-04)

Raw: `raw/N_newcap_native_hooks/` (clean + exit3 transient units on the approved EC2 host, systemd 252.23).
The new `scripts/capture_fresh_30h_systemd.py` (ExecStartPre `start-marker`, first ExecStopPost `stop-post`, post-run `export`) was run unchanged.

OBSERVED:
- Hook stdout rows are unit-native (`_SYSTEMD_UNIT=<unit>`, `_SYSTEMD_INVOCATION_ID=<id>`); the invocation journal held exactly 3 rows per case: one `Starting <unit>`, the supervised process' own INFO line, one `main process exited, code=exited, status=N`.
- `systemctl show --timestamp=us+utc` returns microsecond UTC timestamps on this host (whole-second floors would have sorted before the supervisor's own microsecond `ended_at`, tripping `systemd_stop < supervisor_end` in `_audit_duration`).
- Replaying both cases through the UNCHANGED frozen `AuditorV2._audit_systemd`: clean exit -> PASS; exit status 3 -> FAIL (`systemd_terminal_not_clean`). The nonzero case is not laundered into PASS.

Limits (inference, not observed): the `Starting` / `main process exited` rows are written by scripts the run controls; they are cross-checked against live `systemctl show` and the systemd-provided EXIT_CODE/EXIT_STATUS/SERVICE_RESULT/INVOCATION_ID but are not an independent manager record. The historical f5edeed run result (systemd_terminal FAIL) is unchanged and remains authoritative for that run.
