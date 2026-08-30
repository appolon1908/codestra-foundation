from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import app

TARGET = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != expected:
            raise SystemExit("contracts/openapi.json is stale; run scripts/export_openapi.py")
        return
    TARGET.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
