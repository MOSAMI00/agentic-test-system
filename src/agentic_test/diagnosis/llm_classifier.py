"""
Cognitive LLM failure classifier (Engine 2).
Disambiguates complex execution failures across the canonical taxonomy
using structured LLM synthesis via the LLMService protocol.
Stage 2 Section 4.3.1.6 [FR-19], Stage 3 Section 4.4.4.6.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentic_test.core.models import (
    DiffHunk,
    ExecutionEvidence,
    FailureCategory,
    SymbolContract,
    TestCandidate,
)
from agentic_test.core.protocols.llm import LLMService
from agentic_test.generation.exceptions import (
    LLMCommunicationError,
    SchemaValidationError,
)


class DiagnosisResponseSchema(BaseModel):
    """
    Immutable Pydantic model for cognitive failure diagnosis LLM structured output.
    Stage 3 Section 4.4.4.6.
    """
    model_config = ConfigDict(frozen=True)

    category: FailureCategory
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)


class CognitiveLLMClassifier:
    """
    Cognitive LLM-based failure classifier (Engine 2).
    Disambiguates complex assertion and target-application errors
    across canonical taxonomy using structured cognitive reasoning.
    Stage 2 §4.3.1.6 [FR-19], Stage 3 §4.4.4.6.
    """

    CONFIDENCE_THRESHOLD: float = 0.70

    # Categories reserved exclusively for deterministic Engine 1 triage
    _RESERVED_DETERMINISTIC_CATEGORIES = (
        FailureCategory.ENVIRONMENT_FAILURE,
        FailureCategory.CONFIGURATION_ERROR,
    )

    def __init__(self, llm_service: LLMService) -> None:
        self._llm_service = llm_service

    def _build_prompt(
        self,
        evidence: ExecutionEvidence,
        candidate_code: Optional[str] = None,
        target_symbol: Optional[SymbolContract] = None,
        diff_content: Optional[str] = None,
    ) -> str:
        """Constructs a deterministic prompt from structured fields without file I/O."""
        sections = [
            "=== EXECUTION EVIDENCE ===",
            f"Exit Code: {evidence.exit_code}",
            f"Timed Out: {evidence.timed_out}",
        ]
        if evidence.duration_sec is not None:
            sections.append(f"Duration: {evidence.duration_sec:.2f}s")
        if evidence.traceback:
            sections.append(f"Traceback:\n{evidence.traceback}")
        if evidence.stderr:
            sections.append(f"Stderr:\n{evidence.stderr}")
        if evidence.stdout:
            sections.append(f"Stdout:\n{evidence.stdout}")

        if candidate_code:
            sections.append(f"=== TEST CANDIDATE CODE ===\n{candidate_code}")

        if target_symbol:
            sections.append(
                f"=== TARGET SYMBOL ===\n"
                f"Qualified Name: {target_symbol.qualified_name}\n"
                f"Symbol Type: {target_symbol.symbol_type.value}\n"
                f"Signature: {target_symbol.signature}\n"
                f"Docstring: {target_symbol.docstring or 'None'}"
            )

        if diff_content:
            sections.append(f"=== RECENT CODE MODIFICATIONS (DIFF) ===\n{diff_content}")

        sections.append(
            "=== TRIAGE INSTRUCTIONS ===\n"
            "Analyze the execution failure and disambiguate the root cause across the canonical taxonomy:\n"
            "- APPLICATION_BUG: The test correctly exposed a defect, regression, or unmet requirement in target application code.\n"
            "- TEST_OUTDATED: The test expectations are obsolete due to intentional changes in application behavior or contracts.\n"
            "- INVALID_GENERATED_TEST: The test candidate has flawed logic, invalid assertions, or wrong assumptions.\n"
            "- UNKNOWN: Ambiguous failure where evidence is insufficient to determine root cause.\n\n"
            "Provide your diagnosis strictly conforming to the requested JSON schema: category, confidence (0.0 to 1.0), and explanation."
        )

        return "\n\n".join(sections)

    def classify(
        self,
        evidence: ExecutionEvidence,
        candidate: Optional[TestCandidate] = None,
        candidate_code: Optional[str] = None,
        target_symbol: Optional[SymbolContract] = None,
        diff_hunk: Optional[DiffHunk] = None,
        diff_content: Optional[str] = None,
    ) -> DiagnosisResponseSchema:
        """
        Classifies execution failure using cognitive LLM triage.
        Calls llm_service.generate_structured exactly once with temperature=0.0.
        Catches communication, schema, and provider errors to return deterministic fallback results.

        :param evidence: Execution evidence captured from sandbox run.
        :param candidate: Optional TestCandidate entity.
        :param candidate_code: Optional explicit test candidate code string.
        :param target_symbol: Optional SymbolContract representing the tested entity.
        :param diff_hunk: Optional DiffHunk representing recent patch changes.
        :param diff_content: Optional raw unified diff string.
        :return: Validated DiagnosisResponseSchema.
        """
        resolved_candidate_code = candidate_code or (candidate.candidate_code if candidate else None)
        resolved_diff_content = diff_content or (diff_hunk.content if diff_hunk else None)

        prompt = self._build_prompt(
            evidence=evidence,
            candidate_code=resolved_candidate_code,
            target_symbol=target_symbol,
            diff_content=resolved_diff_content,
        )

        system_instruction = (
            "You are an expert Python QA and testing diagnostic architect. "
            "Analyze the provided test execution failure evidence and determine the precise root cause. "
            "Respond strictly conforming to the JSON response schema."
        )

        try:
            raw_response = self._llm_service.generate_structured(
                prompt=prompt,
                system_instruction=system_instruction,
                response_schema=DiagnosisResponseSchema,
                temperature=0.0,
            )
        except (LLMCommunicationError, TimeoutError) as err:
            return DiagnosisResponseSchema(
                category=FailureCategory.LLM_FAILURE,
                confidence=0.0,
                explanation=f"LLM communication failed: {err}",
            )
        except (SchemaValidationError, ValidationError) as err:
            return DiagnosisResponseSchema(
                category=FailureCategory.LLM_FAILURE,
                confidence=0.0,
                explanation=f"LLM response schema validation failed: {err}",
            )
        except Exception as err:
            return DiagnosisResponseSchema(
                category=FailureCategory.UNKNOWN,
                confidence=0.0,
                explanation=f"Cognitive triage encountered unexpected error: {err}",
            )

        # Policy 1: Normalize reserved deterministic categories to UNKNOWN
        if raw_response.category in self._RESERVED_DETERMINISTIC_CATEGORIES:
            return DiagnosisResponseSchema(
                category=FailureCategory.UNKNOWN,
                confidence=raw_response.confidence,
                explanation=(
                    f"Category {raw_response.category.value} is reserved for deterministic rules; "
                    f"normalized to UNKNOWN: {raw_response.explanation}"
                ),
            )

        # Policy 2: Enforce confidence threshold (confidence < 0.70 -> UNKNOWN)
        if raw_response.confidence < self.CONFIDENCE_THRESHOLD:
            return DiagnosisResponseSchema(
                category=FailureCategory.UNKNOWN,
                confidence=raw_response.confidence,
                explanation=(
                    f"Low confidence ({raw_response.confidence:.2f} < {self.CONFIDENCE_THRESHOLD:.2f}): "
                    f"{raw_response.explanation}"
                ),
            )

        return raw_response
