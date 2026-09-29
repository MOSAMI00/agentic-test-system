"""
Unit tests for deterministic rule-based failure triage classifier (Engine 1).
WBS 1.6.1A / FR-18.
"""

from pathlib import Path
from typing import Optional
from unittest.mock import patch
import pytest
from pydantic import ValidationError

from agentic_test.core.models import (
    ChangeType,
    DiffHunk,
    ExecutionEvidence,
    FailureCategory,
    SymbolContract,
    SymbolType,
    TestCandidate,
)
from agentic_test.diagnosis.llm_classifier import (
    CognitiveLLMClassifier,
    DiagnosisResponseSchema,
)
from agentic_test.diagnosis.rules import (
    DeterministicRuleClassifier,
    RuleClassificationResult,
)
from agentic_test.generation.exceptions import (
    LLMCommunicationError,
    SchemaValidationError,
)
from agentic_test.generation.mock_llm import MockLLMService


def _make_evidence(
    *,
    evidence_id: str = "ev-test-1",
    run_id: str = "run-test-1",
    candidate_id: Optional[str] = "cand-test-1",
    exit_code: int = 1,
    stdout: str = "",
    stderr: str = "",
    traceback: Optional[str] = None,
    timed_out: bool = False,
    duration_sec: float = 1.0,
) -> ExecutionEvidence:
    """Helper factory to create ExecutionEvidence fixtures."""
    return ExecutionEvidence(
        evidence_id=evidence_id,
        run_id=run_id,
        candidate_id=candidate_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        traceback=traceback,
        timed_out=timed_out,
        duration_sec=duration_sec,
    )


# =============================================================================
# 1. Timeout Classification Tests (Rule 2a)
# =============================================================================

def test_classify_timeout_flag() -> None:
    """Verifies timed_out=True is classified as ENVIRONMENT_FAILURE with confidence 1.0."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(exit_code=0, timed_out=True)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.ENVIRONMENT_FAILURE
    assert result.confidence == 1.0
    assert "timeout" in result.explanation.lower()


def test_classify_timeout_exit_code_124() -> None:
    """Verifies exit_code=124 is classified as ENVIRONMENT_FAILURE with confidence 1.0."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(exit_code=124, timed_out=False)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.ENVIRONMENT_FAILURE
    assert result.confidence == 1.0
    assert "124" in result.explanation


# =============================================================================
# 2. Out-of-Memory (OOM) Classification Tests (Rule 2b)
# =============================================================================

def test_classify_exit_code_137_oom() -> None:
    """Verifies exit_code=137 is classified as ENVIRONMENT_FAILURE with confidence 1.0."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(exit_code=137)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.ENVIRONMENT_FAILURE
    assert result.confidence == 1.0
    assert "out-of-memory" in result.explanation.lower()


@pytest.mark.parametrize(
    "oom_snippet",
    [
        "Fatal error: Out of memory",
        "Process terminated: MemoryError",
        "Container killed by oom",
        "Linux kernel: oom-killer invoked",
        "oom kill detected on container",
        "Killed process 12345 (pytest)",
        "/bin/sh: line 1:   123 Killed                  pytest",
    ],
)
def test_classify_explicit_oom_markers(oom_snippet: str) -> None:
    """Verifies various explicit OOM markers in output trigger ENVIRONMENT_FAILURE."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(exit_code=1, stderr=oom_snippet)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.ENVIRONMENT_FAILURE
    assert result.confidence == 1.0


# =============================================================================
# 3. Import / Configuration Error Tests (Rule 2c)
# =============================================================================

def test_classify_module_not_found_error_in_stderr() -> None:
    """Verifies ModuleNotFoundError in stderr triggers CONFIGURATION_ERROR."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=2,
        stderr="ModuleNotFoundError: No module named 'non_existent_dep'",
    )
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.CONFIGURATION_ERROR
    assert result.confidence == 1.0
    assert "configuration error" in result.explanation.lower()


def test_classify_module_not_found_error_in_stdout() -> None:
    """Verifies ModuleNotFoundError in stdout triggers CONFIGURATION_ERROR."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=1,
        stdout="E   ModuleNotFoundError: No module named 'numpy'",
    )
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.CONFIGURATION_ERROR
    assert result.confidence == 1.0


def test_classify_import_error_in_traceback() -> None:
    """Verifies ImportError in traceback field triggers CONFIGURATION_ERROR."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=1,
        traceback="ImportError: cannot import name 'UnknownSymbol' from 'agentic_test'",
    )
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.CONFIGURATION_ERROR
    assert result.confidence == 1.0


# =============================================================================
# 4. Pytest Collection & Fixture Error Tests (Rule 2d)
# =============================================================================

@pytest.mark.parametrize(
    "collection_error_text",
    [
        "ERROR collecting tests/test_candidate.py",
        "Interrupted: 1 error during collection",
        "collected 0 items / 1 error",
        "PytestCollectionWarning: cannot collect test class 'TestHelper'",
        "collection failure while importing test module",
        "failed on collection of tests",
    ],
)
def test_classify_pytest_collection_errors(collection_error_text: str) -> None:
    """Verifies pytest collection errors trigger INVALID_GENERATED_TEST."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(exit_code=2, stdout=collection_error_text)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.INVALID_GENERATED_TEST
    assert result.confidence == 1.0
    assert "collection or fixture" in result.explanation.lower()


@pytest.mark.parametrize(
    "fixture_error_text",
    [
        "fixture 'unregistered_db_fixture' not found",
        "E   FixtureLookupError: fixture 'custom_mock' not found",
        "ScopeMismatch: You tried to access the function scoped fixture",
        "recursive dependency involving fixture 'a' detected",
        "has no fixture named 'invalid_fixture'",
        "ERROR at setup of test_something: fixture setup failed",
        "ERROR at teardown of test_something: teardown failed",
    ],
)
def test_classify_pytest_fixture_errors(fixture_error_text: str) -> None:
    """Verifies pytest fixture diagnostic errors trigger INVALID_GENERATED_TEST."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(exit_code=1, stderr=fixture_error_text)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.INVALID_GENERATED_TEST
    assert result.confidence == 1.0


# =============================================================================
# 5. Generated-Test Syntax & NameError Tests (Rule 2e)
# =============================================================================

def test_classify_generated_test_syntax_error_python_traceback() -> None:
    """Verifies SyntaxError in test file triggers INVALID_GENERATED_TEST."""
    classifier = DeterministicRuleClassifier()
    tb = (
        '  File "/workspace/tests/test_candidate.py", line 5\n'
        '    def test_foo(\n'
        '                ^\n'
        'SyntaxError: unexpected EOF while parsing\n'
    )
    evidence = _make_evidence(exit_code=1, traceback=tb)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.INVALID_GENERATED_TEST
    assert result.confidence == 1.0
    assert "syntax or name error" in result.explanation.lower()


def test_classify_generated_test_syntax_error_pytest_inline() -> None:
    """Verifies pytest inline syntax error reporting on test file triggers INVALID_GENERATED_TEST."""
    classifier = DeterministicRuleClassifier()
    output = "tests/test_sample.py:12: SyntaxError: invalid syntax"
    evidence = _make_evidence(exit_code=2, stderr=output)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.INVALID_GENERATED_TEST
    assert result.confidence == 1.0


def test_classify_generated_test_name_error_pytest_format() -> None:
    """Verifies NameError occurring directly inside test function triggers INVALID_GENERATED_TEST."""
    classifier = DeterministicRuleClassifier()
    tb = (
        "tests/test_candidate.py:15: in test_compute\n"
        "    result = undefined_variable + 1\n"
        "E   NameError: name 'undefined_variable' is not defined\n"
    )
    evidence = _make_evidence(exit_code=1, traceback=tb)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.INVALID_GENERATED_TEST
    assert result.confidence == 1.0


def test_classify_generated_test_name_error_standard_traceback() -> None:
    """Verifies standard Python traceback with NameError in test triggers INVALID_GENERATED_TEST."""
    classifier = DeterministicRuleClassifier()
    tb = (
        "Traceback (most recent call last):\n"
        '  File "/workspace/tests/test_logic.py", line 22, in test_something\n'
        "    bad_call()\n"
        "NameError: name 'bad_call' is not defined\n"
    )
    evidence = _make_evidence(exit_code=1, traceback=tb)
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.INVALID_GENERATED_TEST
    assert result.confidence == 1.0


# =============================================================================
# 6. Target Application Runtime Failures & Ambiguous Cases (Rule 2f -> None)
# =============================================================================

def test_classify_name_error_in_target_application_returns_none() -> None:
    """
    Verifies NameError occurring inside application code (not test code)
    is treated as a target application runtime failure and returns None.
    """
    classifier = DeterministicRuleClassifier()
    tb = (
        "tests/test_candidate.py:10: in test_call_app\n"
        "    app_function()\n"
        "src/calculator/service.py:42: in app_function\n"
        "    return internal_var * 2\n"
        "E   NameError: name 'internal_var' is not defined\n"
    )
    evidence = _make_evidence(exit_code=1, traceback=tb)
    result = classifier.classify(evidence)

    assert result is None


def test_classify_syntax_error_in_target_application_returns_none() -> None:
    """Verifies SyntaxError in target application code returns None."""
    classifier = DeterministicRuleClassifier()
    tb = (
        '  File "/workspace/src/calculator/app.py", line 15\n'
        '    def calculate(\n'
        '                 ^\n'
        'SyntaxError: invalid syntax\n'
    )
    evidence = _make_evidence(exit_code=1, traceback=tb)
    result = classifier.classify(evidence)

    assert result is None


def test_classify_assertion_error_returns_none() -> None:
    """Verifies assertion failure returns None for cognitive LLM triage."""
    classifier = DeterministicRuleClassifier()
    tb = (
        "tests/test_candidate.py:18: in test_math\n"
        "    assert add(2, 2) == 5\n"
        "E   assert 4 == 5\n"
        "E   AssertionError\n"
    )
    evidence = _make_evidence(exit_code=1, traceback=tb)
    result = classifier.classify(evidence)

    assert result is None


@pytest.mark.parametrize(
    "runtime_error_tb",
    [
        "src/app.py:10: in div\n    return 1 / 0\nE   ZeroDivisionError: division by zero",
        "src/repo.py:20: in get\n    return store[key]\nE   KeyError: 'missing_key'",
        "src/service.py:35: in validate\n    raise ValueError('bad input')\nE   ValueError: bad input",
        "src/handler.py:50: in process\n    raise RuntimeError('fatal error')\nE   RuntimeError: fatal error",
    ],
)
def test_classify_target_runtime_errors_return_none(runtime_error_tb: str) -> None:
    """Verifies application runtime exceptions return None for cognitive triage."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(exit_code=1, traceback=runtime_error_tb)
    result = classifier.classify(evidence)

    assert result is None


def test_classify_clean_run_returns_none() -> None:
    """Verifies clean run (exit_code=0, no errors) returns None."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(exit_code=0, stdout="1 passed in 0.05s", timed_out=False)
    result = classifier.classify(evidence)

    assert result is None


def test_classify_generic_unknown_exit_returns_none() -> None:
    """Verifies non-zero exit without recognized deterministic error returns None."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=1,
        stdout="pytest run failed with unformatted internal error",
        stderr="custom error occurred",
    )
    result = classifier.classify(evidence)

    assert result is None


# =============================================================================
# 7. Priority & Precedence Tests
# =============================================================================

def test_precedence_timeout_over_oom_and_import() -> None:
    """Verifies timeout (Rule 2a) takes precedence over OOM and import errors."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=124,
        timed_out=True,
        stdout="Out of memory\nModuleNotFoundError: No module named 'foo'",
        stderr="AssertionError",
    )
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.ENVIRONMENT_FAILURE
    assert "timeout" in result.explanation.lower()


def test_precedence_oom_over_import_and_assertion() -> None:
    """Verifies OOM (Rule 2b) takes precedence over import error and assertion error."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=137,
        timed_out=False,
        stdout="ModuleNotFoundError: No module named 'bar'",
        stderr="AssertionError in test_something",
    )
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.ENVIRONMENT_FAILURE
    assert "out-of-memory" in result.explanation.lower()


def test_precedence_import_over_collection_error() -> None:
    """Verifies ModuleNotFoundError (Rule 2c) takes precedence over collection error (Rule 2d)."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=2,
        stdout="ERROR collecting tests/test_foo.py\nModuleNotFoundError: No module named 'missing_pkg'",
    )
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.CONFIGURATION_ERROR
    assert "configuration error" in result.explanation.lower()


def test_precedence_collection_over_syntax_error() -> None:
    """Verifies collection error (Rule 2d) takes precedence over syntax error (Rule 2e)."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=2,
        stdout=(
            "Interrupted: 1 error during collection\n"
            "tests/test_foo.py:1: SyntaxError: invalid syntax"
        ),
    )
    result = classifier.classify(evidence)

    assert result is not None
    assert result.category == FailureCategory.INVALID_GENERATED_TEST
    assert "collection or fixture" in result.explanation.lower()


# =============================================================================
# 8. Immutability, Safety & Isolation Tests
# =============================================================================

def test_result_immutability() -> None:
    """Verifies RuleClassificationResult is immutable and cannot be modified."""
    result = RuleClassificationResult(
        category=FailureCategory.ENVIRONMENT_FAILURE,
        confidence=1.0,
        explanation="Test explanation",
    )

    with pytest.raises(ValidationError):
        # Attempting in-place attribute assignment must raise ValidationError under frozen=True
        setattr(result, "confidence", 0.5)

    with pytest.raises(ValidationError):
        setattr(result, "category", FailureCategory.UNKNOWN)


def test_no_subprocess_or_external_calls_during_classification() -> None:
    """Verifies DeterministicRuleClassifier performs zero subprocess, network, or OS exec calls."""
    classifier = DeterministicRuleClassifier()
    evidence = _make_evidence(
        exit_code=1,
        stderr="ModuleNotFoundError: No module named 'external'",
    )

    with patch("subprocess.run") as mock_sub_run, \
         patch("subprocess.Popen") as mock_sub_popen, \
         patch("os.system") as mock_os_sys, \
         patch("socket.socket") as mock_socket:

        result = classifier.classify(evidence)

        assert result is not None
        mock_sub_run.assert_not_called()
        mock_sub_popen.assert_not_called()
        mock_os_sys.assert_not_called()
        mock_socket.assert_not_called()


# =============================================================================
# 9. Cognitive LLM Classifier Tests (Engine 2 / Slice 1.6.1B)
# =============================================================================

def test_cognitive_high_confidence_application_bug() -> None:
    """Verifies high-confidence APPLICATION_BUG is returned intact."""
    canned = DiagnosisResponseSchema(
        category=FailureCategory.APPLICATION_BUG,
        confidence=0.92,
        explanation="Target function violates contract on negative inputs.",
    )
    mock_llm = MockLLMService(canned_responses=[canned])
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(
        exit_code=1,
        traceback="AssertionError: assert calculate(-1) == 0",
    )
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.APPLICATION_BUG
    assert result.confidence == 0.92
    assert "Target function violates contract" in result.explanation
    assert mock_llm.call_count == 1


def test_cognitive_high_confidence_test_outdated() -> None:
    """Verifies high-confidence TEST_OUTDATED is returned intact."""
    canned = DiagnosisResponseSchema(
        category=FailureCategory.TEST_OUTDATED,
        confidence=0.88,
        explanation="Symbol signature and return type updated in recent commit.",
    )
    mock_llm = MockLLMService(canned_responses=[canned])
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(
        exit_code=1,
        traceback="TypeError: calculate() takes 2 arguments but 3 were given",
    )
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.TEST_OUTDATED
    assert result.confidence == 0.88
    assert mock_llm.call_count == 1


def test_cognitive_high_confidence_invalid_generated_test() -> None:
    """Verifies high-confidence INVALID_GENERATED_TEST is returned intact."""
    canned = DiagnosisResponseSchema(
        category=FailureCategory.INVALID_GENERATED_TEST,
        confidence=0.85,
        explanation="Test assertion assumes deprecated behavior not present in API.",
    )
    mock_llm = MockLLMService(canned_responses=[canned])
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(exit_code=1, traceback="AssertionError: assert None is not None")
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.INVALID_GENERATED_TEST
    assert result.confidence == 0.85
    assert mock_llm.call_count == 1


def test_cognitive_low_confidence_normalized_to_unknown() -> None:
    """Verifies response with confidence < 0.70 is normalized to UNKNOWN."""
    canned = DiagnosisResponseSchema(
        category=FailureCategory.APPLICATION_BUG,
        confidence=0.65,
        explanation="Ambiguous failure, could be application regression or flaky test.",
    )
    mock_llm = MockLLMService(canned_responses=[canned])
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(exit_code=1, traceback="AssertionError")
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.UNKNOWN
    assert result.confidence == 0.65
    assert "Low confidence (0.65 < 0.70)" in result.explanation
    assert mock_llm.call_count == 1


@pytest.mark.parametrize(
    "reserved_cat",
    [
        FailureCategory.ENVIRONMENT_FAILURE,
        FailureCategory.CONFIGURATION_ERROR,
    ],
)
def test_cognitive_normalizes_reserved_deterministic_categories_to_unknown(
    reserved_cat: FailureCategory,
) -> None:
    """
    Verifies that if the LLM emits a deterministic-owned category,
    it is normalized to UNKNOWN because deterministic rules own those categories.
    """
    canned = DiagnosisResponseSchema(
        category=reserved_cat,
        confidence=0.95,
        explanation="Environment or config issue detected by model.",
    )
    mock_llm = MockLLMService(canned_responses=[canned])
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(exit_code=1, stderr="Something went wrong")
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.UNKNOWN
    assert result.confidence == 0.95
    assert "reserved for deterministic rules" in result.explanation
    assert mock_llm.call_count == 1


def test_cognitive_schema_validation_error_handled_as_llm_failure() -> None:
    """Verifies that schema validation failures in the LLM service return LLM_FAILURE."""
    mock_llm = MockLLMService()
    mock_llm.enqueue_error(SchemaValidationError("Missing required field 'explanation'"))
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(exit_code=1, traceback="AssertionError")
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.LLM_FAILURE
    assert result.confidence == 0.0
    assert "schema validation failed" in result.explanation.lower()
    assert mock_llm.call_count == 1


def test_cognitive_communication_error_handled_as_llm_failure() -> None:
    """Verifies that network/gateway communication errors return LLM_FAILURE without crashing."""
    mock_llm = MockLLMService()
    mock_llm.enqueue_error(LLMCommunicationError("503 Service Unavailable: upstream rate limit"))
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(exit_code=1, traceback="AssertionError")
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.LLM_FAILURE
    assert result.confidence == 0.0
    assert "communication failed" in result.explanation.lower()
    assert mock_llm.call_count == 1


def test_cognitive_timeout_error_handled_as_llm_failure() -> None:
    """Verifies that API timeouts return LLM_FAILURE without crashing."""
    mock_llm = MockLLMService()
    mock_llm.enqueue_error(TimeoutError("Request timed out after 30 seconds"))
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(exit_code=1, traceback="AssertionError")
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.LLM_FAILURE
    assert result.confidence == 0.0
    assert "communication failed" in result.explanation.lower()
    assert mock_llm.call_count == 1


def test_cognitive_unexpected_exception_handled_as_unknown() -> None:
    """Verifies that generic unhandled exceptions return UNKNOWN without crashing."""
    mock_llm = MockLLMService()
    mock_llm.enqueue_error(RuntimeError("Unexpected thread termination"))
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(exit_code=1, traceback="AssertionError")
    result = classifier.classify(evidence)

    assert result.category == FailureCategory.UNKNOWN
    assert result.confidence == 0.0
    assert "unexpected error" in result.explanation.lower()
    assert mock_llm.call_count == 1


def test_cognitive_deterministic_prompt_contents_and_parameters() -> None:
    """Verifies that prompt context is assembled deterministically with temperature=0.0."""
    canned = DiagnosisResponseSchema(
        category=FailureCategory.APPLICATION_BUG,
        confidence=0.90,
        explanation="Regression in target logic.",
    )
    mock_llm = MockLLMService(canned_responses=[canned])
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)

    evidence = _make_evidence(
        exit_code=1,
        traceback="AssertionError: assert 1 == 2",
        stderr="Traceback (most recent call last): ...",
        stdout="collected 1 item\n1 failed",
        duration_sec=2.5,
    )
    candidate = TestCandidate(
        candidate_id="cand-1",
        run_id="run-1",
        target_symbol_name="calculate",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_calc(): assert calculate() == 2",
    )
    target_symbol = SymbolContract(
        qualified_name="calculator.calculate",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/calculator.py"),
        line_range=(10, 20),
        signature="def calculate(x: int) -> int",
        docstring="Computes the calculation.",
    )
    diff = DiffHunk(
        file_path=Path("src/calculator.py"),
        old_start=12,
        old_lines=3,
        new_start=12,
        new_lines=4,
        change_type=ChangeType.MODIFIED,
        content="@@ -12,3 +12,4 @@\n- return x + 1\n+ return x + 2",
    )

    result = classifier.classify(
        evidence=evidence,
        candidate=candidate,
        target_symbol=target_symbol,
        diff_hunk=diff,
    )

    assert result.category == FailureCategory.APPLICATION_BUG
    assert mock_llm.call_count == 1

    call = mock_llm.calls[0]
    assert call["temperature"] == 0.0
    assert call["response_schema"] == DiagnosisResponseSchema
    prompt = call["prompt"]

    # Verify structured elements in prompt
    assert "Exit Code: 1" in prompt
    assert "Duration: 2.50s" in prompt
    assert "AssertionError: assert 1 == 2" in prompt
    assert "def test_calc(): assert calculate() == 2" in prompt
    assert "calculator.calculate" in prompt
    assert "def calculate(x: int) -> int" in prompt
    assert "Computes the calculation." in prompt
    assert "@@ -12,3 +12,4 @@" in prompt


def test_cognitive_no_file_io_or_code_execution() -> None:
    """Verifies that CognitiveLLMClassifier performs zero file I/O, subprocess, or network calls."""
    canned = DiagnosisResponseSchema(
        category=FailureCategory.APPLICATION_BUG,
        confidence=0.88,
        explanation="Valid bug diagnosis",
    )
    mock_llm = MockLLMService(canned_responses=[canned])
    classifier = CognitiveLLMClassifier(llm_service=mock_llm)
    evidence = _make_evidence(exit_code=1, traceback="AssertionError")

    with patch("builtins.open") as mock_open, \
         patch("subprocess.run") as mock_sub_run, \
         patch("socket.socket") as mock_socket:

        result = classifier.classify(evidence)

        assert result.category == FailureCategory.APPLICATION_BUG
        mock_open.assert_not_called()
        mock_sub_run.assert_not_called()
        mock_socket.assert_not_called()


def test_diagnosis_response_schema_validation_and_immutability() -> None:
    """Verifies that DiagnosisResponseSchema validates inputs and enforces immutability."""
    # Valid model
    valid_schema = DiagnosisResponseSchema(
        category=FailureCategory.APPLICATION_BUG,
        confidence=0.85,
        explanation="Valid explanation",
    )
    assert valid_schema.confidence == 0.85

    # Out of range confidence (> 1.0)
    with pytest.raises(ValidationError):
        DiagnosisResponseSchema(
            category=FailureCategory.APPLICATION_BUG,
            confidence=1.1,
            explanation="Invalid confidence",
        )

    # Out of range confidence (< 0.0)
    with pytest.raises(ValidationError):
        DiagnosisResponseSchema(
            category=FailureCategory.APPLICATION_BUG,
            confidence=-0.1,
            explanation="Invalid confidence",
        )

    # Empty explanation
    with pytest.raises(ValidationError):
        DiagnosisResponseSchema(
            category=FailureCategory.APPLICATION_BUG,
            confidence=0.8,
            explanation="",
        )

    # Immutability
    with pytest.raises(ValidationError):
        setattr(valid_schema, "confidence", 0.5)

    with pytest.raises(ValidationError):
        setattr(valid_schema, "category", FailureCategory.UNKNOWN)
