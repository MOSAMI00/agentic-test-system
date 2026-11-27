"""
Unit tests for core models and WorkflowState.
Verifies validation, immutability (NFR-06), serialization, and Option A defaults.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentic_test.core.models import (
    ChangeType,
    DiffHunk,
    ExecutionEvidence,
    ExecutionPlan,
    FailureCategory,
    FailureDiagnosis,
    RepositorySnapshot,
    SymbolContract,
    SymbolType,
    TestCandidate,
    TriageEngine,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.core.state import WorkflowState


def test_diff_hunk_creation_and_immutability() -> None:
    """DiffHunk value object validates attributes and enforces immutability."""
    hunk = DiffHunk(
        file_path=Path("src/module.py"),
        old_start=10,
        old_lines=5,
        new_start=10,
        new_lines=8,
        change_type=ChangeType.MODIFIED,
        content="@@ -10,5 +10,8 @@\n+line",
    )
    assert hunk.file_path == Path("src/module.py")
    assert hunk.old_start == 10
    assert hunk.new_lines == 8
    assert hunk.change_type == ChangeType.MODIFIED

    # Immutability enforcement (frozen=True)
    with pytest.raises(ValidationError):
        setattr(hunk, "old_start", 20)


def test_symbol_contract_creation_and_immutability() -> None:
    """SymbolContract entity validates fields and enforces immutability."""
    symbol = SymbolContract(
        qualified_name="Calculator.add",
        symbol_type=SymbolType.METHOD,
        file_path=Path("src/calc.py"),
        line_range=(15, 25),
        signature="def add(self, a: int, b: int) -> int:",
        docstring="Adds two numbers.",
        dependencies=("typing",),
        is_affected=False,
    )
    assert symbol.qualified_name == "Calculator.add"
    assert symbol.symbol_type == SymbolType.METHOD
    assert symbol.line_range == (15, 25)
    assert symbol.is_affected is False

    with pytest.raises(ValidationError):
        setattr(symbol, "is_affected", True)


def test_repository_snapshot_option_a_defaults() -> None:
    """
    RepositorySnapshot preserves the 9 baseline fields and provides independent
    empty-tuple defaults for diff_hunks, affected_symbols, and syntax_errors (Option A).
    """
    snapshot = RepositorySnapshot(
        repo_path=Path("/tmp/test_repo"),
        current_commit="a" * 40,
        base_commit="b" * 40,
        branch_name="main",
        tracked_files=(Path("app.py"),),
        existing_test_files=(Path("test_app.py"),),
        source_tree_hash="c" * 64,
        is_valid=True,
    )

    # 9 baseline fields verified
    assert snapshot.repo_path == Path("/tmp/test_repo")
    assert snapshot.current_commit == "a" * 40
    assert snapshot.base_commit == "b" * 40
    assert snapshot.branch_name == "main"
    assert snapshot.tracked_files == (Path("app.py"),)
    assert snapshot.existing_test_files == (Path("test_app.py"),)
    assert snapshot.source_tree_hash == "c" * 64
    assert snapshot.is_valid is True
    assert isinstance(snapshot.created_at, datetime)

    # Option A fields default to empty tuples
    assert snapshot.diff_hunks == ()
    assert snapshot.affected_symbols == ()
    assert snapshot.syntax_errors == ()

    # Verify collections default to immutable empty tuples
    snapshot2 = RepositorySnapshot(
        repo_path=Path("/tmp/other_repo"),
        current_commit="d" * 40,
        base_commit="e" * 40,
        branch_name="dev",
        source_tree_hash="f" * 64,
        is_valid=True,
    )
    assert snapshot.diff_hunks == ()
    assert snapshot2.diff_hunks == ()
    assert isinstance(snapshot.diff_hunks, tuple)
    assert isinstance(snapshot.affected_symbols, tuple)
    assert isinstance(snapshot.syntax_errors, tuple)


def test_repository_snapshot_immutability() -> None:
    """RepositorySnapshot is frozen and cannot be mutated in place."""
    snapshot = RepositorySnapshot(
        repo_path=Path("/tmp/test_repo"),
        current_commit="a" * 40,
        base_commit="b" * 40,
        branch_name="main",
        source_tree_hash="c" * 64,
        is_valid=True,
    )
    with pytest.raises(ValidationError):
        setattr(snapshot, "is_valid", False)

    with pytest.raises(ValidationError):
        setattr(snapshot, "current_commit", "f" * 40)


def test_repository_snapshot_serialization() -> None:
    """RepositorySnapshot serializes to JSON and deserializes back faithfully."""
    hunk = DiffHunk(
        file_path=Path("main.py"),
        old_start=1,
        old_lines=3,
        new_start=1,
        new_lines=5,
        change_type=ChangeType.ADDED,
        content="@@ -1,3 +1,5 @@\n+print('hello')",
    )
    snapshot = RepositorySnapshot(
        repo_path=Path("/repo"),
        current_commit="1234567890abcdef1234567890abcdef12345678",
        base_commit="0000000000000000000000000000000000000000",
        branch_name="feature/test",
        tracked_files=(Path("main.py"), Path("utils.py")),
        existing_test_files=(Path("test_main.py"),),
        source_tree_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        is_valid=True,
        diff_hunks=(hunk,),
        syntax_errors=("SyntaxError in legacy.py: line 10",),
    )

    json_data = snapshot.model_dump_json()
    reconstructed = RepositorySnapshot.model_validate_json(json_data)

    assert reconstructed == snapshot
    assert len(reconstructed.diff_hunks) == 1
    assert reconstructed.diff_hunks[0].file_path == Path("main.py")
    assert reconstructed.syntax_errors == ("SyntaxError in legacy.py: line 10",)


def test_workflow_state_creation_and_immutability() -> None:
    """WorkflowState instantiates, captures snapshot, and enforces immutability."""
    snapshot = RepositorySnapshot(
        repo_path=Path("/tmp/repo"),
        current_commit="1" * 40,
        base_commit="2" * 40,
        branch_name="master",
        source_tree_hash="3" * 64,
        is_valid=True,
    )

    state = WorkflowState(
        run_id="run-uuid-001",
        repo_path=Path("/tmp/repo"),
        snapshot=snapshot,
    )

    assert state.run_id == "run-uuid-001"
    assert state.repo_path == Path("/tmp/repo")
    assert isinstance(state.created_at, datetime)
    assert state.snapshot == snapshot
    assert state.plan is None
    assert state.candidates == ()
    assert state.generation_retry_count == 0
    assert state.max_generation_retries == 2
    assert state.evidence is None
    assert state.diagnoses == ()
    assert state.final_status == "INITIALIZED"
    assert state.error_message is None

    # Verify immutability
    with pytest.raises(ValidationError):
        setattr(state, "final_status", "SUCCESS")

    # Serialization test
    state_json = state.model_dump_json()
    restored = WorkflowState.model_validate_json(state_json)
    assert restored.run_id == state.run_id
    assert restored.snapshot is not None
    assert restored.snapshot.current_commit == "1" * 40


def test_workflow_state_rejects_invalid_field_types() -> None:
    """
    Stage 3 §4.4.3: WorkflowState enforces concrete Pydantic schemas on
    plan, candidates, evidence, and diagnoses, rejecting invalid types.
    """
    # 1. Reject invalid plan type (expects ExecutionPlan, reject str or int)
    with pytest.raises(ValidationError):
        WorkflowState(
            run_id="run-01",
            repo_path=Path("/tmp/repo"),
            plan="invalid_string_plan",  # type: ignore[arg-type]
        )

    # 2. Reject invalid candidates list item (expects TestCandidate, reject str or int)
    with pytest.raises(ValidationError):
        WorkflowState(
            run_id="run-01",
            repo_path=Path("/tmp/repo"),
            candidates=("not_a_candidate",),  # type: ignore[arg-type]
        )

    # 3. Reject invalid evidence type (expects ExecutionEvidence, reject str or int)
    with pytest.raises(ValidationError):
        WorkflowState(
            run_id="run-01",
            repo_path=Path("/tmp/repo"),
            evidences=("invalid_evidence",),  # type: ignore[arg-type]
        )

    # 4. Reject invalid diagnoses list item (expects FailureDiagnosis, reject str or int)
    with pytest.raises(ValidationError):
        WorkflowState(
            run_id="run-01",
            repo_path=Path("/tmp/repo"),
            diagnoses=(12345,),  # type: ignore[arg-type]
        )


def test_workflow_state_with_valid_stage3_entities() -> None:
    """
    Stage 3 §4.4.3: WorkflowState instantiates with concrete Stage 3 entities
    and serializes/deserializes faithfully.
    """
    plan = ExecutionPlan(
        plan_id="plan-001",
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        rationale="Uncovered modified symbols detected",
        decision_hash="hash-1234567890",
    )

    candidate = TestCandidate(
        candidate_id="cand-001",
        run_id="run-01",
        target_symbol_name="Calculator.add",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_add(): assert True\n",
        validation_status=ValidationStatus.PENDING,
    )

    evidence = ExecutionEvidence(
        evidence_id="ev-001",
        run_id="run-01",
        candidate_id="cand-001",
        exit_code=0,
        stdout="1 passed",
        stderr="",
        duration_sec=1.25,
        line_coverage=0.95,
        branch_coverage=0.90,
    )

    diagnosis = FailureDiagnosis(
        diagnosis_id="diag-001",
        evidence_id="ev-001",
        canonical_category=FailureCategory.APPLICATION_BUG,
        confidence=0.92,
        triage_engine=TriageEngine.DETERMINISTIC_RULE,
        explanation="Assertion failure indicates application defect",
        is_application_bug=True,
    )

    state = WorkflowState(
        run_id="run-01",
        repo_path=Path("/tmp/repo"),
        plan=plan,
        candidates=(candidate,),
        evidences=(evidence,),
        diagnoses=(diagnosis,),
    )

    assert state.plan == plan
    assert state.plan.route == WorkflowRoute.ROUTE_TO_TEST_GENERATION
    assert len(state.candidates) == 1
    assert state.candidates[0].candidate_id == "cand-001"
    assert state.evidence == evidence
    assert state.evidence.exit_code == 0
    assert len(state.diagnoses) == 1
    assert state.diagnoses[0].canonical_category == FailureCategory.APPLICATION_BUG

    # JSON round-trip
    state_json = state.model_dump_json()
    reconstructed = WorkflowState.model_validate_json(state_json)
    assert reconstructed == state
    assert reconstructed.plan is not None
    assert reconstructed.plan.plan_id == "plan-001"
    assert reconstructed.candidates[0].candidate_id == "cand-001"
    assert reconstructed.evidence is not None
    assert reconstructed.evidence.evidence_id == "ev-001"
    assert reconstructed.diagnoses[0].diagnosis_id == "diag-001"


def test_in_place_mutation_attempts_on_non_empty_collections() -> None:
    """
    NFR-06 / Decision B: In-place mutation attempts on non-empty tuple collections
    must raise AttributeError or TypeError, preventing state corruption.
    """
    hunk = DiffHunk(
        file_path=Path("main.py"),
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=2,
        change_type=ChangeType.MODIFIED,
        content="@@ -1,1 +1,2 @@\n",
    )
    snapshot = RepositorySnapshot(
        repo_path=Path("/tmp/repo"),
        current_commit="a" * 40,
        base_commit="b" * 40,
        branch_name="main",
        tracked_files=(Path("main.py"),),
        diff_hunks=(hunk,),
        syntax_errors=("error line 1",),
        source_tree_hash="c" * 64,
        is_valid=True,
    )

    # 1. snapshot.diff_hunks mutation attempts
    with pytest.raises(AttributeError):
        snapshot.diff_hunks.append(hunk)  # type: ignore[attr-defined]

    with pytest.raises(TypeError):
        snapshot.diff_hunks[0] = hunk  # type: ignore[index]

    # 2. snapshot.tracked_files mutation attempts
    with pytest.raises(AttributeError):
        snapshot.tracked_files.pop()  # type: ignore[attr-defined]

    # 3. snapshot.syntax_errors mutation attempts
    with pytest.raises(AttributeError):
        snapshot.syntax_errors.extend(["another error"])  # type: ignore[attr-defined]

    candidate = TestCandidate(
        candidate_id="cand-001",
        run_id="run-01",
        target_symbol_name="Calculator.add",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_add(): pass\n",
    )
    state = WorkflowState(
        run_id="run-01",
        repo_path=Path("/tmp/repo"),
        candidates=(candidate,),
    )

    # 4. state.candidates mutation attempts
    with pytest.raises(AttributeError):
        state.candidates.append(candidate)  # type: ignore[attr-defined]

    with pytest.raises(TypeError):
        state.candidates[0] = candidate  # type: ignore[index]


def test_independence_from_mutable_input_lists() -> None:
    """
    NFR-06 / Decision B: Models are independent of mutable input lists passed
    to constructors; mutating the caller's list after construction does not alter the model.
    """
    input_paths = [Path("file1.py"), Path("file2.py")]
    input_hunks = [
        DiffHunk(
            file_path=Path("file1.py"),
            old_start=1,
            old_lines=1,
            new_start=1,
            new_lines=2,
            change_type=ChangeType.MODIFIED,
            content="diff",
        )
    ]

    snapshot = RepositorySnapshot(
        repo_path=Path("/tmp/repo"),
        current_commit="a" * 40,
        base_commit="b" * 40,
        branch_name="main",
        tracked_files=input_paths,  # type: ignore[arg-type]
        diff_hunks=input_hunks,  # type: ignore[arg-type]
        source_tree_hash="c" * 64,
        is_valid=True,
    )

    # Mutate the caller's input lists
    input_paths.append(Path("hacked.py"))
    input_paths.clear()
    input_hunks.clear()

    # Verify model state remains completely untouched
    assert len(snapshot.tracked_files) == 2
    assert snapshot.tracked_files[0] == Path("file1.py")
    assert len(snapshot.diff_hunks) == 1
    assert snapshot.diff_hunks[0].file_path == Path("file1.py")


def test_nested_entity_mutation_prevention() -> None:
    """
    NFR-06 / Decision B: Nested entities within collections are themselves
    frozen Pydantic models; modifying their attributes raises ValidationError.
    """
    hunk = DiffHunk(
        file_path=Path("calc.py"),
        old_start=5,
        old_lines=2,
        new_start=5,
        new_lines=4,
        change_type=ChangeType.MODIFIED,
        content="@@ -5,2 +5,4 @@\n",
    )
    snapshot = RepositorySnapshot(
        repo_path=Path("/tmp/repo"),
        current_commit="a" * 40,
        base_commit="b" * 40,
        branch_name="main",
        diff_hunks=(hunk,),
        source_tree_hash="c" * 64,
        is_valid=True,
    )

    # Attempting to mutate an attribute of the nested DiffHunk entity
    with pytest.raises(ValidationError):
        setattr(snapshot.diff_hunks[0], "content", "tampered_content")

    candidate = TestCandidate(
        candidate_id="cand-001",
        run_id="run-01",
        target_symbol_name="Calculator.add",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_add(): pass\n",
    )
    state = WorkflowState(
        run_id="run-01",
        repo_path=Path("/tmp/repo"),
        candidates=(candidate,),
    )

    # Attempting to mutate an attribute of the nested TestCandidate entity
    with pytest.raises(ValidationError):
        setattr(state.candidates[0], "validation_status", ValidationStatus.PASSED)


def test_json_serialization_and_deserialization_immutability() -> None:
    """
    NFR-06 / Decision B: Tuple collections serialize to standard JSON arrays
    and deserialize back into immutable tuple containers with identical data.
    """
    snapshot = RepositorySnapshot(
        repo_path=Path("/repo"),
        current_commit="1" * 40,
        base_commit="0" * 40,
        branch_name="main",
        tracked_files=(Path("a.py"), Path("b.py")),
        source_tree_hash="d" * 64,
        is_valid=True,
        syntax_errors=("error1", "error2"),
    )

    json_str = snapshot.model_dump_json()
    # Verifies standard JSON array format [...]
    assert 'a.py' in json_str  # tracked_files appear in JSON
    assert '"syntax_errors":["error1","error2"]' in json_str or 'error1' in json_str

    deserialized = RepositorySnapshot.model_validate_json(json_str)
    assert deserialized == snapshot
    assert isinstance(deserialized.tracked_files, tuple)
    assert isinstance(deserialized.syntax_errors, tuple)

    # Deserialized instance collections are also protected against in-place mutation
    with pytest.raises(AttributeError):
        deserialized.tracked_files.append(Path("c.py"))  # type: ignore[attr-defined]


def test_copy_update_behavior_with_immutable_collections() -> None:
    """
    NFR-06 / Decision B: model_copy(update={...}) creates a new immutable instance
    with replacement collection values while leaving the original instance untouched.
    """
    hunk1 = DiffHunk(
        file_path=Path("f1.py"),
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=1,
        change_type=ChangeType.MODIFIED,
        content="c1",
    )
    hunk2 = DiffHunk(
        file_path=Path("f2.py"),
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=1,
        change_type=ChangeType.ADDED,
        content="c2",
    )

    orig_snapshot = RepositorySnapshot(
        repo_path=Path("/tmp/repo"),
        current_commit="a" * 40,
        base_commit="b" * 40,
        branch_name="main",
        diff_hunks=(hunk1,),
        source_tree_hash="c" * 64,
        is_valid=True,
    )

    # model_copy with replacement tuple
    copied_snapshot = orig_snapshot.model_copy(update={"diff_hunks": (hunk2,)})

    # Original is untouched
    assert len(orig_snapshot.diff_hunks) == 1
    assert orig_snapshot.diff_hunks[0].file_path == Path("f1.py")

    # Copied snapshot has replacement values
    assert len(copied_snapshot.diff_hunks) == 1
    assert copied_snapshot.diff_hunks[0].file_path == Path("f2.py")
    assert isinstance(copied_snapshot.diff_hunks, tuple)


def test_workflow_state_validated_reconstruction() -> None:
    """
    NFR-06: Demonstrates validated reconstruction for state updates.
    model_copy(update={...}) bypasses validation and can inject invalid or mutable state.
    Validated reconstruction via WorkflowState(**{**state.__dict__, **updates})
    ensures:
    1. Invalid replacement collections (wrong item types) are rejected with ValidationError.
    2. Valid replacement collections (e.g. lists passed in update) are coerced to immutable tuples.
    3. The original WorkflowState instance remains untouched.
    """
    snapshot = RepositorySnapshot(
        repo_path=Path("/tmp/repo"),
        current_commit="1" * 40,
        base_commit="0" * 40,
        branch_name="main",
        source_tree_hash="a" * 64,
        is_valid=True,
    )
    initial_state = WorkflowState(
        run_id="run-001",
        repo_path=Path("/tmp/repo"),
        snapshot=snapshot,
    )

    # 1. Negative test: Invalid replacement collection is rejected with ValidationError
    with pytest.raises(ValidationError):
        WorkflowState(**{**initial_state.__dict__, "candidates": ["not_a_candidate_instance"]})

    with pytest.raises(ValidationError):
        WorkflowState(**{**initial_state.__dict__, "diagnoses": [12345]})

    # 2. Positive test: Valid replacement collection is accepted and coerced to immutable tuple
    candidate = TestCandidate(
        candidate_id="cand-001",
        run_id="run-001",
        target_symbol_name="Calculator.add",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_add(): pass\n",
    )
    # Passing a mutable list in update:
    updated_state = WorkflowState(**{**initial_state.__dict__, "candidates": [candidate]})

    assert len(updated_state.candidates) == 1
    assert updated_state.candidates[0].candidate_id == "cand-001"
    assert isinstance(updated_state.candidates, tuple)
    with pytest.raises(AttributeError):
        updated_state.candidates.append(candidate)  # type: ignore[attr-defined]

    # 3. Original state remains empty and untouched
    assert initial_state.candidates == ()


# =========================================================================
# Pre-Iteration-1.6 Slice 1: Evidence & WorkflowState Data Model Tests
# =========================================================================

def test_execution_evidence_nullable_fields_and_deltas() -> None:
    """
    ExecutionEvidence supports nullable candidate_id, nullable line/branch coverage,
    and nullable coverage deltas, while enforcing immutability.
    """
    # 1. Default instantiation with None for optional fields
    evidence = ExecutionEvidence(
        evidence_id="ev-min-001",
        run_id="run-min-001",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.5,
    )
    assert evidence.candidate_id is None
    assert evidence.line_coverage is None
    assert evidence.branch_coverage is None
    assert evidence.line_coverage_delta is None
    assert evidence.branch_coverage_delta is None
    assert evidence.timed_out is False
    assert evidence.traceback is None

    # 2. Populated instantiation
    populated = ExecutionEvidence(
        evidence_id="ev-pop-001",
        run_id="run-pop-001",
        candidate_id="cand-42",
        exit_code=0,
        stdout="passed",
        stderr="",
        duration_sec=1.2,
        timed_out=False,
        line_coverage=85.5,
        branch_coverage=75.0,
        line_coverage_delta=5.5,
        branch_coverage_delta=2.0,
        traceback=None,
    )
    assert populated.candidate_id == "cand-42"
    assert populated.line_coverage == 85.5
    assert populated.branch_coverage == 75.0
    assert populated.line_coverage_delta == 5.5
    assert populated.branch_coverage_delta == 2.0

    # 3. Immutability enforcement (frozen=True)
    with pytest.raises(ValidationError):
        setattr(populated, "line_coverage", 90.0)

    with pytest.raises(ValidationError):
        setattr(populated, "candidate_id", "cand-99")


def test_workflow_state_evidences_tuple_and_immutability() -> None:
    """
    WorkflowState stores canonical evidences as an immutable Tuple[ExecutionEvidence, ...]
    and defaults to empty tuple.
    """
    state = WorkflowState(
        run_id="run-ev-01",
        repo_path=Path("/tmp/repo"),
    )
    assert state.evidences == ()
    assert state.baseline_evidence is None
    assert state.evidence is None

    # Frozen state mutation rejection
    with pytest.raises(ValidationError):
        setattr(state, "evidences", ())

    with pytest.raises(ValidationError):
        setattr(state, "baseline_evidence", None)


def test_workflow_state_baseline_evidence_with_null_candidate_id() -> None:
    """
    WorkflowState supports separate baseline_evidence with candidate_id=None
    for regression suite baseline runs.
    """
    baseline_ev = ExecutionEvidence(
        evidence_id="ev-base-001",
        run_id="run-base-01",
        candidate_id=None,
        exit_code=0,
        stdout="10 passed",
        stderr="",
        duration_sec=3.5,
        line_coverage=70.0,
        branch_coverage=60.0,
    )

    state = WorkflowState(
        run_id="run-base-01",
        repo_path=Path("/tmp/repo"),
        baseline_evidence=baseline_ev,
    )
    assert state.baseline_evidence == baseline_ev
    assert state.baseline_evidence.candidate_id is None
    assert state.evidences == ()
    assert state.evidence is None


def test_workflow_state_legacy_singular_evidence_migration() -> None:
    """
    WorkflowState model_validator migrates legacy singular 'evidence'
    to canonical 'evidences' tuple.
    """
    ev = ExecutionEvidence(
        evidence_id="ev-leg-001",
        run_id="run-leg-01",
        candidate_id="cand-leg-01",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.2,
    )

    # 1. Constructor passing legacy evidence dictionary unpacking
    legacy_kwargs: dict[str, Any] = {
        "run_id": "run-leg-01",
        "repo_path": Path("/tmp/repo"),
        "evidence": ev,
    }
    state = WorkflowState(**legacy_kwargs)
    assert state.evidences == (ev,)
    assert state.evidence == ev
    assert isinstance(state.evidences, tuple)

    # 2. Dictionary / model_validate passing legacy "evidence"
    state_from_dict = WorkflowState.model_validate({
        "run_id": "run-leg-01",
        "repo_path": Path("/tmp/repo"),
        "evidence": ev.model_dump(),
    })
    assert len(state_from_dict.evidences) == 1
    assert state_from_dict.evidences[0].evidence_id == "ev-leg-001"
    assert state_from_dict.evidence is not None
    assert state_from_dict.evidence.evidence_id == "ev-leg-001"


def test_workflow_state_legacy_evidence_none_migration() -> None:
    """
    Legacy evidence=None migrates cleanly to empty evidences tuple.
    """
    kwargs: dict[str, Any] = {
        "run_id": "run-none-01",
        "repo_path": Path("/tmp/repo"),
        "evidence": None,
    }
    state = WorkflowState(**kwargs)
    assert state.evidences == ()
    assert state.evidence is None


def test_workflow_state_legacy_evidence_list_tuple_migration() -> None:
    """
    Legacy evidence passed as list or tuple migrates to evidences tuple.
    """
    ev1 = ExecutionEvidence(
        evidence_id="ev-1",
        run_id="r1",
        candidate_id="cand-1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.1,
    )
    ev2 = ExecutionEvidence(
        evidence_id="ev-2",
        run_id="r1",
        candidate_id="cand-2",
        exit_code=1,
        stdout="",
        stderr="",
        duration_sec=0.2,
    )

    kwargs: dict[str, Any] = {
        "run_id": "r1",
        "repo_path": Path("/tmp/repo"),
        "evidence": [ev1, ev2],
    }
    state = WorkflowState(**kwargs)
    assert state.evidences == (ev1, ev2)
    assert state.evidence == ev1


def test_workflow_state_rejects_conflicting_evidence_and_evidences() -> None:
    """
    WorkflowState model_validator rejects ambiguous inputs when both non-empty
    legacy 'evidence' and canonical 'evidences' are supplied.
    """
    ev1 = ExecutionEvidence(
        evidence_id="ev-1",
        run_id="r1",
        candidate_id="cand-1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.1,
    )
    ev2 = ExecutionEvidence(
        evidence_id="ev-2",
        run_id="r1",
        candidate_id="cand-2",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.2,
    )

    conflicting_kwargs: dict[str, Any] = {
        "run_id": "r1",
        "repo_path": Path("/tmp/repo"),
        "evidence": ev1,
        "evidences": (ev2,),
    }
    with pytest.raises(ValidationError) as exc_info:
        WorkflowState(**conflicting_kwargs)
    assert "Cannot specify both legacy 'evidence' and canonical 'evidences'" in str(exc_info.value)

    # Non-conflicting: evidence=None with canonical evidences is accepted
    valid_kwargs: dict[str, Any] = {
        "run_id": "r1",
        "repo_path": Path("/tmp/repo"),
        "evidence": None,
        "evidences": (ev2,),
    }
    valid_state = WorkflowState(**valid_kwargs)
    assert valid_state.evidences == (ev2,)


def test_workflow_state_canonical_serialization_and_json_roundtrip() -> None:
    """
    WorkflowState serialization emits canonical 'evidences' (and not legacy 'evidence'),
    and round-trips cleanly through JSON.
    """
    baseline_ev = ExecutionEvidence(
        evidence_id="ev-base-001",
        run_id="r1",
        candidate_id=None,
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=1.0,
        line_coverage=75.0,
    )
    cand_ev = ExecutionEvidence(
        evidence_id="ev-cand-001",
        run_id="r1",
        candidate_id="cand-001",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.5,
        line_coverage=80.0,
        line_coverage_delta=5.0,
    )

    state = WorkflowState(
        run_id="r1",
        repo_path=Path("/tmp/repo"),
        baseline_evidence=baseline_ev,
        evidences=(cand_ev,),
    )

    # 1. model_dump verification
    dump = state.model_dump()
    assert "evidences" in dump
    assert "baseline_evidence" in dump
    assert "evidence" not in dump
    assert len(dump["evidences"]) == 1
    assert dump["evidences"][0]["evidence_id"] == "ev-cand-001"
    assert dump["evidences"][0]["line_coverage_delta"] == 5.0

    # 2. JSON round-trip
    state_json = state.model_dump_json()
    assert '"evidences"' in state_json
    assert '"evidence":' not in state_json  # Ensure legacy singular key is absent from output

    restored = WorkflowState.model_validate_json(state_json)
    assert restored == state
    assert restored.baseline_evidence == baseline_ev
    assert restored.baseline_evidence.candidate_id is None
    assert restored.evidences == (cand_ev,)
    assert restored.evidence == cand_ev
    assert restored.evidence.line_coverage_delta == 5.0


# =============================================================================
# Component 01 Re-engineering Tests (Charter §4.3)
# Covering: validation bounds, FailureDiagnosis invariant, lazy settings,
# shared exception identity.
# =============================================================================

# ---------------------------------------------------------------------------
# ExecutionEvidence — validation bounds (duration_sec, coverage range)
# ---------------------------------------------------------------------------

def test_execution_evidence_duration_must_be_non_negative() -> None:
    """ExecutionEvidence rejects duration_sec < 0."""
    with pytest.raises(ValidationError, match="duration_sec must be >= 0"):
        ExecutionEvidence(
            evidence_id="ev-001",
            run_id="r1",
            exit_code=0,
            stdout="",
            stderr="",
            duration_sec=-0.001,
        )


def test_execution_evidence_duration_zero_is_valid() -> None:
    """ExecutionEvidence accepts duration_sec == 0 (instantaneous result)."""
    ev = ExecutionEvidence(
        evidence_id="ev-001",
        run_id="r1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.0,
    )
    assert ev.duration_sec == 0.0


def test_execution_evidence_line_coverage_out_of_range() -> None:
    """ExecutionEvidence rejects line_coverage outside [0.0, 100.0]."""
    with pytest.raises(ValidationError, match="Coverage value must be in"):
        ExecutionEvidence(
            evidence_id="ev-001",
            run_id="r1",
            exit_code=0,
            stdout="",
            stderr="",
            duration_sec=1.0,
            line_coverage=100.001,
        )

    with pytest.raises(ValidationError, match="Coverage value must be in"):
        ExecutionEvidence(
            evidence_id="ev-001",
            run_id="r1",
            exit_code=0,
            stdout="",
            stderr="",
            duration_sec=1.0,
            line_coverage=-0.001,
        )


def test_execution_evidence_branch_coverage_out_of_range() -> None:
    """ExecutionEvidence rejects branch_coverage outside [0.0, 100.0]."""
    with pytest.raises(ValidationError, match="Coverage value must be in"):
        ExecutionEvidence(
            evidence_id="ev-001",
            run_id="r1",
            exit_code=0,
            stdout="",
            stderr="",
            duration_sec=1.0,
            branch_coverage=101.0,
        )


def test_execution_evidence_coverage_boundaries_valid() -> None:
    """ExecutionEvidence accepts boundary values 0.0 and 100.0 for coverage."""
    ev = ExecutionEvidence(
        evidence_id="ev-001",
        run_id="r1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=1.5,
        line_coverage=0.0,
        branch_coverage=100.0,
    )
    assert ev.line_coverage == 0.0
    assert ev.branch_coverage == 100.0


def test_execution_evidence_none_coverage_bypasses_range_check() -> None:
    """ExecutionEvidence with coverage=None does not trigger range validation."""
    ev = ExecutionEvidence(
        evidence_id="ev-001",
        run_id="r1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=1.0,
        line_coverage=None,
        branch_coverage=None,
    )
    assert ev.line_coverage is None
    assert ev.branch_coverage is None


# ---------------------------------------------------------------------------
# TestCandidate — retry_count >= 0
# ---------------------------------------------------------------------------

def test_test_candidate_retry_count_non_negative() -> None:
    """TestCandidate rejects retry_count < 0."""
    with pytest.raises(ValidationError, match="retry_count must be >= 0"):
        TestCandidate(
            candidate_id="c001",
            run_id="r1",
            target_symbol_name="calc.add",
            test_file_path=Path("tests/test_calc.py"),
            candidate_code="def test_add(): pass\n",
            retry_count=-1,
        )


def test_test_candidate_retry_count_zero_valid() -> None:
    """TestCandidate accepts retry_count == 0 (default)."""
    c = TestCandidate(
        candidate_id="c001",
        run_id="r1",
        target_symbol_name="calc.add",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_add(): pass\n",
        retry_count=0,
    )
    assert c.retry_count == 0


def test_test_candidate_retry_count_positive_valid() -> None:
    """TestCandidate accepts positive retry_count."""
    c = TestCandidate(
        candidate_id="c001",
        run_id="r1",
        target_symbol_name="calc.add",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_add(): pass\n",
        retry_count=2,
    )
    assert c.retry_count == 2


# ---------------------------------------------------------------------------
# FailureDiagnosis — confidence [0.0, 1.0] and APPLICATION_BUG invariant
# ---------------------------------------------------------------------------

def test_failure_diagnosis_confidence_out_of_range() -> None:
    """FailureDiagnosis rejects confidence outside [0.0, 1.0]."""
    with pytest.raises(ValidationError, match="confidence must be in"):
        FailureDiagnosis(
            diagnosis_id="d001",
            evidence_id="ev001",
            canonical_category=FailureCategory.UNKNOWN,
            confidence=1.001,
            triage_engine=TriageEngine.DETERMINISTIC_RULE,
            explanation="test",
        )

    with pytest.raises(ValidationError, match="confidence must be in"):
        FailureDiagnosis(
            diagnosis_id="d001",
            evidence_id="ev001",
            canonical_category=FailureCategory.UNKNOWN,
            confidence=-0.001,
            triage_engine=TriageEngine.DETERMINISTIC_RULE,
            explanation="test",
        )


def test_failure_diagnosis_confidence_boundaries_valid() -> None:
    """FailureDiagnosis accepts boundary values 0.0 and 1.0 for confidence."""
    d_min = FailureDiagnosis(
        diagnosis_id="d001",
        evidence_id="ev001",
        canonical_category=FailureCategory.UNKNOWN,
        confidence=0.0,
        triage_engine=TriageEngine.DETERMINISTIC_RULE,
        explanation="min confidence",
    )
    d_max = FailureDiagnosis(
        diagnosis_id="d002",
        evidence_id="ev001",
        canonical_category=FailureCategory.UNKNOWN,
        confidence=1.0,
        triage_engine=TriageEngine.DETERMINISTIC_RULE,
        explanation="max confidence",
    )
    assert d_min.confidence == 0.0
    assert d_max.confidence == 1.0


def test_failure_diagnosis_application_bug_consistent_true() -> None:
    """FailureDiagnosis accepts APPLICATION_BUG with is_application_bug=True."""
    d = FailureDiagnosis(
        diagnosis_id="d001",
        evidence_id="ev001",
        canonical_category=FailureCategory.APPLICATION_BUG,
        confidence=0.95,
        triage_engine=TriageEngine.COGNITIVE_LLM,
        explanation="Application defect confirmed",
        is_application_bug=True,
    )
    assert d.is_application_bug is True
    assert d.canonical_category == FailureCategory.APPLICATION_BUG


def test_failure_diagnosis_application_bug_category_but_flag_false_rejected() -> None:
    """FailureDiagnosis rejects canonical_category=APPLICATION_BUG with is_application_bug=False."""
    with pytest.raises(
        ValidationError,
        match="canonical_category is APPLICATION_BUG but is_application_bug is False",
    ):
        FailureDiagnosis(
            diagnosis_id="d001",
            evidence_id="ev001",
            canonical_category=FailureCategory.APPLICATION_BUG,
            confidence=0.9,
            triage_engine=TriageEngine.COGNITIVE_LLM,
            explanation="Inconsistent",
            is_application_bug=False,
        )


def test_failure_diagnosis_flag_true_but_wrong_category_rejected() -> None:
    """FailureDiagnosis rejects is_application_bug=True with non-APPLICATION_BUG category."""
    with pytest.raises(
        ValidationError,
        match="is_application_bug is True but canonical_category is",
    ):
        FailureDiagnosis(
            diagnosis_id="d001",
            evidence_id="ev001",
            canonical_category=FailureCategory.UNKNOWN,
            confidence=0.5,
            triage_engine=TriageEngine.DETERMINISTIC_RULE,
            explanation="Inconsistent flag",
            is_application_bug=True,
        )


def test_failure_diagnosis_non_bug_category_with_flag_false_accepted() -> None:
    """FailureDiagnosis accepts non-APPLICATION_BUG category with is_application_bug=False."""
    for cat in [
        FailureCategory.TEST_OUTDATED,
        FailureCategory.INVALID_GENERATED_TEST,
        FailureCategory.ENVIRONMENT_FAILURE,
        FailureCategory.CONFIGURATION_ERROR,
        FailureCategory.LLM_FAILURE,
        FailureCategory.UNKNOWN,
    ]:
        d = FailureDiagnosis(
            diagnosis_id="d001",
            evidence_id="ev001",
            canonical_category=cat,
            confidence=0.8,
            triage_engine=TriageEngine.DETERMINISTIC_RULE,
            explanation=f"Category {cat.value}",
            is_application_bug=False,
        )
        assert d.is_application_bug is False


# ---------------------------------------------------------------------------
# WorkflowState — retry counter validations
# ---------------------------------------------------------------------------

def test_workflow_state_generation_retry_count_non_negative() -> None:
    """WorkflowState rejects generation_retry_count < 0."""
    with pytest.raises(ValidationError, match="generation_retry_count must be >= 0"):
        WorkflowState(
            run_id="r1",
            repo_path=Path("/tmp/repo"),
            generation_retry_count=-1,
        )


def test_workflow_state_max_generation_retries_non_negative() -> None:
    """WorkflowState rejects max_generation_retries < 0."""
    with pytest.raises(ValidationError, match="max_generation_retries must be >= 0"):
        WorkflowState(
            run_id="r1",
            repo_path=Path("/tmp/repo"),
            max_generation_retries=-1,
        )


def test_workflow_state_retry_count_within_budget() -> None:
    """WorkflowState rejects generation_retry_count > max_generation_retries."""
    with pytest.raises(ValidationError, match="exceeds max_generation_retries"):
        WorkflowState(
            run_id="r1",
            repo_path=Path("/tmp/repo"),
            generation_retry_count=3,
            max_generation_retries=2,
        )


def test_workflow_state_retry_count_at_max_accepted() -> None:
    """WorkflowState accepts generation_retry_count == max_generation_retries."""
    state = WorkflowState(
        run_id="r1",
        repo_path=Path("/tmp/repo"),
        generation_retry_count=2,
        max_generation_retries=2,
    )
    assert state.generation_retry_count == 2
    assert state.max_generation_retries == 2


def test_workflow_state_zero_retries_valid() -> None:
    """WorkflowState accepts zero retry counts (default state)."""
    state = WorkflowState(
        run_id="r1",
        repo_path=Path("/tmp/repo"),
        generation_retry_count=0,
        max_generation_retries=0,
    )
    assert state.generation_retry_count == 0
    assert state.max_generation_retries == 0


# ---------------------------------------------------------------------------
# F-05: Lazy get_settings() and lazy settings compatibility
# ---------------------------------------------------------------------------


def test_get_settings_is_lazy_no_eager_env_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Importing config and core does NOT eagerly instantiate Settings (no .env load at import time).
    get_settings() and settings attributes load .env lazily on first access only.
    """
    import importlib

    import agentic_test.core as core_pkg
    import agentic_test.core.config as cfg_module

    env_file = tmp_path / ".env"
    env_file.write_text('AGENTIC_TEST_APP_NAME="LazyEnvLoadedApp"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTIC_TEST_APP_NAME", raising=False)

    # Reload config and core to simulate fresh import in directory containing .env
    importlib.reload(cfg_module)
    importlib.reload(core_pkg)

    try:
        # Must NOT have instantiated Settings or populated lru_cache on import
        assert cfg_module.get_settings.cache_info().currsize == 0

        # Importing `settings` proxy must also NOT trigger instantiation
        from agentic_test.core.config import settings as lazy_settings

        assert cfg_module.get_settings.cache_info().currsize == 0

        # Accessing an attribute triggers lazy load from .env
        assert lazy_settings.app_name == "LazyEnvLoadedApp"
        assert cfg_module.get_settings.cache_info().currsize == 1
        assert cfg_module.get_settings().app_name == "LazyEnvLoadedApp"
    finally:
        cfg_module.get_settings.cache_clear()


def test_get_settings_returns_cached_singleton() -> None:
    """get_settings() with @lru_cache returns the identical object on repeated calls."""
    from agentic_test.core.config import get_settings

    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2  # Same singleton


def test_settings_lazy_proxy_attribute_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Module-level 'settings' is a lazy proxy: accessing and monkeypatching its
    attributes works seamlessly without eager Settings() instantiation on import.
    """
    from agentic_test.core.config import get_settings, settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTIC_TEST_OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        assert isinstance(settings.app_name, str)
        assert settings.default_timeout_sec == 30.0
        assert settings.memory_limit == "512m"
        assert settings.pids_limit == 100
        assert settings.openai_api_key is None

        # Verify monkeypatch compatibility (used in tests/test_cli.py)
        monkeypatch.setattr(settings, "openai_api_key", None)
        assert settings.openai_api_key is None
    finally:
        get_settings.cache_clear()


def test_settings_importable_from_core_and_core_config() -> None:
    """
    'from agentic_test.core.config import settings' (used in cli/service_factory.py)
    and 'from agentic_test.core import get_settings, settings' continue to work.
    """
    from agentic_test.core import get_settings as core_get_settings
    from agentic_test.core import settings as core_settings
    from agentic_test.core.config import get_settings, settings

    assert core_get_settings is get_settings
    assert isinstance(settings.app_name, str)
    assert core_settings.app_name == settings.app_name


def test_execution_evidence_rejects_non_finite_duration() -> None:
    """ExecutionEvidence rejects NaN and Inf duration_sec."""
    for bad_duration in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError, match="duration_sec must be >= 0"):
            ExecutionEvidence(
                evidence_id="ev-nan",
                run_id="r1",
                exit_code=0,
                stdout="",
                stderr="",
                duration_sec=bad_duration,
            )


def test_execution_evidence_coverage_delta_bounds_and_non_finite() -> None:
    """
    ExecutionEvidence enforces [-100.0, 100.0] and finite values on
    line_coverage_delta and branch_coverage_delta when not None.
    """
    # Valid boundary values and None
    ev_min = ExecutionEvidence(
        evidence_id="ev-delta-min",
        run_id="r1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=1.0,
        line_coverage_delta=-100.0,
        branch_coverage_delta=100.0,
    )
    assert ev_min.line_coverage_delta == -100.0
    assert ev_min.branch_coverage_delta == 100.0

    ev_zero = ExecutionEvidence(
        evidence_id="ev-delta-zero",
        run_id="r1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=1.0,
        line_coverage_delta=0.0,
        branch_coverage_delta=None,
    )
    assert ev_zero.line_coverage_delta == 0.0
    assert ev_zero.branch_coverage_delta is None

    for field_name in ("line_coverage_delta", "branch_coverage_delta"):
        for bad_val in (-100.001, 100.001, float("nan"), float("inf"), float("-inf")):
            kwargs: dict[str, Any] = {
                "evidence_id": "ev-bad-delta",
                "run_id": "r1",
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "duration_sec": 1.0,
                field_name: bad_val,
            }
            with pytest.raises(ValidationError, match="Coverage delta value must be finite"):
                ExecutionEvidence(**kwargs)


def test_settings_proxy_mutation_syncs_with_get_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Mutating attributes via `settings` or `monkeypatch.setattr(settings, ...)`
    stays completely in sync with `get_settings()`, and undo restores cleanly.
    """
    from pydantic import SecretStr

    from agentic_test.core.config import get_settings, settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTIC_TEST_OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        # Direct mutation via proxy reflects on get_settings()
        settings.openai_api_key = SecretStr("sk-proxy-test")
        cached = get_settings()
        assert cached.openai_api_key is not None
        assert cached.openai_api_key.get_secret_value() == "sk-proxy-test"

        # monkeypatch.setattr(settings, ...) reflects on get_settings() and restores cleanly
        local_mp = pytest.MonkeyPatch()
        local_mp.setattr(settings, "openai_api_key", None)
        assert settings.openai_api_key is None
        assert get_settings().openai_api_key is None

        local_mp.undo()
        assert settings.openai_api_key is not None
        assert settings.openai_api_key.get_secret_value() == "sk-proxy-test"
        assert get_settings().openai_api_key is not None
        assert get_settings().openai_api_key.get_secret_value() == "sk-proxy-test"
    finally:
        get_settings.cache_clear()


def test_workflow_state_legacy_migration_does_not_mutate_caller_dict() -> None:
    """
    Passing a caller-owned dictionary with legacy 'evidence' to
    WorkflowState.model_validate(...) must not mutate the caller dictionary.
    """
    cand_ev = ExecutionEvidence(
        evidence_id="ev-cand-1",
        run_id="r1",
        candidate_id="cand-1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.2,
    )
    caller_payload: dict[str, Any] = {
        "run_id": "r1",
        "repo_path": Path("/tmp/repo"),
        "evidence": cand_ev,
    }
    snapshot_keys = set(caller_payload.keys())

    state = WorkflowState.model_validate(caller_payload)

    assert state.evidences == (cand_ev,)
    assert set(caller_payload.keys()) == snapshot_keys
    assert "evidence" in caller_payload
    assert caller_payload["evidence"] is cand_ev
    assert "evidences" not in caller_payload
    assert "baseline_evidence" not in caller_payload


def test_workflow_state_legacy_migration_routes_baseline_and_checks_conflicts() -> None:
    """
    Legacy 'evidence' with candidate_id=None routes to baseline_evidence;
    conflicts against baseline_evidence or evidences are rejected cleanly.
    """
    base_ev = ExecutionEvidence(
        evidence_id="ev-base-1",
        run_id="r1",
        candidate_id=None,
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.3,
    )
    cand_ev = ExecutionEvidence(
        evidence_id="ev-cand-1",
        run_id="r1",
        candidate_id="cand-1",
        exit_code=1,
        stdout="",
        stderr="",
        duration_sec=0.4,
    )

    # 1. Single baseline evidence (ExecutionEvidence) routes to baseline_evidence
    s_base = WorkflowState.model_validate(
        {"run_id": "r1", "repo_path": Path("/tmp/repo"), "evidence": base_ev}
    )
    assert s_base.baseline_evidence == base_ev
    assert s_base.evidences == ()
    assert s_base.evidence is None

    # 2. Single baseline dict routes to baseline_evidence
    s_base_dict = WorkflowState.model_validate(
        {
            "run_id": "r1",
            "repo_path": Path("/tmp/repo"),
            "evidence": base_ev.model_dump(),
        }
    )
    assert s_base_dict.baseline_evidence == base_ev
    assert s_base_dict.evidences == ()

    # 3. Mixed legacy list [base_ev, cand_ev] splits cleanly
    s_mixed = WorkflowState.model_validate(
        {
            "run_id": "r1",
            "repo_path": Path("/tmp/repo"),
            "evidence": [base_ev, cand_ev],
        }
    )
    assert s_mixed.baseline_evidence == base_ev
    assert s_mixed.evidences == (cand_ev,)

    # 4. Multiple baseline evidences in legacy list are rejected
    base_ev_2 = ExecutionEvidence(
        evidence_id="ev-base-2",
        run_id="r1",
        candidate_id=None,
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.3,
    )
    with pytest.raises(ValidationError, match="multiple baseline records"):
        WorkflowState.model_validate(
            {
                "run_id": "r1",
                "repo_path": Path("/tmp/repo"),
                "evidence": [base_ev, base_ev_2],
            }
        )

    # 5. Conflict between legacy 'evidence' and canonical 'baseline_evidence' is rejected
    with pytest.raises(
        ValidationError,
        match="Cannot specify both legacy 'evidence' and canonical 'baseline_evidence'",
    ):
        WorkflowState.model_validate(
            {
                "run_id": "r1",
                "repo_path": Path("/tmp/repo"),
                "evidence": cand_ev,
                "baseline_evidence": base_ev,
            }
        )

    # 6. Conflict between legacy baseline 'evidence' and canonical 'baseline_evidence' is rejected
    with pytest.raises(
        ValidationError,
        match="Cannot specify both legacy 'evidence' and canonical 'baseline_evidence'",
    ):
        WorkflowState.model_validate(
            {
                "run_id": "r1",
                "repo_path": Path("/tmp/repo"),
                "evidence": base_ev,
                "baseline_evidence": base_ev_2,
            }
        )


def test_workflow_state_f03_candidate_id_and_run_id_invariants() -> None:
    """
    F-03 invariants:
    - baseline_evidence must have candidate_id=None and match state.run_id.
    - every item in evidences must have candidate_id != None and match state.run_id.
    """
    bad_baseline_with_cand = ExecutionEvidence(
        evidence_id="ev-1",
        run_id="r1",
        candidate_id="cand-1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.1,
    )
    with pytest.raises(ValidationError, match="baseline_evidence must have candidate_id=None"):
        WorkflowState(
            run_id="r1",
            repo_path=Path("/tmp/repo"),
            baseline_evidence=bad_baseline_with_cand,
        )

    bad_candidate_null_id = ExecutionEvidence(
        evidence_id="ev-2",
        run_id="r1",
        candidate_id=None,
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.1,
    )
    with pytest.raises(ValidationError, match="must have a non-None candidate_id"):
        WorkflowState(
            run_id="r1",
            repo_path=Path("/tmp/repo"),
            evidences=(bad_candidate_null_id,),
        )

    mismatched_baseline = ExecutionEvidence(
        evidence_id="ev-3",
        run_id="other-run",
        candidate_id=None,
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.1,
    )
    with pytest.raises(
        ValidationError,
        match=r"baseline_evidence\.run_id .* must match state\.run_id",
    ):
        WorkflowState(
            run_id="r1",
            repo_path=Path("/tmp/repo"),
            baseline_evidence=mismatched_baseline,
        )

    mismatched_cand = ExecutionEvidence(
        evidence_id="ev-4",
        run_id="other-run",
        candidate_id="cand-1",
        exit_code=0,
        stdout="",
        stderr="",
        duration_sec=0.1,
    )
    with pytest.raises(ValidationError, match=r"run_id .* must match state\.run_id"):
        WorkflowState(
            run_id="r1",
            repo_path=Path("/tmp/repo"),
            evidences=(mismatched_cand,),
        )


# ---------------------------------------------------------------------------
# U-02: Shared exception identity — LLMCommunicationError & SchemaValidationError
# ---------------------------------------------------------------------------


def _load_generation_exceptions_module() -> object:
    """
    Import agentic_test.generation.exceptions, falling back to direct module
    loading from disk if optional 'litellm' ([llm] extra) is absent in dev-only
    environments (since generation/__init__.py eagerly imports litellm_service).
    """
    import importlib
    import importlib.util

    if importlib.util.find_spec("litellm") is not None:
        return importlib.import_module("agentic_test.generation.exceptions")

    exceptions_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agentic_test"
        / "generation"
        / "exceptions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "agentic_test_generation_exceptions_direct",
        exceptions_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_llm_communication_error_is_same_class_in_protocols_and_generation() -> None:
    """
    U-02: LLMCommunicationError imported from core.protocols.llm and from
    generation.exceptions must be the IDENTICAL class object (not copies).
    """
    from agentic_test.core.protocols.llm import LLMCommunicationError as ProtoError

    gen_exceptions = _load_generation_exceptions_module()
    gen_error = getattr(gen_exceptions, "LLMCommunicationError")

    assert ProtoError is gen_error, (
        "LLMCommunicationError in generation.exceptions must be the same class "
        "as the one defined in core.protocols.llm (re-export, not a copy)."
    )


def test_schema_validation_error_is_same_class_in_protocols_and_generation() -> None:
    """
    U-02: SchemaValidationError imported from core.protocols.llm and from
    generation.exceptions must be the IDENTICAL class object (not copies).
    """
    from agentic_test.core.protocols.llm import SchemaValidationError as ProtoError

    gen_exceptions = _load_generation_exceptions_module()
    gen_error = getattr(gen_exceptions, "SchemaValidationError")

    assert ProtoError is gen_error, (
        "SchemaValidationError in generation.exceptions must be the same class "
        "as the one defined in core.protocols.llm (re-export, not a copy)."
    )


def test_generation_exceptions_retains_generation_only_errors() -> None:
    """
    U-02: Generation-specific exceptions (GenerationError, ContextBudgetExceededError)
    remain in generation.exceptions.
    """
    gen_exceptions = _load_generation_exceptions_module()
    generation_error = getattr(gen_exceptions, "GenerationError")
    budget_error = getattr(gen_exceptions, "ContextBudgetExceededError")
    assert issubclass(budget_error, generation_error)


def test_llm_communication_error_is_catchable_via_protocol_base() -> None:
    """
    LLMCommunicationError raised in generation context can be caught via
    core.protocols.llm.LLMError base class.
    """
    from agentic_test.core.protocols.llm import LLMError

    gen_exceptions = _load_generation_exceptions_module()
    gen_comm_error = getattr(gen_exceptions, "LLMCommunicationError")

    with pytest.raises(LLMError):
        raise gen_comm_error("provider timeout")


def test_schema_validation_error_is_catchable_via_protocol_base() -> None:
    """
    SchemaValidationError raised in generation context can be caught via
    core.protocols.llm.LLMError base class.
    """
    from agentic_test.core.protocols.llm import LLMError

    gen_exceptions = _load_generation_exceptions_module()
    gen_schema_error = getattr(gen_exceptions, "SchemaValidationError")

    with pytest.raises(LLMError):
        raise gen_schema_error("schema mismatch")


def test_core_init_exports_llm_exceptions() -> None:
    """
    F-12: core.__init__.__all__ includes LLMService, LLMCommunicationError,
    SchemaValidationError and they resolve to the protocol-layer classes.
    """
    import agentic_test.core as core_pkg
    from agentic_test.core.protocols.llm import (
        LLMCommunicationError as ProtoLLMComm,
    )
    from agentic_test.core.protocols.llm import (
        LLMService as ProtoLLMService,
    )
    from agentic_test.core.protocols.llm import (
        SchemaValidationError as ProtoSchema,
    )

    assert "LLMService" in core_pkg.__all__
    assert "LLMCommunicationError" in core_pkg.__all__
    assert "SchemaValidationError" in core_pkg.__all__

    assert core_pkg.LLMService is ProtoLLMService
    assert core_pkg.LLMCommunicationError is ProtoLLMComm
    assert core_pkg.SchemaValidationError is ProtoSchema


def test_core_init_exports_sandbox_types() -> None:
    """
    F-13: core.__init__.__all__ includes SandboxManager, SandboxConfig,
    ExecutionRawResult and sandbox exception classes.
    """
    import agentic_test.core as core_pkg

    for name in [
        "SandboxManager",
        "SandboxConfig",
        "ExecutionRawResult",
        "SandboxError",
        "SandboxExecutionError",
        "SandboxInitializationError",
    ]:
        assert name in core_pkg.__all__, f"{name!r} missing from core.__all__"
