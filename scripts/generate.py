import csv
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

START = date(2023, 11, 1)
END = date(2026, 11, 1)
TARIFF_CHANGE = date(2025, 11, 1)
GAUGE_CHANGE = date(2025, 5, 1)
OBJECTS = 1000
DAY = timedelta(days=1)
DATA = Path("data")
TABLES = (
    "dim_realty",
    "dim_gauges",
    "dim_tariffs",
    "dim_users",
    "fact_gauge_bindings",
    "fact_meter_readings",
    "fact_invoice",
)


def write_readings(realty_id, replacement, readings, invoices):
    gauge = realty_id
    day_total = night_total = 0
    month_day = month_night = 0
    for day_number in range((END - START).days):
        reading_date = START + day_number * DAY
        if replacement and reading_date == GAUGE_CHANGE:
            gauge = replacement
            day_total = night_total = 0

        day_kw = 2 + (realty_id + day_number) % 9
        night_kw = 1 + (realty_id + day_number) % 4
        day_total += day_kw
        night_total += night_kw
        month_day += day_kw
        month_night += night_kw

        reading_key = (realty_id, realty_id, 1, gauge, reading_date)
        if (realty_id * 31 + day_number) % 100 < 3:
            readings.writerow((*reading_key, day_total - 1, night_total, f"{reading_date} 08:00:00"))
        readings.writerow((*reading_key, day_total, night_total, f"{reading_date} 18:00:00"))

        tomorrow = reading_date + DAY
        if tomorrow.month != reading_date.month:
            day_price, night_price = (620, 310) if reading_date >= TARIFF_CHANGE else (540, 270)
            price = month_day * day_price + month_night * night_price
            invoices.writerow(
                (realty_id, realty_id, 1, gauge, month_day, month_night, price,
                 tomorrow.replace(day=10), f"{reading_date} 20:00:00")
            )
            month_day = month_night = 0


def generate():
    DATA.mkdir(exist_ok=True)
    started = f"{START} 00:00:00"
    tariff_changed = f"{TARIFF_CHANGE} 00:00:00"
    gauge_changed = f"{GAUGE_CHANGE} 00:00:00"
    with ExitStack() as files:
        realties, gauges, tariffs, users, bindings, readings, invoices = [
            csv.writer(files.enter_context((DATA / f"{table}.csv").open("w", encoding="utf-8", newline="")),
                       lineterminator="\n")
            for table in TABLES
        ]
        tariffs.writerow((1, "День–Ночь", 540, 270, started, tariff_changed, started, tariff_changed))
        tariffs.writerow((1, "День–Ночь", 620, 310, tariff_changed, "2099-01-01 00:00:00",
                          tariff_changed, tariff_changed))

        for realty_id in range(1, OBJECTS + 1):
            house = (realty_id - 1) // 50 + 1
            unit = (realty_id - 1) % 50 + 1
            flat = unit % 10 != 0
            realties.writerow(
                (realty_id, f"Санкт-Петербург, ул. Энергетиков, д. {house}",
                 35 + unit, "flat" if flat else "nrcp", str(unit) if flat else "",
                 1 + unit % 3 if flat else 0, (unit - 1) % 10 + 1 if flat else 1,
                 (unit - 1) // 10 + 1, started)
            )
            users.writerow((realty_id, f"Смирнов Алексей Ильич {realty_id}",
                            f"+7900{realty_id:07d}", "", started, started))
            gauges.writerow((realty_id, "Меркурий 201", "Россия", started))
            bindings.writerow((realty_id, realty_id, started, 1, 1))

            replacement = OBJECTS + realty_id if realty_id % 10 == 0 else None
            if replacement:
                gauges.writerow((replacement, "Энергомера CE102", "Россия", gauge_changed))
                bindings.writerow((realty_id, realty_id, started, -1, 1))
                bindings.writerow((replacement, realty_id, gauge_changed, 1, 2))

            write_readings(realty_id, replacement, readings, invoices)


if __name__ == "__main__":
    generate()
