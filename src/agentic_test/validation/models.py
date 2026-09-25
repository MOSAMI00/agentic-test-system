"""
Validation domain models and results for test candidate screening.
Iteration 1.4 Component Architecture.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

from agentic_test.core.models import ValidationStatus


class ValidationResult(BaseModel):
    """
    Immutable outcome of evaluating a candidate against a validation gate.
    """
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    status: ValidationStatus
    gate: str
    passed: bool
    error_message: Optional[str] = None
    diagnostics: Tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
