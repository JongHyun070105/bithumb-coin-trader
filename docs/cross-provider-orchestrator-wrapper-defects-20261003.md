# Cross-provider wrapper defects — 2026-10-03

The Phase 3 review handoff could not start because the local ai-orchestrator wrapper failed before a provider returned useful output:

- `orch-consult` raised `NameError: _git is not defined` in `phase1_supervisor.py` at line 420.
- `orch-delegations list` raised `NameError: _iso is not defined` in the same module at line 2299.

The earlier `_run` repair was reported as complete before this task. This task did not make further wrapper changes. The local implementation and verification continued independently; no worker output was treated as evidence.
