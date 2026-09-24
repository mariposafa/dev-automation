"""Expense profiling service. Simple moving average, without income or forecasting."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import os

from bson.decimal128 import Decimal128
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from config import DatabaseConfig


@dataclass(frozen=True)
class ProfilingConfig:
    window_months: int = 3

    @classmethod
    def from_env(cls):
        value = os.environ.get("PROFILING_WINDOW_MONTHS", "3")
        try:
            months = int(value)
        except (ValueError, TypeError):
            raise RuntimeError("PROFILING_WINDOW_MONTHS harus bilangan bulat 1 sampai 24.") from None
        if not 1 <= months <= 24:
            raise RuntimeError("PROFILING_WINDOW_MONTHS harus bilangan bulat 1 sampai 24.")
        return cls(months)


def month_shift(value: date, offset: int) -> date:
    index = (value.year - 1) * 12 + value.month - 1 + offset
    if not 0 <= index < 9999 * 12:
        raise ValueError("Periode di luar rentang tanggal yang didukung.")
    year, month = divmod(index, 12)
    return date(year + 1, month + 1, 1)


def money(value: Decimal):
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(rounded) if rounded == rounded.to_integral_value() else float(rounded)


def decimal_value(value):
    return value.to_decimal() if isinstance(value, Decimal128) else Decimal(str(value))


def expense_pipeline(start: date, end: date):
    # BSON Decimal avoids overflow/float loss when summing int64 currency amounts.
    valid_amount = {"$and": [
        {"$in": [{"$type": "$amount"}, ["int", "long"]]},
        {"$gte": ["$amount", 0]},
    ]}
    return [
        {"$match": {
            "trx_type": "pembelian",
            "date": {"$gte": datetime.combine(start, datetime.min.time()),
                     "$lt": datetime.combine(end, datetime.min.time())},
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$date", "timezone": "UTC"}},
            "total_expense": {"$sum": {"$cond": [valid_amount, {"$toDecimal": "$amount"}, 0]}},
            "transaction_count": {"$sum": 1},
            "invalid_count": {"$sum": {"$cond": [valid_amount, 0, 1]}},
        }},
        {"$sort": {"_id": 1}},
    ]


def summarize_expenses(groups, target: date, config: ProfilingConfig, today: date):
    target = target.replace(day=1)
    previous = [month_shift(target, -offset).strftime("%Y-%m")
                for offset in range(config.window_months, 0, -1)]
    by_month = {group["_id"]: group for group in groups}
    target_key = target.strftime("%Y-%m")
    current = by_month.get(target_key, {})
    current_total = decimal_value(current.get("total_expense", 0))
    history = [{
        "month": key,
        "total_expense": money(decimal_value(by_month[key]["total_expense"])) if key in by_month else None,
        "transaction_count": by_month.get(key, {}).get("transaction_count", 0),
        "has_expense_records": key in by_month,
    } for key in previous]
    missing = [row["month"] for row in history if not row["has_expense_records"]]
    invalid = sum(by_month.get(key, {}).get("invalid_count", 0) for key in previous + [target_key])
    result = {
        "year": target.year, "month": target.month,
        "method": "simple_moving_average", "window_months": config.window_months,
        "history": history, "missing_months": missing,
        "total_expense": money(current_total),
        "transaction_count": current.get("transaction_count", 0),
        "moving_average": None, "difference": None, "above_average_percent": None,
        "alert": False, "status": "insufficient_history",
        "is_provisional": (target.year, target.month) >= (today.year, today.month),
        "message": "Riwayat pengeluaran belum cukup. Terus catat transaksimu untuk membentuk profil.",
        "note": "Pembanding kebiasaan pengeluaran tercatat, bukan budget atau penilaian kemampuan finansial. Bulan tanpa data tidak dianggap nol; kelengkapan bulan berisi data perlu dipastikan pengguna.",
    }
    if target > today.replace(day=1):
        result.update(status="future_period", message="Transaksi bertanggal masa depan belum dinilai sebagai pengeluaran aktual.")
    elif invalid:
        result.update(status="invalid_data", message="Ada nominal pengeluaran lama yang perlu diperbaiki sebelum profil dihitung.")
    elif not missing:
        total_history = sum((decimal_value(by_month[key]["total_expense"]) for key in previous), Decimal(0))
        average = total_history / config.window_months
        # Compare before rounding, including zero baselines.
        alert = current_total * config.window_months > total_history
        result.update(
            moving_average=money(average), difference=money(current_total - average),
            above_average_percent=money((current_total - average) / average * 100) if average else None,
            alert=alert, status="above_average" if alert else "within_average",
            message=("Pengeluaran bulan ini sudah lebih tinggi dari rata-rata beberapa bulan sebelumnya. "
                     "Yuk, cek kebutuhan tambahan dan atur prioritas pengeluaran berikutnya!") if alert else
                    "Pengeluaran bulan ini belum melewati rata-rata historismu. Terus catat agar pola pengeluaranmu makin jelas.",
        )
    return result


async def profiling_summary(transaction_model, target: date, config: ProfilingConfig, today: date):
    start = month_shift(target, -config.window_months)
    end = month_shift(target, 1)
    groups = await transaction_model.aggregate(expense_pipeline(start, end)).to_list()
    return summarize_expenses(groups, target, config, today)


@asynccontextmanager
async def lifespan(app):
    settings = DatabaseConfig.from_env()
    app.state.config = ProfilingConfig.from_env()
    client = AsyncMongoClient(settings.uri, serverSelectionTimeoutMS=10000)
    try:
        app.state.database = client[settings.database]
        await app.state.database.command("ping")
        yield
    finally:
        await client.close()


app = FastAPI(title="Expense Profiling Service", lifespan=lifespan)


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=3, le=9998)
    month: int = Field(ge=1, le=12)


async def calculate_profile(year, month):
    target = date(year, month, 1)
    settings = app.state.config
    try:
        # Read committed transactions; a check never inserts another transaction.
        cursor = await app.state.database["trx_collection"].aggregate(
            expense_pipeline(month_shift(target, -settings.window_months), month_shift(target, 1)))
        groups = await cursor.to_list(length=None)
        today = datetime.now(timezone(timedelta(hours=7))).date()
        return summarize_expenses(groups, target, settings, today)
    except PyMongoError:
        raise HTTPException(503, "Profil pengeluaran sementara tidak tersedia.") from None


@app.get("/profiling/summary")
async def summary(year: int = Query(ge=3, le=9998), month: int = Query(ge=1, le=12)):
    return await calculate_profile(year, month)


@app.post("/profiling/check")
async def check(body: CheckRequest):
    return await calculate_profile(body.year, body.month)


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    database = getattr(app.state, "database", None)
    if database is None:
        raise HTTPException(503, "Database belum siap.")
    try:
        await database.command("ping")
    except PyMongoError:
        raise HTTPException(503, "Database belum siap.") from None
    return {"status": "ready"}
