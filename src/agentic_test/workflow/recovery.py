"""
Crash recovery and checkpoint-based workflow resumption for the Agentic Test Generation System.
Adheres strictly to Stage 3 Section 4.4.3, NFR-07 (Crash Recovery), and WBS 1.6.3B.

Provides idempotent, deterministic state reconstruction and execution continuation
from point-in-time SQLite checkpoints.
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
from agentic_test.storage.repository import CheckpointRecord, RunRecord, SQLiteRepository
from agentic_test.validation.pipeline import ValidationPipeline
from agentic_test.workflow.edges import (
    route_after_execution,
    route_after_planning,
    route_after_validation,
)
from agentic_test.workflow.engine import NODE_STEP_INDICES, WorkflowEngine


class RecoveryError(ValueError):
    """Raised when workflow checkpoint recovery or run resumption fails."""
    pass


def _resolve_resume_node(checkpoint: CheckpointRecord, state: WorkflowState) -> str:
    """
    Determines the next logical node or router decision after a checkpoint.

    Mapping:
    - Step 0 (initialized)           -> ingest_and_analyze_node
    - Step 1 (ingest_and_analyze)    -> plan_execution_node
    - Step 2 (plan_execution)        -> route_after_planning(state)
    - Step 3 (generate_tests)        -> validate_candidates_node
    - Step 4 (validate_candidates)   -> route_after_validation(state)
    - Step 5 (execute_sandbox)       -> route_after_execution(state)
    - Step 6 (diagnose_failure)      -> report_node
    - Step 7 (report_node)           -> terminal (raises RecoveryError)
    """
    step = checkpoint.step_index
    if step == 0:
        return "ingest_and_analyze_node"
    elif step == 1:
        return "plan_execution_node"
    elif step == 2:
        return route_after_planning(state)
    elif step == 3:
        return "validate_candidates_node"
    elif step == 4:
        return route_after_validation(state)
    elif step == 5:
        return route_after_execution(state)
    elif step == 6:
        return "report_node"
    else:
        raise RecoveryError(f"Cannot resume from terminal step index {step}")


class WorkflowRecoveryService:
    """
    Coordinates checkpoint loading, validation, and safe workflow resumption.
    Reuses existing WorkflowEngine dependencies and persistence adapters.
    """

    def __init__(self, engine: WorkflowEngine) -> None:
        self.engine = engine

    @property
    def repository(self) -> SQLiteRepository:
        return self.engine.repository

    @property
    def event_store(self) -> SQLiteEventStore:
        return self.engine.event_store

    def resume(self, run_id: str) -> WorkflowState:
        """
        Resumes an incomplete workflow run from its latest point-in-time checkpoint.

        Contract:
        1. Loads the latest checkpoint; raises RecoveryError if missing.
        2. Rejects runs in terminal status ('COMPLETED' or 'FAILED').
        3. Reconstructs the full WorkflowState from the checkpoint payload.
        4. Emits exactly one RUN_RESUMED event with run_id, step_index, and last node name.
        5. Updates RunRecord to RUNNING.
        6. Resumes execution at the next logical node without re-executing already-completed steps.
        7. On exception: updates run to FAILED with error_message, emits RUN_FAILED, and re-raises.
        8. Returns final completed WorkflowState.
        """
        if not run_id or not run_id.strip():
            raise RecoveryError("run_id must be a non-empty string")

        # 1. Load latest checkpoint
        checkpoint = self.repository.get_latest_checkpoint(run_id)
        if checkpoint is None:
            raise RecoveryError(f"No checkpoint found for run '{run_id}'. Cannot resume.")

        # 2. Verify run is not in terminal status
        run_record = self.repository.get_run(run_id)
        if run_record is not None and run_record.final_status in ("COMPLETED", "FAILED"):
            raise RecoveryError(
                f"Cannot resume run '{run_id}': run is already in terminal status '{run_record.final_status}'."
            )
        if checkpoint.step_index >= 7 or checkpoint.state.final_status in ("COMPLETED", "FAILED"):
            raise RecoveryError(
                f"Cannot resume run '{run_id}': latest checkpoint (step {checkpoint.step_index}) is terminal."
            )

        # 3. Reconstruct WorkflowState from checkpoint
        state: WorkflowState = checkpoint.state

        # 4. Resolve already-executed nodes from existing checkpoints
        existing_checkpoints = self.repository.get_checkpoints(run_id)
        executed_nodes: Set[str] = {
            cp.node_name for cp in existing_checkpoints if cp.node_name != "initialized"
        }

        # 5. Initialize tracking variables for error handling
        current_node: Optional[str] = checkpoint.node_name
        step_index: int = checkpoint.step_index

        try:
            # 6. Emit RUN_RESUMED telemetry event
            self.event_store.emit_event(
                run_id=run_id,
                event_type="RUN_RESUMED",
                node_name=checkpoint.node_name,
                payload={
                    "run_id": run_id,
                    "step_index": checkpoint.step_index,
                    "node_name": checkpoint.node_name,
                },
            )

            # 7. Update RunRecord to RUNNING
            git_commit = (
                run_record.current_commit
                if run_record
                else (state.snapshot.current_commit if state.snapshot else "unknown")
            )
            git_base = (
                run_record.base_commit
                if run_record
                else (state.snapshot.base_commit if state.snapshot else "unknown")
            )
            git_branch = (
                run_record.branch_name
                if run_record
                else (state.snapshot.branch_name if state.snapshot else "unknown")
            )
            git_route = (
                state.plan.route.value
                if state.plan
                else (run_record.route_selected if run_record else None)
            )
            started_at = run_record.started_at if run_record else datetime.now(timezone.utc)

            self.engine._current_run = self.repository.save_run(
                run_id=run_id,
                repo_path=state.repo_path,
                current_commit=git_commit,
                base_commit=git_base,
                branch_name=git_branch,
                route_selected=git_route,
                started_at=started_at,
                final_status="RUNNING",
            )

            # 8. Ensure restored candidates and evidences are in repository for foreign-key consistency
            for candidate in state.candidates:
                cand_to_save = (
                    candidate
                    if candidate.run_id == run_id
                    else candidate.model_copy(update={"run_id": run_id})
                )
                self.repository.save_candidate(cand_to_save)

            # 9. Resolve next node to execute
            current_node = _resolve_resume_node(checkpoint, state)

            # 9. Continuation loop
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
                state = self.engine._dispatch_node(current_node, state)

                # Persist intermediate entities
                self.engine._persist_node_artifacts(current_node, state)

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
                    if self.engine._current_run is not None:
                        self.engine._current_run = self.repository.save_run(
                            run_id=state.run_id,
                            repo_path=state.repo_path,
                            current_commit=self.engine._current_run.current_commit,
                            base_commit=self.engine._current_run.base_commit,
                            branch_name=self.engine._current_run.branch_name,
                            route_selected=state.plan.route.value
                            if state.plan
                            else self.engine._current_run.route_selected,
                            started_at=self.engine._current_run.started_at,
                            completed_at=datetime.now(timezone.utc),
                            final_status="COMPLETED",
                            error_message=state.error_message,
                        )

                # Determine next node via router edges
                current_node = self.engine._resolve_next_node(current_node, state)

            return state

        except Exception as exc:
            error_msg = str(exc)
            curr_run = self.engine._current_run or run_record
            self.engine._current_run = self.repository.save_run(
                run_id=state.run_id,
                repo_path=state.repo_path,
                current_commit=curr_run.current_commit if curr_run else "unknown",
                base_commit=curr_run.base_commit if curr_run else "unknown",
                branch_name=curr_run.branch_name if curr_run else "unknown",
                route_selected=state.plan.route.value
                if state.plan
                else (curr_run.route_selected if curr_run else None),
                started_at=curr_run.started_at if curr_run else datetime.now(timezone.utc),
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


def resume_run(
    run_id: str,
    engine: Optional[WorkflowEngine] = None,
    *,
    repository: Optional[SQLiteRepository] = None,
    event_store: Optional[SQLiteEventStore] = None,
    analysis_service: Optional[AnalysisService] = None,
    planner: Optional[ExecutionPlanner] = None,
    generation_service: Optional[GenerationService] = None,
    validation_pipeline: Optional[ValidationPipeline] = None,
    execution_service: Optional[ExecutionService] = None,
    diagnosis_service: Optional[DiagnosisService] = None,
) -> WorkflowState:
    """
    Synchronous public API for resuming an interrupted workflow run from its latest checkpoint.

    :param run_id: Execution run identifier to resume.
    :param engine: Pre-configured WorkflowEngine coordinator.
    :param repository: SQLiteRepository instance (used if engine is None).
    :param event_store: SQLiteEventStore instance (used if engine is None).
    :param analysis_service: Optional AnalysisService dependency.
    :param planner: Optional ExecutionPlanner dependency.
    :param generation_service: Optional GenerationService dependency.
    :param validation_pipeline: Optional ValidationPipeline dependency.
    :param execution_service: Optional ExecutionService dependency.
    :param diagnosis_service: Optional DiagnosisService dependency.
    :return: Final completed WorkflowState.
    """
    if engine is None:
        if repository is None or event_store is None:
            raise ValueError("Either engine or both repository and event_store must be provided to resume_run")
        engine = WorkflowEngine(
            repository=repository,
            event_store=event_store,
            analysis_service=analysis_service,
            planner=planner,
            generation_service=generation_service,
            validation_pipeline=validation_pipeline,
            execution_service=execution_service,
            diagnosis_service=diagnosis_service,
        )

    recovery_service = WorkflowRecoveryService(engine)
    return recovery_service.resume(run_id)
