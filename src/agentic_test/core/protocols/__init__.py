"""
Layer 4 Protocol Abstractions.
Stage 3 Section 4.4.2.
"""

from agentic_test.core.protocols.analyzer import CodeAnalyzer
from agentic_test.core.protocols.llm import LLMService

__all__ = ["CodeAnalyzer", "LLMService"]
