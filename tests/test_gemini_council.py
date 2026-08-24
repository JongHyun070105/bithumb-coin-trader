from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bithumb_coin_trader import ai_brain, gemini_council
from bithumb_coin_trader.ai_brain import AIStrategyMemory


class _Reviewer:
    def load_completed_trades(self):
        return []

    def pair_trades(self, _trades):
        return []


class GeminiCouncilSafetyTests(unittest.TestCase):
    def test_invalid_gemini_output_preserves_existing_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_path = root / "memory.json"
            journal_path = root / "journal.md"
            existing = AIStrategyMemory(min_entry_confidence=82.0)

            with (
                patch.object(ai_brain, "AI_MEMORY_PATH", memory_path),
                patch.object(gemini_council, "EVOLUTION_JOURNAL_PATH", journal_path),
            ):
                ai_brain.save_ai_memory(existing)
                before = memory_path.read_bytes()
                invalid = json.dumps(
                    {
                        "market_regime": "VOLATILE_CHOP",
                        "market_score_biases": {"KRW-BTC": 99.0},
                    }
                )
                with (
                    patch.object(gemini_council, "get_gemini_api_key", return_value="test-key"),
                    patch.object(gemini_council, "get_gemini_model", return_value="test-model"),
                    patch.object(gemini_council, "EvolutionaryReviewer", return_value=_Reviewer()),
                    patch.object(gemini_council, "call_gemini_api", side_effect=["proposal", invalid]),
                ):
                    returned, _report = gemini_council.run_gemini_autonomous_review()

                self.assertEqual(returned.min_entry_confidence, 82.0)
                self.assertEqual(memory_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
