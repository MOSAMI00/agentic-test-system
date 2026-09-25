"""
Unit tests for ContextAssembler and secret masking.
Verifies token ceiling enforcement, 3-tier progressive pruning, and credential redaction.
"""

from pathlib import Path
import pytest

from agentic_test.core.models import DiffHunk, ChangeType, SymbolContract, SymbolType
from agentic_test.generation.context import (
    ContextAssembler,
    mask_secrets,
)
from agentic_test.generation.exceptions import ContextBudgetExceededError
from agentic_test.generation.mock_llm import MockLLMService


def _make_symbol(
    qualified_name: str = "pkg.calc.add",
    signature: str = "def add(a: int, b: int) -> int",
    docstring: str = "Adds two integers and returns the sum.",
    dependencies: tuple[str, ...] = ("pkg.calc.validator",),
) -> SymbolContract:
    return SymbolContract(
        qualified_name=qualified_name,
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/pkg/calc.py"),
        line_range=(1, 10),
        signature=signature,
        docstring=docstring,
        dependencies=dependencies,
        is_affected=True,
    )


def test_secret_masking() -> None:
    """Verifies that API keys, GitHub tokens, and auth secrets are masked."""
    raw_text = (
        "Here is the config: api_key='supersecretpassword123'\n"
        "OpenAI key: sk-abcdefghijklmnopqrstuvwxyz1234567890\n"
        "GitHub token: ghp_1234567890abcdefghijklmnopqrstuvwxyz12\n"
        "Bearer token: Bearer abcdef1234567890abcdef1234567890\n"
    )
    masked = mask_secrets(raw_text)
    assert "sk-" not in masked
    assert "ghp_" not in masked
    assert "supersecretpassword123" not in masked
    assert "[REDACTED_SECRET]" in masked


def test_context_assembler_normal_budget() -> None:
    """When context fits comfortably within token budget, all fields are retained."""
    symbol = _make_symbol()
    assembler = ContextAssembler(max_token_budget=1000)

    source_code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    hunk = DiffHunk(
        file_path=Path("src/pkg/calc.py"),
        old_start=1,
        old_lines=3,
        new_start=1,
        new_lines=3,
        change_type=ChangeType.MODIFIED,
        content="@@ -1,3 +1,3 @@\n-def add(a, b):\n+def add(a: int, b: int) -> int:\n     return a + b",
    )

    context = assembler.assemble(
        target_symbol=symbol,
        diff_hunks=[hunk],
        existing_test_samples=["def test_existing(): assert True"],
        source_code_override=source_code,
    )

    assert context.target_symbol == symbol
    assert "def add(a: int, b: int)" in context.source_code
    assert "pkg.calc.validator" in context.dependencies
    assert len(context.existing_test_samples) == 1
    assert context.token_count > 0
    assert context.token_count <= 1000


def test_context_assembler_tier_1_pruning() -> None:
    """Tier 1: Prunes dependencies and test samples when budget is slightly exceeded."""
    symbol = _make_symbol(dependencies=("dep1", "dep2", "dep3"))
    source_code = "def add(a: int, b: int) -> int:\n    '''Docstring.'''\n    return a + b\n"

    # Set budget low enough to force dropping test samples/dependencies
    assembler = ContextAssembler(max_token_budget=60)

    context = assembler.assemble(
        target_symbol=symbol,
        existing_test_samples=["def test_sample_1(): pass\n" * 5],
        source_code_override=source_code,
    )

    assert context.existing_test_samples == ()
    assert context.dependencies == ()
    assert context.token_count <= 60


def test_context_assembler_tier_2_pruning() -> None:
    """Tier 2: Prunes docstrings when budget is still exceeded after Tier 1."""
    long_doc = '"""' + "Very long docstring explaining the function in deep detail. " * 15 + '"""'
    source_code = f"def add(a: int, b: int) -> int:\n    {long_doc}\n    return a + b\n"
    symbol = _make_symbol(docstring=long_doc)

    assembler = ContextAssembler(max_token_budget=70)

    context = assembler.assemble(
        target_symbol=symbol,
        source_code_override=source_code,
    )

    assert "[Docstring pruned]" in context.source_code
    assert context.token_count <= 70


def test_context_assembler_tier_3_exceeded_error() -> None:
    """Tier 3: Raises ContextBudgetExceededError if minimal core exceeds budget."""
    symbol = _make_symbol()
    huge_source = "def add(a: int, b: int) -> int:\n" + ("    x = 1\n" * 1000)

    assembler = ContextAssembler(max_token_budget=50)

    with pytest.raises(ContextBudgetExceededError, match="exceeding ceiling of 50"):
        assembler.assemble(
            target_symbol=symbol,
            source_code_override=huge_source,
        )
