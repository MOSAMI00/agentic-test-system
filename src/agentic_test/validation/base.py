"""
Abstract base class for validation pipeline gates.
Iteration 1.4 Component Architecture.
"""

from abc import ABC, abstractmethod

from agentic_test.core.models import TestCandidate
from agentic_test.validation.models import ValidationResult


class BaseValidator(ABC):
    """
    Abstract base specification for an individual static validation gate.
    """

    @property
    @abstractmethod
    def gate_name(self) -> str:
        """Unique identifier of the validation gate."""
        ...

    @abstractmethod
    def validate(self, candidate: TestCandidate) -> ValidationResult:
        """
        Evaluates test candidate against the validation criteria.

        :param candidate: The synthesized TestCandidate to validate.
        :return: ValidationResult indicating whether the candidate passed.
        """
        ...
