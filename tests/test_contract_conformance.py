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


def _routed_scopes_from_source() -> dict[tuple[str, str], str]:
    discovered: dict[tuple[str, str], str] = {}
    for source_path in (ROOT / "app" / "routers").glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        prefix = ""
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "router" for target in node.targets):
                continue
            for keyword in node.value.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    prefix = keyword.value.value
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            route = None
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "router":
                    continue
                if decorator.func.attr not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if decorator.args and isinstance(decorator.args[0], ast.Constant):
                    route = (decorator.func.attr.upper(), prefix + decorator.args[0].value)
            if route is None:
                continue
            scopes: set[str] = set()
            for call in (candidate for candidate in ast.walk(node) if isinstance(candidate, ast.Call)):
                if isinstance(call.func, ast.Name) and call.func.id == "require_admin":
                    scopes.add("foundation.admin")
                if isinstance(call.func, ast.Name) and call.func.id == "authorize" and len(call.args) >= 3:
                    if isinstance(call.args[2], ast.Constant):
                        scopes.add(call.args[2].value)
            assert len(scopes) == 1, f"route must enforce exactly one discoverable scope: {route} -> {scopes}"
            discovered[route] = scopes.pop()
    return discovered


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


def test_route_catalogue_exactly_matches_application_urls():
    document = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    catalogue = json.loads((ROOT / "contracts" / "foundation-routes.v1.json").read_text(encoding="utf-8"))
    actual = {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    declared = {(route["method"], route["path"]) for route in catalogue["routes"]}
    assert declared == actual, {
        "catalogued_not_routed": sorted(declared - actual),
        "routed_not_catalogued": sorted(actual - declared),
    }
    assert len(declared) == len(catalogue["routes"]), "duplicate route catalogue entry"


def test_route_catalogue_scopes_match_source_authorization():
    catalogue = json.loads((ROOT / "contracts" / "foundation-routes.v1.json").read_text(encoding="utf-8"))
    declared = {
        (route["method"], route["path"]): route["scope"]
        for route in catalogue["routes"]
        if route["scope"] != "PUBLIC"
    }
    assert declared == _routed_scopes_from_source()


def test_generated_api_catalogue_is_current():
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_api_catalogue.py"), "--check"],
        check=True,
    )


def test_effectful_routes_require_idempotency_and_correlation_headers():
    document = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            if method not in {"post", "put", "patch", "delete"} or path.endswith("/resolve"):
                continue
            parameters = {item["name"]: item for item in operation.get("parameters", [])}
            assert parameters["Idempotency-Key"]["required"] is True
            assert parameters["X-Correlation-ID"]["required"] is True
