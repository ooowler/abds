import os
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException, Path, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import insert, select

UINT64_MAX = 2**64 - 1
MIN_READING_DATE = date(1970, 1, 1)
MAX_READING_DATE = date(2149, 6, 6)
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

EntityId = Annotated[int, Path(ge=1, le=UINT64_MAX)]
BodyId = Annotated[int, Field(strict=True, ge=1, le=UINT64_MAX)]
Counter = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
PageValue = Annotated[int, Query(ge=0, le=UINT64_MAX)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]

app = FastAPI(title="ЭнергоСбыт")
write_lock = Lock()


class UserPatch(BaseModel):
    fio: NonEmpty | None = None
    phone: NonEmpty | None = None


class Reading(BaseModel):
    user_id: BodyId
    gauge_id: BodyId
    tariff_id: BodyId
    reading_date: str
    day_kw: Counter
    night_kw: Counter


class Gauge(BaseModel):
    gauge_id: BodyId


def invalid(name, kind):
    return HTTPException(status_code=400, detail=f'invalid "{name}" {kind}')


def not_found():
    return HTTPException(status_code=404, detail="Not found")


@app.exception_handler(StarletteHTTPException)
async def http_error(request, error):
    return JSONResponse(status_code=error.status_code, content={"message": error.detail})


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    source, *location = error.errors()[0]["loc"]
    name = location[0] if location and type(location[0]) is str else "body"
    failure = invalid(name, "field" if source == "body" else "parameter")
    return await http_error(request, failure)


def parse_date(value, name, kind="parameter"):
    if value:
        try:
            result = date.fromisoformat(value)
            if result.isoformat() == value:
                return result
        except ValueError:
            pass
    raise invalid(name, kind)


def next_timestamp(previous=None):
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    if previous:
        now = max(now, datetime.fromisoformat(previous) + timedelta(seconds=1))
    return now.strftime(DATETIME_FORMAT)


def user_row(user_id, columns):
    rows = select(
        f"SELECT {columns} FROM dim_users FINAL WHERE id = {{user_id:UInt64}}",
        user_id=user_id,
    )
    if not rows:
        raise not_found()
    return rows[0]


def require_realty(realty_id):
    if not select("SELECT id FROM dim_realty WHERE id = {realty_id:UInt64}", realty_id=realty_id):
        raise not_found()


@app.get("/ping")
def ping():
    return Response()


@app.get("/users/{user_id}")
def get_user(user_id: EntityId):
    return user_row(user_id, "id, fio, phone, phone_prev")


@app.patch("/users/{user_id}", status_code=204)
def update_user(user_id: EntityId, patch: UserPatch):
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        raise invalid("fio", "field")
    for field, value in changes.items():
        if value is None or not value.strip():
            raise invalid(field, "field")

    with write_lock:
        row = user_row(user_id, "id, fio, phone, phone_prev, created_at, updated_at")
        if "phone" in changes and row["phone"] != changes["phone"]:
            row["phone_prev"] = row["phone"]
        row.update(changes, updated_at=next_timestamp(row["updated_at"]))
        insert("dim_users", row)
    return Response(status_code=204)


@app.get("/realty/{realty_id}")
def get_realty(realty_id: EntityId):
    rows = select(
        """SELECT id, address, square, type, number, rooms, floor, porch
           FROM dim_realty WHERE id = {realty_id:UInt64}""",
        realty_id=realty_id,
    )
    if not rows:
        raise not_found()
    return rows[0]


@app.post("/realty/{realty_id}/readings", status_code=201)
def add_reading(realty_id: EntityId, reading: Reading):
    reading_date = parse_date(reading.reading_date, "reading_date", "field")
    if not MIN_READING_DATE <= reading_date <= MAX_READING_DATE:
        raise invalid("reading_date", "field")

    with write_lock:
        row = select(
            """
            SELECT
                EXISTS(SELECT 1 FROM dim_realty WHERE id = {realty_id:UInt64})
                    AND EXISTS(SELECT 1 FROM dim_users WHERE id = {user_id:UInt64})
                    AND EXISTS(SELECT 1 FROM dim_gauges WHERE id = {gauge_id:UInt64})
                    AND EXISTS(SELECT 1 FROM dim_tariffs WHERE id = {tariff_id:UInt64})
                    AS found,
                (SELECT maxOrNull(created_at) FROM fact_meter_readings
                 WHERE realty_id = {realty_id:UInt64} AND reading_date = {reading_date:Date})
                    AS previous
            """,
            realty_id=realty_id,
            user_id=reading.user_id,
            gauge_id=reading.gauge_id,
            tariff_id=reading.tariff_id,
            reading_date=reading_date,
        )[0]
        if not row["found"]:
            raise not_found()
        insert(
            "fact_meter_readings",
            reading.model_dump() | {"realty_id": realty_id, "created_at": next_timestamp(row["previous"])},
        )
    return Response(status_code=201)


@app.get("/realty/{realty_id}/readings")
def list_readings(realty_id: EntityId, date_from: str = "", date_to: str = ""):
    start = parse_date(date_from, "date_from")
    end = parse_date(date_to, "date_to")
    if start > end:
        raise invalid("date_from", "parameter")
    require_realty(realty_id)
    readings = []
    if start <= MAX_READING_DATE and end >= MIN_READING_DATE:
        readings = select(
            """SELECT reading_date, user_id, tariff_id, gauge_id, day_kw, night_kw
               FROM fact_meter_readings FINAL
               WHERE realty_id = {realty_id:UInt64} AND reading_date BETWEEN {start:Date} AND {end:Date}
               ORDER BY reading_date""",
            realty_id=realty_id, start=max(start, MIN_READING_DATE), end=min(end, MAX_READING_DATE),
        )
    return {"readings": readings, "count": len(readings)}


@app.get("/realty/{realty_id}/invoices")
def list_invoices(realty_id: EntityId, limit: PageValue = 10, offset: PageValue = 0):
    require_realty(realty_id)
    rows = select(
        """SELECT tariff_id, gauge_id, day_kw, night_kw, price, deadline, created_at
           FROM fact_invoice WHERE realty_id = {realty_id:UInt64} ORDER BY created_at DESC""",
        realty_id=realty_id,
    )
    return {"invoices": rows[offset : offset + limit], "count": len(rows)}


@app.get("/tariffs")
def get_tariff(on: Annotated[str, Query(alias="date")] = ""):
    moment = f'{parse_date(on, "date")} 00:00:00'
    tariffs = select(
        """SELECT id, name, price_day_kwph, price_night_kwph, valid_from, valid_to
           FROM dim_tariffs FINAL
           WHERE toString(valid_from) <= {moment:String} AND {moment:String} < toString(valid_to)
           ORDER BY valid_from DESC, id LIMIT 1""",
        moment=moment,
    )
    if not tariffs:
        raise not_found()
    return tariffs[0]


@app.post("/realty/{realty_id}/gauges", status_code=201)
def install_gauge(realty_id: EntityId, gauge: Gauge):
    with write_lock:
        found = select(
            """
            SELECT EXISTS(SELECT 1 FROM dim_realty WHERE id = {realty_id:UInt64})
                AND EXISTS(SELECT 1 FROM dim_gauges WHERE id = {gauge_id:UInt64}) AS found
            """,
            realty_id=realty_id, gauge_id=gauge.gauge_id,
        )[0]["found"]
        if not found:
            raise not_found()
        active = select(
            """SELECT gauge_id, realty_id, created_at, sign, version
               FROM fact_gauge_bindings FINAL WHERE realty_id = {realty_id:UInt64} AND sign = 1
               ORDER BY version DESC LIMIT 1""",
            realty_id=realty_id,
        )
        version = active[0]["version"] + 1 if active else 1
        replacement = {
            "gauge_id": gauge.gauge_id,
            "realty_id": realty_id,
            "created_at": next_timestamp(),
            "sign": 1,
            "version": version,
        }
        rows = [active[0] | {"sign": -1}] if active else []
        insert("fact_gauge_bindings", *rows, replacement)
    return Response(status_code=201)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ["APP_PORT"]))
