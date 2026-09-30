"""
Unit and integration tests for workflow node wrappers, conditional edge routers,
and the WorkflowEngine coordinator with persistence wiring.
Adheres strictly to WBS 1.6.3A.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from unittest.mock import MagicMock

import pytest

from agentic_test.core.models import (
    ExecutionEvidence,
    ExecutionPlan,
    FailureCategory,
    FailureDiagnosis,
    RepositorySnapshot,
    TestCandidate,
    TriageEngine,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.validation.models import ValidationResult
from agentic_test.core.state import WorkflowState
from agentic_test.storage.database import get_connection, init_db
from agentic_test.storage.events import SQLiteEventStore
from agentic_test.storage.repository import SQLiteRepository
from agentic_test.workflow.edges import (
    route_after_execution,
    route_after_planning,
    route_after_validation,
)
from agentic_test.workflow.engine import NODE_STEP_INDICES, WorkflowEngine
from agentic_test.workflow.nodes import (
    diagnose_failure_node,
    execute_sandbox_node,
    generate_tests_node,
    ingest_and_analyze_node,
    plan_execution_node,
    report_node,
    validate_candidates_node,
)
from agentic_test.workflow.recovery import (
    RecoveryError,
    WorkflowRecoveryService,
    resume_run,
)


# ============================================================================
# Fixtures and Helpers
# ============================================================================

@pytest.fixture
def repo() -> SQLiteRepository:
    conn = get_connection(":memory:")
    init_db(conn)
    return SQLiteRepository(conn)


@pytest.fixture
def event_store(repo: SQLiteRepository) -> SQLiteEventStore:
    return SQLiteEventStore(repo.connection)


def _make_state(
    run_id: str = "run-001",
    repo_path: Path = Path("/workspace/test-repo"),
    snapshot: Optional[RepositorySnapshot] = None,
    plan: Optional[ExecutionPlan] = None,
    candidates: Tuple[TestCandidate, ...] = (),
    baseline_evidence: Optional[ExecutionEvidence] = None,
    evidences: Tuple[ExecutionEvidence, ...] = (),
    diagnoses: Tuple[FailureDiagnosis, ...] = (),
) -> WorkflowState:
    return WorkflowState(
        run_id=run_id,
        repo_path=repo_path,
        snapshot=snapshot,
        plan=plan,
        candidates=candidates,
        baseline_evidence=baseline_evidence,
        evidences=evidences,
        diagnoses=diagnoses,
    )


def _make_snapshot(
    repo_path: Path = Path("/workspace/test-repo"),
    current_commit: str = "commit-abc",
    base_commit: str = "commit-base",
    branch_name: str = "main",
) -> RepositorySnapshot:
    return RepositorySnapshot(
        repo_path=repo_path,
        current_commit=current_commit,
        base_commit=base_commit,
        branch_name=branch_name,
        source_tree_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        is_valid=True,
    )


def _make_plan(
    plan_id: str = "plan-001",
    route: WorkflowRoute = WorkflowRoute.ROUTE_NO_OP,
    existing_tests: Tuple[Path, ...] = (),
) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=plan_id,
        route=route,
        target_symbols=(),
        existing_tests_to_run=existing_tests,
        rationale="Test planning rationale",
        decision_hash="dec-hash-123",
    )


def _make_candidate(
    candidate_id: str = "cand-001",
    run_id: str = "run-001",
    status: ValidationStatus = ValidationStatus.PENDING,
) -> TestCandidate:
    return TestCandidate(
        candidate_id=candidate_id,
        run_id=run_id,
        target_symbol_name="calc.add",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_add(): assert add(1, 2) == 3\n",
        validation_status=status,
    )


def _make_evidence(
    evidence_id: str = "ev-001",
    run_id: str = "run-001",
    candidate_id: Optional[str] = "cand-001",
    exit_code: int = 0,
) -> ExecutionEvidence:
    return ExecutionEvidence(
        evidence_id=evidence_id,
        run_id=run_id,
        candidate_id=candidate_id,
        exit_code=exit_code,
        stdout="1 passed\n" if exit_code == 0 else "FAIL\n",
        stderr="",
        duration_sec=0.25,
    )


def _make_diagnosis(
    diagnosis_id: str = "diag-001",
    evidence_id: str = "ev-001",
) -> FailureDiagnosis:
    return FailureDiagnosis(
        diagnosis_id=diagnosis_id,
        evidence_id=evidence_id,
        canonical_category=FailureCategory.APPLICATION_BUG,
        confidence=0.95,
        triage_engine=TriageEngine.DETERMINISTIC_RULE,
        explanation="Simulated bug explanation",
        is_application_bug=True,
    )


# ============================================================================
# 1. Workflow Node Adapter Unit Tests
# ============================================================================

def test_ingest_and_analyze_node() -> None:
    initial_state = _make_state()
    mock_snapshot = _make_snapshot()
    mock_service = MagicMock()
    mock_service.analyze.return_value = mock_snapshot

    new_state = ingest_and_analyze_node(initial_state, mock_service)

    assert initial_state.snapshot is None  # Immutability preserved
    assert new_state.snapshot == mock_snapshot
    mock_service.analyze.assert_called_once_with(initial_state.repo_path)


def test_plan_execution_node_success() -> None:
    snapshot = _make_snapshot()
    initial_state = _make_state(snapshot=snapshot)
    mock_plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    mock_planner = MagicMock()
    mock_planner.plan.return_value = mock_plan

    new_state = plan_execution_node(initial_state, mock_planner)

    assert initial_state.plan is None
    assert new_state.plan == mock_plan
    mock_planner.plan.assert_called_once_with(snapshot)


def test_plan_execution_node_missing_snapshot() -> None:
    initial_state = _make_state(snapshot=None)
    mock_planner = MagicMock()

    with pytest.raises(ValueError, match="RepositorySnapshot is required"):
        plan_execution_node(initial_state, mock_planner)


def test_generate_tests_node_success() -> None:
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    initial_state = _make_state(plan=plan)
    cand1 = _make_candidate(candidate_id="c1")
    cand2 = _make_candidate(candidate_id="c2")
    mock_gen_service = MagicMock()
    mock_gen_service.generate.return_value = [cand1, cand2]

    new_state = generate_tests_node(initial_state, mock_gen_service)

    assert initial_state.candidates == ()
    assert new_state.candidates == (cand1, cand2)
    mock_gen_service.generate.assert_called_once_with(plan)


def test_generate_tests_node_missing_plan() -> None:
    initial_state = _make_state(plan=None)
    mock_gen_service = MagicMock()

    with pytest.raises(ValueError, match="ExecutionPlan is required"):
        generate_tests_node(initial_state, mock_gen_service)


def test_validate_candidates_node() -> None:
    cand1 = _make_candidate(candidate_id="c1", status=ValidationStatus.PENDING)
    cand2 = _make_candidate(candidate_id="c2", status=ValidationStatus.PENDING)
    initial_state = _make_state(candidates=(cand1, cand2))

    val_cand1 = _make_candidate(candidate_id="c1", status=ValidationStatus.PASSED)
    val_cand2 = _make_candidate(candidate_id="c2", status=ValidationStatus.QUARANTINED)

    mock_pipeline = MagicMock()
    mock_pipeline.validate_candidates.return_value = [
        (val_cand1, MagicMock(passed=True)),
        (val_cand2, MagicMock(passed=False)),
    ]

    new_state = validate_candidates_node(initial_state, mock_pipeline)

    assert initial_state.candidates == (cand1, cand2)
    assert new_state.candidates == (val_cand1, val_cand2)
    mock_pipeline.validate_candidates.assert_called_once_with((cand1, cand2))


def test_execute_sandbox_node_passed_candidates_only() -> None:
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    c_passed = _make_candidate(candidate_id="c_ok", status=ValidationStatus.PASSED)
    c_quarantine = _make_candidate(candidate_id="c_bad", status=ValidationStatus.QUARANTINED)
    initial_state = _make_state(plan=plan, candidates=(c_passed, c_quarantine))

    ev = _make_evidence(candidate_id="c_ok", exit_code=0)
    mock_exec_service = MagicMock()
    mock_exec_service.execute_candidates.return_value = (ev,)

    new_state = execute_sandbox_node(initial_state, mock_exec_service)

    assert initial_state.evidences == ()
    assert new_state.evidences == (ev,)
    mock_exec_service.execute_candidates.assert_called_once_with(
        plan=plan,
        candidates=[c_passed],
        baseline_evidence=None,
        run_id=initial_state.run_id,
    )


def test_execute_sandbox_node_runs_baseline_when_needed() -> None:
    existing_tests = (Path("tests/test_existing.py"),)
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_DOCKER_EXECUTION, existing_tests=existing_tests)
    initial_state = _make_state(plan=plan, baseline_evidence=None)

    base_ev = _make_evidence(evidence_id="base-ev", candidate_id=None, exit_code=0)
    mock_exec_service = MagicMock()
    mock_exec_service.execute_regression_baseline.return_value = base_ev

    new_state = execute_sandbox_node(initial_state, mock_exec_service)

    assert new_state.baseline_evidence == base_ev
    mock_exec_service.execute_regression_baseline.assert_called_once_with(
        plan=plan,
        run_id=initial_state.run_id,
    )


def test_execute_sandbox_node_missing_plan() -> None:
    initial_state = _make_state(plan=None)
    mock_exec = MagicMock()

    with pytest.raises(ValueError, match="ExecutionPlan is required"):
        execute_sandbox_node(initial_state, mock_exec)


def test_diagnose_failure_node_diagnoses_failed_evidences_only() -> None:
    cand_fail = _make_candidate(candidate_id="c_fail", status=ValidationStatus.PASSED)
    cand_pass = _make_candidate(candidate_id="c_pass", status=ValidationStatus.PASSED)
    ev_fail = _make_evidence(evidence_id="ev_fail", candidate_id="c_fail", exit_code=1)
    ev_pass = _make_evidence(evidence_id="ev_pass", candidate_id="c_pass", exit_code=0)
    base_ev_fail = _make_evidence(evidence_id="base_fail", candidate_id=None, exit_code=1)

    initial_state = _make_state(
        candidates=(cand_fail, cand_pass),
        baseline_evidence=base_ev_fail,
        evidences=(ev_fail, ev_pass),
    )

    diag_base = _make_diagnosis(diagnosis_id="d_base", evidence_id="base_fail")
    diag_cand = _make_diagnosis(diagnosis_id="d_cand", evidence_id="ev_fail")

    mock_diag_service = MagicMock()
    mock_diag_service.diagnose.side_effect = [diag_base, diag_cand]

    new_state = diagnose_failure_node(initial_state, mock_diag_service)

    assert initial_state.diagnoses == ()
    assert new_state.diagnoses == (diag_base, diag_cand)
    assert mock_diag_service.diagnose.call_count == 2


def test_diagnose_failure_node_no_failures() -> None:
    ev_pass = _make_evidence(evidence_id="ev_pass", candidate_id="c_pass", exit_code=0)
    initial_state = _make_state(evidences=(ev_pass,))

    mock_diag_service = MagicMock()
    new_state = diagnose_failure_node(initial_state, mock_diag_service)

    assert new_state.diagnoses == ()
    mock_diag_service.diagnose.assert_not_called()


def test_report_node() -> None:
    initial_state = _make_state()
    assert initial_state.final_status == "INITIALIZED"

    new_state = report_node(initial_state)

    assert initial_state.final_status == "INITIALIZED"
    assert new_state.final_status == "COMPLETED"


# ============================================================================
# 2. Conditional Edge Router Unit Tests
# ============================================================================

def test_route_after_planning_no_op() -> None:
    plan = _make_plan(route=WorkflowRoute.ROUTE_NO_OP)
    state = _make_state(plan=plan)
    assert route_after_planning(state) == "report_node"


def test_route_after_planning_docker_execution() -> None:
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_DOCKER_EXECUTION)
    state = _make_state(plan=plan)
    assert route_after_planning(state) == "execute_sandbox_node"


def test_route_after_planning_test_generation() -> None:
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    state = _make_state(plan=plan)
    assert route_after_planning(state) == "generate_tests_node"


def test_route_after_planning_missing_plan() -> None:
    state = _make_state(plan=None)
    with pytest.raises(ValueError, match="ExecutionPlan is required"):
        route_after_planning(state)


def test_route_after_validation_with_passed_candidate() -> None:
    c1 = _make_candidate(candidate_id="c1", status=ValidationStatus.PASSED)
    c2 = _make_candidate(candidate_id="c2", status=ValidationStatus.QUARANTINED)
    state = _make_state(candidates=(c1, c2))
    assert route_after_validation(state) == "execute_sandbox_node"


def test_route_after_validation_all_quarantined() -> None:
    c1 = _make_candidate(candidate_id="c1", status=ValidationStatus.QUARANTINED)
    c2 = _make_candidate(candidate_id="c2", status=ValidationStatus.REJECTED_SECURITY)
    state = _make_state(candidates=(c1, c2))
    assert route_after_validation(state) == "report_node"


def test_route_after_validation_empty_candidates() -> None:
    state = _make_state(candidates=())
    assert route_after_validation(state) == "report_node"


def test_route_after_execution_all_success() -> None:
    base = _make_evidence(evidence_id="base", candidate_id=None, exit_code=0)
    ev = _make_evidence(evidence_id="cand", candidate_id="c1", exit_code=0)
    state = _make_state(baseline_evidence=base, evidences=(ev,))
    assert route_after_execution(state) == "report_node"


def test_route_after_execution_candidate_failed() -> None:
    ev_fail = _make_evidence(evidence_id="cand", candidate_id="c1", exit_code=1)
    state = _make_state(evidences=(ev_fail,))
    assert route_after_execution(state) == "diagnose_failure_node"


def test_route_after_execution_baseline_failed() -> None:
    base_fail = _make_evidence(evidence_id="base", candidate_id=None, exit_code=2)
    ev_pass = _make_evidence(evidence_id="cand", candidate_id="c1", exit_code=0)
    state = _make_state(baseline_evidence=base_fail, evidences=(ev_pass,))
    assert route_after_execution(state) == "diagnose_failure_node"


# ============================================================================
# 3. WorkflowEngine Integration Tests
# ============================================================================

def test_engine_route_no_op(repo: SQLiteRepository, event_store: SQLiteEventStore) -> None:
    """Verifies that ROUTE_NO_OP transitions: 0 -> 1 -> 2 -> 7 -> END."""
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_NO_OP)

    mock_analysis = MagicMock()
    mock_analysis.analyze.return_value = snapshot

    mock_planner = MagicMock()
    mock_planner.plan.return_value = plan

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
    )

    initial_state = _make_state(run_id="run-noop")
    final_state = engine.run(initial_state)

    # 1. State assertions
    assert final_state.final_status == "COMPLETED"
    assert final_state.snapshot == snapshot
    assert final_state.plan == plan
    assert final_state.candidates == ()
    assert final_state.evidences == ()

    # 2. Run record assertions in repository
    run_rec = repo.get_run("run-noop")
    assert run_rec is not None
    assert run_rec.final_status == "COMPLETED"
    assert run_rec.completed_at is not None
    assert run_rec.route_selected == WorkflowRoute.ROUTE_NO_OP.value

    # 3. Checkpoint step indices: 0 (initialized), 1 (analysis), 2 (planning), 7 (report)
    checkpoints = repo.get_checkpoints("run-noop")
    step_indices = [cp.step_index for cp in checkpoints]
    assert step_indices == [0, 1, 2, 7]

    # 4. Telemetry events assertions
    events = event_store.get_events("run-noop")
    event_types = [e.event_type for e in events]
    assert event_types == [
        "NODE_STARTED", "NODE_COMPLETED",  # ingest_and_analyze_node
        "NODE_STARTED", "NODE_COMPLETED",  # plan_execution_node
        "NODE_STARTED", "NODE_COMPLETED",  # report_node
    ]


def test_engine_route_to_docker_execution_success(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies ROUTE_TO_DOCKER_EXECUTION nominal path: 0 -> 1 -> 2 -> 5 -> 7 -> END."""
    snapshot = _make_snapshot()
    plan = _make_plan(
        route=WorkflowRoute.ROUTE_TO_DOCKER_EXECUTION,
        existing_tests=(Path("tests/test_existing.py"),),
    )
    base_ev = _make_evidence(evidence_id="base-ev", candidate_id=None, exit_code=0)

    mock_analysis = MagicMock()
    mock_analysis.analyze.return_value = snapshot

    mock_planner = MagicMock()
    mock_planner.plan.return_value = plan

    mock_exec = MagicMock()
    mock_exec.execute_regression_baseline.return_value = base_ev

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
        execution_service=mock_exec,
    )

    final_state = engine.run(_make_state(run_id="run-docker-ok"))

    assert final_state.final_status == "COMPLETED"
    assert final_state.baseline_evidence == base_ev

    # Baseline evidence persisted in repository
    ev_persisted = repo.get_evidence("base-ev")
    assert ev_persisted is not None
    assert ev_persisted.exit_code == 0

    # Steps visited: 0, 1, 2, 5, 7 (generation 3 & validation 4 bypassed)
    checkpoints = repo.get_checkpoints("run-docker-ok")
    assert [cp.step_index for cp in checkpoints] == [0, 1, 2, 5, 7]


def test_engine_route_generation_quarantine_path(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that all-quarantined candidates route to report_node: 0 -> 1 -> 2 -> 3 -> 4 -> 7 -> END."""
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    cand_raw = _make_candidate(candidate_id="c_raw", status=ValidationStatus.PENDING)
    cand_quar = _make_candidate(candidate_id="c_raw", status=ValidationStatus.QUARANTINED)

    mock_analysis = MagicMock()
    mock_analysis.analyze.return_value = snapshot

    mock_planner = MagicMock()
    mock_planner.plan.return_value = plan

    mock_gen = MagicMock()
    mock_gen.generate.return_value = [cand_raw]

    mock_val = MagicMock()
    mock_val.validate_candidates.return_value = [(cand_quar, MagicMock(passed=False))]

    mock_exec = MagicMock()

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
        generation_service=mock_gen,
        validation_pipeline=mock_val,
        execution_service=mock_exec,
    )

    final_state = engine.run(_make_state(run_id="run-quarantine"))

    assert final_state.final_status == "COMPLETED"
    assert len(final_state.candidates) == 1
    assert final_state.candidates[0].validation_status == ValidationStatus.QUARANTINED
    assert mock_exec.execute_candidates.call_count == 0  # Sandbox execution bypassed

    # Candidate was persisted to repository with QUARANTINED status
    cand_rec = repo.get_candidate("c_raw")
    assert cand_rec is not None
    assert cand_rec.validation_status == ValidationStatus.QUARANTINED

    # Steps visited: 0, 1, 2, 3, 4, 7
    checkpoints = repo.get_checkpoints("run-quarantine")
    assert [cp.step_index for cp in checkpoints] == [0, 1, 2, 3, 4, 7]


def test_engine_route_generation_nominal_success(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies nominal generation & execution success: 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 7 -> END."""
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    cand_raw = _make_candidate(candidate_id="c1", status=ValidationStatus.PENDING)
    cand_passed = _make_candidate(candidate_id="c1", status=ValidationStatus.PASSED)
    cand_ev = _make_evidence(evidence_id="ev1", candidate_id="c1", exit_code=0)

    mock_analysis = MagicMock(analyze=MagicMock(return_value=snapshot))
    mock_planner = MagicMock(plan=MagicMock(return_value=plan))
    mock_gen = MagicMock(generate=MagicMock(return_value=[cand_raw]))
    mock_val = MagicMock(validate_candidates=MagicMock(return_value=[(cand_passed, MagicMock(passed=True))]))
    mock_exec = MagicMock(execute_candidates=MagicMock(return_value=(cand_ev,)))
    mock_diag = MagicMock()

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
        generation_service=mock_gen,
        validation_pipeline=mock_val,
        execution_service=mock_exec,
        diagnosis_service=mock_diag,
    )

    final_state = engine.run(_make_state(run_id="run-gen-ok"))

    assert final_state.final_status == "COMPLETED"
    assert final_state.evidences == (cand_ev,)
    assert final_state.diagnoses == ()
    mock_diag.diagnose.assert_not_called()  # Diagnosis bypassed

    # Checkpoints visited: 0, 1, 2, 3, 4, 5, 7
    checkpoints = repo.get_checkpoints("run-gen-ok")
    assert [cp.step_index for cp in checkpoints] == [0, 1, 2, 3, 4, 5, 7]


def test_engine_route_generation_failure_diagnosed(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies generation test failure diagnosis: 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> END."""
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    cand = _make_candidate(candidate_id="c_fail", status=ValidationStatus.PASSED)
    ev_fail = _make_evidence(evidence_id="ev_fail", candidate_id="c_fail", exit_code=1)
    diag = _make_diagnosis(diagnosis_id="diag_1", evidence_id="ev_fail")

    mock_analysis = MagicMock(analyze=MagicMock(return_value=snapshot))
    mock_planner = MagicMock(plan=MagicMock(return_value=plan))
    mock_gen = MagicMock(generate=MagicMock(return_value=[cand]))
    mock_val = MagicMock(validate_candidates=MagicMock(return_value=[(cand, MagicMock(passed=True))]))
    mock_exec = MagicMock(execute_candidates=MagicMock(return_value=(ev_fail,)))
    mock_diag = MagicMock(diagnose=MagicMock(return_value=diag))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
        generation_service=mock_gen,
        validation_pipeline=mock_val,
        execution_service=mock_exec,
        diagnosis_service=mock_diag,
    )

    final_state = engine.run(_make_state(run_id="run-gen-diag"))

    assert final_state.final_status == "COMPLETED"
    assert final_state.diagnoses == (diag,)
    mock_diag.diagnose.assert_called_once()

    # Diagnosis persisted to repository
    diag_rec = repo.get_diagnosis("diag_1")
    assert diag_rec is not None
    assert diag_rec.canonical_category == FailureCategory.APPLICATION_BUG

    # Checkpoints visited: 0, 1, 2, 3, 4, 5, 6, 7
    checkpoints = repo.get_checkpoints("run-gen-diag")
    assert [cp.step_index for cp in checkpoints] == [0, 1, 2, 3, 4, 5, 6, 7]


def test_engine_exception_updates_run_to_failed_and_emits_event(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that an unhandled node exception sets final_status=FAILED and emits RUN_FAILED."""
    snapshot = _make_snapshot()
    mock_analysis = MagicMock(analyze=MagicMock(return_value=snapshot))
    mock_planner = MagicMock(plan=MagicMock(side_effect=RuntimeError("Planner crashed")))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
    )

    with pytest.raises(RuntimeError, match="Planner crashed"):
        engine.run(_make_state(run_id="run-err"))

    # RunRecord in SQLite updated to FAILED with error message
    run_rec = repo.get_run("run-err")
    assert run_rec is not None
    assert run_rec.final_status == "FAILED"
    assert "Planner crashed" in (run_rec.error_message or "")

    # RUN_FAILED event emitted
    events = event_store.get_events("run-err")
    failed_events = [e for e in events if e.event_type == "RUN_FAILED"]
    assert len(failed_events) == 1
    assert failed_events[0].node_name == "plan_execution_node"
    assert "Planner crashed" in failed_events[0].payload["error"]


def test_engine_cycle_detection(repo: SQLiteRepository, event_store: SQLiteEventStore) -> None:
    """Verifies that an attempt to execute a node twice triggers cycle prevention."""
    snapshot = _make_snapshot()
    mock_analysis = MagicMock(analyze=MagicMock(return_value=snapshot))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
    )

    # Force a loop by monkeypatching _resolve_next_node to return the same node
    engine._resolve_next_node = lambda cur, st: "ingest_and_analyze_node"  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="Cycle detected"):
        engine.run(_make_state(run_id="run-cycle"))


def test_engine_immutability(repo: SQLiteRepository, event_store: SQLiteEventStore) -> None:
    """Verifies that the caller's initial WorkflowState is not mutated by engine.run."""
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_NO_OP)
    mock_analysis = MagicMock(analyze=MagicMock(return_value=snapshot))
    mock_planner = MagicMock(plan=MagicMock(return_value=plan))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
    )

    initial_state = _make_state(run_id="run-immutable")
    final_state = engine.run(initial_state)

    # Initial state must remain unchanged
    assert initial_state.final_status == "INITIALIZED"
    assert initial_state.snapshot is None
    assert initial_state.plan is None

    # Returned state is a new instance
    assert final_state is not initial_state
    assert final_state.final_status == "COMPLETED"
    assert final_state.snapshot == snapshot
    assert final_state.plan == plan


# ============================================================================
# 4. Crash Recovery and Checkpoint Resumption Tests (WBS 1.6.3B)
# ============================================================================

def test_recovery_missing_checkpoint_raises(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that attempting to resume a run with no checkpoints raises RecoveryError."""
    engine = WorkflowEngine(repository=repo, event_store=event_store)
    with pytest.raises(RecoveryError, match="No checkpoint found for run 'unknown-run'"):
        resume_run("unknown-run", engine)


def test_recovery_terminal_run_completed_rejected(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that attempting to resume an already COMPLETED run is rejected with RecoveryError."""
    engine = WorkflowEngine(repository=repo, event_store=event_store)
    state = _make_state(run_id="run-done")
    repo.save_run(
        run_id="run-done",
        repo_path=state.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="COMPLETED",
    )
    repo.save_checkpoint(state=state, step_index=2, node_name="plan_execution_node")

    with pytest.raises(RecoveryError, match="already in terminal status 'COMPLETED'"):
        resume_run("run-done", engine)


def test_recovery_terminal_run_failed_rejected(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that attempting to resume a FAILED run is rejected with RecoveryError."""
    engine = WorkflowEngine(repository=repo, event_store=event_store)
    state = _make_state(run_id="run-failed")
    repo.save_run(
        run_id="run-failed",
        repo_path=state.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="FAILED",
    )
    repo.save_checkpoint(state=state, step_index=2, node_name="plan_execution_node")

    with pytest.raises(RecoveryError, match="already in terminal status 'FAILED'"):
        resume_run("run-failed", engine)


def test_recovery_terminal_checkpoint_step_7_rejected(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that attempting to resume from checkpoint step 7 is rejected as terminal."""
    engine = WorkflowEngine(repository=repo, event_store=event_store)
    state = _make_state(run_id="run-step7").model_copy(update={"final_status": "COMPLETED"})
    repo.save_run(
        run_id="run-step7",
        repo_path=state.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state, step_index=7, node_name="report_node")

    with pytest.raises(RecoveryError, match="is terminal"):
        resume_run("run-step7", engine)


def test_recovery_latest_checkpoint_selection(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that resumption deterministically selects the latest checkpoint."""
    run_id = "run-multi-cp"
    state_0 = _make_state(run_id=run_id)
    snapshot = _make_snapshot()
    state_1 = state_0.model_copy(update={"snapshot": snapshot})
    plan = _make_plan(route=WorkflowRoute.ROUTE_NO_OP)
    state_2 = state_1.model_copy(update={"plan": plan})

    repo.save_run(
        run_id=run_id,
        repo_path=state_0.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state_0, step_index=0, node_name="initialized")
    repo.save_checkpoint(state=state_1, step_index=1, node_name="ingest_and_analyze_node")
    repo.save_checkpoint(state=state_2, step_index=2, node_name="plan_execution_node")

    mock_analysis = MagicMock()
    mock_planner = MagicMock()

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
    )

    final_state = resume_run(run_id, engine)

    # Started from checkpoint 2 -> ROUTE_NO_OP transitions to report_node (step 7)
    assert final_state.final_status == "COMPLETED"
    mock_analysis.analyze.assert_not_called()  # Step 1 not re-executed
    mock_planner.plan.assert_not_called()     # Step 2 not re-executed

    # RUN_RESUMED event emitted with latest checkpoint details
    events = event_store.get_events(run_id)
    resume_events = [e for e in events if e.event_type == "RUN_RESUMED"]
    assert len(resume_events) == 1
    assert resume_events[0].payload["step_index"] == 2
    assert resume_events[0].payload["node_name"] == "plan_execution_node"


def test_recovery_from_checkpoint_0_initialized(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies resumption from step 0 executes ingest_and_analyze_node next."""
    run_id = "run-cp0"
    state_0 = _make_state(run_id=run_id)
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_NO_OP)

    repo.save_run(
        run_id=run_id,
        repo_path=state_0.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="INITIALIZED",
    )
    repo.save_checkpoint(state=state_0, step_index=0, node_name="initialized")

    mock_analysis = MagicMock(analyze=MagicMock(return_value=snapshot))
    mock_planner = MagicMock(plan=MagicMock(return_value=plan))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
    )

    final_state = resume_run(run_id, engine)

    assert final_state.final_status == "COMPLETED"
    mock_analysis.analyze.assert_called_once()
    mock_planner.plan.assert_called_once()

    checkpoints = repo.get_checkpoints(run_id)
    assert [cp.step_index for cp in checkpoints] == [0, 1, 2, 7]


def test_recovery_from_checkpoint_1_analysis(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies resumption from step 1 executes plan_execution_node next and does not re-analyze."""
    run_id = "run-cp1"
    snapshot = _make_snapshot()
    state_1 = _make_state(run_id=run_id, snapshot=snapshot)
    plan = _make_plan(route=WorkflowRoute.ROUTE_NO_OP)

    repo.save_run(
        run_id=run_id,
        repo_path=state_1.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state_1, step_index=1, node_name="ingest_and_analyze_node")

    mock_analysis = MagicMock()
    mock_planner = MagicMock(plan=MagicMock(return_value=plan))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
    )

    final_state = resume_run(run_id, engine)

    assert final_state.final_status == "COMPLETED"
    mock_analysis.analyze.assert_not_called()  # Analysis NOT repeated
    mock_planner.plan.assert_called_once()

    checkpoints = repo.get_checkpoints(run_id)
    assert [cp.step_index for cp in checkpoints] == [1, 2, 7]


def test_recovery_from_checkpoint_2_planning(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies resumption from step 2 routes to generate_tests_node without repeating planning."""
    run_id = "run-cp2"
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    state_2 = _make_state(run_id=run_id, snapshot=snapshot, plan=plan)

    cand = _make_candidate(run_id=run_id, status=ValidationStatus.PASSED)
    ev = _make_evidence(run_id=run_id, exit_code=0)

    repo.save_run(
        run_id=run_id,
        repo_path=state_2.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state_2, step_index=2, node_name="plan_execution_node")

    mock_analysis = MagicMock()
    mock_planner = MagicMock()
    mock_gen = MagicMock(generate=MagicMock(return_value=[cand]))
    mock_val = MagicMock(validate_candidates=MagicMock(return_value=[(cand, MagicMock(passed=True))]))
    mock_exec = MagicMock(execute_candidates=MagicMock(return_value=(ev,)))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
        generation_service=mock_gen,
        validation_pipeline=mock_val,
        execution_service=mock_exec,
    )

    final_state = resume_run(run_id, engine)

    assert final_state.final_status == "COMPLETED"
    mock_analysis.analyze.assert_not_called()
    mock_planner.plan.assert_not_called()
    mock_gen.generate.assert_called_once()
    mock_val.validate_candidates.assert_called_once()
    mock_exec.execute_candidates.assert_called_once()

    checkpoints = repo.get_checkpoints(run_id)
    assert [cp.step_index for cp in checkpoints] == [2, 3, 4, 5, 7]


def test_recovery_from_checkpoint_3_generation(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies resumption from step 3 executes validate_candidates_node next and does not re-generate."""
    run_id = "run-cp3"
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    cand_raw = _make_candidate(run_id=run_id, status=ValidationStatus.PENDING)
    cand_val = _make_candidate(run_id=run_id, status=ValidationStatus.PASSED)
    state_3 = _make_state(run_id=run_id, snapshot=snapshot, plan=plan, candidates=(cand_raw,))
    ev = _make_evidence(run_id=run_id, exit_code=0)

    repo.save_run(
        run_id=run_id,
        repo_path=state_3.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state_3, step_index=3, node_name="generate_tests_node")

    mock_gen = MagicMock()
    mock_val = MagicMock(validate_candidates=MagicMock(return_value=[(cand_val, MagicMock(passed=True))]))
    mock_exec = MagicMock(execute_candidates=MagicMock(return_value=(ev,)))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        generation_service=mock_gen,
        validation_pipeline=mock_val,
        execution_service=mock_exec,
    )

    final_state = resume_run(run_id, engine)

    assert final_state.final_status == "COMPLETED"
    mock_gen.generate.assert_not_called()  # Generation NOT repeated
    mock_val.validate_candidates.assert_called_once()
    mock_exec.execute_candidates.assert_called_once()

    checkpoints = repo.get_checkpoints(run_id)
    assert [cp.step_index for cp in checkpoints] == [3, 4, 5, 7]


def test_recovery_from_checkpoint_4_validation(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies resumption from step 4 routes to execute_sandbox_node without re-validating."""
    run_id = "run-cp4"
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    cand_val = _make_candidate(run_id=run_id, status=ValidationStatus.PASSED)
    state_4 = _make_state(run_id=run_id, snapshot=snapshot, plan=plan, candidates=(cand_val,))
    ev = _make_evidence(run_id=run_id, exit_code=0)

    repo.save_run(
        run_id=run_id,
        repo_path=state_4.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state_4, step_index=4, node_name="validate_candidates_node")

    mock_val = MagicMock()
    mock_exec = MagicMock(execute_candidates=MagicMock(return_value=(ev,)))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        validation_pipeline=mock_val,
        execution_service=mock_exec,
    )

    final_state = resume_run(run_id, engine)

    assert final_state.final_status == "COMPLETED"
    mock_val.validate_candidates.assert_not_called()  # Validation NOT repeated
    mock_exec.execute_candidates.assert_called_once()

    checkpoints = repo.get_checkpoints(run_id)
    assert [cp.step_index for cp in checkpoints] == [4, 5, 7]


def test_recovery_from_checkpoint_5_execution_routes_to_diagnosis(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies resumption from step 5 routes failed evidences to diagnose_failure_node without re-executing."""
    run_id = "run-cp5"
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    cand = _make_candidate(run_id=run_id, status=ValidationStatus.PASSED)
    ev_fail = _make_evidence(run_id=run_id, candidate_id=cand.candidate_id, exit_code=1)
    diag = _make_diagnosis(evidence_id=ev_fail.evidence_id)

    state_5 = _make_state(
        run_id=run_id,
        snapshot=snapshot,
        plan=plan,
        candidates=(cand,),
        evidences=(ev_fail,),
    )

    repo.save_run(
        run_id=run_id,
        repo_path=state_5.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_candidate(cand)
    repo.save_evidence(ev_fail)
    repo.save_checkpoint(state=state_5, step_index=5, node_name="execute_sandbox_node")

    mock_exec = MagicMock()
    mock_diag = MagicMock(diagnose=MagicMock(return_value=diag))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        execution_service=mock_exec,
        diagnosis_service=mock_diag,
    )

    final_state = resume_run(run_id, engine)

    assert final_state.final_status == "COMPLETED"
    mock_exec.execute_candidates.assert_not_called()  # Execution NOT repeated
    mock_diag.diagnose.assert_called_once()           # Diagnosis executed

    checkpoints = repo.get_checkpoints(run_id)
    assert [cp.step_index for cp in checkpoints] == [5, 6, 7]


def test_recovery_from_checkpoint_6_diagnosis(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies resumption from step 6 dispatches report_node next without repeating diagnosis."""
    run_id = "run-cp6"
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    cand = _make_candidate(run_id=run_id, status=ValidationStatus.PASSED)
    ev_fail = _make_evidence(run_id=run_id, candidate_id=cand.candidate_id, exit_code=1)
    diag = _make_diagnosis(evidence_id=ev_fail.evidence_id)

    state_6 = _make_state(
        run_id=run_id,
        snapshot=snapshot,
        plan=plan,
        candidates=(cand,),
        evidences=(ev_fail,),
        diagnoses=(diag,),
    )

    repo.save_run(
        run_id=run_id,
        repo_path=state_6.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_candidate(cand)
    repo.save_evidence(ev_fail)
    repo.save_diagnosis(diag, run_id=run_id)
    repo.save_checkpoint(state=state_6, step_index=6, node_name="diagnose_failure_node")

    mock_diag = MagicMock()

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        diagnosis_service=mock_diag,
    )

    final_state = resume_run(run_id, engine)

    assert final_state.final_status == "COMPLETED"
    mock_diag.diagnose.assert_not_called()  # Diagnosis NOT repeated

    checkpoints = repo.get_checkpoints(run_id)
    assert [cp.step_index for cp in checkpoints] == [6, 7]


def test_recovery_failure_updates_run_to_failed_and_emits_event(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that an exception during resumption sets final_status=FAILED and emits RUN_FAILED."""
    run_id = "run-resume-err"
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    state_2 = _make_state(run_id=run_id, snapshot=snapshot, plan=plan)

    repo.save_run(
        run_id=run_id,
        repo_path=state_2.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state_2, step_index=2, node_name="plan_execution_node")

    mock_gen = MagicMock(generate=MagicMock(side_effect=RuntimeError("LLM generation crashed")))

    engine = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        generation_service=mock_gen,
    )

    with pytest.raises(RuntimeError, match="LLM generation crashed"):
        resume_run(run_id, engine)

    # RunRecord updated to FAILED with error message
    run_rec = repo.get_run(run_id)
    assert run_rec is not None
    assert run_rec.final_status == "FAILED"
    assert "LLM generation crashed" in (run_rec.error_message or "")

    # RUN_FAILED event emitted
    events = event_store.get_events(run_id)
    fail_events = [e for e in events if e.event_type == "RUN_FAILED"]
    assert len(fail_events) == 1
    assert fail_events[0].node_name == "generate_tests_node"
    assert "LLM generation crashed" in fail_events[0].payload["error"]


def test_recovery_state_roundtrip_fidelity(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that all entities and state collections survive checkpoint round-trip during recovery."""
    run_id = "run-roundtrip"
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_TO_TEST_GENERATION)
    cand = _make_candidate(run_id=run_id, candidate_id="c_rt", status=ValidationStatus.PASSED)
    base_ev = _make_evidence(run_id=run_id, evidence_id="ev_base", candidate_id=None, exit_code=0)
    cand_ev = _make_evidence(run_id=run_id, evidence_id="ev_cand", candidate_id="c_rt", exit_code=1)
    diag = _make_diagnosis(diagnosis_id="d_rt", evidence_id="ev_cand")

    state = _make_state(
        run_id=run_id,
        snapshot=snapshot,
        plan=plan,
        candidates=(cand,),
        baseline_evidence=base_ev,
        evidences=(cand_ev,),
        diagnoses=(diag,),
    )

    repo.save_run(
        run_id=run_id,
        repo_path=state.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_candidate(cand)
    repo.save_evidence(base_ev)
    repo.save_evidence(cand_ev)
    repo.save_diagnosis(diag, run_id=run_id)
    repo.save_checkpoint(state=state, step_index=6, node_name="diagnose_failure_node")

    engine = WorkflowEngine(repository=repo, event_store=event_store)
    final_state = resume_run(run_id, engine)

    assert final_state.final_status == "COMPLETED"
    assert final_state.snapshot == snapshot
    assert final_state.plan == plan
    assert final_state.candidates == (cand,)
    assert final_state.baseline_evidence == base_ev
    assert final_state.evidences == (cand_ev,)
    assert final_state.diagnoses == (diag,)


def test_recovery_no_duplicate_checkpoints_or_artifacts(
    repo: SQLiteRepository, event_store: SQLiteEventStore
) -> None:
    """Verifies that resumption preserves existing checkpoints and creates no duplicate step entries."""
    run_id = "run-no-dupes"
    snapshot = _make_snapshot()
    plan = _make_plan(route=WorkflowRoute.ROUTE_NO_OP)

    # Initial partial run
    mock_analysis = MagicMock(analyze=MagicMock(return_value=snapshot))
    mock_planner = MagicMock(plan=MagicMock(return_value=plan))
    engine_init = WorkflowEngine(
        repository=repo,
        event_store=event_store,
        analysis_service=mock_analysis,
        planner=mock_planner,
    )

    # Simulate crash right after step 2 checkpoint
    init_state = _make_state(run_id=run_id)
    repo.save_run(
        run_id=run_id,
        repo_path=init_state.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=init_state, step_index=0, node_name="initialized")
    state_1 = init_state.model_copy(update={"snapshot": snapshot})
    repo.save_checkpoint(state=state_1, step_index=1, node_name="ingest_and_analyze_node")
    state_2 = state_1.model_copy(update={"plan": plan})
    repo.save_checkpoint(state=state_2, step_index=2, node_name="plan_execution_node")

    # Resume from checkpoint 2 to completion
    final_state = resume_run(run_id, engine_init)
    assert final_state.final_status == "COMPLETED"

    # Verify checkpoints: exactly 0, 1, 2, 7 (no duplicates)
    checkpoints = repo.get_checkpoints(run_id)
    step_indices = [cp.step_index for cp in checkpoints]
    assert step_indices == [0, 1, 2, 7]
    assert len(step_indices) == len(set(step_indices))
