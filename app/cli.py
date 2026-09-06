"""Minimal command-line interface for frozen System C."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict

from app.controller import ApplicationController
from app.config import Settings
from app.runtime import RuntimeValidationError, build_runtime


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ask the evidence-aware Singapore population prototype."
    )
    parser.add_argument("question", help="Population or demographic question")
    parser.add_argument(
        "--json", action="store_true", help="Emit the structured presentation result as JSON"
    )
    return parser.parse_args()


def run(question: str) -> tuple[int, dict]:
    try:
        result = ApplicationController(build_runtime()).answer(question)
    except RuntimeValidationError as exc:
        return 2, {
            "status": "STARTUP_ERROR",
            "message": str(exc),
            "error_code": "RUNTIME_VALIDATION_ERROR",
        }
    payload = asdict(result)
    return (2 if result.error_code else 0), payload


def main() -> None:
    args = _arguments()
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    exit_code, payload = run(args.question)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Status: {payload['status']}")
        print(payload.get("answer") or payload["message"])
        for limitation in payload.get("limitations", []):
            print(f"Limitation: {limitation}")
        for item in payload.get("evidence", []):
            print(
                f"Evidence: {item['dataset']} | {item['geography']} | "
                f"{item['year']} | {item['value']} | {item['evidence_id']}"
            )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
