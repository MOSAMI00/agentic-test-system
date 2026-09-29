"""
Unit and integration tests for Gate 3 isolated containerized pytest collection validation.
Stage 2 Section 4.3.2.4 (UC-04), Stage 3 Section 4.4.4.4.
Verifies sandbox confinement, zero host execution, outcome parsing, and fail-closed cleanup.
"""

from pathlib import Path
import subprocess
from typing import Any
import pytest

from agentic_test.core.models import TestCandidate, ValidationStatus
from agentic_test.core.protocols.sandbox import ExecutionRawResult, SandboxConfig
from agentic_test.execution.mock_sandbox import MockSandboxManager
from agentic_test.execution.staging import CandidateStagingArea, CollectionStagingArea, StagingValidationError
from agentic_test.validation.collection import CollectionValidator


def _make_candidate(
    code: str = "def test_example():\n    assert True\n",
    imports: tuple[str, ...] = ("import pytest",),
    status: ValidationStatus = ValidationStatus.PENDING,
    candidate_id: str = "cand-coll-001",
) -> TestCandidate:
    return TestCandidate(
        candidate_id=candidate_id,
        run_id="run-coll-001",
        target_symbol_name="pkg.math.add",
        test_file_path=Path("tests/test_math.py"),
        candidate_code=code,
        imports=imports,
        validation_status=status,
    )


def test_collection_success_with_collected_items(tmp_path: Path) -> None:
    """Gate 3 passes when pytest collection returns exit code 0 and >= 1 test items."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(
            exit_code=0,
            stdout="tests/test_math.py::test_example\n1 test collected in 0.01s\n",
            stderr="",
            duration_sec=0.2,
            timed_out=False,
            oom_killed=False,
        )
    )

    validator = CollectionValidator(
        sandbox_manager=sandbox,
        staging_root=tmp_path,
    )
    cand = _make_candidate()
    result = validator.validate(cand, static_gates_passed=True)

    assert result.passed is True
    assert result.status == ValidationStatus.PASSED
    assert result.gate == "GATE_3_COLLECTION"
    assert result.candidate_id == cand.candidate_id
    assert result.error_message is None
    assert any("test_example" in d for d in result.diagnostics)


def test_collection_exit_0_with_zero_collected_items(tmp_path: Path) -> None:
    """Exit code 0 is insufficient: zero collected items must map to REJECTED_COLLECTION."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(
            exit_code=0,
            stdout="collected 0 items\n\n======================== no tests collected ========================\n",
            stderr="",
            duration_sec=0.1,
            timed_out=False,
            oom_killed=False,
        )
    )

    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)
    cand = _make_candidate()
    result = validator.validate(cand, static_gates_passed=True)

    assert result.passed is False
    assert result.status == ValidationStatus.REJECTED_COLLECTION
    assert "zero test items were collected" in (result.error_message or "")


def test_collection_import_error(tmp_path: Path) -> None:
    """ImportError or ModuleNotFoundError during collection maps to REJECTED_COLLECTION."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(
            exit_code=2,
            stdout="ERROR collecting tests/test_math.py\n",
            stderr="ModuleNotFoundError: No module named 'unknown_package_xyz'\n",
            duration_sec=0.1,
            timed_out=False,
            oom_killed=False,
        )
    )

    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)
    cand = _make_candidate(imports=("import unknown_package_xyz",))
    result = validator.validate(cand, static_gates_passed=True)

    assert result.passed is False
    assert result.status == ValidationStatus.REJECTED_COLLECTION
    assert "ModuleNotFoundError" in (result.error_message or "")


def test_collection_syntax_error(tmp_path: Path) -> None:
    """Syntax error during collection maps to REJECTED_COLLECTION with diagnostic."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(
            exit_code=2,
            stdout="ERROR collecting tests/test_math.py\n",
            stderr="SyntaxError: invalid syntax in test definition\n",
            duration_sec=0.1,
            timed_out=False,
            oom_killed=False,
        )
    )

    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)
    cand = _make_candidate()
    result = validator.validate(cand, static_gates_passed=True)

    assert result.passed is False
    assert result.status == ValidationStatus.REJECTED_COLLECTION
    assert "SyntaxError" in (result.error_message or "")


def test_collection_missing_fixture_diagnostic(tmp_path: Path) -> None:
    """Missing fixture during collection maps to REJECTED_COLLECTION with diagnostic."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(
            exit_code=1,
            stdout="tests/test_math.py:5: in test_example\nE   fixture 'db_session_fixture' not found\n",
            stderr="",
            duration_sec=0.1,
            timed_out=False,
            oom_killed=False,
        )
    )

    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)
    cand = _make_candidate()
    result = validator.validate(cand, static_gates_passed=True)

    assert result.passed is False
    assert result.status == ValidationStatus.REJECTED_COLLECTION
    assert "fixture 'db_session_fixture' not found" in (result.error_message or "")


def test_collection_timeout(tmp_path: Path) -> None:
    """Timeout during collection maps to REJECTED_COLLECTION."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(
            exit_code=124,
            stdout="",
            stderr="command timed out",
            duration_sec=10.0,
            timed_out=True,
            oom_killed=False,
        )
    )

    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path, timeout_sec=10.0)
    cand = _make_candidate()
    result = validator.validate(cand, static_gates_passed=True)

    assert result.passed is False
    assert result.status == ValidationStatus.REJECTED_COLLECTION
    assert "timed out" in (result.error_message or "").lower()


def test_collection_oom_killed(tmp_path: Path) -> None:
    """Container OOM during collection maps to REJECTED_COLLECTION."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(
            exit_code=137,
            stdout="",
            stderr="Killed (out of memory)",
            duration_sec=1.5,
            timed_out=False,
            oom_killed=True,
        )
    )

    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)
    cand = _make_candidate()
    result = validator.validate(cand, static_gates_passed=True)

    assert result.passed is False
    assert result.status == ValidationStatus.REJECTED_COLLECTION
    assert "out-of-memory" in (result.error_message or "").lower()


def test_candidate_never_imported_or_executed_on_host(tmp_path: Path) -> None:
    """
    Verifies Safety Invariant INV-01: candidate code containing malicious host constructs
    is never imported or executed on the host Python process.
    """
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(
            exit_code=0,
            stdout="tests/test_math.py::test_bomb\n1 test collected in 0.01s\n",
            stderr="",
            duration_sec=0.1,
            timed_out=False,
            oom_killed=False,
        )
    )

    # Code that would crash the host or tamper with host globals if imported
    deadly_code = (
        "import sys\n"
        "if not hasattr(sys, '_container_sandbox'):\n"
        "    raise SystemExit('HOST EXECUTION BREACH DETECTED!')\n"
        "def test_bomb(): pass\n"
    )

    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)
    cand = _make_candidate(code=deadly_code)

    # Must complete safely without raising SystemExit or importing on host
    result = validator.validate(cand, static_gates_passed=True)
    assert result.passed is True


def test_only_gate_1_and_2_passed_candidates_enter_collection(tmp_path: Path) -> None:
    """
    Verifies that candidates without certified Gates 1 & 2 pass are rejected immediately
    at the collection boundary without container provisioning.
    """
    sandbox = MockSandboxManager()
    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)

    cand = _make_candidate()
    result = validator.validate(cand, static_gates_passed=False)

    assert result.passed is False
    assert result.status == ValidationStatus.REJECTED_COLLECTION
    assert "static syntax and security gates" in (result.error_message or "")
    # Sandbox was never invoked
    assert sandbox.call_count == 0


def test_collection_staging_area_rejects_unverified_candidates(tmp_path: Path) -> None:
    """CollectionStagingArea and stage_candidate_for_collection reject static_gates_passed=False."""
    staging = CollectionStagingArea(staging_root=tmp_path / "coll_staging")
    cand = _make_candidate()

    with pytest.raises(StagingValidationError, match="static_gates_passed=True is required"):
        staging.stage_candidate(cand, static_gates_passed=False)

    with pytest.raises(StagingValidationError, match="static_gates_passed=True is required"):
        staging.stage_candidate_for_collection(cand, static_gates_passed=False)


def test_cleanup_on_every_path(tmp_path: Path) -> None:
    """Verifies that container and ephemeral staging mounts are cleaned up on all paths."""
    sandbox = MockSandboxManager()
    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)
    cand = _make_candidate()

    # 1. Success path
    sandbox.enqueue_result(
        ExecutionRawResult(exit_code=0, stdout="tests/t.py::test_ok\n1 test collected\n", stderr="", duration_sec=0.1, timed_out=False, oom_killed=False)
    )
    res1 = validator.validate(cand, static_gates_passed=True)
    assert res1.passed is True
    assert sandbox.active_containers == []
    assert list(tmp_path.iterdir()) == []

    # 2. Failure path (exit code 1)
    sandbox.enqueue_result(
        ExecutionRawResult(exit_code=1, stdout="", stderr="ImportError: missing", duration_sec=0.1, timed_out=False, oom_killed=False)
    )
    res2 = validator.validate(cand, static_gates_passed=True)
    assert res2.passed is False
    assert sandbox.active_containers == []
    assert list(tmp_path.iterdir()) == []

    # 3. Timeout path
    sandbox.enqueue_result(
        ExecutionRawResult(exit_code=124, stdout="", stderr="timed out", duration_sec=10.0, timed_out=True, oom_killed=False)
    )
    res3 = validator.validate(cand, static_gates_passed=True)
    assert res3.passed is False
    assert sandbox.active_containers == []
    assert list(tmp_path.iterdir()) == []

    # 4. Exception path (sandbox execution error)
    sandbox.enqueue_error(RuntimeError("Docker connection dropped"))
    res4 = validator.validate(cand, static_gates_passed=True)
    assert res4.passed is False
    assert sandbox.active_containers == []
    assert list(tmp_path.iterdir()) == []


def test_collection_timeout_is_bounded_to_10s(tmp_path: Path) -> None:
    """Verifies collection timeout ceiling is bounded to <= 10.0 seconds."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(exit_code=0, stdout="tests/t.py::t\n1 test collected\n", stderr="", duration_sec=0.1, timed_out=False, oom_killed=False)
    )

    # Attempt to request 120s timeout
    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path, timeout_sec=120.0)
    assert validator.timeout_sec == 10.0

    validator.validate(_make_candidate(), static_gates_passed=True)
    created_configs = list(sandbox.created_configs.values())
    assert len(created_configs) == 1
    assert created_configs[0].timeout_sec == 10.0


def test_no_direct_subprocess_or_docker_usage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies that no direct subprocess or system commands are called from CollectionValidator."""
    def _forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Forbidden host subprocess execution invoked!")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)

    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(exit_code=0, stdout="tests/t.py::t\n1 test collected\n", stderr="", duration_sec=0.1, timed_out=False, oom_killed=False)
    )

    validator = CollectionValidator(sandbox_manager=sandbox, staging_root=tmp_path)
    result = validator.validate(_make_candidate(), static_gates_passed=True)
    assert result.passed is True


def test_collection_sandbox_confinement_policies(tmp_path: Path) -> None:
    """Verifies that SandboxConfig adheres to isolation policies (network, mounts, memory)."""
    sandbox = MockSandboxManager()
    sandbox.enqueue_result(
        ExecutionRawResult(exit_code=0, stdout="tests/t.py::t\n1 test collected\n", stderr="", duration_sec=0.1, timed_out=False, oom_killed=False)
    )

    source_dir = tmp_path / "app_src"
    source_dir.mkdir()

    validator = CollectionValidator(
        sandbox_manager=sandbox,
        source_root=source_dir,
        staging_root=tmp_path / "staging_parent",
    )
    validator.validate(_make_candidate(), static_gates_passed=True)

    config = list(sandbox.created_configs.values())[0]
    assert config.network_disabled is True
    assert config.read_write_mounts == {}
    assert config.read_only_mounts[source_dir.resolve()] == "/workspace/src"
    assert config.memory_limit == "512m"
    assert config.cpu_quota == 1.0
    assert config.pids_limit == 100
    assert "PYTHONPATH" in config.environment_vars
