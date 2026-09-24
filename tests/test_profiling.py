import os
import unittest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson.decimal128 import Decimal128
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from main import app
from profiling import ProfilingConfig, expense_pipeline, month_shift, summarize_expenses


class ProfilingTests(unittest.TestCase):
    def group(self, month, amount, **extra):
        return {"_id": month, "total_expense": amount, "transaction_count": 1, "invalid_count": 0, **extra}

    def summarize(self, current, history=(100, 200, 300)):
        groups = [self.group(f"2026-{month:02}", value) for month, value in zip((6, 7, 8), history)]
        groups.append(self.group("2026-09", current))
        return summarize_expenses(groups, date(2026, 9, 24), ProfilingConfig(3), date(2026, 9, 24))

    def test_above_equal_below_and_supportive_message(self):
        for current, expected in ((199, False), (200, False), (201, True)):
            profile = self.summarize(current)
            self.assertEqual(profile["moving_average"], 200)
            self.assertEqual(profile["alert"], expected)
            self.assertEqual(profile["difference"], current - 200)
            self.assertNotIn("reckless", profile["message"])
        self.assertEqual(self.summarize(201)["above_average_percent"], 0.5)

    def test_month_rollover_and_pipeline_excludes_income_and_future(self):
        start = month_shift(date(2026, 1, 20), -3)
        self.assertEqual(start, date(2025, 10, 1))
        query = expense_pipeline(start, date(2026, 2, 1))[0]["$match"]
        self.assertEqual(query["trx_type"], "pembelian")
        self.assertEqual(query["date"], {"$gte": datetime(2025, 10, 1), "$lt": datetime(2026, 2, 1)})

    def test_missing_month_is_not_silently_zero_or_skipped(self):
        groups = [self.group("2026-06", 100), self.group("2026-08", 300), self.group("2026-09", 500)]
        result = summarize_expenses(groups, date(2026, 9, 1), ProfilingConfig(3), date(2026, 9, 24))
        self.assertEqual(result["status"], "insufficient_history")
        self.assertIsNone(result["moving_average"])
        self.assertEqual(result["missing_months"], ["2026-07"])
        self.assertIsNone(result["history"][1]["total_expense"])
        self.assertFalse(result["alert"])

    def test_zero_baseline_and_exact_large_amounts(self):
        self.assertFalse(self.summarize(0, (0, 0, 0))["alert"])
        result = self.summarize(1, (0, 0, 0))
        self.assertTrue(result["alert"])
        self.assertIsNone(result["above_average_percent"])
        maximum = 9223372036854775807
        self.assertTrue(self.summarize(maximum, (maximum, maximum, maximum - 1))["alert"])
        self.assertEqual(self.summarize(201, (Decimal128("100"), Decimal128("200"), Decimal128("300")))["moving_average"], 200)

    def test_partial_future_month_never_becomes_history(self):
        groups = [self.group("2026-06", 100), self.group("2026-07", 200), self.group("2026-08", 300)]
        result = summarize_expenses(groups, date(2026, 10, 1), ProfilingConfig(3), date(2026, 9, 24))
        self.assertEqual(result["status"], "future_period")
        self.assertFalse(result["alert"])

    def test_invalid_history_does_not_generate_judgement(self):
        groups = [self.group(f"2026-{m:02}", 100, invalid_count=1 if m == 7 else 0) for m in (6, 7, 8, 9)]
        result = summarize_expenses(groups, date(2026, 9, 1), ProfilingConfig(3), date(2026, 9, 24))
        self.assertEqual(result["status"], "invalid_data")
        self.assertIsNone(result["moving_average"])

    def test_window_configuration(self):
        for value in ("0", "25", "3.5", "", "abc"):
            with self.subTest(value=value), patch.dict(os.environ, {"PROFILING_WINDOW_MONTHS": value}):
                with self.assertRaises(RuntimeError):
                    ProfilingConfig.from_env()
        with patch.dict(os.environ, {"PROFILING_WINDOW_MONTHS": "8"}):
            self.assertEqual(ProfilingConfig.from_env().window_months, 8)


class ProfilingAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.payload = dict(amount=201, method="Cash", desc="Makan", trx_type="pembelian", date="2026-09-24")

    def record(self):
        return SimpleNamespace(id="507f1f77bcf86cd799439011", date=datetime(2026, 9, 24),
                               **{k: v for k, v in self.payload.items() if k != "date"}, insert=AsyncMock())

    def test_post_inserts_before_profile_and_keeps_transaction_fields(self):
        record = self.record()
        async def check(target):
            record.insert.assert_awaited_once()
            self.assertEqual(target, date(2026, 9, 24))
            return {"alert": True, "status": "above_average"}
        with patch("main.Transaction", return_value=record), patch("main.get_expense_profile", side_effect=check):
            response = self.client.post("/transaction/add", json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["amount"], 201)
        self.assertTrue(response.json()["profiling"]["alert"])

    def test_failed_profile_does_not_undo_or_duplicate_successful_insert(self):
        record = self.record()
        with patch("main.Transaction", return_value=record), patch("main.get_expense_profile", side_effect=ServerSelectionTimeoutError("private uri")):
            response = self.client.post("/transaction/add", json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profiling"]["status"], "unavailable")
        self.assertNotIn("private uri", response.text)
        record.insert.assert_awaited_once()

    def test_income_never_triggers_expense_check(self):
        record = self.record()
        record.trx_type = "pemasukan"
        with patch("main.Transaction", return_value=record), patch("main.get_expense_profile", new_callable=AsyncMock) as profile:
            response = self.client.post("/transaction/add", json=self.payload | {"trx_type": "pemasukan"})
        profile.assert_not_awaited()
        self.assertEqual(response.json()["profiling"]["status"], "not_applicable")

    def test_invalid_post_does_not_insert_or_profile(self):
        with patch("main.Transaction") as model, patch("main.get_expense_profile", new_callable=AsyncMock) as profile:
            response = self.client.post("/transaction/add", json=self.payload | {"amount": -1})
        self.assertEqual(response.status_code, 422)
        model.assert_not_called()
        profile.assert_not_awaited()

    def test_summary_endpoint_and_safe_database_error(self):
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[]))
        with patch("main.get_expense_profile", new_callable=AsyncMock, return_value={"status": "insufficient_history"}):
            response = self.client.get("/profiling/summary?year=2026&month=9")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "insufficient_history")
        self.assertEqual(self.client.get("/profiling/summary?year=2026&month=13").status_code, 422)
        with patch("main.get_expense_profile", side_effect=ServerSelectionTimeoutError("private uri")):
            response = self.client.get("/profiling/summary?year=2026&month=9")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private uri", response.text)


if __name__ == "__main__":
    unittest.main()
