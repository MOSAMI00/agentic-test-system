"""
Workflow state container for the Agentic Test Generation System.
Adheres strictly to Stage 3 Section 4.4.3 and Decision 3.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_test.core.models import (
    ExecutionEvidence,
    ExecutionPlan,
    FailureDiagnosis,
    RepositorySnapshot,
    TestCandidate,
)


class WorkflowState(BaseModel):
    """
    Immutable workflow state capturing point-in-time state transitions across the lifecycle.
    Stage 3 Section 4.4.3 + Decision B immutable container clarification (Tuple[T, ...]).
    """
    model_config = ConfigDict(frozen=True)

    # --- Run Identification & Execution Context ---
    run_id: str
    repo_path: Path
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- Populated during Ingestion & Static Analysis (UC-01) ---
    snapshot: Optional[RepositorySnapshot] = None

    # --- Populated during Deterministic Planning (UC-02) ---
    plan: Optional[ExecutionPlan] = None

    # --- Populated during Test Generation & Multi-Gate Validation (UC-03, UC-04) ---
    candidates: Tuple[TestCandidate, ...] = Field(default_factory=tuple)
    generation_retry_count: int = 0
    max_generation_retries: int = 2

    # --- Populated during Sandbox Execution (UC-05) ---
    baseline_evidence: Optional[ExecutionEvidence] = None
    evidences: Tuple[ExecutionEvidence, ...] = Field(default_factory=tuple)

    # --- Populated during Failure Diagnosis (UC-06) ---
    diagnoses: Tuple[FailureDiagnosis, ...] = Field(default_factory=tuple)

    # --- Terminal Execution Status ---
    final_status: str = "INITIALIZED"
    error_message: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_evidence(cls, data: Any) -> Any:
        """
        Migrates legacy singular 'evidence' input to canonical plural 'evidences' tuple.

        Precedence & migration rules:
        1. Non-dict input: return as-is for standard Pydantic validation.
        2. 'evidence' missing: leave canonical 'evidences' untouched (defaults to ()).
        3. 'evidence' is None:
           - If 'evidences' not in data: sets data['evidences'] = ().
           - If 'evidences' is in data: leaves 'evidences' intact.
        4. Both 'evidence' (non-None) and 'evidences' (non-empty) are supplied:
           - Raises ValueError to avoid ambiguous precedence or silent data loss.
        5. 'evidence' provided (non-None) and 'evidences' omitted or empty:
           - Dict or ExecutionEvidence: data['evidences'] = (legacy_val,)
           - List or Tuple: data['evidences'] = tuple(legacy_val)
           - Other types: data['evidences'] = (legacy_val,) (triggers type validation)
        """
        if not isinstance(data, dict):
            return data

        # Work on a shallow copy or in-place dict
        has_evidence = "evidence" in data
        has_evidences = "evidences" in data

        if has_evidence:
            legacy_val = data.pop("evidence")
            if legacy_val is None:
                if not has_evidences:
                    data["evidences"] = ()
            else:
                if has_evidences and data["evidences"]:
                    raise ValueError(
                        "Cannot specify both legacy 'evidence' and canonical 'evidences' with conflicting data. "
                        "Use canonical 'evidences' only."
                    )
                if isinstance(legacy_val, (list, tuple)):
                    data["evidences"] = tuple(legacy_val)
                else:
                    data["evidences"] = (legacy_val,)

        return data

    @property
    def evidence(self) -> Optional[ExecutionEvidence]:
        """
        Backward-compatible read accessor returning primary candidate evidence or None.
        Does not conflict with model fields or serialization.
        """
        return self.evidences[0] if self.evidences else None
