import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bson import BSON
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import (RequestNewTransaction, RequestUpdateTransaction, analyze_spending, app,
                  storage_values, today_wib, transaction_response)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.valid = dict(amount=5000, method="Cash", desc="Parkir", trx_type="pembelian")
        self.client = TestClient(app)  # Skip DB startup; tests use mocks.
        self.addCleanup(self.client.close)
        self.id = "507f1f77bcf86cd799439011"

    def record(self):
        return SimpleNamespace(
            id=self.id, date=datetime(2025, 8, 1), **self.valid,
            set=AsyncMock(), delete=AsyncMock(return_value=SimpleNamespace(deleted_count=1)),
        )

    def test_amount_is_nonnegative_integer(self):
        for value in [0, 1, 5000]:
            self.assertEqual(RequestNewTransaction(**(self.valid | {"amount": value})).amount, value)
        for value in [-1, 1.5, 5.0, True, "5000", None, 9223372036854775808]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                RequestNewTransaction(**(self.valid | {"amount": value}))

    def test_only_requested_choices(self):
        for method in ["Cash", "gopay", "bca", "shopee", "mandiri"]:
            RequestNewTransaction(**(self.valid | {"method": method}))
        for kind in ["pembelian", "pemasukan"]:
            RequestNewTransaction(**(self.valid | {"trx_type": kind}))
        for changes in [{"method": "cash"}, {"method": "GoPay"}, {"trx_type": "income"},
                        {"trx_type": "expense"}, {"trx_type": "transfer"}, {"pid": "x"}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                RequestNewTransaction(**(self.valid | changes))

    def test_date_default_and_custom(self):
        for value in [None, ""]:
            self.assertEqual(RequestNewTransaction(**self.valid, date=value).date, today_wib())
        self.assertEqual(RequestNewTransaction(**self.valid).date, today_wib())
        value = RequestNewTransaction(**self.valid, date="2024-02-29")
        self.assertEqual(value.date, date(2024, 2, 29))
        self.assertEqual(storage_values(value)["date"], datetime(2024, 2, 29))
        self.assertEqual(transaction_response(self.record())["date"], "2025-08-01")

    def test_invalid_date_formats(self):
        for value in ["2025-02-29", "21-09-2026", "2026-9-1",
                      "2026-09-21T09:00:00", 123, True, " "]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                RequestNewTransaction(**self.valid, date=value)

    def test_date_is_bson_date_not_string(self):
        value = RequestNewTransaction(**self.valid, date="2026-09-21")
        encoded = BSON.encode({"date": storage_values(value)["date"]})
        # BSON element type 0x09 means UTC datetime; 0x02 would be a string.
        self.assertEqual(encoded[4], 0x09)
        self.assertEqual(BSON(encoded).decode()["date"], datetime(2026, 9, 21))

    def test_update_requires_full_body_and_defaults_date(self):
        update = RequestUpdateTransaction(**self.valid)
        self.assertEqual(storage_values(update), self.valid | {
            "date": datetime.combine(today_wib(), datetime.min.time())})
        for field in self.valid:
            incomplete = self.valid.copy()
            del incomplete[field]
            with self.subTest(missing=field), self.assertRaises(ValidationError):
                RequestUpdateTransaction(**incomplete)
        for data in [{}, {"amount": None}, {"method": None}, {"amount": -1}, {"_id": self.id}]:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                RequestUpdateTransaction(**data)

    def test_invalid_request_response(self):
        response = self.client.post("/transaction/add", json=self.valid | {"amount": -1})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"][0]["field"], "body.amount")

    def test_add_saves_and_returns_date_only(self):
        trx = self.record()
        trx.insert = AsyncMock()
        with patch("main.Transaction", return_value=trx) as document, \
                patch("main.get_expense_profile", new_callable=AsyncMock, return_value={"alert": False}):
            response = self.client.post("/transaction/add", json=self.valid | {"date": "2025-08-01"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["date"], "2025-08-01")
        self.assertEqual(document.call_args.kwargs["date"], datetime(2025, 8, 1))
        trx.insert.assert_awaited_once()

    def test_update_endpoint(self):
        trx = self.record()
        async def apply_changes(values):
            for key, value in values.items():
                setattr(trx, key, value)
        trx.set.side_effect = apply_changes
        with patch("main.Transaction.get", new_callable=AsyncMock, return_value=trx):
            response = self.client.put(f"/transaction/{self.id}", json=self.valid | {
                "amount": 7000, "date": "2025-09-02"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["amount"], 7000)
        self.assertEqual(response.json()["date"], "2025-09-02")
        trx.set.assert_awaited_once_with(self.valid | {
            "amount": 7000, "date": datetime(2025, 9, 2)})

    def test_partial_put_is_rejected_before_database_access(self):
        with patch("main.Transaction.get", new_callable=AsyncMock) as get:
            response = self.client.put(f"/transaction/{self.id}", json={"amount": 7000})
        self.assertEqual(response.status_code, 422)
        get.assert_not_awaited()

    def test_invalid_and_missing_ids(self):
        for method in ["put", "delete"]:
            kwargs = {"json": self.valid} if method == "put" else {}
            with self.subTest(method=method):
                response = getattr(self.client, method)("/transaction/not-an-id", **kwargs)
                self.assertEqual(response.status_code, 422)
                with patch("main.Transaction.get", new_callable=AsyncMock, return_value=None):
                    response = getattr(self.client, method)(f"/transaction/{self.id}", **kwargs)
                self.assertEqual(response.status_code, 404)

    def test_delete(self):
        trx = self.record()
        with patch("main.Transaction.get", new_callable=AsyncMock, return_value=trx):
            response = self.client.delete(f"/transaction/{self.id}")
        self.assertEqual(response.status_code, 200)
        trx.delete.assert_awaited_once()

    def test_date_query_and_summary(self):
        response = self.client.get("/transaction", params={"start_date": "2026-10-01", "end_date": "2026-09-01"})
        self.assertEqual(response.status_code, 422)
        response = self.client.get("/transaction/summary", params={"year": 2026, "month": 13})
        self.assertEqual(response.status_code, 422)
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[]))
        with patch("main.Transaction.aggregate", return_value=cursor) as aggregate:
            response = self.client.get("/transaction/summary", params={"year": 2026, "month": 12})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "belum_ada_transaksi")
        self.assertEqual(aggregate.call_args.args[0][0]["$match"]["date"],
                         {"$gte": datetime(2026, 12, 1), "$lt": datetime(2027, 1, 1)})

    def test_spending_assessment(self):
        def assess(income, expense):
            return analyze_spending([
                {"_id": "pemasukan", "total_amount": income, "count": 1},
                {"_id": "pembelian", "total_amount": expense, "count": 1},
            ])
        self.assertEqual(assess(100, 79)["status"], "big_saver")
        self.assertEqual(assess(100, 80)["status"], "reckless_spender")
        self.assertEqual(assess(100, 99)["status"], "reckless_spender")
        self.assertEqual(assess(100, 100)["status"], "reckless_spender")
        self.assertEqual(assess(100, 101)["status"], "reckless_spender")
        self.assertEqual(assess(100, 120)["expense_ratio_percent"], 120)
        self.assertEqual(assess(1000000, 799999)["status"], "big_saver")
        self.assertEqual(assess(100, 0)["status"], "big_saver")
        self.assertEqual(assess(100, 0)["net_amount"], 100)
        self.assertEqual(assess(100, 120)["net_amount"], -20)
        self.assertEqual(assess(0, 100)["status"], "belum_bisa_dinilai")
        self.assertEqual(assess(0, 0)["status"], "belum_bisa_dinilai")
        self.assertIsNone(assess(0, 100)["expense_ratio_percent"])
        self.assertEqual(analyze_spending([])["status"], "belum_ada_transaksi")
        for group in [
            {"_id": "string", "total_amount": 10, "count": 1},
            {"_id": "pembelian", "total_amount": 10, "count": 2, "min_amount": -5},
        ]:
            self.assertEqual(analyze_spending([group])["status"], "data_perlu_diperiksa")

    def test_summary_net_amount_response(self):
        groups = [
            {"_id": "pemasukan", "total_amount": 1000000, "count": 1, "min_amount": 1000000},
            {"_id": "pembelian", "total_amount": 900000, "count": 1, "min_amount": 900000},
        ]
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=groups))
        with patch("main.Transaction.aggregate", return_value=cursor), patch("main.today_wib", return_value=date(2025, 8, 15)):
            response = self.client.get("/transaction/summary", params={
                "year": 2025, "month": 8})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "reckless_spender")
        self.assertEqual(body["expense_ratio_percent"], 90)
        self.assertEqual(body["net_amount"], 100000)
        for removed_field in ["saving_rate_percent", "saving_target_percent", "remaining_amount"]:
            self.assertNotIn(removed_field, body)
        self.assertTrue(body["is_provisional"])
        self.assertNotIn("min_amount", body["groups"][0])

    def test_transaction_count_does_not_change_category(self):
        groups = [
            {"_id": "pemasukan", "total_amount": 1000, "count": 1},
            {"_id": "pembelian", "total_amount": 800, "count": 1},
        ]
        original = analyze_spending(groups)
        groups[1]["count"] = 100
        self.assertEqual(analyze_spending(groups), original)


if __name__ == "__main__":
    unittest.main()
