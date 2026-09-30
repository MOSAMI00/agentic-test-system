"""
WorkflowEngine coordinator for the Agentic Test Generation System.
Adheres strictly to Stage 3 Section 4.4.3, WBS 1.6.3A, and human-approved state machine design.

Provides a deterministic local orchestrator with complete persistence wiring
using SQLiteRepository and SQLiteEventStore. Does not use LangGraph or external frameworks.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from agentic_test.analysis.service import AnalysisService
from agentic_test.core.state import WorkflowState
from agentic_test.diagnosis.service import DiagnosisService
from agentic_test.execution.service import ExecutionService
from agentic_test.generation.service import GenerationService
from agentic_test.planning.planner import ExecutionPlanner
from agentic_test.storage.events import SQLiteEventStore
from agentic_test.storage.repository import RunRecord, SQLiteRepository
from agentic_test.validation.pipeline import ValidationPipeline
from agentic_test.workflow.edges import (
    route_after_execution,
    route_after_planning,
    route_after_validation,
)
from agentic_test.workflow.nodes import (
    diagnose_failure_node,
    execute_sandbox_node,
    generate_tests_node,
    ingest_and_analyze_node,
    plan_execution_node,
    report_node,
    validate_candidates_node,
)

NODE_STEP_INDICES: Dict[str, int] = {
    "ingest_and_analyze_node": 1,
    "plan_execution_node": 2,
    "generate_tests_node": 3,
    "validate_candidates_node": 4,
    "execute_sandbox_node": 5,
    "diagnose_failure_node": 6,
    "report_node": 7,
}


class WorkflowEngine:
    """
    Deterministic workflow coordinator managing node execution, conditional routing,
    checkpoint persistence, and telemetry event streaming.
    """

    def __init__(
        self,
        repository: SQLiteRepository,
        event_store: SQLiteEventStore,
        analysis_service: Optional[AnalysisService] = None,
        planner: Optional[ExecutionPlanner] = None,
        generation_service: Optional[GenerationService] = None,
        validation_pipeline: Optional[ValidationPipeline] = None,
        execution_service: Optional[ExecutionService] = None,
        diagnosis_service: Optional[DiagnosisService] = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.analysis_service = analysis_service
        self.planner = planner
        self.generation_service = generation_service
        self.validation_pipeline = validation_pipeline
        self.execution_service = execution_service
        self.diagnosis_service = diagnosis_service
        self._current_run: Optional[RunRecord] = None

    @property
    def current_run(self) -> Optional[RunRecord]:
        """Access the current RunRecord managed by this engine."""
        return self._current_run

    def _resolve_initial_git_meta(self, state: WorkflowState) -> Tuple[str, str, str]:
        """Resolves initial Git metadata (current_commit, base_commit, branch_name)."""
        if state.snapshot is not None:
            return (
                state.snapshot.current_commit,
                state.snapshot.base_commit,
                state.snapshot.branch_name,
            )
        try:
            from agentic_test.analysis.git_service import GitService

            svc = GitService(state.repo_path)
            commit = svc.get_head_commit()
            branch = svc.get_branch_name()
            return commit, commit, branch
        except Exception:
            return "unknown", "unknown", "unknown"

    def _dispatch_node(self, node_name: str, state: WorkflowState) -> WorkflowState:
        """Invokes the corresponding node wrapper with injected domain service."""
        if node_name == "ingest_and_analyze_node":
            if self.analysis_service is None:
                raise RuntimeError("AnalysisService is required to execute ingest_and_analyze_node")
            return ingest_and_analyze_node(state, self.analysis_service)

        elif node_name == "plan_execution_node":
            if self.planner is None:
                raise RuntimeError("ExecutionPlanner is required to execute plan_execution_node")
            return plan_execution_node(state, self.planner)

        elif node_name == "generate_tests_node":
            if self.generation_service is None:
                raise RuntimeError("GenerationService is required to execute generate_tests_node")
            return generate_tests_node(state, self.generation_service)

        elif node_name == "validate_candidates_node":
            if self.validation_pipeline is None:
                raise RuntimeError("ValidationPipeline is required to execute validate_candidates_node")
            return validate_candidates_node(state, self.validation_pipeline)

        elif node_name == "execute_sandbox_node":
            if self.execution_service is None:
                raise RuntimeError("ExecutionService is required to execute execute_sandbox_node")
            return execute_sandbox_node(state, self.execution_service)

        elif node_name == "diagnose_failure_node":
            if self.diagnosis_service is None:
                raise RuntimeError("DiagnosisService is required to execute diagnose_failure_node")
            return diagnose_failure_node(state, self.diagnosis_service)

        elif node_name == "report_node":
            return report_node(state)

        else:
            raise ValueError(f"Unrecognized workflow node: '{node_name}'")

    def _resolve_next_node(self, current_node: str, state: WorkflowState) -> Optional[str]:
        """Evaluates static transitions and conditional edge routers."""
        if current_node == "ingest_and_analyze_node":
            return "plan_execution_node"

        elif current_node == "plan_execution_node":
            return route_after_planning(state)

        elif current_node == "generate_tests_node":
            return "validate_candidates_node"

        elif current_node == "validate_candidates_node":
            return route_after_validation(state)

        elif current_node == "execute_sandbox_node":
            return route_after_execution(state)

        elif current_node == "diagnose_failure_node":
            return "report_node"

        elif current_node == "report_node":
            return None

        else:
            raise ValueError(f"Unrecognized workflow node: '{current_node}'")

    def _persist_node_artifacts(self, node_name: str, state: WorkflowState) -> None:
        """Persists newly generated entities to SQLiteRepository."""
        if node_name == "ingest_and_analyze_node" and state.snapshot is not None:
            if self._current_run is not None:
                self._current_run = self.repository.save_run(
                    run_id=state.run_id,
                    repo_path=state.repo_path,
                    current_commit=state.snapshot.current_commit,
                    base_commit=state.snapshot.base_commit,
                    branch_name=state.snapshot.branch_name,
                    route_selected=self._current_run.route_selected,
                    started_at=self._current_run.started_at,
                    final_status=self._current_run.final_status,
                )

        elif node_name == "plan_execution_node" and state.plan is not None:
            if self._current_run is not None:
                self._current_run = self.repository.save_run(
                    run_id=state.run_id,
                    repo_path=state.repo_path,
                    current_commit=self._current_run.current_commit,
                    base_commit=self._current_run.base_commit,
                    branch_name=self._current_run.branch_name,
                    route_selected=state.plan.route.value,
                    started_at=self._current_run.started_at,
                    final_status=self._current_run.final_status,
                )

        elif node_name in ("generate_tests_node", "validate_candidates_node"):
            for candidate in state.candidates:
                cand_to_save = (
                    candidate
                    if candidate.run_id == state.run_id
                    else candidate.model_copy(update={"run_id": state.run_id})
                )
                self.repository.save_candidate(cand_to_save)

        elif node_name == "execute_sandbox_node":
            if state.baseline_evidence is not None:
                base_to_save = (
                    state.baseline_evidence
                    if state.baseline_evidence.run_id == state.run_id
                    else state.baseline_evidence.model_copy(update={"run_id": state.run_id})
                )
                self.repository.save_evidence(base_to_save)
            for evidence in state.evidences:
                ev_to_save = (
                    evidence
                    if evidence.run_id == state.run_id
                    else evidence.model_copy(update={"run_id": state.run_id})
                )
                self.repository.save_evidence(ev_to_save)

        elif node_name == "diagnose_failure_node":
            for diagnosis in state.diagnoses:
                self.repository.save_diagnosis(diagnosis, run_id=state.run_id)

    def run(self, initial_state: WorkflowState) -> WorkflowState:
        """
        Executes the workflow graph starting from ingest_and_analyze_node to terminal completion.
        Maintains run lifecycle in SQLiteRepository and emits telemetry to SQLiteEventStore.
        """
        if not initial_state.run_id or not initial_state.run_id.strip():
            raise ValueError("initial_state.run_id must be a non-empty string")

        current_node: Optional[str] = "initialized"
        step_index: int = 0
        executed_nodes: Set[str] = set()
        state: WorkflowState = initial_state

        try:
            # 1. Initialize run record (status: INITIALIZED)
            current_commit, base_commit, branch_name = self._resolve_initial_git_meta(state)
            self._current_run = self.repository.save_run(
                run_id=state.run_id,
                repo_path=state.repo_path,
                current_commit=current_commit,
                base_commit=base_commit,
                branch_name=branch_name,
                started_at=datetime.now(timezone.utc),
                final_status="INITIALIZED",
            )

            # 2. Save initial checkpoint step 0
            self.repository.save_checkpoint(
                state=state,
                step_index=0,
                node_name="initialized",
            )

            # 3. Transition run record to RUNNING when execution begins
            self._current_run = self.repository.save_run(
                run_id=state.run_id,
                repo_path=state.repo_path,
                current_commit=self._current_run.current_commit,
                base_commit=self._current_run.base_commit,
                branch_name=self._current_run.branch_name,
                started_at=self._current_run.started_at,
                final_status="RUNNING",
            )

            # 4. Begin node dispatch loop
            current_node = "ingest_and_analyze_node"
            while current_node is not None:
                if current_node in executed_nodes:
                    raise RuntimeError(
                        f"Cycle detected: Node '{current_node}' has already been executed in this run."
                    )
                executed_nodes.add(current_node)
                step_index = NODE_STEP_INDICES[current_node]

                # Telemetry: NODE_STARTED
                self.event_store.emit_event(
                    run_id=state.run_id,
                    event_type="NODE_STARTED",
                    node_name=current_node,
                    payload={"step_index": step_index},
                )

                # Invoke the node wrapper
                state = self._dispatch_node(current_node, state)

                # Persist intermediate entities
                self._persist_node_artifacts(current_node, state)

                # Telemetry: NODE_COMPLETED
                self.event_store.emit_event(
                    run_id=state.run_id,
                    event_type="NODE_COMPLETED",
                    node_name=current_node,
                    payload={"step_index": step_index},
                )

                # Persist checkpoint after successful completion
                self.repository.save_checkpoint(
                    state=state,
                    step_index=step_index,
                    node_name=current_node,
                )

                # Terminal handling on report_node
                if current_node == "report_node":
                    if self._current_run is not None:
                        self._current_run = self.repository.save_run(
                            run_id=state.run_id,
                            repo_path=state.repo_path,
                            current_commit=self._current_run.current_commit,
                            base_commit=self._current_run.base_commit,
                            branch_name=self._current_run.branch_name,
                            route_selected=state.plan.route.value
                            if state.plan
                            else self._current_run.route_selected,
                            started_at=self._current_run.started_at,
                            completed_at=datetime.now(timezone.utc),
                            final_status="COMPLETED",
                            error_message=state.error_message,
                        )

                # Determine next node via router edges
                current_node = self._resolve_next_node(current_node, state)

            return state

        except Exception as exc:
            error_msg = str(exc)
            if self._current_run is not None:
                self._current_run = self.repository.save_run(
                    run_id=state.run_id,
                    repo_path=state.repo_path,
                    current_commit=self._current_run.current_commit,
                    base_commit=self._current_run.base_commit,
                    branch_name=self._current_run.branch_name,
                    route_selected=state.plan.route.value
                    if state.plan
                    else self._current_run.route_selected,
                    started_at=self._current_run.started_at,
                    completed_at=datetime.now(timezone.utc),
                    final_status="FAILED",
                    error_message=error_msg,
                )
            self.event_store.emit_event(
                run_id=state.run_id,
                event_type="RUN_FAILED",
                node_name=current_node,
                payload={"error": error_msg, "step_index": step_index},
            )
            raise
