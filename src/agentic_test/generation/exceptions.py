"""
Generation-local exceptions for cognitive test generation.
Iteration 1.4 Component Architecture.

LLMCommunicationError and SchemaValidationError are defined in the shared
protocols layer (core/protocols/llm.py) and re-exported here for backward
compatibility so that existing imports from generation.exceptions continue
to work without change.
"""

from agentic_test.core.protocols.llm import LLMCommunicationError as LLMCommunicationError
from agentic_test.core.protocols.llm import SchemaValidationError as SchemaValidationError

__all__ = [
    "GenerationError",
    "LLMCommunicationError",
    "SchemaValidationError",
    "ContextBudgetExceededError",
]


class GenerationError(Exception):
    """Base exception for all test generation errors."""


class ContextBudgetExceededError(GenerationError):
    """Raised when prompt assembly exceeds the maximum allowable token budget even after pruning."""
