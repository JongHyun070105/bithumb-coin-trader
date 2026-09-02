from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_cross_market_collector.py"
SPEC = importlib.util.spec_from_file_location("run_cross_market_collector", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ShortSmokeRuntimeConfigTests(unittest.TestCase):
    def test_canonical_fingerprint_ignores_json_formatting(self) -> None:
        payload = {"schema_version": 1, "nested": {"b": 2, "a": 1}}
        expected = MODULE.canonical_config_fingerprint(payload)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(payload, indent=4), encoding="utf-8")
            self.assertEqual(MODULE._load_runtime_config(path, expected), payload)

    def test_fingerprint_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                MODULE._load_runtime_config(path, "0" * 64)

    def test_epoch_template_rejects_additional_placeholders(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            MODULE._render_epoch_template(
                "/var/lib/{collector_epoch}/{run_id}",
                "epoch-a",
                "raw_root_template",
            )


if __name__ == "__main__":
    unittest.main()
