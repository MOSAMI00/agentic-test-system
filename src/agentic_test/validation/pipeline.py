"""
Multi-Gate Static Candidate Validation Pipeline.
Iteration 1.4 Component Architecture.
"""

from typing import List, Optional, Sequence, Tuple

from agentic_test.core.models import TestCandidate, ValidationStatus
from agentic_test.validation.base import BaseValidator
from agentic_test.validation.models import ValidationResult
from agentic_test.validation.security import SecurityValidator
from agentic_test.validation.syntax import SyntaxValidator


class ValidationPipeline:
    """
    Orchestrates sequential static validation gates (Gate 1 Syntax, Gate 2 Security).
    Applies short-circuit evaluation: fails at the first broken gate.
    Does NOT invoke GenerationService or execute repair loops.

    IMPORTANT SAFETY NOTICE (INV-01):
    Gate 3 (pytest collection) is blocked on host environments and must only be executed
    within an isolated Docker container. A candidate with ValidationStatus.PASSED
    indicates that static syntax and security checks have passed; it does not indicate
    Gate 3 collection has passed, nor does it authorize dynamic test execution.
    """

    def __init__(self, validators: Optional[Sequence[BaseValidator]] = None) -> None:
        if validators is not None:
            self._validators: List[BaseValidator] = list(validators)
        else:
            self._validators = [
                SyntaxValidator(),
                SecurityValidator(),
            ]

    @property
    def validators(self) -> List[BaseValidator]:
        return self._validators

    @staticmethod
    def _reconstruct_candidate(
        candidate: TestCandidate, status: ValidationStatus
    ) -> TestCandidate:
        """
        Reconstructs a TestCandidate with an updated validation_status via validated
        instantiation to prevent validation bypass (such as from model_copy).
        Preserves all candidate fields, identity, retry_count, and quarantine_reason.
        """
        return TestCandidate(
            **{**candidate.__dict__, "validation_status": status}
        )

    def validate_candidate(
        self, candidate: TestCandidate
    ) -> Tuple[TestCandidate, ValidationResult]:
        """
        Runs candidate through configured validation gates sequentially.
        Short-circuits upon first gate failure.
        """
        # If candidate was already quarantined during generation, do not validate
        if candidate.validation_status == ValidationStatus.QUARANTINED:
            quarantine_result = ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.QUARANTINED,
                gate="PRE_VALIDATION",
                passed=False,
                error_message=candidate.quarantine_reason or "Candidate quarantined prior to validation",
            )
            return candidate, quarantine_result

        for validator in self._validators:
            result = validator.validate(candidate)
            if not result.passed:
                # Update candidate validation status to match rejected gate via validated reconstruction
                updated_candidate = self._reconstruct_candidate(candidate, result.status)
                return updated_candidate, result

        # All static gates passed
        passed_result = ValidationResult(
            candidate_id=candidate.candidate_id,
            status=ValidationStatus.PASSED,
            gate="STATIC_PIPELINE",
            passed=True,
        )
        updated_candidate = self._reconstruct_candidate(candidate, ValidationStatus.PASSED)
        return updated_candidate, passed_result

    def validate_candidates(
        self, candidates: Sequence[TestCandidate]
    ) -> List[Tuple[TestCandidate, ValidationResult]]:
        """
        Validates a sequence of test candidates.
        """
        return [self.validate_candidate(candidate) for candidate in candidates]
