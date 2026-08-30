#!/usr/bin/env bash
set -euo pipefail

python -m ruff check app tests alembic
python -m compileall -q app tests alembic
python scripts/export_openapi.py --check
python -m pytest
alembic upgrade head
alembic check
