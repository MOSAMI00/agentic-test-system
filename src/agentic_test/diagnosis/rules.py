"""
Deterministic rule-based failure triage classifier (Engine 1).
Evaluates ExecutionEvidence fields and tracebacks using deterministic
rules and regex patterns without calling cognitive models or dynamic execution.
Stage 2 Section 4.3.1.6 [FR-18], Stage 3 Section 4.4.4.6.
"""

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agentic_test.core.models import ExecutionEvidence, FailureCategory


class RuleClassificationResult(BaseModel):
    """
    Immutable typed result of deterministic rule classification (Engine 1).
    Stage 3 Section 4.4.4.6.
    """
    model_config = ConfigDict(frozen=True)

    category: FailureCategory
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    explanation: str


class DeterministicRuleClassifier:
    """
    Deterministic rule-based failure triage classifier (Engine 1).
    Evaluates ExecutionEvidence fields and tracebacks using deterministic
    rules and regex patterns without calling cognitive models or dynamic execution.
    Stage 2 §4.3.1.6 [FR-18], Stage 3 §4.4.4.6.
    """

    # OOM regex patterns (case-insensitive)
    _OOM_PATTERNS = (
        re.compile(r"\bout of memory\b", re.IGNORECASE),
        re.compile(r"\bmemoryerror\b", re.IGNORECASE),
        re.compile(r"\boom[- ]?kill\w*\b", re.IGNORECASE),
        re.compile(r"\bcontainer killed by oom\b", re.IGNORECASE),
        re.compile(r"\bkilled process \d+", re.IGNORECASE),
        re.compile(r"(?:^|\n|\s)Killed(?:\s|\n|$)", re.IGNORECASE),
    )

    # Import / configuration error patterns
    _IMPORT_PATTERNS = (
        re.compile(r"\bModuleNotFoundError:\s*No module named\b"),
        re.compile(r"\bModuleNotFoundError\b"),
        re.compile(r"\bImportError:\s*cannot import name\b"),
        re.compile(r"\bImportError\b"),
    )

    # Collection and fixture error patterns
    _COLLECTION_FIXTURE_PATTERNS = (
        re.compile(r"ERROR collecting\b"),
        re.compile(r"\bcollection error\b", re.IGNORECASE),
        re.compile(r"\bcollection failure\b", re.IGNORECASE),
        re.compile(r"\bfailed on collection\b", re.IGNORECASE),
        re.compile(r"Interrupted:\s*\d+\s*errors?\s*during\s*collection", re.IGNORECASE),
        re.compile(r"collected 0 items\s*/\s*\d+\s*error", re.IGNORECASE),
        re.compile(r"\bPytestCollectionWarning\b"),
        re.compile(r"fixture\s+['\"][^'\"]+['\"]\s+not found", re.IGNORECASE),
        re.compile(r"\bFixtureLookupError\b"),
        re.compile(r"\bScopeMismatch\b"),
        re.compile(r"recursive dependency involving fixture", re.IGNORECASE),
        re.compile(r"has no fixture named\b", re.IGNORECASE),
        re.compile(r"ERROR at setup of\b", re.IGNORECASE),
        re.compile(r"ERROR at teardown of\b", re.IGNORECASE),
    )

    # Syntax / Name error signatures
    _SYNTAX_ERROR_PATTERN = re.compile(r"\b(?:SyntaxError|IndentationError|TabError)\b")
    _NAME_ERROR_PATTERN = re.compile(r"\bNameError\b")

    @staticmethod
    def _is_test_path(path_str: str) -> bool:
        """Determines if a file path belongs to test code rather than production source."""
        normalized = path_str.replace("\\", "/").lower()
        parts = normalized.split("/")
        filename = parts[-1]
        if any(part in ("tests", "test") for part in parts[:-1]):
            return True
        if filename.startswith("test_") or filename.endswith("_test.py"):
            return True
        if "candidate" in filename:
            return True
        return False

    @classmethod
    def _is_oom(cls, text: str) -> bool:
        """Returns True if any explicit OOM signature is present in the text."""
        return any(pat.search(text) for pat in cls._OOM_PATTERNS)

    @classmethod
    def _is_import_error(cls, text: str) -> bool:
        """Returns True if ModuleNotFoundError or ImportError is present in the text."""
        return any(pat.search(text) for pat in cls._IMPORT_PATTERNS)

    @classmethod
    def _is_collection_or_fixture_error(cls, text: str) -> bool:
        """Returns True if a pytest collection or fixture resolution error is detected."""
        return any(pat.search(text) for pat in cls._COLLECTION_FIXTURE_PATTERNS)

    @classmethod
    def _is_syntax_or_name_error_in_test(cls, text: str) -> bool:
        """
        Determines if a SyntaxError or NameError occurred and is attributable
        to the generated test rather than target application code.
        """
        has_syntax = bool(cls._SYNTAX_ERROR_PATTERN.search(text))
        has_name = bool(cls._NAME_ERROR_PATTERN.search(text))

        if not has_syntax and not has_name:
            return False

        # If SyntaxError, check if associated with a test file
        if has_syntax:
            lines = text.splitlines()
            for idx, line in enumerate(lines):
                if cls._SYNTAX_ERROR_PATTERN.search(line):
                    start_idx = max(0, idx - 5)
                    window = "\n".join(lines[start_idx : idx + 1])
                    file_matches = re.findall(r'File "([^"]+)"', window) + re.findall(
                        r"([^\s:\n]+\.py):\d+", window
                    )
                    if file_matches:
                        if cls._is_test_path(file_matches[-1]):
                            return True
                        # Explicitly in application code (not test)
                        return False
                    if any(kw in window.lower() for kw in ("test_", "_test.py", "tests/")):
                        return True

        # If NameError, inspect the innermost (last) traceback frame
        if has_name:
            pytest_frames = re.findall(r"([^\s:\n]+\.py):\d+:\s*in\s*([^\n]+)", text)
            py_frames = re.findall(r'File "([^"]+)", line \d+, in ([^\n]+)', text)
            inline_frames = re.findall(r"([^\s:\n]+\.py):\d+:\s*(?:E\s+)?NameError", text)

            if pytest_frames:
                innermost_path = pytest_frames[-1][0]
                return cls._is_test_path(innermost_path)
            if py_frames:
                innermost_path = py_frames[-1][0]
                return cls._is_test_path(innermost_path)
            if inline_frames:
                return cls._is_test_path(inline_frames[-1])

        return False

    def classify(self, evidence: ExecutionEvidence) -> Optional[RuleClassificationResult]:
        """
        Classifies execution evidence using deterministic rules in strict priority order.

        Priority order:
        1. evidence.timed_out is True OR exit_code == 124 -> ENVIRONMENT_FAILURE (1.0)
        2. exit_code == 137 OR explicit OOM marker -> ENVIRONMENT_FAILURE (1.0)
        3. ModuleNotFoundError or ImportError -> CONFIGURATION_ERROR (1.0)
        4. Collection/fixture/import-time generated-test error -> INVALID_GENERATED_TEST (1.0)
        5. SyntaxError or NameError attributable to the generated test -> INVALID_GENERATED_TEST (1.0)
        6. AssertionError or target-application runtime failure -> None (defer to LLM triage)

        :param evidence: Execution evidence captured from sandbox run.
        :return: Typed RuleClassificationResult with confidence 1.0, or None if ambiguous.
        """
        # Rule 2a: Timeout
        if evidence.timed_out or evidence.exit_code == 124:
            return RuleClassificationResult(
                category=FailureCategory.ENVIRONMENT_FAILURE,
                confidence=1.0,
                explanation="Execution timed out (timeout flag set or exit code 124).",
            )

        combined_text = f"{evidence.stdout or ''}\n{evidence.stderr or ''}\n{evidence.traceback or ''}"

        # Rule 2b: OOM
        if evidence.exit_code == 137 or self._is_oom(combined_text):
            return RuleClassificationResult(
                category=FailureCategory.ENVIRONMENT_FAILURE,
                confidence=1.0,
                explanation="Execution terminated due to out-of-memory condition (exit code 137 or OOM marker).",
            )

        # Rule 2c: ModuleNotFoundError or ImportError
        if self._is_import_error(combined_text):
            return RuleClassificationResult(
                category=FailureCategory.CONFIGURATION_ERROR,
                confidence=1.0,
                explanation="Environment configuration error: missing module or unresolvable import detected.",
            )

        # Rule 2d: Collection / Fixture / Setup error
        if self._is_collection_or_fixture_error(combined_text):
            return RuleClassificationResult(
                category=FailureCategory.INVALID_GENERATED_TEST,
                confidence=1.0,
                explanation="Generated test failed pytest collection or fixture resolution.",
            )

        # Rule 2e: SyntaxError or NameError attributable to generated test
        if self._is_syntax_or_name_error_in_test(combined_text):
            return RuleClassificationResult(
                category=FailureCategory.INVALID_GENERATED_TEST,
                confidence=1.0,
                explanation="Generated test failed due to a syntax or name error attributable to the test code.",
            )

        # Rule 2f: AssertionError, target application runtime failures, or ambiguous exit codes
        return None
