from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "foundation-routes.v1.json"
OUTPUT = ROOT / "docs" / "API_CATALOGUE.md"


def render() -> str:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    lines = [
        "# Foundation API catalogue",
        "",
        "This file is generated from `contracts/foundation-routes.v1.json`; do not edit it by hand.",
        "",
        "## Base URLs",
        "",
        f"- Local: `{contract['baseUrls']['local']}`",
        f"- Container network: `{contract['baseUrls']['container']}`",
        f"- Production: **{contract['baseUrls']['productionStatus']}**",
        "",
        "No production DNS name or deployment is claimed by this repository.",
        "",
        "## Protocol rules",
        "",
        f"- Authentication: {contract['rules']['authentication']}",
        f"- Effectful headers: `{', '.join(contract['rules']['effectfulHeaders'])}`",
        f"- Deletion: {contract['rules']['deletion']}",
        "",
        "## Routes",
        "",
        "| Method | URL | Required scope | Effect | Logic |",
        "|---|---|---|---|---|",
    ]
    for route in contract["routes"]:
        lines.append(
            f"| {route['method']} | `{route['path']}` | `{route['scope']}` | "
            f"{route['effect']} | {route['logic']} |"
        )
    lines.extend(
        [
            "",
            "## State-preserving removals",
            "",
            "There are deliberately no `DELETE` routes. Identities are revoked, preferences are unsubscribed, "
            "entitlements and meters are disabled, subscriptions are cancelled, accounts are closed, and invoices "
            "are voided or refunded. Consent, usage, audit, invoice lines, and merge evidence remain append-only.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("docs/API_CATALOGUE.md is stale; run scripts/export_api_catalogue.py")
        return
    OUTPUT.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
