
import re
from contextlib import asynccontextmanager
from io import BytesIO
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError
from typing import Annotated, Literal
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, BeforeValidator, ValidationError
from datetime import date as Date, datetime, time, timedelta, timezone
from beanie import Document, PydanticObjectId, init_beanie
from bson.errors import InvalidId
from pymongo import AsyncMongoClient
from pymongo import ASCENDING, IndexModel
from pymongo.errors import PyMongoError
import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException
from starlette.concurrency import run_in_threadpool
from config import DatabaseConfig
import os
import httpx


@asynccontextmanager
async def lifespan(app: FastAPI):
    # D2: konfigurasi dibaca saat startup; tidak ada kredensial bawaan di kode.
    settings = DatabaseConfig.from_env()
    client = AsyncMongoClient(settings.uri, serverSelectionTimeoutMS=10000)
    try:
        database = client[settings.database]
        await init_beanie(database=database, document_models=[Transaction])
        app.state.database = database
        yield
    finally:
        # Tutup koneksi saat server berhenti, termasuk jika startup gagal.
        await client.close()


app = FastAPI(lifespan=lifespan)

WIB = timezone(timedelta(hours=7))
Amount = Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]
Method = Literal["Cash", "gopay", "bca", "shopee", "mandiri"]
TransactionType = Literal["pembelian", "pemasukan"]


def today_wib() -> Date:
    return datetime.now(WIB).date()


def parse_date(value):
    if value is None or value == "":
        return today_wib()
    if isinstance(value, Date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Tanggal harus berformat YYYY-MM-DD, tanpa jam.")
    try:
        return Date.fromisoformat(value)
    except ValueError:
        raise ValueError("Tanggal tidak valid. Gunakan YYYY-MM-DD.")


InputDate = Annotated[Date, BeforeValidator(parse_date)]


class Transaction(Document):
    # datetime disimpan sebagai BSON Date (ISODate), bukan string.
    date: datetime
    amount: int
    method: str
    desc: str
    trx_type: str

    class Settings:
        name = "trx_collection"
        indexes = [IndexModel([("trx_type", ASCENDING), ("date", ASCENDING)], name="expense_month")]

class RequestNewTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Amount
    method: Method
    desc: str
    trx_type: TransactionType
    date: InputDate = Field(
        default_factory=today_wib,
        description="YYYY-MM-DD. Kosong, null, atau tidak dikirim: tanggal hari ini (WIB).",
        json_schema_extra={"examples": ["2026-09-21"]},
    )


class RequestUpdateTransaction(RequestNewTransaction):
    # PUT memerlukan seluruh kolom seperti tambah transaksi; date tetap opsional.
    pass


def parse_import_date(value):
    if value is None or value == "":
        raise ValueError("Tanggal transaksi lama wajib diisi.")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value):
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").date()
    return parse_date(value)


class ImportedTransaction(RequestNewTransaction):
    # Pertahankan teks metode dari Excel; aturan input manual tidak diubah.
    method: str
    date: Annotated[Date, BeforeValidator(parse_import_date)]


def storage_values(request_body):
    values = request_body.model_dump()
    if "date" in values:
        # MongoDB tidak punya tipe date-only: gunakan jam 00:00 sebagai representasi.
        values["date"] = datetime.combine(values["date"], time.min)
    return values


def transaction_response(trx):
    return {
        "_id": str(trx.id),
        "date": trx.date.date().isoformat(),
        "amount": trx.amount,
        "method": trx.method,
        "desc": trx.desc,
        "trx_type": trx.trx_type,
    }


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        "message": "Input tidak valid. Periksa kolom berikut.",
        "detail": [
            {"field": ".".join(str(part) for part in error["loc"]),
             "message": error["msg"]}
            for error in exc.errors()
        ],
    })

@app.post("/transaction/add")
async def add_transaction(request_body: RequestNewTransaction):
    trx = Transaction(**storage_values(request_body))
    await trx.insert()
    response = transaction_response(trx)
    if trx.trx_type == "pembelian":
        try:
            response["profiling"] = await get_expense_profile(trx.date.date())
        except (httpx.HTTPError, PyMongoError, ValueError, ArithmeticError):
            # Insert already succeeded: returning 500 could invite a duplicate retry.
            response["profiling"] = {
                "status": "unavailable", "alert": None,
                "message": "Transaksi sudah tersimpan. Profil pengeluaran belum tersedia; cek kembali melalui endpoint profiling.",
            }
    else:
        response["profiling"] = {
            "status": "not_applicable", "alert": False,
            "message": "Pemasukan tersimpan dan tidak diperhitungkan dalam profil pengeluaran.",
        }
    return response


async def get_expense_profile(target: Date):
    url = os.environ.get("PROFILING_SERVICE_URL", "http://127.0.0.1:8011").rstrip("/")
    async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
        response = await client.post(url + "/profiling/check", json={
            "year": target.year, "month": target.month,
        })
        response.raise_for_status()
        profile = response.json()
        if not isinstance(profile, dict) or "status" not in profile or "alert" not in profile:
            raise ValueError("Respons profiling tidak valid.")
        return profile


@app.get("/profiling/summary")
async def expense_profile(
    year: Annotated[int, Query(ge=3, le=9998)],
    month: Annotated[int, Query(ge=1, le=12)],
):
    try:
        return await get_expense_profile(Date(year, month, 1))
    except (httpx.HTTPError, PyMongoError, ValueError):
        raise HTTPException(503, "Profil pengeluaran sementara tidak tersedia.") from None


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    database = getattr(app.state, "database", None)
    if database is None:
        raise HTTPException(503, "Database belum siap.")
    try:
        await database.command("ping")
    except PyMongoError:
        raise HTTPException(503, "Database belum siap.") from None
    return {"status": "ready"}

EXCEL_COLUMNS = ["date", "amount", "method", "desc", "trx_type"]
LEGACY_EXCEL_COLUMNS = ["datetime", "amount", "payment_method", "description"]
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 10000


def read_excel_transactions(content):
    try:
        # header=None memungkinkan pemeriksaan nama kolom duplikat sebelum diubah pandas.
        df = pd.read_excel(
            BytesIO(content), engine="openpyxl", sheet_name=0,
            header=None, dtype=object, keep_default_na=False,
            nrows=MAX_IMPORT_ROWS + 2,
            engine_kwargs={"data_only": False},
        )
        header = df.iloc[0].tolist() if not df.empty else []
        columns = [str(value).strip() if value is not None else "" for value in header]
        legacy = len(columns) == len(LEGACY_EXCEL_COLUMNS) and set(columns) == set(LEGACY_EXCEL_COLUMNS)
        if not legacy and (len(columns) != len(EXCEL_COLUMNS) or set(columns) != set(EXCEL_COLUMNS)):
            raise HTTPException(422, {
                "message": "Nama kolom tidak sesuai. Gunakan salah satu format berikut.",
                "columns": EXCEL_COLUMNS,
                "legacy_columns": LEGACY_EXCEL_COLUMNS,
            })

        df = df.iloc[1:].copy()
        df.columns = columns
        if len(df) > MAX_IMPORT_ROWS:
            raise HTTPException(422, "Maksimal 10.000 baris data Excel.")
        df = df.where(pd.notna(df), None)
        transactions, errors = [], []
        for row_number, data in enumerate(df.to_dict(orient="records"), start=2):
            values = list(data.values())
            if all(value is None or value == "" for value in values):
                continue
            if any(isinstance(value, str) and value.startswith("=") for value in values):
                errors.append({"row": row_number, "message": "Gunakan nilai langsung, bukan formula Excel."})
                continue

            if legacy:
                signed_amount = data["amount"]
                if isinstance(signed_amount, float) and signed_amount.is_integer():
                    signed_amount = int(signed_amount)
                if type(signed_amount) is not int or signed_amount == 0:
                    errors.append({"row": row_number, "errors": [{
                        "field": "amount", "message": "Format lama memerlukan angka bulat bukan nol; tanda menentukan tipe transaksi."
                    }]})
                    continue
                data = {
                    "date": data["datetime"],
                    "amount": abs(signed_amount),
                    "method": data["payment_method"],
                    "desc": data["description"],
                    "trx_type": "pembelian" if signed_amount < 0 else "pemasukan",
                }

            # Excel menyimpan sel tanggal sebagai datetime; input API tetap date-only.
            if isinstance(data["date"], datetime) and data["date"].time() == time.min:
                data["date"] = data["date"].date()
            # Sel angka Excel 5000.0 setara 5000; pecahan tetap ditolak.
            if isinstance(data["amount"], float) and data["amount"].is_integer():
                data["amount"] = int(data["amount"])
            try:
                transactions.append(ImportedTransaction.model_validate(data))
            except ValidationError as exc:
                errors.append({"row": row_number, "errors": [
                    {"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
                    for error in exc.errors()
                ]})

        if errors:
            raise HTTPException(422, {
                "message": "Impor dibatalkan; belum ada data disimpan. Perbaiki baris berikut.",
                "errors": errors,
            })
        if not transactions:
            raise HTTPException(422, "Excel tidak berisi transaksi.")
        return transactions
    except (BadZipFile, InvalidFileException, ParseError, KeyError, IndexError, ValueError, OSError):
        raise HTTPException(422, "File tidak dapat dibaca sebagai Excel .xlsx yang valid.")


@app.post("/transaction/import")
async def import_transactions(file: Annotated[UploadFile, File(description="File .xlsx dengan data pada sheet pertama.")]):
    try:
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(422, "Pilih file Excel berformat .xlsx.")
        content = await file.read(MAX_IMPORT_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "Ukuran file maksimal 5 MB.")

    # Periksa semua baris sebelum melakukan penulisan ke database.
    rows = await run_in_threadpool(read_excel_transactions, content)
    result = await Transaction.insert_many([
        Transaction(**storage_values(row)) for row in rows
    ])
    return {
        "message": "Transaksi berhasil diimpor ke MongoDB.",
        "imported_count": len(result.inserted_ids),
    }


@app.get("/transaction")
async def get_transaction(start_date: InputDate, end_date: InputDate):
    if start_date > end_date:
        raise HTTPException(422, "Tanggal awal tidak boleh setelah tanggal akhir.")
    transactions = await Transaction.find(
        Transaction.date >= datetime.combine(start_date, time.min),
        Transaction.date <= datetime.combine(end_date, time.max),
    ).to_list()
    return [transaction_response(trx) for trx in transactions]

def analyze_spending(groups):
    income = sum(g["total_amount"] for g in groups if g["_id"] == "pemasukan")
    expense = sum(g["total_amount"] for g in groups if g["_id"] == "pembelian")
    unknown_count = sum(g["count"] for g in groups if g["_id"] not in ("pemasukan", "pembelian"))
    net_amount = income - expense
    expense_ratio_percent = None

    if unknown_count or any(g.get("min_amount", g["total_amount"]) < 0 for g in groups):
        status = "data_perlu_diperiksa"
        explanation = "Ada tipe transaksi lama yang tidak dikenal atau nominal negatif. Perbaiki sebelum menilai pengeluaran."
    elif not groups:
        status = "belum_ada_transaksi"
        explanation = "Belum ada transaksi pada bulan ini."
    elif income == 0:
        status = "belum_bisa_dinilai"
        explanation = "Pemasukan nol; rasio pembelian terhadap pemasukan tidak dapat dihitung."
    else:
        expense_ratio_percent = round(expense / income * 100, 2)
        # Bandingkan nominal sebelum pembulatan rasio. Count tidak memengaruhi kategori.
        if expense * 100 >= income * 80:
            status = "reckless_spender"
            explanation = "Pembelian mencapai atau melebihi 80% dari pemasukan."
        else:
            status = "big_saver"
            explanation = "Pembelian kurang dari 80% dari pemasukan."

    return {
        "total_income": income,
        "total_expense": expense,
        "net_amount": net_amount,
        "expense_ratio_percent": expense_ratio_percent,
        "status": status,
        "explanation": explanation,
        "unclassified_count": unknown_count,
    }


@app.get("/transaction/summary")
async def summary_by_type(
    year: Annotated[int, Query(ge=1, le=9998)],
    month: Annotated[int, Query(ge=1, le=12)],
):
    start = datetime(year, month, 1)
    # first day of next month
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start,
                    "$lt": end
                }
            }
        },
        {
            "$group": {
                "_id": "$trx_type",
                "total_amount": {
                    "$sum": "$amount"
                },
                "count": {
                    "$sum": 1
                },
                "min_amount": {"$min": "$amount"},
            }
        }
    ]

    groups = await Transaction.aggregate(pipeline).to_list()
    today = today_wib()
    return {
        "year": year,
        "month": month,
        **analyze_spending(groups),
        "is_provisional": (year, month) >= (today.year, today.month),
        "note": "Indikator berdasarkan transaksi tercatat, bukan kesimpulan mutlak perilaku belanja. Sisa periode ini bukan saldo rekening atau tabungan aktual.",
        "groups": [{key: value for key, value in group.items() if key != "min_amount"} for group in groups],
    }


async def find_transaction(transaction_id: str):
    try:
        object_id = PydanticObjectId(transaction_id)
    except (InvalidId, ValueError, TypeError):
        raise HTTPException(422, "ID transaksi harus berupa ObjectId yang valid.")
    trx = await Transaction.get(object_id)
    if trx is None:
        raise HTTPException(404, "Transaksi tidak ditemukan.")
    return trx


@app.put("/transaction/{transaction_id}")
async def update_transaction(transaction_id: str, request_body: RequestUpdateTransaction):
    trx = await find_transaction(transaction_id)
    # Ganti semua kolom transaksi. Date kosong menggunakan tanggal hari ini.
    await trx.set(storage_values(request_body))
    return transaction_response(trx)


@app.delete("/transaction/{transaction_id}")
async def delete_transaction(transaction_id: str):
    trx = await find_transaction(transaction_id)
    result = await trx.delete()
    if result is None or result.deleted_count == 0:
        raise HTTPException(404, "Transaksi tidak ditemukan.")
    return {"message": "Transaksi berhasil dihapus.", "_id": transaction_id}
