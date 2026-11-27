"""
Formal protocol defining Large Language Model (LLM) cognitive services.
Stage 3 Section 4.4.2.3 Listing 4.3.

Shared LLM exceptions (U-02) live here so that both the generation layer and
the diagnosis layer can import them without creating a cross-layer dependency.
"""

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base exception for all LLM service errors."""


class LLMCommunicationError(LLMError):
    """Raised when communication with an LLM provider fails or times out."""


class SchemaValidationError(LLMError):
    """Raised when LLM output violates the required structured output schema or is unparseable."""


@runtime_checkable
class LLMService(Protocol):
    """
    Formal protocol providing cognitive synthesis and classification services.
    Enforces structured output parsing, token estimation, and timeout handling.
    Stage 3 Listing 4.3.
    """

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        response_schema: type[T],
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> T:
        """
        Transmits prompt to cognitive model and parses response into validated schema.

        :param prompt: Formatted contextual prompt.
        :param system_instruction: Guiding behavioral system prompt.
        :param response_schema: Pydantic model enforcing JSON schema compliance.
        :param temperature: Sampling temperature (0.0 for deterministic code).
        :param max_tokens: Maximum completion token ceiling.
        :return: Validated Pydantic instance of response_schema.
        """
        ...

    def estimate_tokens(self, text: str) -> int:
        """
        Computes estimated token count for text using target model tokenizers.

        :param text: Text string to analyze.
        :return: Integer count of estimated tokens.
        """
        ...
