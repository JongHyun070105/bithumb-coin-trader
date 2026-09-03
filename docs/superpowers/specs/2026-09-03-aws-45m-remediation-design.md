# AWS 45-Minute Short-Smoke Failure Remediation Design

## Scope

Remediate three source-level failures from the failed AWS short smoke without running Terraform, changing IAM/network/storage infrastructure, writing production archives, or starting another collector run. The failed epoch `aws-short-smoke-20260902-38cb8a72` and its run ID remain immutable failed evidence.

## Binance diagnostic and decision boundary

Add a public-data-only diagnostic that measures DNS resolution, every IPv4/IPv6 candidate, TCP connect, TLS completion, and WebSocket upgrade independently for BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT. It runs both normal `websockets` proxy auto-detection and explicit `proxy=None` direct mode. Proxy reporting contains only variable names, boolean presence, sanitized scheme/host/port metadata, and sanitized `urllib.request.getproxies()` output; credentials and raw proxy URLs are forbidden.

The diagnostic preserves the production URL and port. It records exact stages and exception classes/messages so an AWS-only transport failure can be distinguished from an application failure. No production timeout or endpoint change is permitted until the same diagnostic is run locally and on the guest. A collector-source change is allowed only when those results prove it is needed.

## Writable partition lifecycle

`RawMicrostructureStorage.append_raw_record` opens a path only for the append and closes it before returning. Therefore the collector has no persistent file handles: after an append completes, no file is literally open or writable by an existing handle. The operational meaning of `active_partition_files` is consequently the set of current-hour paths selected for feeds that may receive another write while the collector is accepting events. Old-hour paths are never active, including idle feeds, because a future append selects the current UTC hour rather than reusing the old path.

The collector tracks the latest path per `(exchange, stream, market)` and derives the exported active set by retaining only paths in the injected clock's current UTC hour while it is accepting writes. On hour rollover the derived set drops every previous-hour path immediately, whether or not that feed has emitted a new event. Shutdown marks writes closed after queue drain and before final metrics persistence, making the active set empty. Manifest generation uses all touched paths so closed partitions remain finalizable even after leaving the active set.

## Bounded supervisor and systemd launch

A single Python supervisor owns the collector subprocess and optional publisher subprocess for one sealed run. It uses an exact 2700-second monotonic deadline, starts the publisher only after a fresh durable metrics snapshot matches the run ID and collector PID, forwards SIGINT/SIGTERM to the collector process group, stops the publisher after collector exit, and atomically writes a run-scoped result JSON containing start/end timestamps, PIDs, signal, child exit codes, publisher outcome, and final-flush evidence.

The launch renderer produces a detached transient systemd command with a unique run-scoped unit, `User=bitcoin-trader`, `Restart=no`, `KillMode=mixed`, no enable/timer/cron, and a hard ceiling slightly above the supervisor deadline. The result JSON is authoritative even if `--collect` removes unit metadata. A bounded non-market mini-smoke exercises the same supervisor and transient-unit shape without invoking the collector or publisher.

## Publisher lifecycle

The supervisor validates the durable metrics snapshot before starting exactly one publisher loop. The snapshot must match the sealed run and live collector PID. Publisher failure is separately recorded and causes the overall candidate lifecycle result to fail; it cannot be hidden by a clean collector exit. No zero-value synthetic metrics are emitted.

## Validation and release

Use strict TDD for diagnostic schemas, partition rotation/archive guard behavior, supervisor signals/disconnect independence/result persistence, and launch rendering. Run the full Python suite, compileall, pip check, and scheduling-sensitive tests at least five times. On AWS, run only the four-symbol diagnostic and a non-market transient lifecycle mini-smoke. After all checks pass, generate a new epoch and run ID, update the generic runtime seal input, calculate the canonical config fingerprint, commit the final runtime candidate, and prepare an epoch-only IAM diff without applying it.

The remediation branch is `codex/aws-45m-remediation`. Push that branch only. Never merge, rebase, or cherry-pick it into `main` during this task.
