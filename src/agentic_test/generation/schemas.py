"""
Pydantic schemas enforcing structured cognitive model outputs for test generation.
Iteration 1.4 Component Architecture.
"""

from typing import Any, Dict, Tuple, Type
from pydantic import BaseModel, ConfigDict, Field


def _enforce_strict_required(schema: Dict[str, Any], model_class: Type[Any]) -> None:
    """Ensures all properties are explicitly listed in required for OpenAI strict compatibility."""
    properties = schema.get("properties", {})
    if properties:
        schema["required"] = list(properties.keys())


class CandidateSynthesisSchema(BaseModel):
    """
    Schema enforcing structured response from LLM for synthesized test candidates.
    Adheres strictly to Pydantic JSON schema generation for structured outputs.
    """
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra=_enforce_strict_required,
    )

    imports: Tuple[str, ...] = Field(
        default_factory=tuple,
        description="Explicit import statements required by the test function(s)."
    )
    test_code: str = Field(
        description="Synthesized pytest test function(s) implementing unit test cases."
    )
    rationale: str = Field(
        description="Concise engineering justification for the test scenario and assertions."
    )
    mock_targets: Tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of mock targets or monkeypatched symbols used in the test."
    )
