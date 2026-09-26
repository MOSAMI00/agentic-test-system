"""
Offline tests for LiteLLMService adapter and RunBudgetTracker.
Verifies protocol conformance, privacy gates, credentials, JSON schemas,
refusal handling, error handling, retries, and request caps without network calls.
"""

import os
from pathlib import Path
import socket
from typing import Any, Generator
from unittest.mock import MagicMock, patch
import pytest
from pydantic import BaseModel, SecretStr

import litellm
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    Timeout,
)
from agentic_test.core.models import (
    ExecutionPlan,
    SymbolContract,
    SymbolType,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.core.protocols.llm import LLMService
from agentic_test.generation.exceptions import LLMCommunicationError, SchemaValidationError
from agentic_test.generation.litellm_service import LiteLLMService, RunBudgetTracker
from agentic_test.generation.schemas import CandidateSynthesisSchema
from agentic_test.generation.service import GenerationService


class OfflineNetworkViolationError(RuntimeError):
    """Raised when an offline test attempts outbound socket or DNS transmission."""
    pass


@pytest.fixture(autouse=True)
def offline_network_guard() -> Generator[None, None, None]:
    """
    Permanent offline network guard for the LiteLLM adapter test suite.
    Blocks non-loopback socket.connect, socket.create_connection, and DNS getaddrinfo.
    Permits localhost, 127.0.0.1, and ::1 loopback communication only.
    Restores original socket functions upon test teardown.
    """
    orig_connect = socket.socket.connect
    orig_create_connection = socket.create_connection
    orig_getaddrinfo = socket.getaddrinfo

    loopback_hosts = {"localhost", "127.0.0.1", "::1"}

    def _is_loopback(host: str) -> bool:
        return host in loopback_hosts or host.startswith("127.")

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        host = ""
        if isinstance(address, tuple) and len(address) > 0:
            host = str(address[0])
        elif isinstance(address, str):
            host = address

        if _is_loopback(host):
            return orig_connect(self, address)

        raise OfflineNetworkViolationError(
            f"OFFLINE TEST BOUNDARY VIOLATION: socket.connect attempted to non-loopback destination {address!r}."
        )

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        host = ""
        if isinstance(address, tuple) and len(address) > 0:
            host = str(address[0])
        elif isinstance(address, str):
            host = address

        if _is_loopback(host):
            return orig_create_connection(address, *args, **kwargs)

        raise OfflineNetworkViolationError(
            f"OFFLINE TEST BOUNDARY VIOLATION: socket.create_connection attempted to non-loopback destination {address!r}."
        )

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        str_host = str(host) if host is not None else ""
        if _is_loopback(str_host):
            return orig_getaddrinfo(host, *args, **kwargs)

        raise OfflineNetworkViolationError(
            f"OFFLINE TEST BOUNDARY VIOLATION: DNS resolution attempted for non-loopback host {str_host!r}."
        )

    setattr(socket.socket, "connect", guarded_connect)
    setattr(socket, "create_connection", guarded_create_connection)
    setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    try:
        yield
    finally:
        setattr(socket.socket, "connect", orig_connect)
        setattr(socket, "create_connection", orig_create_connection)
        setattr(socket, "getaddrinfo", orig_getaddrinfo)


class SimpleTestSchema(BaseModel):
    name: str
    score: int


def _create_mock_response(content: str, finish_reason: str = "stop", refusal: str | None = None) -> MagicMock:
    """Helper to construct a mock LiteLLM completion response object."""
    mock_choice = MagicMock()
    mock_choice.finish_reason = finish_reason
    mock_choice.message = MagicMock()
    mock_choice.message.content = content
    mock_choice.message.refusal = refusal

    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    return mock_resp


# =========================================================================
# 0. Offline Network Guard Verification Tests
# =========================================================================

def test_network_guard_actively_blocks_outbound_dns() -> None:
    """Verifies that the offline network guard actively intercepts non-loopback DNS resolution."""
    with pytest.raises(OfflineNetworkViolationError, match="OFFLINE TEST BOUNDARY VIOLATION: DNS resolution"):
        socket.getaddrinfo("daily-cloudcode-pa.googleapis.com", 443)


def test_network_guard_actively_blocks_outbound_socket() -> None:
    """Verifies that the offline network guard actively intercepts non-loopback socket.connect."""
    with pytest.raises(OfflineNetworkViolationError, match="OFFLINE TEST BOUNDARY VIOLATION: socket.connect"):
        s = socket.socket()
        try:
            s.connect(("8.8.8.8", 53))
        finally:
            s.close()



# =========================================================================
# 1. Protocol Conformance
# =========================================================================

def test_protocol_conformance() -> None:
    service = LiteLLMService()
    assert isinstance(service, LLMService)
    assert issubclass(LiteLLMService, LLMService)


# =========================================================================
# 2. Privacy Gate Checks
# =========================================================================

def test_privacy_gate_blocks_external_model_by_default() -> None:
    service = LiteLLMService(model="gpt-4o-mini", allow_external_transmission=False)
    with patch("litellm.completion") as mock_completion:
        with pytest.raises(LLMCommunicationError, match="External LLM transmission disallowed"):
            service.generate_structured(
                prompt="Write a test",
                system_instruction="Be helpful",
                response_schema=SimpleTestSchema,
            )
        mock_completion.assert_not_called()


def test_privacy_gate_allows_local_model() -> None:
    service = LiteLLMService(model="ollama/llama3", allow_external_transmission=False)
    mock_resp = _create_mock_response('{"name": "test_local", "score": 100}')
    with patch("litellm.completion", return_value=mock_resp) as mock_completion:
        result = service.generate_structured(
            prompt="Write a test",
            system_instruction="Be helpful",
            response_schema=SimpleTestSchema,
        )
        assert result.name == "test_local"
        assert result.score == 100
        mock_completion.assert_called_once()


# =========================================================================
# 3. Credential Resolution & SecretStr Handling
# =========================================================================

def test_credential_injection_via_secret_str() -> None:
    secret = SecretStr("sk-secret-key-12345")
    service = LiteLLMService(
        model="gpt-4o-mini",
        api_key=secret,
        allow_external_transmission=True,
    )
    # Ensure raw secret is not in repr or str
    assert "sk-secret-key-12345" not in str(service._api_key)

    mock_resp = _create_mock_response('{"name": "test_secret", "score": 90}')
    with patch("litellm.completion", return_value=mock_resp) as mock_completion:
        result = service.generate_structured(
            prompt="Write a test",
            system_instruction="Be helpful",
            response_schema=SimpleTestSchema,
        )
        assert result.name == "test_secret"
        _, kwargs = mock_completion.call_args
        assert kwargs["api_key"] == "sk-secret-key-12345"


def test_credential_injection_via_plain_str() -> None:
    service = LiteLLMService(
        model="gpt-4o-mini",
        api_key="sk-plain-key-999",
        allow_external_transmission=True,
    )
    assert isinstance(service._api_key, SecretStr)

    mock_resp = _create_mock_response('{"name": "test_plain", "score": 80}')
    with patch("litellm.completion", return_value=mock_resp) as mock_completion:
        result = service.generate_structured(
            prompt="Write a test",
            system_instruction="Be helpful",
            response_schema=SimpleTestSchema,
        )
        assert result.name == "test_plain"
        _, kwargs = mock_completion.call_args
        assert kwargs["api_key"] == "sk-plain-key-999"


def test_credential_resolution_from_env() -> None:
    service = LiteLLMService(
        model="gpt-4o-mini",
        api_key=None,
        allow_external_transmission=True,
    )
    mock_resp = _create_mock_response('{"name": "test_env", "score": 70}')
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-key-777"}, clear=False):
        with patch("litellm.completion", return_value=mock_resp) as mock_completion:
            result = service.generate_structured(
                prompt="Write a test",
                system_instruction="Be helpful",
                response_schema=SimpleTestSchema,
            )
            assert result.name == "test_env"
            _, kwargs = mock_completion.call_args
            assert kwargs["api_key"] == "sk-env-key-777"


def test_missing_credentials_raises_communication_error() -> None:
    service = LiteLLMService(
        model="gpt-4o-mini",
        api_key=None,
        allow_external_transmission=True,
    )
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("OPENAI_API_KEY", None)
        with patch("litellm.completion") as mock_completion:
            with pytest.raises(LLMCommunicationError, match="Missing credentials"):
                service.generate_structured(
                    prompt="Write a test",
                    system_instruction="Be helpful",
                    response_schema=SimpleTestSchema,
                )
            mock_completion.assert_not_called()


# =========================================================================
# 4. Strict Structured Output Payload & Verification
# =========================================================================

def test_strict_schema_payload_structure() -> None:
    service = LiteLLMService(
        model="gpt-4o-mini",
        api_key=SecretStr("sk-dummy"),
        allow_external_transmission=True,
    )
    mock_resp = _create_mock_response(
        '{"test_code": "def test_foo(): pass", "imports": ["import pytest"], "rationale": "valid test", "mock_targets": []}'
    )
    with patch("litellm.completion", return_value=mock_resp) as mock_completion:
        result = service.generate_structured(
            prompt="Generate a test",
            system_instruction="Follow constraints",
            response_schema=CandidateSynthesisSchema,
            temperature=0.0,
            max_tokens=1024,
        )
        assert isinstance(result, CandidateSynthesisSchema)
        assert result.test_code == "def test_foo(): pass"
        assert result.imports == ("import pytest",)

        # Check call arguments
        _, kwargs = mock_completion.call_args
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == 1024
        assert kwargs["num_retries"] == 0
        assert kwargs["no-log"] is True
        assert kwargs["messages"] == [
            {"role": "system", "content": "Follow constraints"},
            {"role": "user", "content": "Generate a test"},
        ]
        assert kwargs["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "CandidateSynthesisSchema",
                "schema": CandidateSynthesisSchema.model_json_schema(),
                "strict": True,
            },
        }


# =========================================================================
# 5. Refusal, Truncation, Empty, and Malformed Output
# =========================================================================

def test_model_refusal_raises_communication_error_terminal() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True)
    mock_resp = _create_mock_response(
        content="",
        refusal="Safety policy violation: cannot synthesize code for requested topic."
    )
    with patch("litellm.completion", return_value=mock_resp):
        with pytest.raises(LLMCommunicationError, match="Model refused generation request"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )


def test_finish_reason_length_raises_schema_validation_error() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True)
    mock_resp = _create_mock_response(
        content='{"name": "partial"',
        finish_reason="length",
    )
    with patch("litellm.completion", return_value=mock_resp):
        with pytest.raises(SchemaValidationError, match="finish_reason='length'"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )


def test_empty_choices_raises_schema_validation_error() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True)
    mock_resp = MagicMock()
    mock_resp.choices = []
    with patch("litellm.completion", return_value=mock_resp):
        with pytest.raises(SchemaValidationError, match="contained no choices"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )


def test_empty_content_raises_schema_validation_error() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True)
    mock_resp = _create_mock_response(content="   ")
    with patch("litellm.completion", return_value=mock_resp):
        with pytest.raises(SchemaValidationError, match="empty content"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )


def test_malformed_json_raises_schema_validation_error() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True)
    mock_resp = _create_mock_response(content="{not valid json at all")
    with patch("litellm.completion", return_value=mock_resp):
        with pytest.raises(SchemaValidationError, match="failed schema validation"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )


def test_schema_mismatch_raises_schema_validation_error() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True)
    mock_resp = _create_mock_response(content='{"unrelated_field": "val"}')
    with patch("litellm.completion", return_value=mock_resp):
        with pytest.raises(SchemaValidationError, match="failed schema validation"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )


# =========================================================================
# 6. Provider Error Handling & Bounded Transport Retries
# =========================================================================

def test_auth_error_is_fatal_and_does_not_retry() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True, max_retries=2)
    auth_err = AuthenticationError(
        message="Invalid API Key",
        llm_provider="openai",
        model="gpt-4o-mini",
    )
    with patch("litellm.completion", side_effect=auth_err) as mock_completion:
        with pytest.raises(LLMCommunicationError, match="authentication/permission failure"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )
        assert mock_completion.call_count == 1  # 0 retries


def test_permission_denied_is_fatal_and_does_not_retry() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True, max_retries=2)
    import httpx
    req = httpx.Request("POST", "https://api.openai.com")
    resp = httpx.Response(403, request=req)
    perm_err = PermissionDeniedError(
        message="Account suspended",
        llm_provider="openai",
        model="gpt-4o-mini",
        response=resp,
    )
    with patch("litellm.completion", side_effect=perm_err) as mock_completion:
        with pytest.raises(LLMCommunicationError, match="authentication/permission failure"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )
        assert mock_completion.call_count == 1  # 0 retries


def test_rate_limit_retries_and_exhausts_budget() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True, max_retries=2)
    rate_err = RateLimitError(
        message="Rate limit exceeded",
        llm_provider="openai",
        model="gpt-4o-mini",
    )
    with patch("litellm.completion", side_effect=rate_err) as mock_completion:
        with pytest.raises(LLMCommunicationError, match="LLM transport error after 3 attempts"):
            service.generate_structured(
                prompt="test",
                system_instruction="test",
                response_schema=SimpleTestSchema,
            )
        assert mock_completion.call_count == 3  # 1 initial + 2 retries


def test_transient_error_succeeds_on_retry() -> None:
    service = LiteLLMService(api_key=SecretStr("sk-dummy"), allow_external_transmission=True, max_retries=2)
    timeout_err = Timeout(
        message="Connection timed out",
        model="gpt-4o-mini",
        llm_provider="openai",
    )
    success_resp = _create_mock_response('{"name": "recovered", "score": 95}')

    with patch("litellm.completion", side_effect=[timeout_err, success_resp]) as mock_completion:
        result = service.generate_structured(
            prompt="test",
            system_instruction="test",
            response_schema=SimpleTestSchema,
        )
        assert result.name == "recovered"
        assert result.score == 95
        assert mock_completion.call_count == 2


# =========================================================================
# 7. RunBudgetTracker & Pre-dispatch Request Cap
# =========================================================================

def test_run_budget_tracker_preflight_cap() -> None:
    tracker = RunBudgetTracker(max_requests=2)
    assert tracker.total_requests == 0
    assert tracker.max_requests == 2

    tracker.record_preflight()
    assert tracker.total_requests == 1

    tracker.record_preflight()
    assert tracker.total_requests == 2

    with pytest.raises(LLMCommunicationError, match="Run-scoped LLM request budget exceeded"):
        tracker.record_preflight()

    tracker.reset()
    assert tracker.total_requests == 0


def test_service_enforces_run_budget_cap_pre_dispatch() -> None:
    tracker = RunBudgetTracker(max_requests=2)
    service = LiteLLMService(
        api_key=SecretStr("sk-dummy"),
        allow_external_transmission=True,
        budget_tracker=tracker,
    )
    mock_resp = _create_mock_response('{"name": "req", "score": 1}')

    with patch("litellm.completion", return_value=mock_resp) as mock_completion:
        # Request 1
        service.generate_structured("p", "s", SimpleTestSchema)
        assert tracker.total_requests == 1

        # Request 2
        service.generate_structured("p", "s", SimpleTestSchema)
        assert tracker.total_requests == 2

        # Request 3 should fail before dispatching to litellm.completion
        with pytest.raises(LLMCommunicationError, match="Run-scoped LLM request budget exceeded"):
            service.generate_structured("p", "s", SimpleTestSchema)

        assert mock_completion.call_count == 2


def test_budget_counting_increments_per_retry_attempt() -> None:
    tracker = RunBudgetTracker(max_requests=2)
    service = LiteLLMService(
        api_key=SecretStr("sk-dummy"),
        allow_external_transmission=True,
        budget_tracker=tracker,
        max_retries=2,
    )
    timeout_err = Timeout(message="Timeout", model="gpt-4o-mini", llm_provider="openai")

    with patch("litellm.completion", side_effect=timeout_err) as mock_completion:
        # Attempt 1: consumes budget 1 -> fails with timeout
        # Attempt 2: consumes budget 2 -> fails with timeout
        # Attempt 3: budget check raises LLMCommunicationError before 3rd completion call!
        with pytest.raises(LLMCommunicationError, match="Run-scoped LLM request budget exceeded"):
            service.generate_structured("p", "s", SimpleTestSchema)

        assert mock_completion.call_count == 2
        assert tracker.total_requests == 2


# =========================================================================
# 8. Token Estimation
# =========================================================================

def test_token_estimation_non_empty_and_empty() -> None:
    service = LiteLLMService()
    assert service.estimate_tokens("") == 0
    tokens = service.estimate_tokens("def test_something(): assert 1 == 1")
    assert tokens > 0


def test_token_estimation_fallback_on_failure() -> None:
    service = LiteLLMService()
    with patch("litellm.token_counter", side_effect=RuntimeError("tokenizer unavailable")):
        tokens = service.estimate_tokens("12345678")  # 8 chars -> ~2 tokens
        assert tokens == 2


# =========================================================================
# 9. Sanitization of Secrets
# =========================================================================

def test_secrets_never_leaked_in_exception_or_repr() -> None:
    secret_value = "sk-super-secret-production-key-never-leak"
    service = LiteLLMService(
        model="gpt-4o-mini",
        api_key=SecretStr(secret_value),
        allow_external_transmission=True,
    )
    assert secret_value not in repr(service)
    assert secret_value not in str(service)

    with patch("litellm.completion", side_effect=RuntimeError("Raw connection crash")):
        with pytest.raises(LLMCommunicationError) as exc_info:
            service.generate_structured("p", "s", SimpleTestSchema)
        assert secret_value not in str(exc_info.value)


# =========================================================================
# 10. End-to-End Offline Integration with GenerationService
# =========================================================================

def test_generation_service_with_litellm_adapter_offline() -> None:
    service = LiteLLMService(
        model="gpt-4o-mini",
        api_key=SecretStr("sk-dummy"),
        allow_external_transmission=True,
    )
    raw_json = (
        '{"test_code": "def test_multiply():\\n    assert multiply(2, 3) == 6\\n", '
        '"imports": ["import pytest", "from pkg.calc import multiply"], '
        '"rationale": "Standard multiplication assertion", '
        '"mock_targets": []}'
    )
    mock_resp = _create_mock_response(raw_json)

    with patch("litellm.completion", return_value=mock_resp):
        gen_service = GenerationService(llm_service=service)
        symbol = SymbolContract(
            qualified_name="pkg.calc.multiply",
            symbol_type=SymbolType.FUNCTION,
            file_path=Path("src/pkg/calc.py"),
            line_range=(1, 5),
            signature="def multiply(x: int, y: int) -> int",
            docstring="Multiplies two numbers.",
            dependencies=(),
            is_affected=True,
        )
        plan = ExecutionPlan(
            plan_id="plan-test-1234",
            route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
            target_symbols=(symbol,),
            existing_tests_to_run=(),
            rationale="Unit test generation for affected symbols",
            decision_hash="hash-1234",
        )
        candidates = gen_service.generate(plan)

        assert len(candidates) == 1
        cand = candidates[0]
        assert cand.target_symbol_name == "pkg.calc.multiply"
        assert cand.validation_status == ValidationStatus.PENDING
        assert cand.retry_count == 0
        assert cand.quarantine_reason is None
        assert "assert multiply(2, 3) == 6" in cand.candidate_code
        assert "import pytest" in cand.imports
