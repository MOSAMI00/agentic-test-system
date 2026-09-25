"""
Unit tests for MockLLMService.
Verifies LLMService protocol conformance, queue behavior, telemetry, and error handling.
"""

import pytest
from pydantic import BaseModel

from agentic_test.core.protocols.llm import LLMService
from agentic_test.generation.exceptions import (
    LLMCommunicationError,
    SchemaValidationError,
)
from agentic_test.generation.mock_llm import MockLLMService
from agentic_test.generation.schemas import CandidateSynthesisSchema


class SimpleResponse(BaseModel):
    value: int
    note: str


def test_mock_llm_service_implements_protocol() -> None:
    """MockLLMService must satisfy the LLMService runtime checkable protocol."""
    service = MockLLMService()
    assert isinstance(service, LLMService)


def test_mock_llm_service_generate_structured_queue() -> None:
    """generate_structured returns queued canned responses in FIFO order."""
    response_1 = SimpleResponse(value=10, note="First")
    response_2 = SimpleResponse(value=20, note="Second")

    service = MockLLMService(canned_responses=[response_1, response_2])

    result_1 = service.generate_structured(
        prompt="Prompt 1",
        system_instruction="Instruction",
        response_schema=SimpleResponse,
    )
    assert result_1.value == 10
    assert result_1.note == "First"

    result_2 = service.generate_structured(
        prompt="Prompt 2",
        system_instruction="Instruction",
        response_schema=SimpleResponse,
    )
    assert result_2.value == 20
    assert result_2.note == "Second"

    # Queue exhausted: raises LLMCommunicationError
    with pytest.raises(LLMCommunicationError, match="exhausted"):
        service.generate_structured(
            prompt="Prompt 3",
            system_instruction="Instruction",
            response_schema=SimpleResponse,
        )


def test_mock_llm_service_telemetry_recording() -> None:
    """Call telemetry records all invocation parameters."""
    response = CandidateSynthesisSchema(
        imports=("import pytest",),
        test_code="def test_sample(): pass",
        rationale="Sample test case",
        mock_targets=(),
    )
    service = MockLLMService(canned_responses=[response])

    service.generate_structured(
        prompt="Generate a test",
        system_instruction="Follow rules",
        response_schema=CandidateSynthesisSchema,
        temperature=0.2,
        max_tokens=1024,
    )

    assert service.call_count == 1
    call = service.calls[0]
    assert call["prompt"] == "Generate a test"
    assert call["system_instruction"] == "Follow rules"
    assert call["response_schema"] is CandidateSynthesisSchema
    assert call["temperature"] == 0.2
    assert call["max_tokens"] == 1024


def test_mock_llm_service_error_queue() -> None:
    """Simulated errors queued via enqueue_error are raised on the next call."""
    service = MockLLMService()
    service.enqueue_error(LLMCommunicationError("Simulated network timeout"))

    with pytest.raises(LLMCommunicationError, match="Simulated network timeout"):
        service.generate_structured(
            prompt="Prompt",
            system_instruction="Instruction",
            response_schema=SimpleResponse,
        )


def test_mock_llm_service_token_estimation() -> None:
    """estimate_tokens returns deterministic offline estimates."""
    service = MockLLMService()
    assert service.estimate_tokens("") == 0
    assert service.estimate_tokens("abc") == 1
    assert service.estimate_tokens("a" * 100) == 25
