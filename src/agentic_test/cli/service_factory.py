"""
Dependency injection service factory for the Agentic Test Generation System CLI.
Adheres strictly to Stage 3 Layer 1 Presentation Layer specifications and WBS 1.6.3C.

Constructs WorkflowEngine instances with SQLite persistence connections and swappable
domain service adapters, enabling pure mock execution during CLI testing without real
Docker or LLM providers.
"""

from pathlib import Path
from typing import Any, Callable, Optional, Union
import os
import sqlite3

from agentic_test.analysis.service import AnalysisService
from agentic_test.core.config import settings
from agentic_test.core.models import FailureCategory
from agentic_test.diagnosis.llm_classifier import DiagnosisResponseSchema
from agentic_test.diagnosis.service import DiagnosisService
from agentic_test.execution.mock_sandbox import MockSandboxManager
from agentic_test.execution.service import ExecutionService
from agentic_test.generation.mock_llm import MockLLMService
from agentic_test.generation.schemas import CandidateSynthesisSchema
from agentic_test.generation.service import GenerationService
from agentic_test.planning.planner import ExecutionPlanner
from agentic_test.storage.database import init_database
from agentic_test.storage.events import SQLiteEventStore
from agentic_test.storage.repository import SQLiteRepository
from agentic_test.validation.pipeline import ValidationPipeline
from agentic_test.workflow.engine import WorkflowEngine


class ConfigurationError(Exception):
    """Raised when runtime environment configuration, credentials, or live adapters are unavailable."""
    pass


def _default_mock_llm_factory(schema: Any) -> Any:
    if schema == CandidateSynthesisSchema:
        return CandidateSynthesisSchema(
            imports=(),
            test_code="def test_candidate_offline():\n    assert True\n",
            rationale="Offline deterministic synthesis for verification.",
            mock_targets=(),
        )
    if schema == DiagnosisResponseSchema:
        return DiagnosisResponseSchema(
            category=FailureCategory.UNKNOWN,
            confidence=0.75,
            explanation="Offline deterministic failure diagnosis.",
        )
    raise ValueError(f"Unsupported mock LLM response schema: {schema}")


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
    offline: bool = False,
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
    Supports explicit offline mode (offline=True) using in-memory mock adapters
    and live mode (offline=False) failing closed with ConfigurationError if live
    adapters are unconfigured.
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

    if offline:
        # Offline mode: Use existing in-memory mock adapters without external API or Docker calls
        mock_llm = MockLLMService(default_factory=_default_mock_llm_factory)
        mock_sandbox = MockSandboxManager()

        if validation_pipeline is None:
            validation_pipeline = ValidationPipeline(
                sandbox_manager=mock_sandbox,
                source_root=repo_path,
            )
        if generation_service is None:
            generation_service = GenerationService(
                llm_service=mock_llm,
                repo_root=repo_path,
            )
        if execution_service is None:
            execution_service = ExecutionService(
                sandbox_manager=mock_sandbox,
                source_root=repo_path or Path("."),
            )
        if diagnosis_service is None:
            diagnosis_service = DiagnosisService(
                llm_service=mock_llm,
            )
    else:
        # Live mode: Always construct ValidationPipeline with safe static validators
        if validation_pipeline is None:
            validation_pipeline = ValidationPipeline()

        # In live mode, verify required live adapters or fail closed
        if generation_service is None:
            has_api_key = (
                (settings.openai_api_key is not None and bool(settings.openai_api_key.get_secret_value()))
                or bool(os.environ.get("OPENAI_API_KEY"))
            )
            if not has_api_key:
                raise ConfigurationError(
                    "Live LLM service unavailable: 'OPENAI_API_KEY' is not configured. "
                    "Configure OPENAI_API_KEY or run with --offline for local deterministic execution."
                )
            from agentic_test.generation.litellm_service import LiteLLMService

            live_llm = LiteLLMService(
                api_key=settings.openai_api_key or os.environ.get("OPENAI_API_KEY"),
                allow_external_transmission=True,
            )
            generation_service = GenerationService(
                llm_service=live_llm,
                repo_root=repo_path,
            )
            if diagnosis_service is None:
                diagnosis_service = DiagnosisService(llm_service=live_llm)

        if execution_service is None:
            from agentic_test.execution.docker_sandbox import is_docker_sdk_available
            if not is_docker_sdk_available():
                raise ConfigurationError(
                    "Live Docker sandbox unavailable: The 'docker' Python package is not installed. "
                    "Install it or run with --offline for local deterministic execution."
                )
            from agentic_test.execution.docker_sandbox import DockerSandboxManager
            try:
                docker_mgr = DockerSandboxManager()
                docker_mgr._get_client().ping()
            except Exception as err:
                raise ConfigurationError(
                    f"Live Docker sandbox unavailable: Docker daemon is not reachable ({err}). "
                    "Ensure Docker is running or run with --offline for local deterministic execution."
                ) from err

            execution_service = ExecutionService(
                sandbox_manager=docker_mgr,
                source_root=repo_path or Path("."),
            )

        if diagnosis_service is None:
            raise ConfigurationError(
                "Live diagnosis service unavailable: DiagnosisService requires a configured LLMService. "
                "Configure OPENAI_API_KEY or run with --offline for local deterministic execution."
            )

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
    offline: bool = False,
    **kwargs: object,
) -> WorkflowEngine:
    """Dispatches engine creation to the configured factory."""
    factory = get_engine_factory()
    return factory(db_path=db_path, repo_path=repo_path, offline=offline, **kwargs)
