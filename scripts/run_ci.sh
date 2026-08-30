#!/usr/bin/env bash
set -euo pipefail

python -m ruff check app tests alembic scripts
python -m compileall -q app tests alembic scripts
python scripts/export_openapi.py --check
python scripts/export_api_catalogue.py --check
python -m pytest
alembic upgrade head
alembic check
