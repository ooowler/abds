# ЭнергоСбыт

Учебный API для показаний счётчиков и счетов за электричество. В лабораторной работе №1 данные хранятся в ClickHouse 26.7, сервис работает на Python и FastAPI

[![Energosbyt Labs](https://github.com/ooowler/abds/actions/workflows/energosbyt.lab.yml/badge.svg)](https://github.com/ooowler/abds/actions/workflows/energosbyt.lab.yml)

## Запуск

Нужны Docker с Compose v2, Make и Python 3.10 или новее для генерации CSV

```sh
make run
make setup
curl -i http://localhost:8080/ping
```

`make setup` создаёт таблицы, вызывает `make gen` для генерации CSV и `make load` для загрузки. `make load` заново заполняет все семь таблиц, включая изменённые через API данные. Если импорт прервался, повторный запуск начинает загрузку с очистки таблиц. Генерация и импорт используют по одному CSV на таблицу, без запросов на отдельные строки

`make teardown` удаляет таблицы и папку `data/`. После него можно снова выполнить `make setup`. `make stop` останавливает контейнеры, данные ClickHouse сохраняются в `volume/`

Порты и параметры подключения заданы в [.env.local](.env.local). Ссылка `.env` на этот файл позволяет командам Compose без `--env-file` использовать ту же конфигурацию. С хоста API доступен на порту 8080, ClickHouse на 8123 (HTTP) и 9000 (нативный протокол). Порт ClickHouse внутри проекта остаётся 8123 независимо от порта, опубликованного на хосте

## API

[Коллекция запросов](api/energosbyt.http) открывается в IDE с HTTP Client. После запуска приложения доступен [Swagger UI](http://localhost:8080/docs)

| Запрос | Результат |
| --- | --- |
| `GET /ping` | 200, пустое тело |
| `GET /users/{id}` | Собственник и предыдущий телефон |
| `PATCH /users/{id}` | 204 при изменении ФИО или телефона |
| `GET /realty/{id}` | Карточка объекта |
| `POST /realty/{id}/readings` | 201 при записи показания или корректировки |
| `GET /realty/{id}/readings?date_from=…&date_to=…` | Актуальные показания за включительный период |
| `GET /realty/{id}/invoices?limit=10&offset=0` | Счета от новых к старым и общее количество |
| `GET /tariffs?date=…` | Версия тарифа, действовавшая на дату |
| `POST /realty/{id}/gauges` | 201 при установке счётчика |

Ошибки возвращаются как `{"message": "..."}`. Например, для отсутствующего объекта ответ будет 404 с `{"message":"Not found"}`, а для `limit=-1` — 400 с `{"message":"invalid \"limit\" parameter"}`

## Схема и ключи сортировки

Факты связываются с измерениями по `realty_id`, `gauge_id`, `user_id` и `tariff_id`. DDL находится в [scripts/schema.sql](scripts/schema.sql)

| Таблица | Ключ и партиционирование | Причина |
| --- | --- | --- |
| `dim_realty`, `dim_gauges` | `id`, без партиций | Поиск по идентификатору в небольших справочниках |
| `dim_users` | `id`, без партиций | Версии одного владельца объединяются по `id`, побеждает больший `updated_at` |
| `dim_tariffs` | `(id, valid_from)`, без партиций | Интервалы одного тарифа сохраняются, правки одного интервала заменяются по `updated_at` |
| `fact_gauge_bindings` | `(realty_id, gauge_id)`, без партиций | Поиск установки по объекту, пара с одинаковой версией остаётся в одной партиции |
| `fact_meter_readings` | `(realty_id, reading_date)`, месяц показания | Чтение по объекту и периоду, все корректировки дня попадают в одну партицию |
| `fact_invoice` | `(realty_id, created_at)`, месяц выставления | Выборка счетов объекта в порядке даты и хранение истории по месяцам |

## Данные и объёмы

Период показаний — с 1 ноября 2023 по 31 октября 2026 включительно. Генератор создаёт 20 домов, 1 000 объектов, 1 000 владельцев, 1 100 счётчиков, 2 версии тарифа и 36 000 счетов. Для 100 объектов генерируется замена счётчика, что даёт 1 200 строк привязок в CSV и 1 000 активных привязок. Для 1 096 000 дней по объектам записывается 32 880 корректировок, то есть 3% от количества исходных показаний

SQL из следующих примеров можно выполнять в клиенте ClickHouse

```sh
docker compose --env-file .env.local exec clickhouse sh -c \
  'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --database "$CLICKHOUSE_DATABASE"'
```

```sql
SELECT count() FROM dim_realty;
SELECT count() FROM dim_users FINAL;
SELECT count() FROM dim_gauges;
SELECT id FROM dim_tariffs FINAL GROUP BY id HAVING count() >= 2;
SELECT count(), countIf(version >= 2) FROM fact_gauge_bindings FINAL;
SELECT count() FROM fact_meter_readings FINAL;
SELECT count() FROM fact_invoice;
```

Ожидаемые результаты по порядку — 1 000, 1 000, 1 100, тариф `1`, пара `1000, 100`, 1 096 000 и 36 000. Число физических строк в таблицах с фоновым слиянием может меняться

## Воспроизведение сценариев SCD

Начните со свежего `make setup`. Блоки запросов в [api/energosbyt.http](api/energosbyt.http) пронумерованы в том же порядке

### 1. Фамилия владельца (SCD 1)

Выполните PATCH для владельца 1 с `fio` равным `Соколов Алексей Ильич`. GET и запрос ниже показывают новое значение. После слияния частей предыдущая строка удаляется движком

```sql
SELECT fio FROM dim_users FINAL WHERE id = 1;
```

### 2. Две смены телефона (SCD 3)

Передайте владельцу 1 сначала `+79111111111`, затем `+79222222222`. GET и SQL показывают `+79222222222` в `phone` и `+79111111111` в `phone_prev`

```sql
SELECT phone, phone_prev FROM dim_users FINAL WHERE id = 1;
```

### 3. Граница действия тарифа (SCD 2)

Запросы `GET /tariffs?date=2025-10-31` и `GET /tariffs?date=2025-11-01` возвращают цены `540/270` и `620/310` копеек. Интервал заканчивается без включения границы

```sql
SELECT valid_from, valid_to, price_day_kwph, price_night_kwph
FROM dim_tariffs FINAL
WHERE id = 1 ORDER BY valid_from;
```

### 4. Пересчёт старого счёта (SCD 2)

Для счёта объекта 1 за октябрь 2025 подбираем тариф по дате выставления. `recalculated` равен сохранённому `price`. Запрос только читает данные, поэтому исходный счёт сохраняется

```sql
SELECT i.created_at, i.price, t.price_day_kwph, t.price_night_kwph,
       i.day_kw * t.price_day_kwph + i.night_kw * t.price_night_kwph AS recalculated
FROM fact_invoice AS i
INNER JOIN (SELECT * FROM dim_tariffs FINAL) AS t ON i.tariff_id = t.id
WHERE i.realty_id = 1 AND i.deadline = '2025-11-10'
  AND t.valid_from <= i.created_at AND i.created_at < t.valid_to;
```

### 5. Корректировка показания

Дважды передайте показание объекта 1 за 1 октября 2026. Во втором запросе измените `day_kw` с 8 500 на 8 510. GET за этот день и SQL возвращают ровно одну строку с `day_kw = 8510`

```sql
SELECT reading_date, day_kw, night_kw, created_at
FROM fact_meter_readings FINAL
WHERE realty_id = 1 AND reading_date = '2026-10-01';
```

### 6. Замена счётчика

До запроса на объекте 1 активен счётчик 1 с версией 1. POST с `gauge_id = 2` добавляет аннулирование старой установки и новую строку одним INSERT. После запроса SQL показывает одну строку со счётчиком 2, `sign = 1` и `version = 2`

```sql
SELECT gauge_id, sign, version
FROM fact_gauge_bindings FINAL WHERE realty_id = 1;
```

### 7. Удаление ошибочного показания

Вставьте через POST пример с лишним нулём из коллекции. Он использует отдельную дату 1 ноября 2026, чтобы удаление не затронуло сгенерированные показания. Первый SELECT находит ошибочное значение, второй возвращает 0 строк

```sql
SELECT day_kw FROM fact_meter_readings FINAL
WHERE realty_id = 1 AND reading_date = '2026-11-01';

ALTER TABLE fact_meter_readings
DELETE WHERE realty_id = 1 AND reading_date = '2026-11-01' AND day_kw = 85100
SETTINGS mutations_sync = 2;

SELECT count() FROM fact_meter_readings FINAL
WHERE realty_id = 1 AND reading_date = '2026-11-01';
```

[Условия лабораторной](https://github.com/sitnikovik/abds/blob/master/lab/01/README.md) · [Правила сдачи](CONTRIBUTING.md)
