import pytest
from bithumb_coin_trader.research_cli import main
from bithumb_coin_trader.experiment_runner import GovernedExperimentRunner, PreregistrationManifest


def test_research_cli_power_plan(capsys):
    ret = main(["power-plan", "--sharpe", "0.10", "--alpha", "0.01", "--power", "0.80"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Required observations" in captured.out


def test_research_cli_run_synthetic_sim(capsys):
    ret = main(["run-synthetic-sim", "--count", "20"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Generated and replayed 20 synthetic microstructure events" in captured.out


def test_research_cli_verify_ledger(tmp_path, capsys):
    ledger_file = tmp_path / "ledger.json"
    runner = GovernedExperimentRunner(ledger_file)
    m = PreregistrationManifest("t1", "f1", "hypo", ("f1",), 100, 1000)
    runner.record_trial(m, {"sharpe": 1.2})

    ret = main(["verify-ledger", "--ledger", str(ledger_file)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "SUCCESS: Ledger chain verified" in captured.out
