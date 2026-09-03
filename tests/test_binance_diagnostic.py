from __future__ import annotations

import asyncio
import unittest

from bithumb_coin_trader.binance_diagnostic import (
    BINANCE_HOST,
    BINANCE_PORT,
    BINANCE_SYMBOLS,
    collect_proxy_metadata,
    run_diagnostic,
    sanitize_detail,
)


class BinanceDiagnosticTests(unittest.TestCase):
    def test_proxy_metadata_never_exposes_credentials_or_raw_urls(self) -> None:
        metadata = collect_proxy_metadata(
            {
                "HTTPS_PROXY": "http://alice:top-secret@proxy.example:8443",
                "WSS_PROXY": "socks5h://bob:another-secret@socks.example:1080",
                "NO_PROXY": "localhost,169.254.169.254",
                "AWS_SESSION_TOKEN": "must-not-appear",
            },
            system_proxies={"https": "http://carol:hidden@system.example:3128"},
        )

        rendered = repr(metadata)
        self.assertNotIn("alice", rendered)
        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("bob", rendered)
        self.assertNotIn("another-secret", rendered)
        self.assertNotIn("carol", rendered)
        self.assertNotIn("hidden", rendered)
        self.assertNotIn("must-not-appear", rendered)
        self.assertEqual(
            metadata["environment"]["HTTPS_PROXY"],
            {"present": True, "scheme": "http", "host": "proxy.example", "port": 8443},
        )
        self.assertEqual(metadata["environment"]["NO_PROXY"], {"present": True})
        self.assertEqual(
            metadata["getproxies"]["https"],
            {"present": True, "scheme": "http", "host": "system.example", "port": 3128},
        )

    def test_exception_detail_redacts_url_userinfo(self) -> None:
        detail = sanitize_detail("proxy refused https://name:password@proxy.example:443/path")
        self.assertEqual(detail, "proxy refused https://[REDACTED]@proxy.example:443/path")

    def test_staged_report_preserves_address_families_and_exact_failure_stage(self) -> None:
        async def exercise() -> dict[str, object]:
            def resolver(host: str, port: int) -> list[dict[str, object]]:
                self.assertEqual((host, port), (BINANCE_HOST, BINANCE_PORT))
                return [
                    {"family": "IPv4", "address": "192.0.2.10", "sockaddr": ("192.0.2.10", port)},
                    {"family": "IPv6", "address": "2001:db8::10", "sockaddr": ("2001:db8::10", port, 0, 0)},
                ]

            async def transport(candidate: dict[str, object], timeout: float) -> dict[str, object]:
                if candidate["family"] == "IPv6":
                    return {
                        "family": "IPv6",
                        "address": "2001:db8::10",
                        "tcp": {"status": "FAIL", "elapsed_ms": 7.0, "exception_class": "OSError", "exception_message": "no route"},
                        "tls": {"status": "NOT_RUN"},
                    }
                return {
                    "family": "IPv4",
                    "address": "192.0.2.10",
                    "tcp": {"status": "PASS", "elapsed_ms": 4.0},
                    "tls": {"status": "PASS", "elapsed_ms": 6.0},
                }

            async def websocket(uri: str, proxy_mode: str, timeout: float) -> dict[str, object]:
                return {
                    "status": "PASS",
                    "elapsed_ms": 8.0,
                    "selected_address_family": "IPv4",
                    "peer_address": "192.0.2.10",
                }

            return await run_diagnostic(
                timeout=10.0,
                resolver=resolver,
                transport_probe=transport,
                websocket_probe=websocket,
                environ={},
                system_proxies={},
            )

        report = asyncio.run(exercise())
        self.assertEqual([item["family"] for item in report["dns"]["candidates"]], ["IPv4", "IPv6"])
        self.assertEqual(report["transport"][1]["tcp"]["status"], "FAIL")
        self.assertEqual(report["transport"][1]["tls"]["status"], "NOT_RUN")

    def test_four_symbols_run_in_auto_and_direct_modes_plus_combined_endpoint(self) -> None:
        calls: list[tuple[str, str]] = []

        async def exercise() -> dict[str, object]:
            async def transport(candidate: dict[str, object], timeout: float) -> dict[str, object]:
                return {
                    "family": candidate["family"],
                    "address": candidate["address"],
                    "tcp": {"status": "PASS", "elapsed_ms": 1.0},
                    "tls": {"status": "PASS", "elapsed_ms": 1.0},
                }

            async def websocket(uri: str, proxy_mode: str, timeout: float) -> dict[str, object]:
                calls.append((uri, proxy_mode))
                return {"status": "PASS", "elapsed_ms": 1.0, "selected_address_family": "IPv4"}

            return await run_diagnostic(
                timeout=10.0,
                resolver=lambda host, port: [
                    {"family": "IPv4", "address": "192.0.2.1", "sockaddr": ("192.0.2.1", port)}
                ],
                transport_probe=transport,
                websocket_probe=websocket,
                environ={},
                system_proxies={},
            )

        report = asyncio.run(exercise())
        self.assertEqual(BINANCE_SYMBOLS, ("btcusdt", "ethusdt", "solusdt", "xrpusdt"))
        self.assertEqual(len(report["websocket_attempts"]), 10)
        self.assertEqual({mode for _, mode in calls}, {"auto", "direct"})
        for symbol in BINANCE_SYMBOLS:
            self.assertEqual(sum(f"/ws/{symbol}@trade" in uri for uri, _ in calls), 2)
        self.assertEqual(sum("/stream?streams=" in uri for uri, _ in calls), 2)
        self.assertTrue(report["all_symbol_handshakes_passed"])

    def test_official_port_443_override_applies_to_every_diagnostic_stage(self) -> None:
        resolved: list[tuple[str, int]] = []
        websocket_uris: list[str] = []

        async def exercise() -> dict[str, object]:
            def resolver(host: str, port: int) -> list[dict[str, object]]:
                resolved.append((host, port))
                return [{"family": "IPv4", "address": "192.0.2.1", "sockaddr": ("192.0.2.1", port)}]

            async def transport(candidate: dict[str, object], timeout: float) -> dict[str, object]:
                return {"family": "IPv4", "address": candidate["address"], "tcp": {"status": "PASS"}, "tls": {"status": "PASS"}}

            async def websocket(uri: str, proxy_mode: str, timeout: float) -> dict[str, object]:
                websocket_uris.append(uri)
                return {"status": "PASS", "selected_address_family": "IPv4"}

            return await run_diagnostic(
                port=443,
                resolver=resolver,
                transport_probe=transport,
                websocket_probe=websocket,
                environ={},
                system_proxies={},
            )

        report = asyncio.run(exercise())
        self.assertEqual(resolved, [(BINANCE_HOST, 443)])
        self.assertEqual(report["target"]["port"], 443)
        self.assertTrue(all(uri.startswith("wss://stream.binance.com:443/") for uri in websocket_uris))
        self.assertIn("websockets_version", report)

    def test_binance_port_single_source_of_truth_aligns_production_diagnostic_and_cli(self) -> None:
        from urllib.parse import urlsplit
        from bithumb_coin_trader.cross_market_collector import BINANCE_WS_URL
        from scripts import diagnose_binance_websocket as diag_cli

        production_port = urlsplit(BINANCE_WS_URL).port or 443
        self.assertEqual(production_port, 443)
        self.assertEqual(BINANCE_PORT, production_port)

        parser = diag_cli.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.port, production_port)
        self.assertEqual(parser.get_default("port"), production_port)

    def test_diagnose_cli_defaults_to_production_port_without_override(self) -> None:
        from unittest.mock import AsyncMock, patch
        from scripts import diagnose_binance_websocket as diag_cli

        dummy_report = {
            "all_symbol_handshakes_passed": True,
            "production_combined_passed": True,
        }
        with patch("scripts.diagnose_binance_websocket.run_diagnostic", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = dummy_report
            with patch("sys.stdout"):
                exit_code = diag_cli.main([])
            self.assertEqual(exit_code, 0)
            mock_run.assert_awaited_once_with(timeout=10.0, port=BINANCE_PORT)


if __name__ == "__main__":
    unittest.main()

