from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bithumb_coin_trader import ai_brain
from bithumb_coin_trader.ai_brain import AIStrategyMemory


class AIStrategyMemoryValidationTests(unittest.TestCase):
    def test_rejects_invalid_market_and_out_of_range_bias(self) -> None:
        with self.assertRaises(ValueError):
            AIStrategyMemory(banned_markets=["BTC"])
        with self.assertRaises(ValueError):
            AIStrategyMemory(market_score_biases={"KRW-BTC": 20.1})

    def test_rejects_wrong_types_and_oversized_lists(self) -> None:
        with self.assertRaises(TypeError):
            AIStrategyMemory(min_entry_confidence="75")
        with self.assertRaises(ValueError):
            AIStrategyMemory(preferred_sectors=[f"sector-{index}" for index in range(21)])

    def test_untrusted_mapping_requires_exact_strategy_schema(self) -> None:
        data = {
            key: value
            for key, value in ai_brain.asdict(AIStrategyMemory()).items()
            if key in AIStrategyMemory.UNTRUSTED_FIELDS
        }
        data["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            AIStrategyMemory.from_untrusted_mapping(
                data,
                analyst_model="test",
                last_updated="2026-08-24 12:00:00",
            )

    def test_stored_mapping_rejects_class_metadata_as_unknown(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown"):
            AIStrategyMemory.from_mapping({"UNTRUSTED_FIELDS": []})

    def test_live_memory_is_report_only_without_explicit_opt_in(self) -> None:
        reviewed = AIStrategyMemory(min_entry_confidence=91.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            with patch.object(ai_brain, "AI_MEMORY_PATH", path):
                ai_brain.save_ai_memory(reviewed)
                self.assertEqual(ai_brain.load_ai_memory(env={}).min_entry_confidence, 75.0)
                self.assertEqual(
                    ai_brain.load_ai_memory(env={ai_brain.LIVE_AI_CONFIG_ENV: "true"}).min_entry_confidence,
                    91.0,
                )
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(path.read_text())["min_entry_confidence"], 91.0)

    def test_disabled_memory_is_neutral_and_enabled_corruption_fails_closed(self) -> None:
        neutral = ai_brain.load_ai_memory(env={})
        self.assertEqual(neutral.market_score_biases, {})
        self.assertEqual(neutral.banned_markets, [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text("not-json", encoding="utf-8")
            with patch.object(ai_brain, "AI_MEMORY_PATH", path):
                with self.assertRaises(json.JSONDecodeError):
                    ai_brain.load_ai_memory(
                        env={ai_brain.LIVE_AI_CONFIG_ENV: "true"}
                    )

    def test_atomic_save_preserves_existing_file_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            with patch.object(ai_brain, "AI_MEMORY_PATH", path):
                ai_brain.save_ai_memory(AIStrategyMemory(min_entry_confidence=80.0))
                before = path.read_bytes()
                invalid = AIStrategyMemory()
                invalid.market_score_biases["KRW-BTC"] = 99.0
                with self.assertRaises(ValueError):
                    ai_brain.save_ai_memory(invalid)
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
