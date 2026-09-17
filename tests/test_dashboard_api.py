from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import MagicMock

from bithumb_coin_trader.dashboard_api import (
    DashboardHandler,
    get_cross_exchange_research,
    get_datasets,
    get_evidence,
    get_maker_research,
    get_research_state,
    get_safety,
    get_status,
    get_storage,
    get_trials_summary,
    get_v2_research,
    get_v2_summary,
    get_v4_research,
    get_v4_status,
    get_validation_v4,
    get_validations,
)


class DashboardApiUnitTests(unittest.TestCase):
    def test_get_status_returns_state_dict(self):
        data = get_status()
        self.assertIn("scientific", data)
        self.assertEqual(data["scientific"]["alpha"], "UNPROVEN")
        self.assertEqual(data["scientific"]["live"], "DISABLED")
        self.assertEqual(data["v4"]["final_verdict"], "FAIL")
        self.assertEqual(data["maker"]["status"], "COMPLETE")
        self.assertEqual(data["cross_exchange"]["status"], "COMPLETE")

    def test_get_datasets(self):
        data = get_datasets()
        self.assertIn("datasets", data)
        self.assertIsInstance(data["datasets"], list)

    def test_get_validations(self):
        data = get_validations()
        self.assertIn("validations", data)
        self.assertTrue(any(v["epoch"] == "aws-validation-30h-20260915-v4" for v in data["validations"]))

    def test_get_validation_v4(self):
        data = get_validation_v4()
        self.assertEqual(data["verdict"], "FAIL")
        self.assertEqual(data["dataset_research_usability"], "NOT_RESEARCH_USABLE")

    def test_get_v2_research(self):
        data = get_v2_research()
        self.assertIsInstance(data, dict)

    def test_get_v2_summary(self):
        data = get_v2_summary()
        self.assertIsInstance(data, dict)

    def test_get_v4_research(self):
        data = get_v4_research()
        self.assertEqual(data["status"], "FAIL")
        self.assertEqual(data["research_usability"], "NOT_RESEARCH_USABLE")
        self.assertIn("report_markdown", data)

    def test_get_v4_status(self):
        data = get_v4_status()
        self.assertEqual(data["final_verdict"], "FAIL")
        self.assertEqual(data["dataset_research_usability"], "NOT_RESEARCH_USABLE")

    def test_get_research_state(self):
        data = get_research_state()
        self.assertEqual(data["scientific"]["alpha"], "UNPROVEN")
        self.assertEqual(data["datasets"]["v4"]["final_verdict"], "FAIL")
        self.assertEqual(data["maker"]["classification"], "RETROSPECTIVE_DEV_MARKET_SPECIFIC_LEAD (NOT VALIDATED)")
        self.assertEqual(data["cross_exchange"]["classification"], "NO_LOOKAHEAD_PREDICTIVE_LEAD")

    def test_get_maker_research(self):
        data = get_maker_research()
        self.assertEqual(data.get("status"), "COMPLETE")
        self.assertIn("findings", data)

    def test_get_cross_exchange_research(self):
        data = get_cross_exchange_research()
        self.assertIn("results", data)
        self.assertGreater(len(data["results"]), 0)

    def test_get_trials_summary(self):
        data = get_trials_summary()
        self.assertGreater(data["total_trials"], 0)
        self.assertIn("by_hypothesis", data)
        self.assertIn("by_classification", data)

    def test_get_evidence(self):
        data = get_evidence()
        self.assertIn("artifacts", data)

    def test_get_storage(self):
        data = get_storage()
        self.assertEqual(data["status"], "STORAGE_SAFE")
        self.assertEqual(data["reclaimed_percent"], 98.4)

    def test_get_safety(self):
        data = get_safety()
        self.assertTrue(data["fail_closed"])
        self.assertEqual(data["live_trading"], "DISABLED")
        self.assertEqual(data["private_api"], "DISABLED")
        self.assertEqual(data["paper_trading"], "NOT STARTED")


class MockDashboardHandler(DashboardHandler):
    def __init__(self, path: str):
        self.path = path
        self.requestline = f"GET {path} HTTP/1.1"
        self.request_version = "HTTP/1.1"
        self.command = "GET"
        self.wfile = BytesIO()
        self.headers = {}
        self.status_code = None
        self.response_headers = {}

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, keyword, value):
        self.response_headers[keyword] = value

    def end_headers(self):
        pass


class DashboardHandlerRoutingTests(unittest.TestCase):
    def test_routes(self):
        routes = [
            "/api/status",
            "/api/datasets",
            "/api/validations",
            "/api/validations/v4",
            "/api/research/v2",
            "/api/research/v4",
            "/api/research/state",
            "/api/research/maker",
            "/api/research/cross-exchange",
            "/api/research/trials/summary",
            "/api/evidence",
            "/api/storage",
            "/api/safety",
            "/api/v2/research",
            "/api/v2/summary",
            "/api/v4/status",
        ]
        for route in routes:
            handler = MockDashboardHandler(route)
            handler.do_GET()
            self.assertEqual(handler.status_code, 200, f"Failed on route {route}")
            self.assertEqual(handler.response_headers.get("Content-Type"), "application/json")
            self.assertEqual(handler.response_headers.get("Access-Control-Allow-Origin"), "*")

    def test_404(self):
        handler = MockDashboardHandler("/api/unknown_route")
        handler.do_GET()
        self.assertEqual(handler.status_code, 404)
