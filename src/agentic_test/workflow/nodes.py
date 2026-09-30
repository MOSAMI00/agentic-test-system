"""
Workflow state transition node adapters for the Agentic Test Generation System.
Adheres strictly to Stage 3 Section 4.4.3 and WBS 1.6.3A.

All nodes are pure adapter functions operating on immutable WorkflowState models
using model_copy(update=...). They do NOT perform direct persistence or event emission.
"""

from typing import List, Optional, Sequence, Tuple
from pathlib import Path

from agentic_test.analysis.service import AnalysisService
from agentic_test.core.models import (
    ExecutionEvidence,
    ExecutionPlan,
    FailureDiagnosis,
    RepositorySnapshot,
    TestCandidate,
    ValidationStatus,
)
from agentic_test.core.state import WorkflowState
from agentic_test.diagnosis.service import DiagnosisService
from agentic_test.execution.service import ExecutionService
from agentic_test.generation.service import GenerationService
from agentic_test.planning.planner import ExecutionPlanner
from agentic_test.validation.pipeline import ValidationPipeline


def ingest_and_analyze_node(
    state: WorkflowState,
    analysis_service: AnalysisService,
) -> WorkflowState:
    """
    Ingests and analyzes target repository AST and Git metadata.
    Populates state.snapshot with an immutable RepositorySnapshot.
    """
    snapshot: RepositorySnapshot = analysis_service.analyze(state.repo_path)
    return state.model_copy(update={"snapshot": snapshot})


def plan_execution_node(
    state: WorkflowState,
    planner: ExecutionPlanner,
) -> WorkflowState:
    """
    Synthesizes a deterministic ExecutionPlan from the repository snapshot.
    Requires state.snapshot to be present.
    """
    if state.snapshot is None:
        raise ValueError("RepositorySnapshot is required for plan_execution_node")
    plan: ExecutionPlan = planner.plan(state.snapshot)
    return state.model_copy(update={"plan": plan})


def generate_tests_node(
    state: WorkflowState,
    generation_service: GenerationService,
) -> WorkflowState:
    """
    Generates test candidates for planned target symbols.
    Requires state.plan to be present.
    Stores generated candidates as an immutable tuple.
    """
    if state.plan is None:
        raise ValueError("ExecutionPlan is required for generate_tests_node")
    generated = generation_service.generate(state.plan)
    return state.model_copy(update={"candidates": tuple(generated)})


def validate_candidates_node(
    state: WorkflowState,
    validation_pipeline: ValidationPipeline,
) -> WorkflowState:
    """
    Executes multi-gate static and collection validation across all candidates.
    Updates candidate validation_status and preserves them in an immutable tuple.
    """
    results = validation_pipeline.validate_candidates(state.candidates)
    updated_candidates = tuple(candidate for candidate, _ in results)
    return state.model_copy(update={"candidates": updated_candidates})


def execute_sandbox_node(
    state: WorkflowState,
    execution_service: ExecutionService,
) -> WorkflowState:
    """
    Executes tests within isolated container sandboxes.
    Requires state.plan to be present.
    Executes only candidates with ValidationStatus.PASSED.
    Captures baseline evidence for existing regression tests if configured,
    and captures candidate execution evidences.
    """
    if state.plan is None:
        raise ValueError("ExecutionPlan is required for execute_sandbox_node")

    baseline_evidence: Optional[ExecutionEvidence] = state.baseline_evidence
    if baseline_evidence is None and state.plan.existing_tests_to_run:
        baseline_evidence = execution_service.execute_regression_baseline(
            plan=state.plan,
            run_id=state.run_id,
        )

    passed_candidates = [
        c for c in state.candidates if c.validation_status == ValidationStatus.PASSED
    ]

    if passed_candidates:
        candidate_evidences = execution_service.execute_candidates(
            plan=state.plan,
            candidates=passed_candidates,
            baseline_evidence=baseline_evidence,
            run_id=state.run_id,
        )
        evidences = tuple(candidate_evidences)
    else:
        evidences = state.evidences

    return state.model_copy(
        update={
            "baseline_evidence": baseline_evidence,
            "evidences": evidences,
        }
    )


def diagnose_failure_node(
    state: WorkflowState,
    diagnosis_service: DiagnosisService,
) -> WorkflowState:
    """
    Triage and diagnostic classification of execution failures.
    Diagnoses only failed evidences (exit_code != 0), including baseline
    evidence and candidate execution evidence, and appends new diagnoses.
    """
    diagnoses_list: List[FailureDiagnosis] = []
    candidate_by_id = {c.candidate_id: c for c in state.candidates}

    # 1. Check baseline evidence for failure
    if state.baseline_evidence is not None and state.baseline_evidence.exit_code != 0:
        diag = diagnosis_service.diagnose(
            evidence=state.baseline_evidence,
            candidate=None,
            candidate_code=None,
            target_symbol=None,
        )
        diagnoses_list.append(diag)

    # 2. Check candidate execution evidences for failures
    for ev in state.evidences:
        if ev.exit_code != 0:
            cand = candidate_by_id.get(ev.candidate_id) if ev.candidate_id else None
            cand_code = cand.candidate_code if cand else None
            target_sym = cand.target_symbol_name if cand else None
            diag = diagnosis_service.diagnose(
                evidence=ev,
                candidate=cand,
                candidate_code=cand_code,
                target_symbol=target_sym,
            )
            diagnoses_list.append(diag)

    return state.model_copy(
        update={"diagnoses": state.diagnoses + tuple(diagnoses_list)}
    )


def report_node(state: WorkflowState) -> WorkflowState:
    """
    Finalizes workflow run status.
    Sets final_status to 'COMPLETED' while preserving all existing state data.
    """
    return state.model_copy(update={"final_status": "COMPLETED"})
