"""
Core domain models and value objects for the Agentic Test Generation System.
Adheres strictly to Stage 2 Section 4.3.3 and the Human-Approved Option A decision.
"""

import math
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RepositoryValidationError(Exception):
    """Raised when repository validation fails (e.g. non-existent or uninitialized directory)."""


class ChangeType(StrEnum):
    """Classification of code modification types from Git diff hunks."""

    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


class SymbolType(StrEnum):
    """Structural classification of callable code entities extracted via AST."""

    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    CLASS = "CLASS"
    MODULE = "MODULE"


class DiffHunk(BaseModel):
    """
    Immutable value object representing a unified diff block.
    Stage 2 Section 4.3.3.
    """

    model_config = ConfigDict(frozen=True)

    file_path: Path
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    change_type: ChangeType
    content: str


class SymbolContract(BaseModel):
    """
    Structural specification and interface contract of a callable Python symbol.
    Stage 2 Section 4.3.3.
    """

    model_config = ConfigDict(frozen=True)

    qualified_name: str
    symbol_type: SymbolType
    file_path: Path
    line_range: tuple[int, int]
    signature: str
    docstring: str | None = None
    dependencies: tuple[str, ...] = Field(default_factory=tuple)
    is_affected: bool = False


class RepositorySnapshot(BaseModel):
    """
    Immutable point-in-time capture of the target Git repository.
    Stage 2 Section 4.3.3 + Human-Approved Option A Clarification (2026-09-23)
    + Decision B immutable container clarification (Tuple[T, ...]).
    + F-01 Ratified: deleted_symbols field approved.
    """

    model_config = ConfigDict(frozen=True)

    # Nine baseline fields from Stage 2 Section 4.3.3
    repo_path: Path
    current_commit: str
    base_commit: str
    branch_name: str
    tracked_files: tuple[Path, ...] = Field(default_factory=tuple)
    existing_test_files: tuple[Path, ...] = Field(default_factory=tuple)
    source_tree_hash: str
    is_valid: bool
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Approved Option A fields with independent immutable tuple defaults
    diff_hunks: tuple[DiffHunk, ...] = Field(default_factory=tuple)
    affected_symbols: tuple[SymbolContract, ...] = Field(default_factory=tuple)
    # F-01 Ratified: deleted_symbols approved by human architectural decision
    deleted_symbols: tuple[SymbolContract, ...] = Field(default_factory=tuple)
    syntax_errors: tuple[str, ...] = Field(default_factory=tuple)


class WorkflowRoute(StrEnum):
    """Workflow execution routing decisions. Stage 2 §4.3.3."""

    ROUTE_NO_OP = "ROUTE_NO_OP"
    ROUTE_TO_DOCKER_EXECUTION = "ROUTE_TO_DOCKER_EXECUTION"
    ROUTE_TO_TEST_GENERATION = "ROUTE_TO_TEST_GENERATION"


class ValidationStatus(StrEnum):
    """Test candidate validation status. Stage 2 §4.3.3."""

    PENDING = "PENDING"
    PASSED = "PASSED"
    REJECTED_SYNTAX = "REJECTED_SYNTAX"
    REJECTED_SECURITY = "REJECTED_SECURITY"
    REJECTED_COLLECTION = "REJECTED_COLLECTION"
    QUARANTINED = "QUARANTINED"


class FailureCategory(StrEnum):
    """Seven-category failure taxonomy. Stage 2 §4.3.3."""

    TEST_OUTDATED = "TEST_OUTDATED"
    APPLICATION_BUG = "APPLICATION_BUG"
    INVALID_GENERATED_TEST = "INVALID_GENERATED_TEST"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    LLM_FAILURE = "LLM_FAILURE"
    UNKNOWN = "UNKNOWN"


class TriageEngine(StrEnum):
    """Classification engine tier. Stage 2 §4.3.3."""

    DETERMINISTIC_RULE = "DETERMINISTIC_RULE"
    COGNITIVE_LLM = "COGNITIVE_LLM"


class ExecutionPlan(BaseModel):
    """
    Deterministic output of the planning phase.
    Stage 2 Section 4.3.3.
    """

    model_config = ConfigDict(frozen=True)

    plan_id: str
    route: WorkflowRoute
    target_symbols: tuple[SymbolContract, ...] = Field(default_factory=tuple)
    existing_tests_to_run: tuple[Path, ...] = Field(default_factory=tuple)
    rationale: str
    decision_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TestCandidate(BaseModel):
    """
    Synthesized unit test candidate and validation state.
    Stage 2 Section 4.3.3.
    """

    model_config = ConfigDict(frozen=True)
    __test__ = False

    candidate_id: str
    run_id: str
    target_symbol_name: str
    test_file_path: Path
    candidate_code: str
    imports: tuple[str, ...] = Field(default_factory=tuple)
    validation_status: ValidationStatus = ValidationStatus.PENDING
    quarantine_reason: str | None = None
    retry_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("retry_count", mode="after")
    @classmethod
    def _validate_retry_count(cls, v: int) -> int:
        """Enforce retry_count >= 0."""
        if v < 0:
            raise ValueError(f"retry_count must be >= 0, got {v}")
        return v


class ExecutionEvidence(BaseModel):
    """
    Container execution telemetry and coverage metrics.
    Stage 2 Section 4.3.3.
    F-02 Ratified: nullable candidate_id, line_coverage, branch_coverage,
    line_coverage_delta, branch_coverage_delta are all approved.
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    run_id: str
    candidate_id: str | None = None  # F-02: nullable for baseline evidence
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False
    line_coverage: float | None = None  # F-02: nullable
    branch_coverage: float | None = None  # F-02: nullable
    line_coverage_delta: float | None = None  # F-02: nullable delta
    branch_coverage_delta: float | None = None  # F-02: nullable delta
    traceback: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("duration_sec", mode="after")
    @classmethod
    def _validate_duration(cls, v: float) -> float:
        """Enforce duration_sec >= 0 and finite."""
        if not math.isfinite(v) or v < 0:
            raise ValueError(f"duration_sec must be >= 0 and finite, got {v}")
        return v

    @field_validator("line_coverage", "branch_coverage", mode="after")
    @classmethod
    def _validate_coverage_range(cls, v: float | None) -> float | None:
        """Enforce finite coverage values within [0.0, 100.0] when not None."""
        if v is not None and (not math.isfinite(v) or not (0.0 <= v <= 100.0)):
            raise ValueError(f"Coverage value must be in [0.0, 100.0] and finite, got {v}")
        return v

    @field_validator("line_coverage_delta", "branch_coverage_delta", mode="after")
    @classmethod
    def _validate_coverage_delta_range(cls, v: float | None) -> float | None:
        """Enforce finite coverage delta values within [-100.0, 100.0] when not None."""
        if v is not None and (not math.isfinite(v) or not (-100.0 <= v <= 100.0)):
            raise ValueError(
                f"Coverage delta value must be finite and in [-100.0, 100.0], got {v}"
            )
        return v


class FailureDiagnosis(BaseModel):
    """
    Diagnostic classification of a failed test candidate.
    Stage 2 Section 4.3.3.
    """

    model_config = ConfigDict(frozen=True)

    diagnosis_id: str
    evidence_id: str
    canonical_category: FailureCategory
    confidence: float
    triage_engine: TriageEngine
    explanation: str
    is_application_bug: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("confidence", mode="after")
    @classmethod
    def _validate_confidence(cls, v: float) -> float:
        """Enforce confidence within [0.0, 1.0]."""
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {v}")
        return v

    @model_validator(mode="after")
    def _validate_application_bug_consistency(self) -> "FailureDiagnosis":
        """
        Enforce consistency between canonical_category == APPLICATION_BUG and is_application_bug.
        Rejects combinations where:
          - canonical_category == APPLICATION_BUG but is_application_bug is False
          - is_application_bug is True but canonical_category != APPLICATION_BUG
        """
        is_bug_category = self.canonical_category == FailureCategory.APPLICATION_BUG
        if is_bug_category and not self.is_application_bug:
            raise ValueError(
                "canonical_category is APPLICATION_BUG but is_application_bug is False. "
                "These must be consistent."
            )
        if self.is_application_bug and not is_bug_category:
            raise ValueError(
                f"is_application_bug is True but canonical_category is "
                f"{self.canonical_category.value!r}, not APPLICATION_BUG. "
                "These must be consistent."
            )
        return self
