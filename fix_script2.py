import sys

with open("src/bithumb_coin_trader/experiment_runner.py", "r") as f:
    content = f.read()

# Fix _load_or_init_ledger to clear entries first
load_ledger_orig = """    def _load_or_init_ledger(self) -> None:
        if self.ledger_file.exists():
            data = json.loads(self.ledger_file.read_text())
            for item in data:"""
load_ledger_new = """    def _load_or_init_ledger(self) -> None:
        if self.ledger_file.exists():
            data = json.loads(self.ledger_file.read_text())
            self._entries.clear()
            for item in data:"""
content = content.replace(load_ledger_orig, load_ledger_new)

with open("src/bithumb_coin_trader/experiment_runner.py", "w") as f:
    f.write(content)

with open("tests/test_experiment_runner.py", "r") as f:
    test_content = f.read()

# Fix test_budget_counts_failed_trials
test_content = test_content.replace(
    'runner.update_trial_status("will_fail", TrialStatus.FAILED)',
    'runner.update_trial_status("will_fail", TrialStatus.RUNNING)\n    runner.update_trial_status("will_fail", TrialStatus.FAILED)'
)

# Fix test_terminal_states_are_final
test_terminal_orig = """        runner.reserve_trial(m)
        runner.update_trial_status(tid, status)
        with pytest.raises(Exception): # InvalidStatusTransitionError"""
test_terminal_new = """        runner.reserve_trial(m)
        runner.update_trial_status(tid, TrialStatus.RUNNING)
        runner.update_trial_status(tid, status)
        with pytest.raises(Exception): # InvalidStatusTransitionError"""
test_content = test_content.replace(test_terminal_orig, test_terminal_new)

# Fix test_concurrent_reservation_exactly_one_success by moving worker outside
worker_str = """def worker(q, ledger):
    try:
        from bithumb_coin_trader.experiment_runner import GovernedExperimentRunner, TrialBudgetExceededError, ExperimentGatingError
        r = GovernedExperimentRunner(ledger)
        from test_experiment_runner import _make_manifest
        m = _make_manifest("conc_test", "fam_conc", max_trials=1)
        r.reserve_trial(m)
        q.put("success")
    except Exception as e:
        q.put(type(e).__name__)

def test_concurrent_reservation_exactly_one_success(tmp_path):"""

test_content = test_content.replace("""def test_concurrent_reservation_exactly_one_success(tmp_path):
    ledger = tmp_path / 'ledger.json'
    
    def worker(q):
        try:
            r = GovernedExperimentRunner(ledger)
            m = _make_manifest("conc_test", "fam_conc", max_trials=1)
            r.reserve_trial(m)
            q.put("success")
        except TrialBudgetExceededError:
            q.put("budget_exceeded")
        except ExperimentGatingError:
            q.put("gating_error")
        except Exception as e:
            q.put(f"error: {e}")""", worker_str + "\n    ledger = tmp_path / 'ledger.json'")
            
test_content = test_content.replace("p = ctx.Process(target=worker, args=(q,))", "p = ctx.Process(target=worker, args=(q, ledger))")

with open("tests/test_experiment_runner.py", "w") as f:
    f.write(test_content)
