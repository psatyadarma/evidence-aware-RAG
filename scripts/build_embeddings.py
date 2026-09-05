"""Build the pinned BGE corpus-embedding artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.retrieval import build_retrieval_units  # noqa: E402
from app.semantic_retrieval import (  # noqa: E402
    MODEL_DIMENSION,
    MODEL_ID,
    MODEL_REVISION,
    build_embedding_artifact,
    load_embedding_artifact,
)


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "data" / "embeddings" / "bge-small-en-v1.5"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--force", action="store_true", help="replace an existing embedding artifact"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    units = build_retrieval_units(PROCESSED_DIR)
    metadata_path = args.artifact_dir / "metadata.json"
    if metadata_path.exists() and not args.force:
        metadata, _ = load_embedding_artifact(
            units,
            args.artifact_dir,
            expected_model_id=MODEL_ID,
            expected_model_revision=MODEL_REVISION,
            expected_dimension=MODEL_DIMENSION,
        )
        print("Existing embedding artifact is current; use --force to rebuild.")
        print(metadata.model_dump_json(indent=2))
        return

    from sentence_transformers import SentenceTransformer, __version__

    encoder = SentenceTransformer(
        MODEL_ID,
        revision=MODEL_REVISION,
        device=args.device,
    )
    metadata = build_embedding_artifact(
        units,
        encoder,
        args.artifact_dir,
        model_id=MODEL_ID,
        model_revision=MODEL_REVISION,
        encoder_library="sentence-transformers",
        encoder_library_version=__version__,
        batch_size=args.batch_size,
        device=args.device,
        expected_dimension=MODEL_DIMENSION,
    )
    artifact_size = sum(
        path.stat().st_size for path in args.artifact_dir.iterdir() if path.is_file()
    )
    output = metadata.model_dump(mode="json")
    output["artifact_size_bytes"] = artifact_size
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
