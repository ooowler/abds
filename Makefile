.DEFAULT_GOAL = run
.PHONY: run rund services stop clean gen load setup teardown schema

compose = docker compose --env-file .env.local
client = $(compose) exec -T clickhouse sh -c 'exec clickhouse-client --user "$$CLICKHOUSE_USER" --password "$$CLICKHOUSE_PASSWORD" --database "$$CLICKHOUSE_DATABASE" "$$@"' client
tables = dim_realty dim_gauges dim_tariffs dim_users fact_gauge_bindings fact_meter_readings fact_invoice

run:
	$(compose) up -d --build

rund:
	$(compose) up --build

services:
	$(compose) ps

stop:
	$(compose) down

clean:
	$(compose) down -v

gen:
	python3 scripts/generate.py

schema:
	$(compose) up -d --no-recreate --wait clickhouse
	$(client) --multiquery < scripts/schema.sql

load:
	@set -e; for table in $(tables); do test -s "data/$$table.csv"; done
	$(client) --multiquery --query "$(foreach table,$(tables),TRUNCATE TABLE $(table);)"
	@set -e; for table in $(tables); do \
		$(client) --query "INSERT INTO $$table FORMAT CSV" < "data/$$table.csv"; \
	done

setup:
	$(MAKE) schema
	$(MAKE) gen
	$(MAKE) load

teardown:
	$(client) --multiquery --query "$(foreach table,$(tables),DROP TABLE IF EXISTS $(table) SYNC;)"
	rm -rf data/
