# MediaServer developer commands.
#
# The virtualenv lives inside this project (`.venv`) and is managed by pipenv:
#   PIPENV_VENV_IN_PROJECT is exported here so it is impossible to forget.
PIPENV ?= pipenv
PY := $(PIPENV) run python
export PIPENV_VENV_IN_PROJECT := 1
export PIPENV_IGNORE_VIRTUALENVS := 1

.PHONY: setup run serve start stop restart status test lint fmt selfcheck security seed docker clean

setup:            ## create .venv and install runtime + dev packages
	$(PIPENV) install --dev

run:              ## development server on 127.0.0.1:8080
	$(PY) -m mediaserver --dev

serve:            ## production WSGI server (waitress)
	$(PY) -m mediaserver

start:            ## background: LAN + public + tunnel
	./start.sh start

stop:
	./start.sh stop

restart:
	./start.sh restart

status:
	./start.sh status

test:
	$(PIPENV) run pytest -q

lint:
	$(PIPENV) run ruff check .

fmt:
	$(PIPENV) run ruff format .

selfcheck:        ## acceptance checks against a running server
	$(PY) scripts/selfcheck.py --base http://127.0.0.1:8080

security:         ## scan the working tree for personal information
	$(PY) scripts/security_scan.py .

seed:             ## generate a small demo library in ./media
	$(PY) scripts/seed_demo.py

docker:
	docker compose -f deploy/docker/compose.yaml up --build

clean:
	rm -rf .venv .pytest_cache .ruff_cache data/test-media
