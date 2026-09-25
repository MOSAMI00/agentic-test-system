"""
Mock LLM Service for deterministic offline test generation and pipeline verification.
Iteration 1.4 Component Architecture.
"""

from typing import Any, Callable, Dict, List, Optional, Type, TypeVar
from pydantic import BaseModel

from agentic_test.core.protocols.llm import LLMService
from agentic_test.generation.exceptions import LLMCommunicationError, SchemaValidationError

T = TypeVar("T", bound=BaseModel)


class MockLLMService(LLMService):
    """
    Offline, deterministic implementation of LLMService for unit and integration testing.
    Maintains canned response queues, records call telemetry, and simulates error modes.
    """

    def __init__(
        self,
        canned_responses: Optional[List[BaseModel]] = None,
        default_factory: Optional[Callable[[Type[BaseModel]], BaseModel]] = None,
    ) -> None:
        self._canned_queue: List[BaseModel] = list(canned_responses) if canned_responses else []
        self._default_factory = default_factory
        self._calls: List[Dict[str, Any]] = []
        self._error_queue: List[Exception] = []

    @property
    def calls(self) -> List[Dict[str, Any]]:
        """List of all calls made to generate_structured."""
        return self._calls

    @property
    def call_count(self) -> int:
        """Total number of generate_structured invocations."""
        return len(self._calls)

    def enqueue_response(self, response: BaseModel) -> None:
        """Appends a canned response to the end of the queue."""
        self._canned_queue.append(response)

    def enqueue_error(self, error: Exception) -> None:
        """Appends an error to be raised on the next generate_structured call."""
        self._error_queue.append(error)

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        response_schema: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> T:
        """
        Simulates structured output generation.
        Records call telemetry and returns queued responses or simulated errors.
        """
        self._calls.append({
            "prompt": prompt,
            "system_instruction": system_instruction,
            "response_schema": response_schema,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })

        if self._error_queue:
            raise self._error_queue.pop(0)

        if self._canned_queue:
            candidate = self._canned_queue.pop(0)
            if isinstance(candidate, response_schema):
                return candidate
            # If candidate is a dict or another model, attempt validation
            if isinstance(candidate, BaseModel):
                try:
                    return response_schema.model_validate(candidate.model_dump())
                except Exception as err:
                    raise SchemaValidationError(f"Mock response schema mismatch: {err}") from err
            raise SchemaValidationError(
                f"Mock response {type(candidate)} is not compatible with {response_schema}"
            )

        if self._default_factory is not None:
            result = self._default_factory(response_schema)
            if isinstance(result, response_schema):
                return result
            try:
                return response_schema.model_validate(result.model_dump())
            except Exception as err:
                raise SchemaValidationError(f"Mock factory output mismatch: {err}") from err

        raise LLMCommunicationError("MockLLMService response queue exhausted and no default factory configured.")

    def estimate_tokens(self, text: str) -> int:
        """
        Deterministic offline token count heuristic.
        Estimates ~4 characters per token with minimum count of 1 for non-empty text.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)
