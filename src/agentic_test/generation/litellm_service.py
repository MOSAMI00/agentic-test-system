"""
LiteLLM adapter service implementing LLMService protocol for cognitive synthesis.
Stage 3 Section 4.4.2.3 Listing 4.3 and Iteration 1.4 Component Architecture.
"""

import os
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union

# Enforce local offline cost map and disable background telemetry before import
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    Timeout,
)
from pydantic import BaseModel, SecretStr

from agentic_test.core.protocols.llm import LLMService
from agentic_test.generation.exceptions import LLMCommunicationError, SchemaValidationError

T = TypeVar("T", bound=BaseModel)


class RunBudgetTracker:
    """
    Run-scoped request budget counter.
    Tracks outbound attempts across generation calls to prevent runaway API spend.
    Enforces pre-dispatch counting for every outbound attempt.
    """

    def __init__(self, max_requests: int = 5) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        self._max_requests: int = max_requests
        self._total_requests: int = 0

    @property
    def max_requests(self) -> int:
        """Maximum allowable request attempts for the run."""
        return self._max_requests

    @property
    def total_requests(self) -> int:
        """Total outbound request attempts recorded so far."""
        return self._total_requests

    def record_preflight(self) -> None:
        """
        Records an outbound request attempt before dispatch.
        Raises LLMCommunicationError if budget would be exceeded.
        """
        if self._total_requests >= self._max_requests:
            raise LLMCommunicationError(
                f"Run-scoped LLM request budget exceeded: cap is {self._max_requests}, "
                f"attempted {self._total_requests + 1}."
            )
        self._total_requests += 1

    def reset(self) -> None:
        """Resets the recorded request count to zero."""
        self._total_requests = 0


class LiteLLMService(LLMService):
    """
    Adapter implementing LLMService protocol via LiteLLM.
    Enforces privacy boundary, SecretStr credentials, run budget caps,
    structured output schema enforcement, refusal detection, and bounded retries.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[Union[SecretStr, str]] = None,
        allow_external_transmission: bool = False,
        budget_tracker: Optional[RunBudgetTracker] = None,
        max_retries: int = 2,
        timeout: float = 30.0,
    ) -> None:
        self._model: str = model
        self._allow_external_transmission: bool = allow_external_transmission
        if isinstance(api_key, str):
            self._api_key: Optional[SecretStr] = SecretStr(api_key)
        else:
            self._api_key = api_key
        self._budget_tracker: RunBudgetTracker = (
            budget_tracker if budget_tracker is not None else RunBudgetTracker(max_requests=5)
        )
        self._max_retries: int = max_retries
        self._timeout: float = timeout

        # Suppress LiteLLM console output and telemetry
        litellm.telemetry = False
        litellm.suppress_debug_info = True
        setattr(litellm, "set_verbose", False)

    @property
    def model(self) -> str:
        """Configured model name."""
        return self._model

    @property
    def allow_external_transmission(self) -> bool:
        """Whether external provider network transmission is authorized."""
        return self._allow_external_transmission

    @property
    def budget_tracker(self) -> RunBudgetTracker:
        """Run-scoped budget tracker."""
        return self._budget_tracker

    @property
    def timeout(self) -> float:
        """Configured request timeout in seconds."""
        return self._timeout

    @staticmethod
    def _is_local_model(model: str) -> bool:
        """Determines if the target model is hosted locally without external network transit."""
        local_prefixes = ("ollama/", "ollama_chat/", "vllm/", "local/", "hosted_vllm/")
        return model.lower().startswith(local_prefixes)

    @staticmethod
    def _get_provider_env_var(model: str) -> str:
        """Maps model name to expected provider environment variable."""
        m = model.lower()
        if m.startswith("anthropic/") or "claude" in m:
            return "ANTHROPIC_API_KEY"
        if m.startswith("gemini/") or "gemini" in m:
            return "GEMINI_API_KEY"
        return "OPENAI_API_KEY"

    def _resolve_api_key(self) -> Optional[str]:
        """Resolves API key string from SecretStr or environment, sanitizing exceptions."""
        if self._api_key is not None:
            return self._api_key.get_secret_value()

        if self._is_local_model(self._model):
            return None

        env_var = self._get_provider_env_var(self._model)
        key_from_env = os.environ.get(env_var)
        if not key_from_env:
            raise LLMCommunicationError(
                f"Missing credentials: No API key provided for model '{self._model}'. "
                f"Pass api_key as SecretStr or configure environment variable {env_var}."
            )
        return key_from_env

    @staticmethod
    def _extract_choice_data(choice: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extracts content, finish_reason, and refusal from a choice object or dict."""
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason is None and isinstance(choice, dict):
            finish_reason = choice.get("finish_reason")

        message = getattr(choice, "message", None)
        if message is None and isinstance(choice, dict):
            message = choice.get("message")

        content: Optional[str] = None
        refusal: Optional[str] = None
        if message is not None:
            content = getattr(message, "content", None)
            refusal = getattr(message, "refusal", None)
            if isinstance(message, dict):
                if content is None:
                    content = message.get("content")
                if refusal is None:
                    refusal = message.get("refusal")

        return content, finish_reason, refusal

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        response_schema: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> T:
        """
        Transmits prompt to cognitive model via LiteLLM and parses response into validated schema.
        Enforces privacy gate, request cap, structured schema, refusal abort, and bounded retries.
        """
        # 1. Privacy Pre-flight Gate
        if not self._allow_external_transmission and not self._is_local_model(self._model):
            raise LLMCommunicationError(
                f"External LLM transmission disallowed (allow_external_transmission=False) for model '{self._model}'."
            )

        # 2. Credential Resolution
        api_key_str = self._resolve_api_key()

        # 3. Payload Construction
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.__name__,
                "schema": response_schema.model_json_schema(),
                "strict": True,
            },
        }

        # 4. Dispatch with Pre-flight Counting and Bounded Transport Retries
        attempts = 0
        max_attempts = 1 + self._max_retries
        response: Any = None

        while attempts < max_attempts:
            # Pre-dispatch budget gate (increments attempt count before network call)
            self._budget_tracker.record_preflight()
            attempts += 1

            try:
                response = litellm.completion(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    api_key=api_key_str,
                    timeout=self._timeout,
                    num_retries=0,
                    **{"no-log": True},
                )
                break
            except (AuthenticationError, PermissionDeniedError) as err:
                status = getattr(err, "status_code", 401)
                raise LLMCommunicationError(
                    f"Provider authentication/permission failure for model '{self._model}' (HTTP {status})."
                ) from None
            except (RateLimitError, Timeout, InternalServerError, APIConnectionError) as err:
                if attempts >= max_attempts:
                    raise LLMCommunicationError(
                        f"LLM transport error after {attempts} attempts for model '{self._model}': {type(err).__name__}"
                    ) from None
                continue
            except Exception as err:
                err_msg = str(err).lower()
                if "missing credentials" in err_msg or "unauthorized" in err_msg or "authentication" in err_msg:
                    raise LLMCommunicationError(
                        f"Provider authentication error for model '{self._model}'."
                    ) from None
                if attempts >= max_attempts:
                    raise LLMCommunicationError(
                        f"LLM communication error after {attempts} attempts for model '{self._model}': {type(err).__name__}"
                    ) from None
                continue

        # 5. Response Validation and Structure Parsing
        choices = getattr(response, "choices", None)
        if not choices or len(choices) == 0:
            raise SchemaValidationError("LLM response contained no choices.")

        choice = choices[0]
        content, finish_reason, refusal = self._extract_choice_data(choice)

        # Refusal is a non-repairable terminal abort
        if refusal:
            raise LLMCommunicationError(
                f"Model refused generation request: {refusal}"
            )

        # Truncation is a schema validation defect
        if finish_reason == "length":
            raise SchemaValidationError(
                "LLM output truncated: response exceeded max_tokens (finish_reason='length')."
            )

        if not content or not content.strip():
            raise SchemaValidationError("LLM response contained empty content.")

        # Local Pydantic schema validation
        try:
            return response_schema.model_validate_json(content)
        except Exception as err:
            raise SchemaValidationError(
                f"LLM response failed schema validation for {response_schema.__name__}: {err}"
            ) from err

    def estimate_tokens(self, text: str) -> int:
        """
        Computes estimated token count for text using target model tokenizers.
        Falls back to deterministic offline character heuristic (~4 chars/token).
        """
        if not text:
            return 0
        try:
            return int(litellm.token_counter(model=self._model, text=text))
        except Exception:
            return max(1, len(text) // 4)
