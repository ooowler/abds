import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def query(sql, **parameters):
    settings = {
        "database": os.environ["CLICKHOUSE_DATABASE"],
        "output_format_json_quote_64bit_integers": 0,
        **{f"param_{name}": value for name, value in parameters.items()},
    }
    host = os.environ["CLICKHOUSE_HOST"]
    port = os.environ["CLICKHOUSE_HTTP_PORT"]
    request = Request(
        f"http://{host}:{port}/?{urlencode(settings)}",
        data=sql.encode(),
        headers={
            "X-ClickHouse-User": os.environ["CLICKHOUSE_USER"],
            "X-ClickHouse-Key": os.environ["CLICKHOUSE_PASSWORD"],
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read()


def select(sql, **parameters):
    return [json.loads(line) for line in query(f"{sql} FORMAT JSONEachRow", **parameters).splitlines()]


def insert(table, *rows):
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    query(f"INSERT INTO {table} FORMAT JSONEachRow\n{payload}")
