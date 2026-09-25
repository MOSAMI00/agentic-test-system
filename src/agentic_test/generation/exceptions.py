"""
Generation-local exceptions for cognitive test generation.
Iteration 1.4 Component Architecture.
"""


class GenerationError(Exception):
    """Base exception for all test generation errors."""
    pass


class LLMCommunicationError(GenerationError):
    """Raised when communication with an LLM service fails or times out."""
    pass


class SchemaValidationError(GenerationError):
    """Raised when LLM output violates the required structured schema or is unparseable."""
    pass


class ContextBudgetExceededError(GenerationError):
    """Raised when prompt assembly exceeds the maximum allowable token budget even after pruning."""
    pass
