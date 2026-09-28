"""Provenance binding for future qualified dataset builds."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from bithumb_coin_trader.research_infra.dataset_manifest import DatasetBuildManifest


class TestDatasetBuildManifest(unittest.TestCase):
    def _make_manifest(self) -> DatasetBuildManifest:
        return DatasetBuildManifest(
            dataset_id="qualified-v1",
            source_run_ids=("run-001", "run-002"),
            source_hashes={"run-001/raw.tar.zst": "a" * 64, "run-002/raw.tar.zst": "b" * 64},
            build_code_sha="c" * 40,
            schema_version="raw-v9.1.0",
            start_utc="2026-09-01T00:00:00Z",
            end_utc="2026-09-02T00:00:00Z",
            venues=("bithumb", "upbit"),
            symbols=("KRW-BTC", "KRW-ETH"),
            record_counts={"trade": 120, "orderbook": 240},
            dq_result="PASS",
        )

    def test_dataset_fingerprint_is_content_derived(self) -> None:
        manifest = self._make_manifest()
        different_build_time = replace(manifest, created_at_utc="2026-09-28T00:00:00+00:00")
        changed_source = replace(manifest, source_hashes={"run-001/raw.tar.zst": "d" * 64})

        self.assertEqual(manifest.compute_dataset_fingerprint(), different_build_time.compute_dataset_fingerprint())
        self.assertNotEqual(manifest.compute_dataset_fingerprint(), changed_source.compute_dataset_fingerprint())

    def test_manifest_roundtrip_and_no_clobber(self) -> None:
        manifest = self._make_manifest()
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dataset-manifest.json"
            manifest.save(path)
            loaded = DatasetBuildManifest.load(path)
            self.assertEqual(loaded.dataset_id, manifest.dataset_id)
            self.assertEqual(loaded.compute_dataset_fingerprint(), manifest.compute_dataset_fingerprint())
            with self.assertRaises(FileExistsError):
                manifest.save(path)

    def test_manifest_load_rejects_tampered_source_hash(self) -> None:
        manifest = self._make_manifest()
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dataset-manifest.json"
            manifest.save(path)
            data = json.loads(path.read_text())
            data["source_hashes"]["run-001/raw.tar.zst"] = "e" * 64
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "manifest fingerprint mismatch"):
                DatasetBuildManifest.load(path)
