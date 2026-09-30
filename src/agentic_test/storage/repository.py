"""
Relational repository and workflow state checkpointer for the Agentic Test Generation System.
Adheres strictly to Stage 3 Section 4.4.7 (Listing 4.4) and Slice 1.6.2C contracts.
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import sqlite3
from typing import Any, List, Optional, Union
import uuid

from pydantic import BaseModel, ConfigDict, Field

from agentic_test.core.models import (
    ExecutionEvidence,
    FailureCategory,
    FailureDiagnosis,
    TestCandidate,
    TriageEngine,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.core.state import WorkflowState
from agentic_test.storage.database import get_connection


class RunRecord(BaseModel):
    """
    Immutable point-in-time record of a pipeline execution run.
    Maps 1:1 to the 'runs' table in SQLite.
    """
    model_config = ConfigDict(frozen=True)

    run_id: str
    repo_path: Path
    current_commit: str
    base_commit: str
    branch_name: str
    route_selected: Optional[str] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    final_status: str = "INITIALIZED"
    error_message: Optional[str] = None


class CheckpointRecord(BaseModel):
    """
    Immutable representation of a persisted workflow state checkpoint.
    Maps 1:1 to the 'checkpoints' table in SQLite.
    """
    model_config = ConfigDict(frozen=True)

    checkpoint_id: str
    run_id: str
    step_index: int
    node_name: str
    state: WorkflowState
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SQLiteRepository:
    """
    Synchronous relational persistence repository for runs, test candidates,
    execution telemetry evidence, failure diagnoses, and state checkpoints.
    """

    def __init__(self, target: Union[sqlite3.Connection, str, Path] = ":memory:") -> None:
        if isinstance(target, sqlite3.Connection):
            self._conn = target
        else:
            self._conn = get_connection(target)

    @property
    def connection(self) -> sqlite3.Connection:
        """Access the underlying sqlite3 connection."""
        return self._conn

    # ------------------------------------------------------------------------
    # Run Persistence & Retrieval
    # ------------------------------------------------------------------------

    def save_run(
        self,
        run: Optional[RunRecord] = None,
        *,
        run_id: Optional[str] = None,
        repo_path: Optional[Union[str, Path]] = None,
        current_commit: Optional[str] = None,
        base_commit: Optional[str] = None,
        branch_name: Optional[str] = None,
        route_selected: Optional[Union[str, WorkflowRoute]] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        final_status: str = "INITIALIZED",
        error_message: Optional[str] = None,
    ) -> RunRecord:
        """
        Saves or updates a pipeline execution run record.
        """
        if run is not None:
            target_run = run
        else:
            if not run_id or not run_id.strip():
                raise ValueError("run_id must be provided when run is omitted")
            if repo_path is None:
                raise ValueError("repo_path must be provided when run is omitted")
            if current_commit is None or base_commit is None or branch_name is None:
                raise ValueError("Git metadata (current_commit, base_commit, branch_name) must be provided")

            route_str = route_selected.value if isinstance(route_selected, Enum) else route_selected
            target_run = RunRecord(
                run_id=run_id.strip(),
                repo_path=Path(repo_path),
                current_commit=current_commit.strip(),
                base_commit=base_commit.strip(),
                branch_name=branch_name.strip(),
                route_selected=route_str,
                started_at=started_at or datetime.now(timezone.utc),
                completed_at=completed_at,
                final_status=final_status,
                error_message=error_message,
            )

        sql = """
        INSERT INTO runs (
            run_id, repo_path, current_commit, base_commit, branch_name,
            route_selected, started_at, completed_at, final_status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            repo_path = excluded.repo_path,
            current_commit = excluded.current_commit,
            base_commit = excluded.base_commit,
            branch_name = excluded.branch_name,
            route_selected = excluded.route_selected,
            started_at = excluded.started_at,
            completed_at = excluded.completed_at,
            final_status = excluded.final_status,
            error_message = excluded.error_message;
        """
        self._conn.execute(
            sql,
            (
                target_run.run_id,
                str(target_run.repo_path),
                target_run.current_commit,
                target_run.base_commit,
                target_run.branch_name,
                target_run.route_selected,
                target_run.started_at.isoformat(),
                target_run.completed_at.isoformat() if target_run.completed_at else None,
                target_run.final_status,
                target_run.error_message,
            ),
        )
        self._conn.commit()
        return target_run

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """Retrieves a run record by run_id, or None if not found."""
        if not run_id or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        cursor = self._conn.execute(
            """
            SELECT run_id, repo_path, current_commit, base_commit, branch_name,
                   route_selected, started_at, completed_at, final_status, error_message
            FROM runs
            WHERE run_id = ?;
            """,
            (run_id.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return RunRecord(
            run_id=row["run_id"],
            repo_path=Path(row["repo_path"]),
            current_commit=row["current_commit"],
            base_commit=row["base_commit"],
            branch_name=row["branch_name"],
            route_selected=row["route_selected"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            final_status=row["final_status"],
            error_message=row["error_message"],
        )

    # ------------------------------------------------------------------------
    # Test Candidate Persistence & Retrieval
    # ------------------------------------------------------------------------

    def save_candidate(self, candidate: TestCandidate) -> TestCandidate:
        """
        Saves a test candidate entity.
        Never mutates the input candidate instance.
        """
        sql = """
        INSERT INTO test_candidates (
            candidate_id, run_id, target_symbol, test_file_path, candidate_code,
            validation_status, quarantine_reason, retry_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            run_id = excluded.run_id,
            target_symbol = excluded.target_symbol,
            test_file_path = excluded.test_file_path,
            candidate_code = excluded.candidate_code,
            validation_status = excluded.validation_status,
            quarantine_reason = excluded.quarantine_reason,
            retry_count = excluded.retry_count,
            created_at = excluded.created_at;
        """
        self._conn.execute(
            sql,
            (
                candidate.candidate_id,
                candidate.run_id,
                candidate.target_symbol_name,
                str(candidate.test_file_path),
                candidate.candidate_code,
                candidate.validation_status.value
                if isinstance(candidate.validation_status, Enum)
                else str(candidate.validation_status),
                candidate.quarantine_reason,
                candidate.retry_count,
                candidate.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return candidate

    def get_candidate(self, candidate_id: str) -> Optional[TestCandidate]:
        """Retrieves a single test candidate by candidate_id, or None if not found."""
        if not candidate_id or not candidate_id.strip():
            raise ValueError("candidate_id must be a non-empty string")

        cursor = self._conn.execute(
            """
            SELECT candidate_id, run_id, target_symbol, test_file_path, candidate_code,
                   validation_status, quarantine_reason, retry_count, created_at
            FROM test_candidates
            WHERE candidate_id = ?;
            """,
            (candidate_id.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return TestCandidate(
            candidate_id=row["candidate_id"],
            run_id=row["run_id"],
            target_symbol_name=row["target_symbol"],
            test_file_path=Path(row["test_file_path"]),
            candidate_code=row["candidate_code"],
            validation_status=ValidationStatus(row["validation_status"]),
            quarantine_reason=row["quarantine_reason"],
            retry_count=int(row["retry_count"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_candidates(self, run_id: str) -> List[TestCandidate]:
        """Retrieves all test candidates for a run, ordered chronologically."""
        if not run_id or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        cursor = self._conn.execute(
            """
            SELECT candidate_id, run_id, target_symbol, test_file_path, candidate_code,
                   validation_status, quarantine_reason, retry_count, created_at
            FROM test_candidates
            WHERE run_id = ?
            ORDER BY created_at ASC, candidate_id ASC;
            """,
            (run_id.strip(),),
        )
        candidates: List[TestCandidate] = []
        for row in cursor.fetchall():
            candidates.append(
                TestCandidate(
                    candidate_id=row["candidate_id"],
                    run_id=row["run_id"],
                    target_symbol_name=row["target_symbol"],
                    test_file_path=Path(row["test_file_path"]),
                    candidate_code=row["candidate_code"],
                    validation_status=ValidationStatus(row["validation_status"]),
                    quarantine_reason=row["quarantine_reason"],
                    retry_count=int(row["retry_count"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return candidates

    # ------------------------------------------------------------------------
    # Execution Evidence Persistence & Retrieval
    # ------------------------------------------------------------------------

    def save_evidence(self, evidence: ExecutionEvidence) -> ExecutionEvidence:
        """
        Saves execution telemetry evidence, supporting nullable candidate_id for baseline evidence.
        Enforces candidate/run consistency before insertion.
        """
        if evidence.candidate_id is not None:
            # Enforce run consistency: verify candidate belongs to the same run
            cursor = self._conn.execute(
                "SELECT run_id FROM test_candidates WHERE candidate_id = ?;",
                (evidence.candidate_id,),
            )
            cand_row = cursor.fetchone()
            if cand_row is not None and cand_row["run_id"] != evidence.run_id:
                raise ValueError(
                    f"Run consistency violation: candidate '{evidence.candidate_id}' belongs to run "
                    f"'{cand_row['run_id']}', but evidence specifies run '{evidence.run_id}'"
                )

        sql = """
        INSERT INTO execution_evidence (
            evidence_id, run_id, candidate_id, exit_code, stdout, stderr,
            duration_sec, timed_out, line_coverage, branch_coverage,
            line_coverage_delta, branch_coverage_delta, traceback, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(evidence_id) DO UPDATE SET
            run_id = excluded.run_id,
            candidate_id = excluded.candidate_id,
            exit_code = excluded.exit_code,
            stdout = excluded.stdout,
            stderr = excluded.stderr,
            duration_sec = excluded.duration_sec,
            timed_out = excluded.timed_out,
            line_coverage = excluded.line_coverage,
            branch_coverage = excluded.branch_coverage,
            line_coverage_delta = excluded.line_coverage_delta,
            branch_coverage_delta = excluded.branch_coverage_delta,
            traceback = excluded.traceback,
            created_at = excluded.created_at;
        """
        self._conn.execute(
            sql,
            (
                evidence.evidence_id,
                evidence.run_id,
                evidence.candidate_id,
                evidence.exit_code,
                evidence.stdout,
                evidence.stderr,
                evidence.duration_sec,
                1 if evidence.timed_out else 0,
                evidence.line_coverage,
                evidence.branch_coverage,
                evidence.line_coverage_delta,
                evidence.branch_coverage_delta,
                evidence.traceback,
                evidence.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return evidence

    def get_evidence(self, evidence_id: str) -> Optional[ExecutionEvidence]:
        """Retrieves an execution evidence record by evidence_id, or None if not found."""
        if not evidence_id or not evidence_id.strip():
            raise ValueError("evidence_id must be a non-empty string")

        cursor = self._conn.execute(
            """
            SELECT evidence_id, run_id, candidate_id, exit_code, stdout, stderr,
                   duration_sec, timed_out, line_coverage, branch_coverage,
                   line_coverage_delta, branch_coverage_delta, traceback, created_at
            FROM execution_evidence
            WHERE evidence_id = ?;
            """,
            (evidence_id.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return ExecutionEvidence(
            evidence_id=row["evidence_id"],
            run_id=row["run_id"],
            candidate_id=row["candidate_id"],
            exit_code=int(row["exit_code"]),
            stdout=row["stdout"] or "",
            stderr=row["stderr"] or "",
            duration_sec=float(row["duration_sec"]),
            timed_out=bool(row["timed_out"]),
            line_coverage=float(row["line_coverage"]) if row["line_coverage"] is not None else None,
            branch_coverage=float(row["branch_coverage"]) if row["branch_coverage"] is not None else None,
            line_coverage_delta=float(row["line_coverage_delta"]) if row["line_coverage_delta"] is not None else None,
            branch_coverage_delta=float(row["branch_coverage_delta"]) if row["branch_coverage_delta"] is not None else None,
            traceback=row["traceback"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_run_evidence(
        self,
        run_id: str,
        candidate_id: Optional[str] = None,
        baseline_only: bool = False,
    ) -> List[ExecutionEvidence]:
        """
        Retrieves execution evidence records for a run.
        If baseline_only is True, retrieves only rows where candidate_id is NULL.
        """
        if not run_id or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        if baseline_only:
            query = """
            SELECT evidence_id, run_id, candidate_id, exit_code, stdout, stderr,
                   duration_sec, timed_out, line_coverage, branch_coverage,
                   line_coverage_delta, branch_coverage_delta, traceback, created_at
            FROM execution_evidence
            WHERE run_id = ? AND candidate_id IS NULL
            ORDER BY created_at ASC, evidence_id ASC;
            """
            params: List[Any] = [run_id.strip()]
        elif candidate_id is not None:
            query = """
            SELECT evidence_id, run_id, candidate_id, exit_code, stdout, stderr,
                   duration_sec, timed_out, line_coverage, branch_coverage,
                   line_coverage_delta, branch_coverage_delta, traceback, created_at
            FROM execution_evidence
            WHERE run_id = ? AND candidate_id = ?
            ORDER BY created_at ASC, evidence_id ASC;
            """
            params = [run_id.strip(), candidate_id.strip()]
        else:
            query = """
            SELECT evidence_id, run_id, candidate_id, exit_code, stdout, stderr,
                   duration_sec, timed_out, line_coverage, branch_coverage,
                   line_coverage_delta, branch_coverage_delta, traceback, created_at
            FROM execution_evidence
            WHERE run_id = ?
            ORDER BY created_at ASC, evidence_id ASC;
            """
            params = [run_id.strip()]

        cursor = self._conn.execute(query, params)
        records: List[ExecutionEvidence] = []
        for row in cursor.fetchall():
            records.append(
                ExecutionEvidence(
                    evidence_id=row["evidence_id"],
                    run_id=row["run_id"],
                    candidate_id=row["candidate_id"],
                    exit_code=int(row["exit_code"]),
                    stdout=row["stdout"] or "",
                    stderr=row["stderr"] or "",
                    duration_sec=float(row["duration_sec"]),
                    timed_out=bool(row["timed_out"]),
                    line_coverage=float(row["line_coverage"]) if row["line_coverage"] is not None else None,
                    branch_coverage=float(row["branch_coverage"]) if row["branch_coverage"] is not None else None,
                    line_coverage_delta=float(row["line_coverage_delta"]) if row["line_coverage_delta"] is not None else None,
                    branch_coverage_delta=float(row["branch_coverage_delta"]) if row["branch_coverage_delta"] is not None else None,
                    traceback=row["traceback"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return records

    # ------------------------------------------------------------------------
    # Failure Diagnosis Persistence & Retrieval
    # ------------------------------------------------------------------------

    def save_diagnosis(
        self,
        diagnosis: FailureDiagnosis,
        run_id: Optional[str] = None,
    ) -> FailureDiagnosis:
        """
        Saves a failure diagnosis entity.
        Resolves run_id from associated execution evidence or validates supplied run_id.
        """
        cursor = self._conn.execute(
            "SELECT run_id FROM execution_evidence WHERE evidence_id = ?;",
            (diagnosis.evidence_id,),
        )
        ev_row = cursor.fetchone()

        if ev_row is not None:
            ev_run_id = ev_row["run_id"]
            if run_id is not None and run_id != ev_run_id:
                raise ValueError(
                    f"Run consistency violation: evidence '{diagnosis.evidence_id}' belongs to run "
                    f"'{ev_run_id}', but diagnosis specifies run '{run_id}'"
                )
            effective_run_id = ev_run_id
        else:
            if run_id is None:
                raise ValueError(
                    f"Evidence '{diagnosis.evidence_id}' not found; run_id must be provided"
                )
            effective_run_id = run_id

        sql = """
        INSERT INTO failure_diagnoses (
            diagnosis_id, run_id, evidence_id, canonical_category, confidence,
            triage_engine, explanation, is_application_bug, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(diagnosis_id) DO UPDATE SET
            run_id = excluded.run_id,
            evidence_id = excluded.evidence_id,
            canonical_category = excluded.canonical_category,
            confidence = excluded.confidence,
            triage_engine = excluded.triage_engine,
            explanation = excluded.explanation,
            is_application_bug = excluded.is_application_bug,
            created_at = excluded.created_at;
        """
        self._conn.execute(
            sql,
            (
                diagnosis.diagnosis_id,
                effective_run_id,
                diagnosis.evidence_id,
                diagnosis.canonical_category.value
                if isinstance(diagnosis.canonical_category, Enum)
                else str(diagnosis.canonical_category),
                float(diagnosis.confidence),
                diagnosis.triage_engine.value
                if isinstance(diagnosis.triage_engine, Enum)
                else str(diagnosis.triage_engine),
                diagnosis.explanation,
                1 if diagnosis.is_application_bug else 0,
                diagnosis.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return diagnosis

    def get_diagnosis(self, diagnosis_id: str) -> Optional[FailureDiagnosis]:
        """Retrieves a single failure diagnosis by diagnosis_id, or None if not found."""
        if not diagnosis_id or not diagnosis_id.strip():
            raise ValueError("diagnosis_id must be a non-empty string")

        cursor = self._conn.execute(
            """
            SELECT diagnosis_id, run_id, evidence_id, canonical_category, confidence,
                   triage_engine, explanation, is_application_bug, created_at
            FROM failure_diagnoses
            WHERE diagnosis_id = ?;
            """,
            (diagnosis_id.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return FailureDiagnosis(
            diagnosis_id=row["diagnosis_id"],
            evidence_id=row["evidence_id"],
            canonical_category=FailureCategory(row["canonical_category"]),
            confidence=float(row["confidence"]),
            triage_engine=TriageEngine(row["triage_engine"]),
            explanation=row["explanation"],
            is_application_bug=bool(row["is_application_bug"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_diagnoses(self, run_id: str) -> List[FailureDiagnosis]:
        """Retrieves all failure diagnoses for a run, ordered chronologically."""
        if not run_id or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        cursor = self._conn.execute(
            """
            SELECT diagnosis_id, run_id, evidence_id, canonical_category, confidence,
                   triage_engine, explanation, is_application_bug, created_at
            FROM failure_diagnoses
            WHERE run_id = ?
            ORDER BY created_at ASC, diagnosis_id ASC;
            """,
            (run_id.strip(),),
        )
        diagnoses: List[FailureDiagnosis] = []
        for row in cursor.fetchall():
            diagnoses.append(
                FailureDiagnosis(
                    diagnosis_id=row["diagnosis_id"],
                    evidence_id=row["evidence_id"],
                    canonical_category=FailureCategory(row["canonical_category"]),
                    confidence=float(row["confidence"]),
                    triage_engine=TriageEngine(row["triage_engine"]),
                    explanation=row["explanation"],
                    is_application_bug=bool(row["is_application_bug"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return diagnoses

    # ------------------------------------------------------------------------
    # State Checkpointing & Crash Recovery (NFR-07)
    # ------------------------------------------------------------------------

    def save_checkpoint(
        self,
        state: WorkflowState,
        step_index: int,
        node_name: str,
        checkpoint_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> CheckpointRecord:
        """
        Persists a WorkflowState checkpoint atomically.
        Serializes state to JSON using model_dump_json() preserving collection immutability.
        """
        if not node_name or not node_name.strip():
            raise ValueError("node_name must be a non-empty string")
        if step_index < 0:
            raise ValueError("step_index must be a non-negative integer")

        final_checkpoint_id = checkpoint_id.strip() if checkpoint_id and checkpoint_id.strip() else str(uuid.uuid4())
        final_created_at = created_at if created_at is not None else datetime.now(timezone.utc)
        if final_created_at.tzinfo is None:
            final_created_at = final_created_at.replace(tzinfo=timezone.utc)

        state_payload = state.model_dump_json()

        sql = """
        INSERT INTO checkpoints (
            checkpoint_id, run_id, step_index, node_name, state_payload, created_at
        ) VALUES (?, ?, ?, ?, ?, ?);
        """
        self._conn.execute(
            sql,
            (
                final_checkpoint_id,
                state.run_id,
                step_index,
                node_name.strip(),
                state_payload,
                final_created_at.isoformat(),
            ),
        )
        self._conn.commit()

        return CheckpointRecord(
            checkpoint_id=final_checkpoint_id,
            run_id=state.run_id,
            step_index=step_index,
            node_name=node_name.strip(),
            state=state,
            created_at=final_created_at,
        )

    def get_latest_checkpoint(self, run_id: str) -> Optional[CheckpointRecord]:
        """
        Retrieves the latest checkpoint for a run.
        Orders deterministically by step_index DESC, created_at DESC, and checkpoint_id DESC.
        Returns None when no checkpoint exists.
        """
        if not run_id or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        cursor = self._conn.execute(
            """
            SELECT checkpoint_id, run_id, step_index, node_name, state_payload, created_at
            FROM checkpoints
            WHERE run_id = ?
            ORDER BY step_index DESC, created_at DESC, checkpoint_id DESC
            LIMIT 1;
            """,
            (run_id.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        reconstructed_state = WorkflowState.model_validate_json(row["state_payload"])

        return CheckpointRecord(
            checkpoint_id=row["checkpoint_id"],
            run_id=row["run_id"],
            step_index=int(row["step_index"]),
            node_name=row["node_name"],
            state=reconstructed_state,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_checkpoints(self, run_id: str) -> List[CheckpointRecord]:
        """
        Retrieves all checkpoints for a run ordered deterministically by step_index ASC, created_at ASC.
        """
        if not run_id or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        cursor = self._conn.execute(
            """
            SELECT checkpoint_id, run_id, step_index, node_name, state_payload, created_at
            FROM checkpoints
            WHERE run_id = ?
            ORDER BY step_index ASC, created_at ASC, checkpoint_id ASC;
            """,
            (run_id.strip(),),
        )
        checkpoints: List[CheckpointRecord] = []
        for row in cursor.fetchall():
            reconstructed_state = WorkflowState.model_validate_json(row["state_payload"])
            checkpoints.append(
                CheckpointRecord(
                    checkpoint_id=row["checkpoint_id"],
                    run_id=row["run_id"],
                    step_index=int(row["step_index"]),
                    node_name=row["node_name"],
                    state=reconstructed_state,
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return checkpoints
