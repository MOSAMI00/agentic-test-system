"""
Dependency injection service factory for the Agentic Test Generation System CLI.
Adheres strictly to Stage 3 Layer 1 Presentation Layer specifications and WBS 1.6.3C.

Constructs WorkflowEngine instances with SQLite persistence connections and swappable
domain service adapters, enabling pure mock execution during CLI testing without real
Docker or LLM providers.
"""

from pathlib import Path
from typing import Callable, Optional, Union
import sqlite3

from agentic_test.analysis.service import AnalysisService
from agentic_test.diagnosis.service import DiagnosisService
from agentic_test.execution.service import ExecutionService
from agentic_test.generation.service import GenerationService
from agentic_test.planning.planner import ExecutionPlanner
from agentic_test.storage.database import init_database
from agentic_test.storage.events import SQLiteEventStore
from agentic_test.storage.repository import SQLiteRepository
from agentic_test.validation.pipeline import ValidationPipeline
from agentic_test.workflow.engine import WorkflowEngine

EngineFactory = Callable[..., WorkflowEngine]

_custom_engine_factory: Optional[EngineFactory] = None


def set_engine_factory(factory: Optional[EngineFactory]) -> None:
    """Configures a custom factory for creating WorkflowEngine instances (used in tests)."""
    global _custom_engine_factory
    _custom_engine_factory = factory


def get_engine_factory() -> EngineFactory:
    """Returns the currently active engine factory."""
    return _custom_engine_factory or default_create_workflow_engine


def default_create_workflow_engine(
    db_path: Union[str, Path] = ".agentic_test.db",
    repo_path: Optional[Path] = None,
    *,
    repository: Optional[SQLiteRepository] = None,
    event_store: Optional[SQLiteEventStore] = None,
    analysis_service: Optional[AnalysisService] = None,
    planner: Optional[ExecutionPlanner] = None,
    generation_service: Optional[GenerationService] = None,
    validation_pipeline: Optional[ValidationPipeline] = None,
    execution_service: Optional[ExecutionService] = None,
    diagnosis_service: Optional[DiagnosisService] = None,
) -> WorkflowEngine:
    """
    Constructs a WorkflowEngine with initialized SQLite repository and event store.
    Permits optional domain service injection for testing seams.
    """
    if repository is None or event_store is None:
        conn = init_database(db_path)
        if repository is None:
            repository = SQLiteRepository(conn)
        if event_store is None:
            event_store = SQLiteEventStore(conn)

    # Instantiate default domain analysis and planning services if not injected
    if analysis_service is None:
        analysis_service = AnalysisService()
    if planner is None:
        planner = ExecutionPlanner()

    return WorkflowEngine(
        repository=repository,
        event_store=event_store,
        analysis_service=analysis_service,
        planner=planner,
        generation_service=generation_service,
        validation_pipeline=validation_pipeline,
        execution_service=execution_service,
        diagnosis_service=diagnosis_service,
    )


def create_workflow_engine(
    db_path: Union[str, Path] = ".agentic_test.db",
    repo_path: Optional[Path] = None,
    **kwargs: object,
) -> WorkflowEngine:
    """Dispatches engine creation to the configured factory."""
    factory = get_engine_factory()
    return factory(db_path=db_path, repo_path=repo_path, **kwargs)
