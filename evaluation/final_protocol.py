"""Integrity and immutable-output helpers for the preregistered final run."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Mapping


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProtocolIntegrityError(RuntimeError):
    """The final protocol or one of its frozen inputs failed validation."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_protocol_checksum(path: Path) -> str:
    try:
        checksum = path.read_text(encoding="ascii").strip().split()[0]
    except (OSError, IndexError) as exc:
        raise ProtocolIntegrityError(f"cannot read protocol checksum: {path}") from exc
    if not SHA256_RE.fullmatch(checksum):
        raise ProtocolIntegrityError(f"invalid SHA-256 checksum file: {path}")
    return checksum


def load_and_validate_protocol(protocol_path: Path, checksum_path: Path) -> dict:
    expected = read_protocol_checksum(checksum_path)
    actual = sha256_file(protocol_path)
    if actual != expected:
        raise ProtocolIntegrityError(
            f"protocol checksum mismatch: expected {expected}, got {actual}"
        )
    try:
        payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolIntegrityError(f"invalid protocol JSON: {protocol_path}") from exc
    if payload.get("status") != "FROZEN_NOT_RUN":
        raise ProtocolIntegrityError("protocol status must be FROZEN_NOT_RUN")
    if payload.get("heldout", {}).get("evaluated") is not False:
        raise ProtocolIntegrityError("protocol must record held-out as not evaluated")
    return payload


def _project_file(project_root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ProtocolIntegrityError(f"artifact path must be project-relative: {relative_path!r}")
    root = project_root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ProtocolIntegrityError(f"artifact path escapes project root: {relative_path}")
    return candidate


def validate_frozen_artifacts(
    project_root: Path, artifacts: Mapping[str, str]
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for relative_path, expected in sorted(artifacts.items()):
        if not SHA256_RE.fullmatch(expected):
            raise ProtocolIntegrityError(
                f"invalid frozen checksum for {relative_path}: {expected!r}"
            )
        path = _project_file(project_root, relative_path)
        if not path.is_file():
            raise ProtocolIntegrityError(f"frozen artifact is missing: {relative_path}")
        actual = sha256_file(path)
        if actual != expected:
            raise ProtocolIntegrityError(
                f"frozen artifact changed: {relative_path}; expected {expected}, got {actual}"
            )
        validated[relative_path] = actual
    return validated


def validate_heldout_checksum(
    project_root: Path, relative_path: str, expected_sha256: str
) -> str:
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ProtocolIntegrityError("invalid preregistered held-out checksum")
    path = _project_file(project_root, relative_path)
    if not path.is_file():
        raise ProtocolIntegrityError(f"held-out benchmark is missing: {relative_path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ProtocolIntegrityError(
            f"held-out checksum mismatch: expected {expected_sha256}, got {actual}"
        )
    return actual


def refuse_existing_outputs(paths: Iterable[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "final held-out output already exists; refusing overwrite: " + ", ".join(existing)
        )


def write_immutable_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def write_immutable_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
