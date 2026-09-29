"""
Unit tests for deterministic rule-based failure triage classifier (Engine 1).
WBS 1.6.1A / FR-18.
"""

from typing import Optional
from unittest.mock import patch
import pytest
from pydantic import ValidationError

from agentic_test.core.models import ExecutionEvidence, FailureCategory
from agentic_test.diagnosis.rules import (
    DeterministicRuleClassifier,
    RuleClassificationResult,
)


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
