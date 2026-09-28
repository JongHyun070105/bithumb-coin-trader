from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.explore_unqualified_krw_btc_dev import read_development_prefix


class DevelopmentPrefixReadTests(unittest.TestCase):
    def test_reader_stops_at_requested_development_rows(self) -> None:
        prefix = (
            b"market,timestamp,open,high,low,close,volume\n"
            b"KRW-BTC,2020-01-01T00:00:00Z,100,101,99,100,1\n"
            b"KRW-BTC,2020-01-02T00:00:00Z,100,102,98,101,2\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candles.csv"
            path.write_bytes(prefix + b"this sealed row is intentionally invalid\n")

            candles, digest, bytes_read = read_development_prefix(path, 2)

        self.assertEqual(len(candles), 2)
        self.assertEqual(bytes_read, len(prefix))
        self.assertEqual(digest, hashlib.sha256(prefix).hexdigest())


if __name__ == "__main__":
    unittest.main()
