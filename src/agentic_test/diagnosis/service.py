"""
Dual-engine failure triage service facade.
Orchestrates deterministic rule classification (Engine 1) with fallback
to cognitive LLM reasoning (Engine 2) across the canonical 7-category taxonomy.
Stage 2 Section 4.3.1.6 [FR-18, FR-19, FR-20], Stage 3 Section 4.4.4.6.
"""

from pathlib import Path
from typing import Optional, Union
import uuid

from agentic_test.core.models import (
    ExecutionEvidence,
    FailureCategory,
    FailureDiagnosis,
    SymbolContract,
    SymbolType,
    TestCandidate,
    TriageEngine,
)
from agentic_test.core.protocols.llm import LLMService
from agentic_test.diagnosis.llm_classifier import CognitiveLLMClassifier
from agentic_test.diagnosis.rules import DeterministicRuleClassifier


class DiagnosisService:
    """
    High-level facade orchestrating dual-engine failure triage.
    Executes fast deterministic rules first; conditionally delegates ambiguous
    failures to cognitive LLM reasoning. Isolates APPLICATION_BUG diagnoses.
    Stage 2 Section 4.3.1.6 [FR-18 - FR-20], Stage 3 Section 4.4.4.6.
    """

    def __init__(
        self,
        rule_classifier: Optional[DeterministicRuleClassifier] = None,
        cognitive_classifier: Optional[CognitiveLLMClassifier] = None,
        llm_service: Optional[LLMService] = None,
    ) -> None:
        """
        Initializes the diagnosis service with injected classifiers or LLM service.

        :param rule_classifier: Optional DeterministicRuleClassifier (Engine 1).
        :param cognitive_classifier: Optional CognitiveLLMClassifier (Engine 2).
        :param llm_service: Optional LLMService used to construct CognitiveLLMClassifier.
        :raises ValueError: If neither cognitive_classifier nor llm_service is supplied.
        """
        self._rule_classifier = rule_classifier or DeterministicRuleClassifier()

        if cognitive_classifier is not None:
            self._cognitive_classifier = cognitive_classifier
        elif llm_service is not None:
            self._cognitive_classifier = CognitiveLLMClassifier(llm_service=llm_service)
        else:
            raise ValueError(
                "DiagnosisService requires either a CognitiveLLMClassifier or an LLMService instance."
            )

    def diagnose(
        self,
        evidence: ExecutionEvidence,
        candidate: Optional[TestCandidate] = None,
        candidate_code: Optional[str] = None,
        target_symbol: Optional[Union[str, SymbolContract]] = None,
        target_source: Optional[str] = None,
        diff_content: Optional[str] = None,
    ) -> FailureDiagnosis:
        """
        Diagnoses execution failure using the two-tier cascade.

        Cascade:
        1. Evaluates DeterministicRuleClassifier (Engine 1) first.
        2. If a deterministic match is found, bypasses Engine 2 entirely.
        3. If ambiguous (None), invokes CognitiveLLMClassifier (Engine 2) exactly once.
        4. Constructs an immutable FailureDiagnosis with UUID4 diagnosis_id,
           evidence link, canonical category, triage engine, and is_application_bug flag.

        :param evidence: Sandbox execution evidence record.
        :param candidate: Optional TestCandidate entity.
        :param candidate_code: Optional explicit test candidate code string.
        :param target_symbol: Optional symbol name string or SymbolContract entity.
        :param target_source: Optional target symbol source code string.
        :param diff_content: Optional unified diff patch string.
        :return: Fully populated, immutable FailureDiagnosis entity.
        """
        # Step 1: Evaluate deterministic rules (Engine 1)
        rule_result = self._rule_classifier.classify(evidence)

        if rule_result is not None:
            return FailureDiagnosis(
                diagnosis_id=str(uuid.uuid4()),
                evidence_id=evidence.evidence_id,
                canonical_category=rule_result.category,
                confidence=rule_result.confidence,
                triage_engine=TriageEngine.DETERMINISTIC_RULE,
                explanation=rule_result.explanation,
                is_application_bug=False,
            )

        # Step 2: Adapt string context to entity contracts if needed for Engine 2
        resolved_symbol_contract: Optional[SymbolContract] = None
        if isinstance(target_symbol, SymbolContract):
            resolved_symbol_contract = target_symbol
        elif isinstance(target_symbol, str):
            resolved_symbol_contract = SymbolContract(
                qualified_name=target_symbol,
                symbol_type=SymbolType.FUNCTION,
                file_path=Path("unknown"),
                line_range=(0, 0),
                signature=target_source or f"def {target_symbol}(): ...",
            )
        elif target_source is not None:
            resolved_symbol_contract = SymbolContract(
                qualified_name="target",
                symbol_type=SymbolType.FUNCTION,
                file_path=Path("unknown"),
                line_range=(0, 0),
                signature=target_source,
            )

        # Step 3: Invoke cognitive LLM triage (Engine 2) exactly once
        cognitive_result = self._cognitive_classifier.classify(
            evidence=evidence,
            candidate=candidate,
            candidate_code=candidate_code,
            target_symbol=resolved_symbol_contract,
            diff_content=diff_content,
        )

        is_bug = cognitive_result.category == FailureCategory.APPLICATION_BUG

        return FailureDiagnosis(
            diagnosis_id=str(uuid.uuid4()),
            evidence_id=evidence.evidence_id,
            canonical_category=cognitive_result.category,
            confidence=cognitive_result.confidence,
            triage_engine=TriageEngine.COGNITIVE_LLM,
            explanation=cognitive_result.explanation,
            is_application_bug=is_bug,
        )
