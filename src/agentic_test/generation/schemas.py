"""
Pydantic schemas enforcing structured cognitive model outputs for test generation.
Iteration 1.4 Component Architecture.
"""

from typing import Tuple
from pydantic import BaseModel, ConfigDict, Field


class CandidateSynthesisSchema(BaseModel):
    """
    Schema enforcing structured response from LLM for synthesized test candidates.
    Adheres strictly to Pydantic JSON schema generation for structured outputs.
    """
    model_config = ConfigDict(frozen=True)

    imports: Tuple[str, ...] = Field(
        default_factory=tuple,
        description="Explicit import statements required by the test function(s)."
    )
    test_code: str = Field(
        description="Synthesized pytest test function(s) implementing unit test cases."
    )
    rationale: str = Field(
        description="Chain-of-thought reasoning justifying the test scenario and assertions."
    )
    mock_targets: Tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of mock targets or monkeypatched symbols used in the test."
    )
