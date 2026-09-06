import json
from pathlib import Path

import pytest

from evaluation.final_protocol import (
    ProtocolIntegrityError,
    load_and_validate_protocol,
    refuse_existing_outputs,
    sha256_file,
    validate_frozen_artifacts,
    validate_heldout_checksum,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = PROJECT_ROOT / "evaluation" / "final_evaluation_protocol.freeze.json"
CHECKSUM_PATH = PROJECT_ROOT / "evaluation" / "final_evaluation_protocol.sha256"


FROZEN_SYSTEM_C = {
    "app/answerability.py": "a828c9c6c037566463a64b7e49c3586254de84ab016c9d7c340b2658215eca5d",
    "app/computation.py": "eb0f4ca1e1ad6d6a50ceeb27bcd4928c043a6dd909e5585dee2bc2ba131cf68c",
    "app/generation.py": "d17d2a78408290c23ccf919dafd2719bc6f938d094c589eb58940b7e71f9ffea",
    "app/pipeline.py": "9a098f10a52a0651079917f66257069a9b9728276e4bca8e0c2768a97842983f",
    "evaluation/results/development_grounded_generation_v2_report.jsonl": "8ef36f2d0d148ae9e3ce2ced98e653a7b74d2e2d3f3ad7ca60759bd96b2d29b2",
    "evaluation/results/development_grounded_generation_v2_summary.json": "0ceaa2dac6bc1954f9796bba39549d83f309913c04949c64e7736b63a1050735",
}


def test_selected_system_c_is_frozen() -> None:
    validate_frozen_artifacts(PROJECT_ROOT, FROZEN_SYSTEM_C)


def test_protocol_checksum_and_all_frozen_artifacts_are_valid() -> None:
    protocol = load_and_validate_protocol(PROTOCOL_PATH, CHECKSUM_PATH)

    assert protocol["explicit_statement"] == (
        "The 45-question held-out benchmark has not yet been evaluated under this protocol."
    )
    assert protocol["system_b"]["threshold"] == 0.6387588977813721
    assert len(validate_frozen_artifacts(PROJECT_ROOT, protocol["frozen_artifacts"])) > 50


def test_heldout_checksum_matches_freeze_without_loading_records() -> None:
    protocol = load_and_validate_protocol(PROTOCOL_PATH, CHECKSUM_PATH)
    heldout = protocol["heldout"]

    assert validate_heldout_checksum(
        PROJECT_ROOT, heldout["path"], heldout["sha256"]
    ) == "99eb8be69c33d1744ae15c8891bec5f7e963142e334172753b4fe39361947065"


def test_heldout_checksum_validation_detects_changed_bytes(tmp_path: Path) -> None:
    benchmark = tmp_path / "reserved.jsonl"
    benchmark.write_bytes(b"changed\n")

    with pytest.raises(ProtocolIntegrityError, match="checksum mismatch"):
        validate_heldout_checksum(tmp_path, benchmark.name, "0" * 64)


def test_protocol_checksum_detects_tampering(tmp_path: Path) -> None:
    protocol = tmp_path / "protocol.json"
    protocol.write_text(
        json.dumps({"status": "FROZEN_NOT_RUN", "heldout": {"evaluated": False}}),
        encoding="utf-8",
    )
    checksum = tmp_path / "protocol.sha256"
    checksum.write_text(f"{sha256_file(protocol)}  protocol.json\n", encoding="ascii")
    protocol.write_text("{}", encoding="utf-8")

    with pytest.raises(ProtocolIntegrityError, match="protocol checksum mismatch"):
        load_and_validate_protocol(protocol, checksum)


def test_final_run_refuses_any_existing_output(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    second.write_text("reserved", encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing overwrite"):
        refuse_existing_outputs([first, second])


def test_final_runner_is_the_only_runtime_entry_point_for_reserved_records() -> None:
    final_source = (PROJECT_ROOT / "scripts" / "evaluate_final_heldout.py").read_text(
        encoding="utf-8"
    )
    assert '"evaluation" / "final_evaluation_protocol.freeze.json"' in final_source
    assert "load_and_validate_protocol" in final_source
    assert final_source.index("validate_heldout_checksum(") < final_source.index(
        "load_benchmark(benchmark_path"
    )

    isolated = [
        PROJECT_ROOT / "app" / "baseline_generation.py",
        PROJECT_ROOT / "app" / "comparison_systems.py",
        PROJECT_ROOT / "app" / "threshold_gate.py",
        PROJECT_ROOT / "evaluation" / "threshold_selection.py",
        PROJECT_ROOT / "evaluation" / "system_comparison.py",
        PROJECT_ROOT / "scripts" / "select_threshold.py",
        PROJECT_ROOT / "scripts" / "evaluate_development_systems.py",
    ]
    for path in isolated:
        source = path.read_text(encoding="utf-8").casefold()
        assert "heldout_benchmark" not in source
        assert "held-out benchmark" not in source


def test_original_one_shot_markers_are_preserved() -> None:
    results = PROJECT_ROOT / "evaluation" / "results"
    started = json.loads((results / "final_heldout_run_started.json").read_text())
    completed = json.loads((results / "final_heldout_run_completed.json").read_text())

    assert started["status"] == "RUNNING"
    assert completed["status"] == "COMPLETED"
    assert started["protocol_sha256"] == sha256_file(PROTOCOL_PATH)
    assert completed["protocol_sha256"] == sha256_file(PROTOCOL_PATH)
