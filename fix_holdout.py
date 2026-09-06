with open("src/bithumb_coin_trader/experiment_runner.py", "r") as f:
    content = f.read()

import re
old_holdout = """        if role == DatasetRole.HOLDOUT:
            if self._cycle_state != ResearchCycleState.HOLDOUT_AUTHORIZED:
                raise HoldoutContaminationError(
                    f"Forbidden access to HOLDOUT dataset '{dataset_name}'. "
                    f"Current research cycle state: {self._cycle_state.value}. "
                    f"Required state: {ResearchCycleState.HOLDOUT_AUTHORIZED.value}. "
                    f"Use advance_research_state(HOLDOUT_AUTHORIZED, justification=...) "
                    f"to authorize holdout access."
                )
        return f"ACCESS_GRANTED:{dataset_name}:{role.value}" """

new_holdout = """        if role == DatasetRole.HOLDOUT:
            if self._cycle_state == ResearchCycleState.HOLDOUT_CONSUMED:
                raise HoldoutAlreadyConsumedError("Holdout dataset already consumed.")
            if self._cycle_state != ResearchCycleState.HOLDOUT_AUTHORIZED:
                raise HoldoutContaminationError(
                    f"Forbidden access to HOLDOUT dataset '{dataset_name}'. "
                    f"Current research cycle state: {self._cycle_state.value}. "
                    f"Required state: {ResearchCycleState.HOLDOUT_AUTHORIZED.value}. "
                    f"Use advance_research_state(HOLDOUT_AUTHORIZED, justification=...) "
                    f"to authorize holdout access."
                )
            self._cycle_state = ResearchCycleState.HOLDOUT_CONSUMED
            self._save_cycle_state("Consumed holdout dataset")
            
        return f"ACCESS_GRANTED:{dataset_name}:{role.value}" """

# It might have missed it because of spaces or trailing spaces. Let's just use regex.
content = re.sub(r'        if role == DatasetRole.HOLDOUT:\n.*?return f"ACCESS_GRANTED:\{dataset_name\}:\{role\.value\}"', new_holdout, content, flags=re.DOTALL)

with open("src/bithumb_coin_trader/experiment_runner.py", "w") as f:
    f.write(content)
