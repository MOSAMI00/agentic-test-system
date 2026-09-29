"""
Unit tests for CoverageExtractor JSON parsing and delta calculation.
Iteration 1.5 Slice C.2.
"""

import json
from pathlib import Path
import pytest

from agentic_test.execution.coverage import (
    CoverageExtractionError,
    CoverageExtractor,
    CoverageMetrics,
    FileCoverageMetrics,
)


# =====================================================================
# 1. Valid Coverage JSON Parsing Tests
# =====================================================================

def test_parse_valid_coverage_json_file(tmp_path: Path) -> None:
    """Verifies reading and parsing a standard coverage.py JSON file from disk."""
    report_data = {
        "meta": {
            "version": "7.6.1",
            "timestamp": "2026-09-28T12:00:00",
            "branch_coverage": True,
        },
        "files": {
            "src/calculator.py": {
                "executed_lines": [1, 2, 4, 6],
                "summary": {
                    "covered_lines": 4,
                    "num_statements": 5,
                    "percent_covered": 80.0,
                    "missing_lines": 1,
                    "excluded_lines": 0,
                    "num_branches": 2,
                    "covered_branches": 2,
                    "missing_branches": 0,
                },
                "missing_lines": [7],
                "excluded_lines": [],
            }
        },
        "totals": {
            "covered_lines": 4,
            "num_statements": 5,
            "percent_covered": 80.0,
            "missing_lines": 1,
            "excluded_lines": 0,
            "num_branches": 2,
            "covered_branches": 2,
            "missing_branches": 0,
        },
    }
    report_file = tmp_path / "coverage.json"
    report_file.write_text(json.dumps(report_data), encoding="utf-8")

    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_json(report_file)

    assert metrics.is_valid is True
    assert metrics.error_message is None
    assert metrics.line_coverage == 80.0
    assert metrics.branch_coverage == 100.0
    assert metrics.covered_lines == 4
    assert metrics.num_statements == 5
    assert metrics.missing_lines == 1
    assert metrics.excluded_lines == 0
    assert metrics.num_branches == 2
    assert metrics.covered_branches == 2
    assert metrics.missing_branches == 0

    assert "src/calculator.py" in metrics.files
    file_m = metrics.files["src/calculator.py"]
    assert file_m.covered_lines == 4
    assert file_m.num_statements == 5
    assert file_m.line_coverage == 80.0
    assert file_m.branch_coverage == 100.0
    assert file_m.executed_lines == [1, 2, 4, 6]
    assert file_m.missing_line_numbers == [7]


def test_parse_valid_coverage_string_without_branches() -> None:
    """Verifies parsing of coverage JSON where branch coverage was not enabled."""
    json_str = json.dumps({
        "totals": {
            "covered_lines": 15,
            "num_statements": 20,
            "percent_covered": 75.0,
            "missing_lines": 5,
            "excluded_lines": 1,
        }
    })

    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_string(json_str)

    assert metrics.is_valid is True
    assert metrics.line_coverage == 75.0
    assert metrics.branch_coverage == 0.0
    assert metrics.covered_lines == 15
    assert metrics.num_statements == 20
    assert metrics.missing_lines == 5
    assert metrics.excluded_lines == 1
    assert metrics.num_branches == 0
    assert metrics.covered_branches == 0


def test_per_file_coverage_breakdown() -> None:
    """Verifies that multiple source files are extracted into the files map."""
    data = {
        "files": {
            "src/math/add.py": {
                "summary": {
                    "covered_lines": 10,
                    "num_statements": 10,
                    "percent_covered": 100.0,
                    "missing_lines": 0,
                    "excluded_lines": 0,
                    "num_branches": 0,
                    "covered_branches": 0,
                    "missing_branches": 0,
                },
                "executed_lines": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                "missing_lines": [],
            },
            "src/math/sub.py": {
                "summary": {
                    "covered_lines": 5,
                    "num_statements": 10,
                    "percent_covered": 50.0,
                    "missing_lines": 5,
                    "excluded_lines": 0,
                    "num_branches": 2,
                    "covered_branches": 1,
                    "missing_branches": 1,
                },
                "executed_lines": [1, 2, 3, 4, 5],
                "missing_lines": [6, 7, 8, 9, 10],
            },
        },
        "totals": {
            "covered_lines": 15,
            "num_statements": 20,
            "percent_covered": 75.0,
            "missing_lines": 5,
            "excluded_lines": 0,
            "num_branches": 2,
            "covered_branches": 1,
            "missing_branches": 1,
        },
    }

    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_dict(data)

    assert len(metrics.files) == 2
    assert "src/math/add.py" in metrics.files
    assert "src/math/sub.py" in metrics.files

    add_file = metrics.files["src/math/add.py"]
    assert add_file.line_coverage == 100.0
    assert add_file.branch_coverage == 0.0

    sub_file = metrics.files["src/math/sub.py"]
    assert sub_file.line_coverage == 50.0
    assert sub_file.branch_coverage == 50.0
    assert sub_file.num_branches == 2
    assert sub_file.covered_branches == 1


# =====================================================================
# 2. Resiliency & Edge Case Handling
# =====================================================================

def test_zero_executable_statements_avoids_division_by_zero() -> None:
    """Verifies that an empty source file or report with zero statements returns 0.0%."""
    data = {
        "totals": {
            "covered_lines": 0,
            "num_statements": 0,
            "percent_covered": 0.0,
            "missing_lines": 0,
            "excluded_lines": 0,
            "num_branches": 0,
            "covered_branches": 0,
            "missing_branches": 0,
        }
    }
    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_dict(data)

    assert metrics.is_valid is True
    assert metrics.line_coverage == 0.0
    assert metrics.branch_coverage == 0.0
    assert metrics.covered_lines == 0
    assert metrics.num_statements == 0


def test_fallback_aggregation_when_totals_section_missing() -> None:
    """Verifies that totals are computed across files when totals key is absent."""
    data = {
        "files": {
            "src/a.py": {
                "summary": {"covered_lines": 8, "num_statements": 10, "missing_lines": 2, "excluded_lines": 0},
            },
            "src/b.py": {
                "summary": {"covered_lines": 2, "num_statements": 10, "missing_lines": 8, "excluded_lines": 1},
            },
        }
    }
    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_dict(data)

    assert metrics.is_valid is True
    assert metrics.covered_lines == 10
    assert metrics.num_statements == 20
    assert metrics.missing_lines == 10
    assert metrics.excluded_lines == 1
    assert metrics.line_coverage == 50.0


def test_empty_json_object_returns_zero_defaults() -> None:
    """Verifies that an empty JSON object returns clean default metrics without errors."""
    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_string("{}")

    assert metrics.is_valid is True
    assert metrics.line_coverage == 0.0
    assert metrics.branch_coverage == 0.0
    assert metrics.covered_lines == 0
    assert metrics.num_statements == 0
    assert metrics.files == {}


# =====================================================================
# 3. Error Handling: Missing, Empty, and Malformed Files
# =====================================================================

def test_missing_report_file_returns_invalid_metrics() -> None:
    """Verifies that a missing file returns is_valid=False with error message."""
    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_json("non_existent_path_coverage.json")

    assert metrics.is_valid is False
    assert metrics.error_message is not None
    assert "not found" in metrics.error_message.lower()


def test_missing_report_file_strict_mode_raises() -> None:
    """Verifies that strict=True raises CoverageExtractionError on missing file."""
    extractor = CoverageExtractor()
    with pytest.raises(CoverageExtractionError, match="not found"):
        extractor.parse_coverage_json("non_existent_path_coverage.json", strict=True)


def test_empty_json_string_handling() -> None:
    """Verifies that empty string returns is_valid=False or raises in strict mode."""
    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_string("")

    assert metrics.is_valid is False
    assert metrics.error_message is not None
    assert "empty" in metrics.error_message.lower()

    with pytest.raises(CoverageExtractionError, match="empty"):
        extractor.parse_coverage_string("", strict=True)


def test_malformed_json_handling() -> None:
    """Verifies that syntax-invalid JSON returns is_valid=False or raises in strict mode."""
    extractor = CoverageExtractor()
    malformed = '{"totals": {"covered_lines": 5, '

    metrics = extractor.parse_coverage_string(malformed)
    assert metrics.is_valid is False
    assert metrics.error_message is not None
    assert "malformed" in metrics.error_message.lower()

    with pytest.raises(CoverageExtractionError, match="Malformed JSON"):
        extractor.parse_coverage_string(malformed, strict=True)


def test_non_dict_json_root_handling() -> None:
    """Verifies that a JSON array at root is rejected as invalid schema."""
    extractor = CoverageExtractor()
    metrics = extractor.parse_coverage_string("[1, 2, 3]")

    assert metrics.is_valid is False
    assert metrics.error_message is not None
    assert "must be a json object" in metrics.error_message.lower()

    with pytest.raises(CoverageExtractionError, match="must be a JSON object"):
        extractor.parse_coverage_string("[1, 2, 3]", strict=True)


# =====================================================================
# 4. Coverage Delta Calculation Tests
# =====================================================================

def test_calculate_deltas_positive_increase() -> None:
    """Verifies positive delta calculation when coverage increases."""
    extractor = CoverageExtractor()
    delta = extractor.calculate_deltas(pre_cov=60.0, post_cov=75.5)
    assert delta == 15.5


def test_calculate_deltas_zero_change() -> None:
    """Verifies zero delta when coverage remains identical."""
    extractor = CoverageExtractor()
    delta = extractor.calculate_deltas(pre_cov=82.35, post_cov=82.35)
    assert delta == 0.0


def test_calculate_deltas_negative_decrease() -> None:
    """Verifies negative delta when coverage decreases."""
    extractor = CoverageExtractor()
    delta = extractor.calculate_deltas(pre_cov=80.0, post_cov=75.0)
    assert delta == -5.0


def test_calculate_deltas_floating_point_rounding() -> None:
    """Verifies that floating-point arithmetic errors are rounded to 4 decimals."""
    extractor = CoverageExtractor()
    # In standard IEEE 754 float: 12.4 - 12.1 == 0.29999999999999893
    delta = extractor.calculate_deltas(pre_cov=12.1, post_cov=12.4)
    assert delta == 0.3


def test_calculate_deltas_rejects_out_of_range_values() -> None:
    """Verifies that percentages outside [0.0, 100.0] raise ValueError."""
    extractor = CoverageExtractor()

    with pytest.raises(ValueError, match="pre_cov must be between 0.0 and 100.0"):
        extractor.calculate_deltas(pre_cov=-1.0, post_cov=50.0)

    with pytest.raises(ValueError, match="pre_cov must be between 0.0 and 100.0"):
        extractor.calculate_deltas(pre_cov=105.0, post_cov=50.0)

    with pytest.raises(ValueError, match="post_cov must be between 0.0 and 100.0"):
        extractor.calculate_deltas(pre_cov=50.0, post_cov=-0.5)

    with pytest.raises(ValueError, match="post_cov must be between 0.0 and 100.0"):
        extractor.calculate_deltas(pre_cov=50.0, post_cov=100.1)


def test_calculate_deltas_rejects_nan() -> None:
    """Verifies that NaN values raise ValueError."""
    extractor = CoverageExtractor()

    with pytest.raises(ValueError, match="cannot be NaN"):
        extractor.calculate_deltas(pre_cov=float("nan"), post_cov=50.0)

    with pytest.raises(ValueError, match="cannot be NaN"):
        extractor.calculate_deltas(pre_cov=50.0, post_cov=float("nan"))


def test_calculate_deltas_with_none_returns_none() -> None:
    """Verifies that calculate_deltas returns None if either input is None."""
    extractor = CoverageExtractor()

    assert extractor.calculate_deltas(pre_cov=None, post_cov=50.0) is None
    assert extractor.calculate_deltas(pre_cov=50.0, post_cov=None) is None
    assert extractor.calculate_deltas(pre_cov=None, post_cov=None) is None


def test_calculate_deltas_boundary_values() -> None:
    """Verifies boundary delta calculations at 0.0% and 100.0%."""
    extractor = CoverageExtractor()

    assert extractor.calculate_deltas(pre_cov=0.0, post_cov=100.0) == 100.0
    assert extractor.calculate_deltas(pre_cov=100.0, post_cov=0.0) == -100.0
    assert extractor.calculate_deltas(pre_cov=0.0, post_cov=0.0) == 0.0
    assert extractor.calculate_deltas(pre_cov=100.0, post_cov=100.0) == 0.0
