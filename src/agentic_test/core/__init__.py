"""
Core models, state, protocols, and configuration for agentic_test.
F-12/F-13: LLMService, LLMCommunicationError, SchemaValidationError,
SandboxManager, SandboxConfig, ExecutionRawResult, and sandbox exceptions
are re-exported from this package for convenient top-level access.
"""

from agentic_test.core.config import Settings, get_settings, settings
from agentic_test.core.models import (
    ChangeType,
    DiffHunk,
    ExecutionEvidence,
    ExecutionPlan,
    FailureCategory,
    FailureDiagnosis,
    RepositorySnapshot,
    RepositoryValidationError,
    SymbolContract,
    SymbolType,
    TestCandidate,
    TriageEngine,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.core.protocols.analyzer import CodeAnalyzer, SyntaxParsingError
from agentic_test.core.protocols.llm import (
    LLMCommunicationError,
    LLMError,
    LLMService,
    SchemaValidationError,
)
from agentic_test.core.protocols.sandbox import (
    ExecutionRawResult,
    SandboxConfig,
    SandboxError,
    SandboxExecutionError,
    SandboxInitializationError,
    SandboxManager,
)
from agentic_test.core.state import WorkflowState

__all__ = [
    # Models
    "ChangeType",
    "DiffHunk",
    "ExecutionEvidence",
    "ExecutionPlan",
    "FailureCategory",
    "FailureDiagnosis",
    "RepositorySnapshot",
    "RepositoryValidationError",
    "SymbolContract",
    "SymbolType",
    "TestCandidate",
    "TriageEngine",
    "ValidationStatus",
    "WorkflowRoute",
    # State
    "WorkflowState",
    # Protocols — Analyzer
    "CodeAnalyzer",
    "SyntaxParsingError",
    # Protocols — LLM (F-12)
    "LLMCommunicationError",
    "LLMError",
    "LLMService",
    "SchemaValidationError",
    # Protocols — Sandbox (F-13)
    "ExecutionRawResult",
    "SandboxConfig",
    "SandboxError",
    "SandboxExecutionError",
    "SandboxInitializationError",
    "SandboxManager",
    # Configuration
    "Settings",
    "get_settings",
    "settings",
]
