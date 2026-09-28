"""
Coverage report parser and coverage telemetry delta calculator.
Stage 3 Section 4.4.4.5 Listing 4.2.
Iteration 1.5 Slice C.2.
"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class CoverageExtractionError(Exception):
    """Raised when coverage report parsing fails in strict mode."""
    pass


class FileCoverageMetrics(BaseModel):
    """
    Coverage telemetry metrics for an individual source file.
    Stage 3 Section 4.4.4.5.
    """
    model_config = ConfigDict(frozen=True)

    file_path: str
    covered_lines: int = 0
    num_statements: int = 0
    missing_lines: int = 0
    excluded_lines: int = 0
    line_coverage: float = 0.0
    num_branches: int = 0
    covered_branches: int = 0
    missing_branches: int = 0
    branch_coverage: float = 0.0
    executed_lines: List[int] = Field(default_factory=list)
    missing_line_numbers: List[int] = Field(default_factory=list)


class CoverageMetrics(BaseModel):
    """
    Aggregate coverage metrics and per-file telemetry breakdown parsed from coverage.json.
    Stage 3 Section 4.4.4.5.
    """
    model_config = ConfigDict(frozen=True)

    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    covered_lines: int = 0
    num_statements: int = 0
    missing_lines: int = 0
    excluded_lines: int = 0
    num_branches: int = 0
    covered_branches: int = 0
    missing_branches: int = 0
    files: Dict[str, FileCoverageMetrics] = Field(default_factory=dict)
    is_valid: bool = True
    error_message: Optional[str] = None


class CoverageExtractor:
    """
    Pure Python parser for coverage.py JSON reports and delta calculation.
    Stage 3 Section 4.4.4.5.
    """

    def parse_coverage_json(
        self,
        json_path: Union[Path, str],
        strict: bool = False,
    ) -> CoverageMetrics:
        """
        Reads and parses a coverage.py JSON report file from disk.

        :param json_path: Path to coverage.json artifact.
        :param strict: If True, raises CoverageExtractionError on missing/malformed report.
                       If False, returns a deterministic CoverageMetrics(is_valid=False, ...).
        :return: CoverageMetrics populated with parsed line/branch telemetry.
        :raises CoverageExtractionError: If strict is True and reading or parsing fails.
        """
        path_obj = Path(json_path)
        if not path_obj.is_file():
            err_msg = f"Coverage report file not found: {json_path}"
            if strict:
                raise CoverageExtractionError(err_msg)
            return CoverageMetrics(is_valid=False, error_message=err_msg)

        try:
            content = path_obj.read_text(encoding="utf-8")
        except Exception as err:
            err_msg = f"Failed to read coverage report file '{json_path}': {err}"
            if strict:
                raise CoverageExtractionError(err_msg) from err
            return CoverageMetrics(is_valid=False, error_message=err_msg)

        return self.parse_coverage_string(content, strict=strict)

    def parse_coverage_string(
        self,
        json_content: str,
        strict: bool = False,
    ) -> CoverageMetrics:
        """
        Parses raw coverage JSON text in memory.

        :param json_content: Serialized JSON report string.
        :param strict: If True, raises CoverageExtractionError on malformed JSON.
                       If False, returns a deterministic CoverageMetrics(is_valid=False, ...).
        :return: CoverageMetrics populated with parsed telemetry.
        :raises CoverageExtractionError: If strict is True and parsing fails.
        """
        if not json_content or not json_content.strip():
            err_msg = "Coverage JSON content is empty"
            if strict:
                raise CoverageExtractionError(err_msg)
            return CoverageMetrics(is_valid=False, error_message=err_msg)

        try:
            data = json.loads(json_content)
        except Exception as err:
            err_msg = f"Malformed JSON in coverage report: {err}"
            if strict:
                raise CoverageExtractionError(err_msg) from err
            return CoverageMetrics(is_valid=False, error_message=err_msg)

        if not isinstance(data, dict):
            err_msg = "Coverage JSON root must be a JSON object (dict)"
            if strict:
                raise CoverageExtractionError(err_msg)
            return CoverageMetrics(is_valid=False, error_message=err_msg)

        return self.parse_coverage_dict(data)

    def parse_coverage_dict(
        self,
        data: Dict[str, Any],
    ) -> CoverageMetrics:
        """
        Extracts structured coverage telemetry from an in-memory parsed coverage dictionary.

        :param data: Parsed JSON data dictionary conforming to coverage.py schema.
        :return: Fully populated CoverageMetrics instance.
        """
        files_map: Dict[str, FileCoverageMetrics] = {}
        raw_files = data.get("files", {})
        if isinstance(raw_files, dict):
            for file_path, file_data in raw_files.items():
                if isinstance(file_data, dict):
                    files_map[file_path] = self._parse_file_entry(file_path, file_data)

        # 1. Primary path: extract from 'totals' section if present
        raw_totals = data.get("totals")
        if isinstance(raw_totals, dict):
            num_statements = int(raw_totals.get("num_statements", 0))
            covered_lines = int(raw_totals.get("covered_lines", 0))
            missing_lines = int(raw_totals.get("missing_lines", 0))
            excluded_lines = int(raw_totals.get("excluded_lines", 0))

            num_branches = int(raw_totals.get("num_branches", 0))
            covered_branches = int(raw_totals.get("covered_branches", 0))
            missing_branches = int(raw_totals.get("missing_branches", 0))

            # Prefer percent_covered directly reported by coverage.py if present
            if "percent_covered" in raw_totals:
                try:
                    line_coverage = round(float(raw_totals["percent_covered"]), 2)
                except (ValueError, TypeError):
                    line_coverage = self._compute_percentage(covered_lines, num_statements)
            else:
                line_coverage = self._compute_percentage(covered_lines, num_statements)

            branch_coverage = self._compute_percentage(covered_branches, num_branches)

            return CoverageMetrics(
                line_coverage=line_coverage,
                branch_coverage=branch_coverage,
                covered_lines=covered_lines,
                num_statements=num_statements,
                missing_lines=missing_lines,
                excluded_lines=excluded_lines,
                num_branches=num_branches,
                covered_branches=covered_branches,
                missing_branches=missing_branches,
                files=files_map,
                is_valid=True,
                error_message=None,
            )

        # 2. Fallback path: aggregate across individual files if 'totals' is absent
        if files_map:
            total_statements = sum(f.num_statements for f in files_map.values())
            total_covered = sum(f.covered_lines for f in files_map.values())
            total_missing = sum(f.missing_lines for f in files_map.values())
            total_excluded = sum(f.excluded_lines for f in files_map.values())

            total_branches = sum(f.num_branches for f in files_map.values())
            total_covered_branches = sum(f.covered_branches for f in files_map.values())
            total_missing_branches = sum(f.missing_branches for f in files_map.values())

            line_cov = self._compute_percentage(total_covered, total_statements)
            branch_cov = self._compute_percentage(total_covered_branches, total_branches)

            return CoverageMetrics(
                line_coverage=line_cov,
                branch_coverage=branch_cov,
                covered_lines=total_covered,
                num_statements=total_statements,
                missing_lines=total_missing,
                excluded_lines=total_excluded,
                num_branches=total_branches,
                covered_branches=total_covered_branches,
                missing_branches=total_missing_branches,
                files=files_map,
                is_valid=True,
                error_message=None,
            )

        # 3. Default empty metrics when no totals or files are present
        return CoverageMetrics(
            line_coverage=0.0,
            branch_coverage=0.0,
            covered_lines=0,
            num_statements=0,
            missing_lines=0,
            excluded_lines=0,
            num_branches=0,
            covered_branches=0,
            missing_branches=0,
            files={},
            is_valid=True,
            error_message=None,
        )

    def calculate_deltas(
        self,
        pre_cov: float,
        post_cov: float,
    ) -> float:
        """
        Calculates the coverage delta between pre-execution and post-execution coverage.

        :param pre_cov: Baseline coverage percentage [0.0, 100.0].
        :param post_cov: Subsequent coverage percentage [0.0, 100.0].
        :return: Float delta rounded to 4 decimal places (post_cov - pre_cov).
        :raises ValueError: If coverage values are NaN or outside the valid [0.0, 100.0] range.
        """
        if math.isnan(pre_cov) or math.isnan(post_cov):
            raise ValueError(f"Coverage percentages cannot be NaN: pre={pre_cov}, post={post_cov}")

        if not (0.0 <= pre_cov <= 100.0):
            raise ValueError(f"pre_cov must be between 0.0 and 100.0, got: {pre_cov}")

        if not (0.0 <= post_cov <= 100.0):
            raise ValueError(f"post_cov must be between 0.0 and 100.0, got: {post_cov}")

        return round(post_cov - pre_cov, 4)

    @classmethod
    def _parse_file_entry(cls, file_path: str, file_data: Dict[str, Any]) -> FileCoverageMetrics:
        """Parses telemetry for a single file entry in coverage.json."""
        summary = file_data.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}

        num_statements = int(summary.get("num_statements", 0))
        covered_lines = int(summary.get("covered_lines", 0))
        missing_lines = int(summary.get("missing_lines", 0))
        excluded_lines = int(summary.get("excluded_lines", 0))

        num_branches = int(summary.get("num_branches", 0))
        covered_branches = int(summary.get("covered_branches", 0))
        missing_branches = int(summary.get("missing_branches", 0))

        if "percent_covered" in summary:
            try:
                line_coverage = round(float(summary["percent_covered"]), 2)
            except (ValueError, TypeError):
                line_coverage = cls._compute_percentage(covered_lines, num_statements)
        else:
            line_coverage = cls._compute_percentage(covered_lines, num_statements)

        branch_coverage = cls._compute_percentage(covered_branches, num_branches)

        executed_lines = [int(x) for x in file_data.get("executed_lines", []) if isinstance(x, (int, float))]
        missing_line_numbers = [int(x) for x in file_data.get("missing_lines", []) if isinstance(x, (int, float))]

        return FileCoverageMetrics(
            file_path=file_path,
            covered_lines=covered_lines,
            num_statements=num_statements,
            missing_lines=missing_lines,
            excluded_lines=excluded_lines,
            line_coverage=line_coverage,
            num_branches=num_branches,
            covered_branches=covered_branches,
            missing_branches=missing_branches,
            branch_coverage=branch_coverage,
            executed_lines=executed_lines,
            missing_line_numbers=missing_line_numbers,
        )

    @staticmethod
    def _compute_percentage(numerator: int, denominator: int) -> float:
        """Computes percentage avoiding division by zero."""
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100.0, 2)
