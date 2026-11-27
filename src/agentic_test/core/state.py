"""
Workflow state container for the Agentic Test Generation System.
Adheres strictly to Stage 3 Section 4.4.3 and Decision 3.
F-03 Ratified: baseline_evidence + plural evidences with legacy migration and
backward-compatible .evidence property are officially approved.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_test.core.models import (
    ExecutionEvidence,
    ExecutionPlan,
    FailureDiagnosis,
    RepositorySnapshot,
    TestCandidate,
)


def _is_baseline_evidence_item(item: Any) -> bool:
    """Return True if item represents baseline evidence (candidate_id is None)."""
    if isinstance(item, ExecutionEvidence):
        return item.candidate_id is None
    if isinstance(item, Mapping):
        return item.get("candidate_id") is None
    return False


class WorkflowState(BaseModel):
    """
    Immutable workflow state capturing point-in-time state transitions across the lifecycle.
    Stage 3 Section 4.4.3 + Decision B immutable container clarification (Tuple[T, ...]).
    F-03 Ratified: baseline_evidence and plural evidences approved.
    """

    model_config = ConfigDict(frozen=True)

    # --- Run Identification & Execution Context ---
    run_id: str
    repo_path: Path
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # --- Populated during Ingestion & Static Analysis (UC-01) ---
    snapshot: RepositorySnapshot | None = None

    # --- Populated during Deterministic Planning (UC-02) ---
    plan: ExecutionPlan | None = None

    # --- Populated during Test Generation & Multi-Gate Validation (UC-03, UC-04) ---
    candidates: tuple[TestCandidate, ...] = Field(default_factory=tuple)
    generation_retry_count: int = 0
    max_generation_retries: int = 2

    # --- Populated during Sandbox Execution (UC-05) ---
    # F-03 Ratified: baseline_evidence for regression baseline, evidences for candidates
    baseline_evidence: ExecutionEvidence | None = None
    evidences: tuple[ExecutionEvidence, ...] = Field(default_factory=tuple)

    # --- Populated during Failure Diagnosis (UC-06) ---
    diagnoses: tuple[FailureDiagnosis, ...] = Field(default_factory=tuple)

    # --- Terminal Execution Status ---
    final_status: str = "INITIALIZED"
    error_message: str | None = None

    @field_validator("generation_retry_count", mode="after")
    @classmethod
    def _validate_generation_retry_count(cls, v: int) -> int:
        """Enforce generation_retry_count >= 0."""
        if v < 0:
            raise ValueError(f"generation_retry_count must be >= 0, got {v}")
        return v

    @field_validator("max_generation_retries", mode="after")
    @classmethod
    def _validate_max_generation_retries(cls, v: int) -> int:
        """Enforce max_generation_retries >= 0."""
        if v < 0:
            raise ValueError(f"max_generation_retries must be >= 0, got {v}")
        return v

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_evidence(cls, data: Any) -> Any:
        """
        Migrates legacy singular 'evidence' input to canonical F-03 fields without
        mutating caller-owned dictionaries.

        Precedence & migration rules:
        1. Non-Mapping input: return as-is for standard Pydantic validation.
        2. 'evidence' missing: return data untouched.
        3. 'evidence' is None:
           - Copies mapping and removes 'evidence'.
           - If 'evidences' not in data: sets data['evidences'] = ().
           - Leaves 'baseline_evidence' and existing 'evidences' intact.
        4. 'evidence' is non-None and either canonical 'baseline_evidence' is non-None
           or canonical 'evidences' is non-empty:
           - Raises ValueError to avoid ambiguous precedence or silent data loss.
        5. 'evidence' is non-None without canonical conflict:
           - Single item with candidate_id=None -> routed to 'baseline_evidence'.
           - Single item with non-None candidate_id (or invalid scalar) -> routed to 'evidences'.
           - List/tuple -> splits baseline (candidate_id=None, max 1) to 'baseline_evidence'
             and remaining items to 'evidences' tuple.
        """
        if not isinstance(data, Mapping):
            return data

        if "evidence" not in data:
            return data

        # Copy incoming mapping so caller-owned dict is never mutated.
        migrated = dict(data)
        legacy_val = migrated.pop("evidence")
        has_evidences = "evidences" in migrated
        has_baseline = "baseline_evidence" in migrated

        if legacy_val is None:
            if not has_evidences:
                migrated["evidences"] = ()
            return migrated

        if has_baseline and migrated["baseline_evidence"] is not None:
            raise ValueError(
                "Cannot specify both legacy 'evidence' and canonical 'baseline_evidence' "
                "with conflicting data. Use canonical fields only."
            )

        if has_evidences and migrated["evidences"]:
            raise ValueError(
                "Cannot specify both legacy 'evidence' and canonical 'evidences' "
                "with conflicting data. Use canonical 'evidences' only."
            )

        if isinstance(legacy_val, (list, tuple)):
            baseline_items: list[Any] = []
            candidate_items: list[Any] = []
            for item in legacy_val:
                if _is_baseline_evidence_item(item):
                    baseline_items.append(item)
                else:
                    candidate_items.append(item)

            if len(baseline_items) > 1:
                raise ValueError(
                    "Legacy 'evidence' contains multiple baseline records (candidate_id=None); "
                    "at most one baseline_evidence is allowed."
                )

            if baseline_items:
                migrated["baseline_evidence"] = baseline_items[0]
            migrated["evidences"] = tuple(candidate_items)
        elif _is_baseline_evidence_item(legacy_val):
            migrated["baseline_evidence"] = legacy_val
            if not has_evidences:
                migrated["evidences"] = ()
        else:
            migrated["evidences"] = (legacy_val,)

        return migrated

    @model_validator(mode="after")
    def _validate_state_invariants(self) -> "WorkflowState":
        """
        Enforce cross-field WorkflowState invariants:
        1. generation_retry_count <= max_generation_retries.
        2. F-03: baseline_evidence (if present) must have candidate_id=None and match state.run_id.
        3. F-03: every item in evidences must have candidate_id != None and match state.run_id.
        """
        if self.generation_retry_count > self.max_generation_retries:
            raise ValueError(
                f"generation_retry_count ({self.generation_retry_count}) exceeds "
                f"max_generation_retries ({self.max_generation_retries})."
            )

        if self.baseline_evidence is not None:
            if self.baseline_evidence.candidate_id is not None:
                raise ValueError(
                    f"baseline_evidence must have candidate_id=None, "
                    f"got {self.baseline_evidence.candidate_id!r}."
                )
            if self.baseline_evidence.run_id != self.run_id:
                raise ValueError(
                    f"baseline_evidence.run_id ({self.baseline_evidence.run_id!r}) "
                    f"must match state.run_id ({self.run_id!r})."
                )

        for ev in self.evidences:
            if ev.candidate_id is None:
                raise ValueError(
                    f"Evidence {ev.evidence_id!r} in WorkflowState.evidences "
                    "must have a non-None candidate_id."
                )
            if ev.run_id != self.run_id:
                raise ValueError(
                    f"Evidence {ev.evidence_id!r} run_id ({ev.run_id!r}) "
                    f"must match state.run_id ({self.run_id!r})."
                )

        return self

    @property
    def evidence(self) -> ExecutionEvidence | None:
        """
        Backward-compatible read accessor returning primary candidate evidence or None.
        Does not conflict with model fields or serialization.
        """
        return self.evidences[0] if self.evidences else None
