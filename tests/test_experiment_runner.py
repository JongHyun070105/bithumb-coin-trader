import pytest
import json
from bithumb_coin_trader.experiment_runner import (
    DatasetRole,
    GovernedExperimentRunner,
    PreregistrationManifest,
    TrialBudgetExceededError,
    HoldoutContaminationError,
    LedgerTamperError,
    ExperimentGatingError,
)


def test_preregistration_and_ledger_chain(tmp_path):
    ledger_file = tmp_path / "ledger.json"
    runner = GovernedExperimentRunner(ledger_file)

    manifest_1 = PreregistrationManifest(
        trial_id="exp_001",
        family_id="fam_ofi",
        hypothesis="OFI predicts 500ms price change",
        features=("ofi_v2",),
        target_horizon_ms=500,
        sample_budget=10000,
        max_trials_in_family=3,
    )
    runner.reserve_trial(manifest_1)
    entry_1 = runner.record_trial(manifest_1, {"sharpe": 1.25, "p_value": 0.02})

    assert entry_1.entry_index == 0
    assert entry_1.previous_hash == "0" * 64
    assert len(entry_1.entry_hash) == 64

    # Second trial
    manifest_2 = PreregistrationManifest(
        trial_id="exp_002",
        family_id="fam_ofi",
        hypothesis="OFI + ATI predicts 500ms price change",
        features=("ofi_v2", "ati"),
        target_horizon_ms=500,
        sample_budget=10000,
        max_trials_in_family=3,
    )
    runner.reserve_trial(manifest_2)
    entry_2 = runner.record_trial(manifest_2, {"sharpe": 1.45, "p_value": 0.005})

    assert entry_2.entry_index == 1
    assert entry_2.previous_hash == entry_1.entry_hash
    assert runner.verify_ledger_chain() is True


def test_budget_exceeded(tmp_path):
    ledger_file = tmp_path / "ledger.json"
    runner = GovernedExperimentRunner(ledger_file)

    for i in range(3):
        m = PreregistrationManifest(
            trial_id=f"exp_{i}",
            family_id="fam_budget",
            hypothesis=f"Hypothesis {i}",
            features=("f1",),
            target_horizon_ms=100,
            sample_budget=1000,
            max_trials_in_family=3,
        )
        runner.reserve_trial(m)
        runner.record_trial(m, {"sharpe": 0.5})

    # 4th trial should be blocked by budget
    m_excess = PreregistrationManifest(
        trial_id="exp_excess",
        family_id="fam_budget",
        hypothesis="Excess hypothesis",
        features=("f1",),
        target_horizon_ms=100,
        sample_budget=1000,
        max_trials_in_family=3,
    )
    with pytest.raises(TrialBudgetExceededError, match="trial budget"):
        runner.reserve_trial(m_excess)

    with pytest.raises(TrialBudgetExceededError, match="trial budget exhausted"):
        runner.record_trial(m_excess, {"sharpe": 0.1})


def test_tamper_detection(tmp_path):
    ledger_file = tmp_path / "ledger.json"
    runner = GovernedExperimentRunner(ledger_file)

    m = PreregistrationManifest("t1", "f1", "hypo", ("f1",), 100, 1000)
    runner.record_trial(m, {"sharpe": 1.0})

    # Tamper with file content
    raw = json.loads(ledger_file.read_text())
    raw[0]["results"]["sharpe"] = 99.9  # Fraudulent alteration
    ledger_file.write_text(json.dumps(raw))

    # Reloading runner should detect tampering and raise LedgerTamperError
    with pytest.raises(LedgerTamperError):
        GovernedExperimentRunner(ledger_file)


def test_holdout_contamination_prevention(tmp_path):
    ledger_file = tmp_path / "ledger.json"
    runner = GovernedExperimentRunner(ledger_file)

    # TRAIN and VALIDATION access are allowed
    assert "ACCESS_GRANTED" in runner.access_dataset("dataset_72h", DatasetRole.TRAIN)
    assert "ACCESS_GRANTED" in runner.access_dataset("dataset_72h", DatasetRole.VALIDATION)

    # HOLDOUT access without final verification flag is forbidden
    with pytest.raises(HoldoutContaminationError, match="Forbidden access to HOLDOUT"):
        runner.access_dataset("dataset_72h", DatasetRole.HOLDOUT, is_final_verification=False)

    # HOLDOUT access with explicit final verification flag is permitted
    assert "ACCESS_GRANTED" in runner.access_dataset("dataset_72h", DatasetRole.HOLDOUT, is_final_verification=True)
