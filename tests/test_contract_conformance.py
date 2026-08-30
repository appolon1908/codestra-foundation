from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _emitted_event_types() -> set[str]:
    values: set[str] = set()
    for path in (ROOT / "app" / "routers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "record_change":
                continue
            for keyword in node.keywords:
                if keyword.arg == "event_type" and isinstance(keyword.value, ast.Constant):
                    values.add(keyword.value.value)
    return values


def test_declared_event_types_exactly_match_source_emissions():
    schema = json.loads((ROOT / "contracts" / "foundation-event.v1.schema.json").read_text(encoding="utf-8"))
    declared = set(schema["properties"]["event_type"]["enum"])
    emitted = _emitted_event_types()
    assert declared == emitted, {
        "declared_not_emitted": sorted(declared - emitted),
        "emitted_not_declared": sorted(emitted - declared),
    }


def test_openapi_contract_is_current():
    from app.main import app

    expected = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    assert (ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8") == expected


def test_effectful_routes_require_idempotency_and_correlation_headers():
    document = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            if method not in {"post", "put", "patch", "delete"} or path.endswith("/resolve"):
                continue
            parameters = {item["name"]: item for item in operation.get("parameters", [])}
            assert parameters["Idempotency-Key"]["required"] is True
            assert parameters["X-Correlation-ID"]["required"] is True
