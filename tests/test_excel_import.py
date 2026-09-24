import unittest
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import Workbook

from main import EXCEL_COLUMNS, LEGACY_EXCEL_COLUMNS, app, read_excel_transactions


def excel_bytes(rows, columns=EXCEL_COLUMNS):
    workbook = Workbook()
    workbook.active.append(columns)
    for row in rows:
        workbook.active.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class ExcelImportTests(unittest.TestCase):
    def test_valid_dates_blank_rows_and_literal_na(self):
        rows = read_excel_transactions(excel_bytes([
            [datetime(2025, 8, 1), 5000, "Cash", "NA", "pembelian"],
            [None] * 5,
            ["2025-08-02", 10000, "bca", "Gaji", "pemasukan"],
        ]))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].date.isoformat(), "2025-08-01")
        self.assertEqual(rows[0].desc, "NA")
        self.assertEqual(rows[1].date.isoformat(), "2025-08-02")

    def test_legacy_file_preserves_text_and_uses_sign(self):
        rows = read_excel_transactions(excel_bytes([
            [datetime(2026, 8, 1, 13, 9), -243000, "cash", "Belanja bulanan di Indomaret"],
            ["2026-08-02 14:41:00", 6248000, "cash", "Gaji bulan berjalan dari kantor"],
        ], LEGACY_EXCEL_COLUMNS))
        self.assertEqual(rows[0].date.isoformat(), "2026-08-01")
        self.assertEqual(rows[0].amount, 243000)
        self.assertEqual(rows[0].trx_type, "pembelian")
        self.assertEqual(rows[0].method, "cash")
        self.assertEqual(rows[0].desc, "Belanja bulanan di Indomaret")
        self.assertEqual(rows[1].trx_type, "pemasukan")
        self.assertEqual(rows[1].amount, 6248000)

    def test_legacy_invalid_values_and_missing_date(self):
        for amount in [0, 1.5, True, "Rp 243,000"]:
            with self.subTest(amount=amount), self.assertRaises(HTTPException):
                read_excel_transactions(excel_bytes([
                    [datetime(2026, 8, 1), amount, "cash", "Test"]
                ], LEGACY_EXCEL_COLUMNS))
        with self.assertRaises(HTTPException):
            read_excel_transactions(excel_bytes([[None, -100, "cash", "Test"]], LEGACY_EXCEL_COLUMNS))

    def test_bad_headers_empty_and_corrupt_files(self):
        for content in [b"invalid", excel_bytes([]), excel_bytes([], ["date"]),
                        excel_bytes([], ["date", "amount", "method", "desc", "desc"])]:
            with self.subTest(size=len(content)), self.assertRaises(HTTPException):
                read_excel_transactions(content)

    def test_invalid_rows_include_excel_row_number(self):
        for amount in [-1, 1.5, "5000", True, "=2+3"]:
            content = excel_bytes([
                ["2025-08-01", 10, "Cash", "Valid", "pembelian"],
                [None] * 5,
                ["2025-08-01", amount, "Cash", "Invalid", "pembelian"],
            ])
            with self.subTest(amount=amount), self.assertRaises(HTTPException) as caught:
                read_excel_transactions(content)
            self.assertEqual(caught.exception.detail["errors"][0]["row"], 4)

    def test_invalid_import_never_writes(self):
        content = excel_bytes([["2025-08-01", -1, "Cash", "Parkir", "pembelian"]])
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch("main.Transaction.insert_many", new_callable=AsyncMock) as insert:
            response = client.post("/transaction/import", files={"file": ("data.xlsx", content)})
        self.assertEqual(response.status_code, 422)
        insert.assert_not_awaited()

    def test_successful_import_keeps_historical_date(self):
        content = excel_bytes([["2025-08-01", 5000, "Cash", "Parkir", "pembelian"]])
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch("main.Transaction") as document:
            document.insert_many = AsyncMock(return_value=SimpleNamespace(inserted_ids=["id"]))
            response = client.post("/transaction/import", files={"file": ("data.xlsx", content)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported_count"], 1)
        self.assertEqual(document.call_args.kwargs["date"], datetime(2025, 8, 1))


if __name__ == "__main__":
    unittest.main()
