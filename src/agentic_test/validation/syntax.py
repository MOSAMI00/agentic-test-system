"""
Gate 1 Syntax Validator using pure static AST parsing.
Iteration 1.4 Component Architecture.
"""

import ast
from typing import Tuple

from agentic_test.core.models import TestCandidate, ValidationStatus
from agentic_test.validation.base import BaseValidator
from agentic_test.validation.models import ValidationResult


class SyntaxValidator(BaseValidator):
    """
    Gate 1: Statically verifies that candidate code and imports form valid Python syntax.
    Enforces ast.parse on the combined candidate code without executing it.
    """

    @property
    def gate_name(self) -> str:
        return "GATE_1_SYNTAX"

    def validate(self, candidate: TestCandidate) -> ValidationResult:
        """
        Parses imports and candidate code using Python's standard ast.parse.
        """
        combined_source = self._build_source(candidate)

        try:
            ast.parse(combined_source)
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.PASSED,
                gate=self.gate_name,
                passed=True,
            )
        except SyntaxError as err:
            diag: Tuple[str, ...] = (
                f"Line {err.lineno}: {err.text.strip() if err.text else ''}",
                f"Offset: {err.offset}",
            )
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_SYNTAX,
                gate=self.gate_name,
                passed=False,
                error_message=f"SyntaxError: {err.msg} at line {err.lineno}",
                diagnostics=diag,
            )
        except Exception as err:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_SYNTAX,
                gate=self.gate_name,
                passed=False,
                error_message=f"AST parsing failure: {err}",
                diagnostics=(str(err),),
            )

    def _build_source(self, candidate: TestCandidate) -> str:
        """Combines imports and candidate code into unified parseable source."""
        parts = []
        if candidate.imports:
            parts.append("\n".join(candidate.imports))
        if candidate.candidate_code:
            parts.append(candidate.candidate_code)
        return "\n\n".join(parts)
