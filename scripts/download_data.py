"""Download and normalize the reviewed official population datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ingestion import ingest_payload, validate_api_payload, write_jsonl
from app.sources import OPEN_DATA_LICENCE_URL, SOURCES, SOURCES_BY_ID, SourceSpec


LOGGER = logging.getLogger("population-data")
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MANIFEST_NAME = "manifest.json"


class DownloadError(RuntimeError):
    """An official source could not be downloaded or verified."""


def _read_manifest(raw_dir: Path) -> dict[str, Any]:
    path = raw_dir / MANIFEST_NAME
    if not path.exists():
        return {
            "licence_url": OPEN_DATA_LICENCE_URL,
            "sources": {},
        }
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DownloadError(f"cannot read {path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), dict):
        raise DownloadError(f"invalid manifest structure in {path}")
    return manifest


def _atomic_write_bytes(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json(destination: Path, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(destination, encoded)


def _decode_and_validate(content: bytes, source: SourceSpec) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError(f"{source.source_id}: response was not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise DownloadError(f"{source.source_id}: response was not a JSON object")
    validate_api_payload(payload, source)
    return payload


def _download(source: SourceSpec, timeout_seconds: float) -> tuple[bytes, dict[str, Any]]:
    request = Request(
        source.api_url,
        headers={"User-Agent": "evidence-aware-sg-rag/0.1 (+research assessment)"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise DownloadError(f"{source.source_id}: HTTP {status}")
            content = response.read()
    except HTTPError as exc:
        raise DownloadError(f"{source.source_id}: HTTP {exc.code}") from exc
    except URLError as exc:
        raise DownloadError(f"{source.source_id}: network error: {exc.reason}") from exc
    if not content:
        raise DownloadError(f"{source.source_id}: empty HTTP response")
    return content, _decode_and_validate(content, source)


def _verify_cached(
    source: SourceSpec,
    raw_path: Path,
    manifest_entry: Optional[dict[str, Any]],
) -> tuple[bytes, dict[str, Any]]:
    if not isinstance(manifest_entry, dict):
        raise DownloadError(
            f"{source.source_id}: raw file exists without provenance manifest; "
            "rerun with --force"
        )
    content = raw_path.read_bytes()
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != manifest_entry.get("sha256"):
        raise DownloadError(
            f"{source.source_id}: cached raw file hash differs from manifest; "
            "rerun with --force"
        )
    return content, _decode_and_validate(content, source)


def _parse_retrieval_date(entry: dict[str, Any], source: SourceSpec) -> date:
    raw_timestamp = entry.get("downloaded_at")
    if not isinstance(raw_timestamp, str):
        raise DownloadError(f"{source.source_id}: manifest has no downloaded_at timestamp")
    try:
        return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise DownloadError(
            f"{source.source_id}: invalid downloaded_at timestamp {raw_timestamp!r}"
        ) from exc


def download_and_ingest(
    sources: tuple[SourceSpec, ...],
    *,
    raw_dir: Path,
    processed_dir: Path,
    force: bool,
    timeout_seconds: float,
) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(raw_dir)

    payloads: dict[str, dict[str, Any]] = {}
    for source in sources:
        raw_path = raw_dir / source.raw_filename
        existing_entry = manifest["sources"].get(source.source_id)
        if raw_path.exists() and not force:
            content, payload = _verify_cached(source, raw_path, existing_entry)
            LOGGER.info("Using verified cached source %s", raw_path)
        else:
            LOGGER.info("Downloading %s", source.api_url)
            content, payload = _download(source, timeout_seconds)
            downloaded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            existing_entry = {
                "api_url": source.api_url,
                "bytes": len(content),
                "catalog_url": source.catalog_url,
                "dataset_id": source.dataset_id,
                "downloaded_at": downloaded_at,
                "publisher": "Singapore Department of Statistics (SingStat)",
                "sha256": hashlib.sha256(content).hexdigest(),
                "title": source.title,
            }
            _atomic_write_bytes(raw_path, content)
            manifest["sources"][source.source_id] = existing_entry
            LOGGER.info("Saved %d bytes to %s", len(content), raw_path)
        payloads[source.source_id] = payload

    _atomic_write_json(raw_dir / MANIFEST_NAME, manifest)

    for source in sources:
        entry = manifest["sources"][source.source_id]
        retrieved_at = _parse_retrieval_date(entry, source)
        records = ingest_payload(payloads[source.source_id], source, retrieved_at)
        destination = processed_dir / source.processed_filename
        write_jsonl(records, destination)
        LOGGER.info("Wrote %d normalized records to %s", len(records), destination)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        choices=sorted(SOURCES_BY_ID),
        help="Download only this source; may be repeated. Defaults to all selected sources.",
    )
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--force", action="store_true", help="Replace verified cached raw data.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    selected = (
        tuple(SOURCES_BY_ID[source_id] for source_id in args.source)
        if args.source
        else SOURCES
    )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        download_and_ingest(
            selected,
            raw_dir=args.raw_dir,
            processed_dir=args.processed_dir,
            force=args.force,
            timeout_seconds=args.timeout_seconds,
        )
    except (DownloadError, ValueError, OSError) as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
