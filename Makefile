.PHONY: install run small test lint web data serve demo

install:            ## Install the Python package with dev tools
	pip install -e ".[dev]"

run:                ## Full pipeline on the simulated network (about 2.5 minutes on 2 cores)
	demand-agent run --out artifacts

small:              ## The same pipeline on a tiny world (a few seconds)
	demand-agent run --small --out artifacts

test:               ## Unit and integration tests
	pytest

lint:
	ruff check .
	ruff format --check .

data: run           ## Refresh the data the dashboard ships with
	cp artifacts/dashboard.json web/public/data/dashboard.json

web:                ## Build the dashboard into web/dist
	cd web && npm ci && npm run build

serve:              ## Serve the dashboard and the API at http://127.0.0.1:8000
	demand-agent serve --artifacts artifacts

demo: install run data web serve
