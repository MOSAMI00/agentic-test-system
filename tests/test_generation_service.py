"""
Unit tests for GenerationService.
Verifies target symbol bounding, R_max=2 pre-validation repair loop, and quarantine handling.
"""

from pathlib import Path
import pytest

from agentic_test.core.models import (
    ChangeType,
    ExecutionPlan,
    SymbolContract,
    SymbolType,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.generation.context import ContextAssembler
from agentic_test.generation.exceptions import SchemaValidationError
from agentic_test.generation.mock_llm import MockLLMService
from agentic_test.generation.schemas import CandidateSynthesisSchema
from agentic_test.generation.service import GenerationService


def _make_symbol(name: str = "pkg.calc.multiply") -> SymbolContract:
    return SymbolContract(
        qualified_name=name,
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/pkg/calc.py"),
        line_range=(1, 5),
        signature=f"def {name.split('.')[-1]}(x: int, y: int) -> int",
        docstring="Multiplies two numbers.",
        dependencies=(),
        is_affected=True,
    )


def _make_plan(target_symbols: tuple[SymbolContract, ...] = ()) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan-test-1234",
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        target_symbols=target_symbols,
        existing_tests_to_run=(),
        rationale="Unit test generation for affected symbols",
        decision_hash="hash-1234",
    )


def test_generation_service_successful_synthesis() -> None:
    """Generates TestCandidate with PENDING status on valid synthesis."""
    symbol = _make_symbol("pkg.calc.multiply")
    plan = _make_plan(target_symbols=(symbol,))

    response = CandidateSynthesisSchema(
        imports=("import pytest", "from pkg.calc import multiply"),
        test_code="def test_multiply():\n    assert multiply(2, 3) == 6\n",
        rationale="Standard multiplication assertion",
        mock_targets=(),
    )
    mock_llm = MockLLMService(canned_responses=[response])
    service = GenerationService(llm_service=mock_llm)

    candidates = service.generate(plan)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.target_symbol_name == "pkg.calc.multiply"
    assert cand.validation_status == ValidationStatus.PENDING
    assert cand.retry_count == 0
    assert cand.quarantine_reason is None
    assert cand.run_id == "plan-test-1234"
    assert cand.test_file_path == Path("tests/test_calc.py")
    assert "assert multiply(2, 3) == 6" in cand.candidate_code
    assert "import pytest" in cand.imports


def test_generation_service_repair_loop_success_on_attempt_1() -> None:
    """When first attempt has syntax error, repairs successfully on first retry (R_max=2)."""
    symbol = _make_symbol("pkg.calc.add")
    plan = _make_plan(target_symbols=(symbol,))

    # Attempt 0: syntax error
    broken_response = CandidateSynthesisSchema(
        imports=(),
        test_code="def test_broken(:\n    assert True",
        rationale="Broken syntax",
    )
    # Attempt 1 (repair 1): valid syntax
    valid_response = CandidateSynthesisSchema(
        imports=("import pytest",),
        test_code="def test_broken():\n    assert True\n",
        rationale="Fixed syntax",
    )

    mock_llm = MockLLMService(canned_responses=[broken_response, valid_response])
    service = GenerationService(llm_service=mock_llm)

    candidates = service.generate(plan)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.validation_status == ValidationStatus.PENDING
    assert cand.retry_count == 1
    assert mock_llm.call_count == 2
    # Verify repair prompt contains previous error instruction
    second_call_prompt = mock_llm.calls[1]["prompt"]
    assert "=== REPAIR INSTRUCTION ===" in second_call_prompt
    assert "SyntaxError" in second_call_prompt


def test_generation_service_repair_budget_exhausted_quarantine() -> None:
    """When all repairs fail (initial + 2 repairs = 3 attempts), candidate is QUARANTINED."""
    symbol = _make_symbol("pkg.calc.divide")
    plan = _make_plan(target_symbols=(symbol,))

    # 3 broken responses (exceeds R_max = 2)
    broken_1 = CandidateSynthesisSchema(
        imports=(),
        test_code="def broken_1(: pass",
        rationale="Broken 1",
    )
    broken_2 = CandidateSynthesisSchema(
        imports=(),
        test_code="def broken_2(: pass",
        rationale="Broken 2",
    )
    broken_3 = CandidateSynthesisSchema(
        imports=(),
        test_code="def broken_3(: pass",
        rationale="Broken 3",
    )

    mock_llm = MockLLMService(canned_responses=[broken_1, broken_2, broken_3])
    service = GenerationService(llm_service=mock_llm)

    candidates = service.generate(plan)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.validation_status == ValidationStatus.QUARANTINED
    assert cand.quarantine_reason == "EXHAUSTED_GENERATION_REPAIRS"
    assert cand.retry_count == 2
    assert mock_llm.call_count == 3


def test_generation_service_context_budget_exceeded_quarantine() -> None:
    """When context assembly exceeds token ceiling, candidate is immediately quarantined."""
    symbol = _make_symbol("pkg.huge.process")
    plan = _make_plan(target_symbols=(symbol,))

    mock_llm = MockLLMService()
    # Tiny token budget to force ContextBudgetExceededError
    assembler = ContextAssembler(max_token_budget=10)
    service = GenerationService(llm_service=mock_llm, context_assembler=assembler)

    candidates = service.generate(plan)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.validation_status == ValidationStatus.QUARANTINED
    assert cand.quarantine_reason is not None
    assert "CONTEXT_BUDGET_EXCEEDED" in cand.quarantine_reason
    assert mock_llm.call_count == 0  # No LLM calls wasted
