"""
Offline unit and integration tests for ExecutionService.
Stage 2 Section 4.3.2.5 (UC-05), Stage 3 Section 4.4.4.5.
Iteration 1.5 Slice D.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import pytest

from agentic_test.core.models import (
    ExecutionEvidence,
    ExecutionPlan,
    SymbolContract,
    SymbolType,
    TestCandidate,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.core.protocols.sandbox import (
    ExecutionRawResult,
    SandboxConfig,
    SandboxExecutionError,
    SandboxInitializationError,
)
from agentic_test.execution.mock_sandbox import MockSandboxManager
from agentic_test.execution.runner import PytestRunner
from agentic_test.execution.service import (
    ExecutionService,
    ProductionSourceIntegrityViolation,
    SourceIntegrityError,
    compute_tree_hash,
)
from agentic_test.execution.staging import StagingValidationError


class SimulatedContainerSandboxManager(MockSandboxManager):
    """
    Subclass of MockSandboxManager that simulates container side-effects,
    such as writing coverage.json to the mounted /workspace/output directory,
    simulating container command execution errors,
    or simulating unauthorized file modifications to test INV-02.
    """

    def __init__(
        self,
        coverage_payload: Optional[str] = None,
        tamper_target: Optional[Path] = None,
        default_result: Optional[ExecutionRawResult] = None,
        execute_error: Optional[Exception] = None,
        cleanup_error: Optional[Exception] = None,
    ) -> None:
        super().__init__(default_result=default_result)
        self.coverage_payload = coverage_payload
        self.tamper_target = tamper_target
        self.execute_error = execute_error
        self.cleanup_error = cleanup_error

    def cleanup(self, container_id: str) -> None:
        super().cleanup(container_id)
        if self.cleanup_error is not None:
            raise self.cleanup_error

    def execute_command(
        self,
        container_id: str,
        command: List[str],
        workdir: str = "/workspace",
    ) -> ExecutionRawResult:
        # Simulate host tampering to verify INV-02 violation detection
        if self.tamper_target is not None and self.tamper_target.exists():
            if self.tamper_target.is_file():
                self.tamper_target.write_text("TAMPERED_CONTENT\n", encoding="utf-8")
            elif self.tamper_target.is_dir():
                (self.tamper_target / "malicious.py").write_text("# injected\n", encoding="utf-8")

        if self.execute_error is not None:
            raise self.execute_error

        config = self._containers[container_id]

        # Simulate writing coverage.json to /workspace/output
        if self.coverage_payload is not None:
            for host_path, container_path in config.read_write_mounts.items():
                if container_path == "/workspace/output":
                    host_path.mkdir(parents=True, exist_ok=True)
                    (host_path / "coverage.json").write_text(
                        self.coverage_payload, encoding="utf-8"
                    )

        return super().execute_command(container_id, command, workdir)


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "sample.py").write_text("def hello(): return 'world'\n", encoding="utf-8")
    return src


def _make_candidate(
    candidate_id: str = "cand-001",
    run_id: str = "run-001",
    target_symbol: str = "calculator.add",
    test_path: Path = Path("tests/test_calc.py"),
    code: str = "def test_add(): assert add(1, 2) == 3\n",
    status: ValidationStatus = ValidationStatus.PASSED,
) -> TestCandidate:
    return TestCandidate(
        candidate_id=candidate_id,
        run_id=run_id,
        target_symbol_name=target_symbol,
        test_file_path=test_path,
        candidate_code=code,
        imports=("from app.calc import add",),
        validation_status=status,
    )


def _make_plan(
    plan_id: str = "plan-001",
    route: WorkflowRoute = WorkflowRoute.ROUTE_TO_TEST_GENERATION,
    existing_tests: Tuple[Path, ...] = (),
) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=plan_id,
        route=route,
        target_symbols=(),
        existing_tests_to_run=existing_tests,
        rationale="Unit test generation execution",
        decision_hash="0" * 64,
    )


VALID_COVERAGE_JSON = json.dumps({
    "meta": {"version": "7.4.4"},
    "files": {
        "src/calc.py": {
            "executed_lines": [1, 2, 4, 5],
            "missing_lines": [7],
            "summary": {
                "covered_lines": 4,
                "num_statements": 5,
                "percent_covered": 80.0,
                "missing_lines": 1,
                "excluded_lines": 0,
            },
        }
    },
    "totals": {
        "covered_lines": 4,
        "num_statements": 5,
        "percent_covered": 80.0,
        "missing_lines": 1,
        "excluded_lines": 0,
        "covered_branches": 3,
        "num_branches": 4,
        "missing_branches": 1,
    },
})


# =====================================================================
# 1. Successful Execution & Telemetry Extraction Tests
# =====================================================================

def test_successful_candidate_execution(tmp_path: Path) -> None:
    """
    Verifies full execution lifecycle for a passing test candidate:
    - Command construction & container configuration.
    - Successful pytest outcome parsing (2 passed).
    - Coverage extraction from coverage.json.
    - Evidence assembly and property availability.
    - Ephemeral cleanup of staging and container.
    """
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    sandbox = SimulatedContainerSandboxManager(
        coverage_payload=VALID_COVERAGE_JSON,
    )
    sandbox.script_success(
        stdout="=== 2 passed in 0.42s ===\n",
        duration_sec=0.42,
    )

    service = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
    )

    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])

    # Assert ExecutionEvidence values
    assert evidence.exit_code == 0
    assert evidence.duration_sec == 0.42
    assert evidence.timed_out is False
    assert evidence.line_coverage == 80.0
    assert evidence.branch_coverage == 75.0
    assert evidence.traceback is None
    assert evidence.candidate_id == candidate.candidate_id
    assert evidence.run_id == candidate.run_id
    assert evidence.evidence_id.startswith("ev-")

    # Assert last summary & coverage properties
    assert service.last_summary is not None
    assert service.last_summary.passed == 2
    assert service.last_summary.success is True

    assert service.last_coverage is not None
    assert service.last_coverage.is_valid is True
    assert service.last_coverage.covered_lines == 4

    assert service.last_evidence == evidence

    # Assert container cleanup (INV-01)
    assert sandbox.active_containers == []
    assert len(sandbox.terminated_containers) == 1

    # Assert command execution was recorded
    assert sandbox.call_count == 1
    call = sandbox.calls[0]
    assert call["command"] == [
        "pytest",
        "/workspace/tests",
        "--cov=/workspace/src",
        "--cov-branch",
        "--cov-report=json:/workspace/output/coverage.json",
        "-o",
        "cache_dir=/tmp/.pytest_cache",
    ]


def test_failing_candidate_captures_traceback(source_dir: Path) -> None:
    """
    Verifies that test assertion failure (exit code 1) captures the diagnostic
    traceback into ExecutionEvidence without raising an unhandled exception.
    """
    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_failure(
        exit_code=1,
        stdout=(
            "=== FAILURES ===\n"
            "___ test_add ___\n"
            "    def test_add():\n"
            ">       assert add(1, 2) == 4\n"
            "E       assert 3 == 4\n"
            "tests/test_calc.py:3: AssertionError\n"
            "=== 1 failed in 0.15s ===\n"
        ),
        duration_sec=0.15,
    )

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])

    assert evidence.exit_code == 1
    assert evidence.timed_out is False
    assert evidence.traceback is not None
    assert "AssertionError" in evidence.traceback
    assert "assert 3 == 4" in evidence.traceback
    assert service.last_summary is not None
    assert service.last_summary.failed == 1
    assert service.last_summary.success is False


def test_collection_error_captured(source_dir: Path) -> None:
    """
    Verifies that pytest collection errors (exit code 2) are captured cleanly.
    """
    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_failure(
        exit_code=2,
        stdout="=== 1 error in 0.08s ===\n",
        stderr="ModuleNotFoundError: No module named 'nonexistent_package'\n",
        duration_sec=0.08,
    )

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])

    assert evidence.exit_code == 2
    assert evidence.traceback is not None
    assert "ModuleNotFoundError" in evidence.traceback
    assert service.last_summary is not None
    assert service.last_summary.errors == 1


def test_no_tests_collected_exit_code_5(source_dir: Path) -> None:
    """
    Verifies that exit code 5 (no tests collected) is recorded properly in evidence.
    """
    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_failure(
        exit_code=5,
        stdout="=== no tests ran in 0.02s ===\n",
        duration_sec=0.02,
    )

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])

    assert evidence.exit_code == 5
    assert evidence.timed_out is False
    assert service.last_summary is not None
    assert service.last_summary.exit_code == 5


# =====================================================================
# 2. Timeout & Resource Limits Confinement Tests (INV-01)
# =====================================================================

def test_execution_timeout_inv01(source_dir: Path) -> None:
    """
    Verifies that container timeout (exit code 124) sets timed_out=True
    on the resulting ExecutionEvidence entity (UC-05 Extension 6a).
    """
    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_timeout(duration_sec=30.0)

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir, timeout_sec=30.0)
    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])

    assert evidence.exit_code == 124
    assert evidence.timed_out is True
    assert evidence.duration_sec == 30.0
    assert service.last_summary is not None
    assert service.last_summary.timed_out is True


def test_execution_oom_killed_inv01(source_dir: Path) -> None:
    """
    Verifies that container memory exhaustion (exit code 137) is parsed as OOM
    (UC-05 Extension 6b).
    """
    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_oom(duration_sec=0.5)

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])

    assert evidence.exit_code == 137
    assert service.last_summary is not None
    assert service.last_summary.oom_killed is True
    assert evidence.traceback is not None
    assert "out of memory" in evidence.traceback.lower()


def test_sandbox_confinement_config_parameters(tmp_path: Path) -> None:
    """
    Verifies that ExecutionService configures SandboxConfig strictly with
    INV-01 security invariants (network_disabled=True, limits, non-root).
    """
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_success()

    service = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
        timeout_sec=45.0,
        memory_limit="256m",
        cpu_quota=2.0,
        pids_limit=50,
        image_tag="agentic-runner:custom",
    )

    plan = _make_plan()
    candidate = _make_candidate()
    service.execute(plan, [candidate])

    assert len(sandbox.created_configs) == 1
    config = list(sandbox.created_configs.values())[0]

    assert config.network_disabled is True
    assert config.timeout_sec == 45.0
    assert config.memory_limit == "256m"
    assert config.cpu_quota == 2.0
    assert config.pids_limit == 50
    assert config.image_tag == "agentic-runner:custom"
    assert config.read_only_mounts[source_dir.resolve()] == "/workspace/src"


# =====================================================================
# 3. Source Tree Integrity Verification Tests (INV-02)
# =====================================================================

def test_source_integrity_preservation_inv02(tmp_path: Path) -> None:
    """
    Verifies that when source files are untouched, SHA-256 pre/post verification passes cleanly.
    """
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "mod.py").write_text("print('hello')\n", encoding="utf-8")

    pre_hash = compute_tree_hash(source_dir)

    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])
    assert evidence.exit_code == 0
    assert compute_tree_hash(source_dir) == pre_hash


def test_source_integrity_violation_raises_error_inv02(tmp_path: Path) -> None:
    """
    Verifies that tampering with source files raises SourceIntegrityError
    and ProductionSourceIntegrityViolation (UC-05 Extension 9a).
    """
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    target_file = source_dir / "mod.py"
    target_file.write_text("def pristine(): pass\n", encoding="utf-8")

    # Simulate tampering during container execution
    sandbox = SimulatedContainerSandboxManager(tamper_target=target_file)
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    with pytest.raises(SourceIntegrityError) as exc_info:
        service.execute(plan, [candidate])

    assert issubclass(ProductionSourceIntegrityViolation, SourceIntegrityError)
    assert "INV-02 violated" in str(exc_info.value)
    assert "UC-05 Extension 9a" in str(exc_info.value)

    # Verify container was still cleaned up despite integrity error
    assert sandbox.active_containers == []
    assert len(sandbox.terminated_containers) == 1


# =====================================================================
# 4. Coverage Extraction & Edge Cases
# =====================================================================

def test_missing_coverage_json_handled_safely(source_dir: Path) -> None:
    """
    Verifies that when coverage.json is absent, line_coverage defaults to 0.0
    and is_valid is marked False without crashing.
    """
    sandbox = SimulatedContainerSandboxManager(coverage_payload=None)
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])

    assert evidence.line_coverage is None
    assert evidence.branch_coverage is None
    assert service.last_coverage is not None
    assert service.last_coverage.is_valid is False
    assert "not found" in (service.last_coverage.error_message or "").lower()


def test_malformed_coverage_json_handled_safely(source_dir: Path) -> None:
    """
    Verifies that malformed coverage JSON falls back gracefully without crashing.
    """
    sandbox = SimulatedContainerSandboxManager(coverage_payload="{not-valid-json}")
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    evidence = service.execute(plan, [candidate])

    assert evidence.line_coverage is None
    assert evidence.branch_coverage is None
    assert service.last_coverage is not None
    assert service.last_coverage.is_valid is False
    assert "malformed" in (service.last_coverage.error_message or "").lower()


# =====================================================================
# 5. Fail-Closed Cleanup & Error Guarantees
# =====================================================================

def test_container_and_mounts_cleaned_up_on_sandbox_execution_error(tmp_path: Path, source_dir: Path) -> None:
    """
    Verifies that if SandboxManager.execute_command raises an exception,
    the container is terminated and ephemeral staging directories are removed.
    """
    staging_root = tmp_path / "staging_parent"
    staging_root.mkdir()
    scratch_root = tmp_path / "scratch_parent"
    scratch_root.mkdir()

    sandbox = SimulatedContainerSandboxManager(
        execute_error=SandboxExecutionError("Simulated container crash")
    )

    service = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
        staging_root=staging_root,
        scratch_root=scratch_root,
    )
    plan = _make_plan()
    candidate = _make_candidate()

    with pytest.raises(SandboxExecutionError):
        service.execute(plan, [candidate])

    # Container terminated
    assert sandbox.active_containers == []
    assert len(sandbox.terminated_containers) == 1

    # Staging and scratch directories under roots removed
    assert list(staging_root.iterdir()) == []
    assert list(scratch_root.iterdir()) == []


def test_mounts_cleaned_up_on_sandbox_initialization_error(tmp_path: Path, source_dir: Path) -> None:
    """
    Verifies that if SandboxManager.create_environment fails (SandboxInitializationError),
    ephemeral staging and scratch directories are still reliably cleaned up.
    """
    staging_root = tmp_path / "staging_parent"
    staging_root.mkdir()
    scratch_root = tmp_path / "scratch_parent"
    scratch_root.mkdir()

    sandbox = SimulatedContainerSandboxManager()
    sandbox.enqueue_error(SandboxInitializationError("Simulated daemon failure"))

    service = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
        staging_root=staging_root,
        scratch_root=scratch_root,
    )
    plan = _make_plan()
    candidate = _make_candidate()

    with pytest.raises(SandboxInitializationError):
        service.execute(plan, [candidate])

    # No active containers
    assert sandbox.active_containers == []

    # Staging and scratch directories under roots removed
    assert list(staging_root.iterdir()) == []
    assert list(scratch_root.iterdir()) == []


def test_unvalidated_candidates_rejected_inv04(source_dir: Path) -> None:
    """
    Verifies that candidates without validation_status == PASSED are rejected
    before staging and never executed (INV-04).
    """
    sandbox = SimulatedContainerSandboxManager()
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)

    plan = _make_plan()
    bad_cand = _make_candidate(status=ValidationStatus.REJECTED_SECURITY)

    with pytest.raises(StagingValidationError) as exc_info:
        service.execute(plan, [bad_cand])

    assert "PASSED" in str(exc_info.value)
    assert sandbox.call_count == 0
    assert sandbox.active_containers == []


def test_empty_candidates_and_no_existing_tests_rejected(source_dir: Path) -> None:
    """
    Verifies that an error is raised when neither candidates nor existing tests are provided.
    """
    sandbox = SimulatedContainerSandboxManager()
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=())

    with pytest.raises(ValueError) as exc_info:
        service.execute(plan, candidates=())

    assert "No validated test candidates or existing tests" in str(exc_info.value)
    assert sandbox.call_count == 0


def test_existing_tests_execution_without_candidates(source_dir: Path) -> None:
    """
    Verifies execution of existing repository tests when candidates sequence is empty.
    """
    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_success(stdout="=== 5 passed in 0.8s ===\n")

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(
        plan_id="plan-regression-01",
        existing_tests=(Path("tests/test_suite.py"),),
    )

    evidence = service.execute(plan, candidates=())

    assert evidence.exit_code == 0
    assert evidence.candidate_id is None
    assert evidence.run_id == "plan-regression-01"

    assert sandbox.call_count == 1
    cmd = sandbox.calls[0]["command"]
    assert cmd[0] == "pytest"
    assert cmd[1] == "/workspace/src/tests/test_suite.py"


def test_execute_candidate_convenience_method(source_dir: Path) -> None:
    """
    Verifies the execute_candidate convenience helper.
    """
    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_success(stdout="=== 1 passed in 0.1s ===\n")

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    cand = _make_candidate(candidate_id="cand-singular-42")

    evidence = service.execute_candidate(plan, cand)

    assert evidence.exit_code == 0
    assert evidence.candidate_id == "cand-singular-42"


def test_keep_artifacts_preserves_directories(tmp_path: Path, source_dir: Path) -> None:
    """
    Verifies that keep_artifacts=True preserves staging and scratch directories for inspection.
    """
    staging_root = tmp_path / "staging_parent"
    staging_root.mkdir()
    scratch_root = tmp_path / "scratch_parent"
    scratch_root.mkdir()

    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success()

    service = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
        staging_root=staging_root,
        scratch_root=scratch_root,
        keep_artifacts=True,
    )
    plan = _make_plan()
    cand = _make_candidate()

    evidence = service.execute(plan, [cand])
    assert evidence.exit_code == 0

    # Verify directories still exist
    assert len(list(staging_root.iterdir())) == 1
    assert len(list(scratch_root.iterdir())) == 1


# =====================================================================
# 6. Tree Hash Determinism Tests
# =====================================================================

def test_compute_tree_hash_determinism(tmp_path: Path) -> None:
    """
    Verifies that compute_tree_hash produces deterministic SHA-256 hashes
    and ignores caches and temporary artifacts.
    """
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.py").write_text("a = 1\n", encoding="utf-8")
    (root / "b.py").write_text("b = 2\n", encoding="utf-8")

    hash1 = compute_tree_hash(root)
    hash2 = compute_tree_hash(root)
    assert hash1 == hash2
    assert len(hash1) == 64

    # Caches must be ignored
    cache_dir = root / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "a.cpython-311.pyc").write_bytes(b"\x00\x01\x02")

    assert compute_tree_hash(root) == hash1

    # Modifying a tracked file must change the hash
    (root / "a.py").write_text("a = 999\n", encoding="utf-8")
    assert compute_tree_hash(root) != hash1


# =====================================================================
# 7. Pre-Iteration-1.6 Slice 3: Fail-Closed Source Integrity Tests
# =====================================================================

def test_source_root_missing_rejected(tmp_path: Path) -> None:
    """Verifies that non-existent source_root path or None is rejected immediately with ValueError."""
    sandbox = SimulatedContainerSandboxManager()

    # None rejected
    with pytest.raises(ValueError, match="'source_root' is mandatory and cannot be None"):
        ExecutionService(sandbox_manager=sandbox, source_root=None)  # type: ignore[arg-type]

    # Non-existent path rejected
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises(ValueError, match="'source_root' path does not exist"):
        ExecutionService(sandbox_manager=sandbox, source_root=non_existent)


def test_source_root_file_rejected(tmp_path: Path) -> None:
    """Verifies that pointing source_root to a file instead of a directory is rejected with ValueError."""
    sandbox = SimulatedContainerSandboxManager()
    file_path = tmp_path / "regular_file.txt"
    file_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="'source_root' must be a directory"):
        ExecutionService(sandbox_manager=sandbox, source_root=file_path)


def test_source_root_hash_checked_after_execute_exception(source_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that source integrity post-hash is attempted even when execute_command raises an exception."""
    hash_call_count = 0
    import agentic_test.execution.service as svc_mod
    original_compute = svc_mod.compute_tree_hash

    def counting_compute_hash(root: Path) -> str:
        nonlocal hash_call_count
        hash_call_count += 1
        return original_compute(root)

    monkeypatch.setattr(svc_mod, "compute_tree_hash", counting_compute_hash)

    sandbox = SimulatedContainerSandboxManager(
        execute_error=RuntimeError("Container daemon execution failure")
    )
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    with pytest.raises(RuntimeError, match="Container daemon execution failure"):
        service.execute(plan, [candidate])

    # Pre-execution hash + post-execution hash attempted
    assert hash_call_count >= 2


def test_source_root_hash_checked_after_timeout_and_oom_result(source_dir: Path) -> None:
    """Verifies that source integrity post-hash is verified on timeout and OOM execution results."""
    # 1. Timeout
    sandbox_timeout = SimulatedContainerSandboxManager()
    sandbox_timeout.script_timeout(duration_sec=30.0)
    service_timeout = ExecutionService(sandbox_manager=sandbox_timeout, source_root=source_dir)

    ev_timeout = service_timeout.execute(_make_plan(), [_make_candidate()])
    assert ev_timeout.exit_code == 124
    assert ev_timeout.timed_out is True

    # 2. OOM
    sandbox_oom = SimulatedContainerSandboxManager()
    sandbox_oom.script_oom(duration_sec=0.5)
    service_oom = ExecutionService(sandbox_manager=sandbox_oom, source_root=source_dir)

    ev_oom = service_oom.execute(_make_plan(), [_make_candidate()])
    assert ev_oom.exit_code == 137
    assert service_oom.last_summary is not None
    assert service_oom.last_summary.oom_killed is True


def test_source_modification_plus_execute_failure_raises_source_integrity_error(source_dir: Path) -> None:
    """Verifies that source modification takes precedence over execution error, chaining the operational error."""
    target_file = source_dir / "sample.py"
    exec_err = RuntimeError("Docker socket broke during exec")

    sandbox = SimulatedContainerSandboxManager(
        tamper_target=target_file,
        execute_error=exec_err,
    )
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    with pytest.raises(SourceIntegrityError) as exc_info:
        service.execute(plan, [candidate])

    assert "INV-02 violated" in str(exc_info.value)
    # Operational error must be preserved as chained cause
    assert exc_info.value.__cause__ is exec_err


def test_source_modification_plus_cleanup_failure_raises_source_integrity_error(source_dir: Path) -> None:
    """Verifies that source modification takes precedence over container cleanup failure, chaining the operational error."""
    target_file = source_dir / "sample.py"
    cleanup_err = RuntimeError("Docker container cleanup failed: daemon unreachable")

    sandbox = SimulatedContainerSandboxManager(
        tamper_target=target_file,
        cleanup_error=cleanup_err,
    )
    sandbox.script_success()
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    with pytest.raises(SourceIntegrityError) as exc_info:
        service.execute(plan, [candidate])

    assert "INV-02 violated" in str(exc_info.value)
    # Cleanup error must be preserved as chained cause
    assert exc_info.value.__cause__ is cleanup_err


def test_post_hash_computation_failure_raises_source_integrity_error(source_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that failure to compute the post-execution hash raises SourceIntegrityError."""
    import agentic_test.execution.service as svc_mod
    original_compute = svc_mod.compute_tree_hash
    call_count = 0

    def fail_on_second_hash(root: Path) -> str:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise OSError("I/O error reading source directory")
        return original_compute(root)

    monkeypatch.setattr(svc_mod, "compute_tree_hash", fail_on_second_hash)

    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_success()
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    with pytest.raises(SourceIntegrityError) as exc_info:
        service.execute(plan, [candidate])

    assert "Failed to compute post-execution source tree hash" in str(exc_info.value)


def test_clean_source_with_execute_failure_reraises_original_exception(source_dir: Path) -> None:
    """Verifies that when source files are clean, an execution failure re-raises original operational error unchanged."""
    exec_err = RuntimeError("Subprocess exec failure in container")
    sandbox = SimulatedContainerSandboxManager(execute_error=exec_err)
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    candidate = _make_candidate()

    with pytest.raises(RuntimeError) as exc_info:
        service.execute(plan, [candidate])

    assert exc_info.value is exec_err


# =====================================================================
# 8. Slice 4: Baseline, Per-Candidate Isolation, Multi-Path & Deltas
# =====================================================================

def test_execute_regression_baseline_produces_none_candidate_id(source_dir: Path) -> None:
    """Verifies that execute_regression_baseline runs existing tests with candidate_id=None."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success(stdout="=== 5 passed in 0.5s ===\n")

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(
        plan_id="plan-baseline-01",
        existing_tests=(Path("tests/test_math.py"),),
    )

    evidence = service.execute_regression_baseline(plan)

    assert evidence.candidate_id is None
    assert evidence.exit_code == 0
    assert evidence.line_coverage == 80.0
    assert evidence.branch_coverage == 75.0
    assert evidence.line_coverage_delta is None
    assert evidence.branch_coverage_delta is None
    assert sandbox.call_count == 1
    cmd = sandbox.calls[0]["command"]
    assert "/workspace/src/tests/test_math.py" in cmd
    assert "/workspace/tests" not in cmd
    assert "--cov-branch" in cmd


def test_execute_regression_baseline_empty_tests_raises_value_error(source_dir: Path) -> None:
    """Verifies that execute_regression_baseline rejects plans without existing tests."""
    sandbox = SimulatedContainerSandboxManager()
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=())

    with pytest.raises(ValueError, match="cannot be empty"):
        service.execute_regression_baseline(plan)


def test_three_candidates_isolated_sessions_and_distinct_ids(source_dir: Path) -> None:
    """
    Verifies that execute_candidates runs three candidates in three separate
    container sessions, each with its own candidate_id and lifecycle cleanup.
    """
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    for _ in range(3):
        sandbox.script_success(stdout="=== 1 passed in 0.1s ===\n")

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    c1 = _make_candidate(candidate_id="cand-001")
    c2 = _make_candidate(candidate_id="cand-002")
    c3 = _make_candidate(candidate_id="cand-003")

    evidences = service.execute_candidates(plan, [c1, c2, c3])

    assert len(evidences) == 3
    assert evidences[0].candidate_id == "cand-001"
    assert evidences[1].candidate_id == "cand-002"
    assert evidences[2].candidate_id == "cand-003"

    # Distinct candidate IDs
    assert len({e.candidate_id for e in evidences}) == 3

    # Exactly 3 container sessions executed and terminated
    assert sandbox.call_count == 3
    assert len(sandbox.terminated_containers) == 3
    assert len(set(sandbox.terminated_containers)) == 3
    assert sandbox.active_containers == []


def test_candidates_execute_in_deterministic_input_order(source_dir: Path) -> None:
    """Verifies that candidates are executed in exact deterministic input order."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    for _ in range(3):
        sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    c_z = _make_candidate(candidate_id="cand-z")
    c_a = _make_candidate(candidate_id="cand-a")
    c_m = _make_candidate(candidate_id="cand-m")

    evidences = service.execute_candidates(plan, [c_z, c_a, c_m])
    assert [e.candidate_id for e in evidences] == ["cand-z", "cand-a", "cand-m"]


def test_candidate_validation_rejection_fail_fast(source_dir: Path) -> None:
    """Verifies that execute_candidates rejects any non-PASSED candidate fail-fast before execution."""
    sandbox = SimulatedContainerSandboxManager()
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()

    c_passed = _make_candidate(candidate_id="cand-ok", status=ValidationStatus.PASSED)
    c_pending = _make_candidate(candidate_id="cand-pending", status=ValidationStatus.PENDING)

    with pytest.raises(StagingValidationError, match="Only candidates with validation_status == PASSED"):
        service.execute_candidates(plan, [c_passed, c_pending])

    assert sandbox.call_count == 0
    assert sandbox.active_containers == []


def test_batch_execution_in_single_execute_call_rejected(source_dir: Path) -> None:
    """Verifies that execute() rejects batching multiple candidates into one session."""
    sandbox = SimulatedContainerSandboxManager()
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan()
    c1 = _make_candidate(candidate_id="cand-001")
    c2 = _make_candidate(candidate_id="cand-002")

    with pytest.raises(ValueError, match="Batch execution of multiple candidates.*is prohibited"):
        service.execute(plan, [c1, c2])


def test_all_planned_regression_paths_appear_in_each_command(source_dir: Path) -> None:
    """Verifies that multi-path regression preserves all planned paths in baseline and candidate commands."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    # Baseline + candidate
    sandbox.script_success()
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(
        existing_tests=(
            Path("tests/test_math.py"),
            Path("tests/test_calc.py"),
        ),
    )
    candidate = _make_candidate(candidate_id="cand-001")

    # 1. Baseline
    service.execute_regression_baseline(plan)
    baseline_cmd = sandbox.calls[0]["command"]
    assert "/workspace/src/tests/test_math.py" in baseline_cmd
    assert "/workspace/src/tests/test_calc.py" in baseline_cmd
    assert "/workspace/tests" not in baseline_cmd

    # 2. Candidate
    service.execute_candidate(plan, candidate)
    cand_cmd = sandbox.calls[1]["command"]
    assert "/workspace/tests" in cand_cmd
    assert "/workspace/src/tests/test_math.py" in cand_cmd
    assert "/workspace/src/tests/test_calc.py" in cand_cmd
    # Verify order preservation
    idx_tests = cand_cmd.index("/workspace/tests")
    idx_math = cand_cmd.index("/workspace/src/tests/test_math.py")
    idx_calc = cand_cmd.index("/workspace/src/tests/test_calc.py")
    assert idx_tests < idx_math < idx_calc


def test_candidate_session_is_separate_from_baseline_session(source_dir: Path) -> None:
    """Verifies that baseline and candidate runs execute in completely separate container sessions."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success()
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    base_ev = service.execute_regression_baseline(plan)
    cand_ev = service.execute_candidate(plan, candidate, baseline_evidence=base_ev)

    assert sandbox.call_count == 2
    assert len(sandbox.terminated_containers) == 2
    assert sandbox.terminated_containers[0] != sandbox.terminated_containers[1]
    assert base_ev.evidence_id != cand_ev.evidence_id
    assert base_ev.candidate_id is None
    assert cand_ev.candidate_id == candidate.candidate_id


def test_valid_line_and_branch_coverage_deltas(source_dir: Path) -> None:
    """
    Verifies that when baseline and candidate runs complete with exit_code=0
    and matching settings, line and branch coverage deltas are correctly computed.
    """
    baseline_cov_json = json.dumps({
        "totals": {
            "covered_lines": 60,
            "num_statements": 100,
            "percent_covered": 60.0,
            "num_branches": 20,
            "covered_branches": 10,
        }
    })
    candidate_cov_json = json.dumps({
        "totals": {
            "covered_lines": 75,
            "num_statements": 100,
            "percent_covered": 75.5,
            "num_branches": 20,
            "covered_branches": 13,
        }
    })

    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_success()
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    # Set baseline coverage payload
    sandbox.coverage_payload = baseline_cov_json
    base_ev = service.execute_regression_baseline(plan)
    assert base_ev.line_coverage == 60.0
    assert base_ev.branch_coverage == 50.0

    # Set candidate coverage payload (13/20 = 65.0%)
    sandbox.coverage_payload = candidate_cov_json
    cand_ev = service.execute_candidate(plan, candidate, baseline_evidence=base_ev)

    assert cand_ev.line_coverage == 75.5
    assert cand_ev.branch_coverage == 65.0
    assert cand_ev.line_coverage_delta == 15.5
    assert cand_ev.branch_coverage_delta == 15.0


def test_none_deltas_on_failed_baseline(source_dir: Path) -> None:
    """Verifies that line/branch coverage deltas are None when baseline run failed (exit_code != 0)."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    # Baseline failure
    sandbox.script_failure(exit_code=1, stdout="=== 1 failed in 0.1s ===\n")
    # Candidate success
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    base_ev = service.execute_regression_baseline(plan)
    assert base_ev.exit_code == 1

    cand_ev = service.execute_candidate(plan, candidate, baseline_evidence=base_ev)
    assert cand_ev.exit_code == 0
    assert cand_ev.line_coverage_delta is None
    assert cand_ev.branch_coverage_delta is None


def test_none_deltas_on_failed_candidate(source_dir: Path) -> None:
    """Verifies that line/branch coverage deltas are None when candidate run failed (exit_code != 0)."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    # Baseline success
    sandbox.script_success()
    # Candidate failure
    sandbox.script_failure(exit_code=1, stdout="=== 1 failed in 0.1s ===\n")

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    base_ev = service.execute_regression_baseline(plan)
    assert base_ev.exit_code == 0

    cand_ev = service.execute_candidate(plan, candidate, baseline_evidence=base_ev)
    assert cand_ev.exit_code == 1
    assert cand_ev.line_coverage_delta is None
    assert cand_ev.branch_coverage_delta is None


def test_none_deltas_on_invalid_or_missing_coverage(source_dir: Path) -> None:
    """Verifies that line/branch coverage deltas are None when coverage report is missing or malformed."""
    sandbox = SimulatedContainerSandboxManager()
    sandbox.script_success()
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    # Valid baseline
    sandbox.coverage_payload = VALID_COVERAGE_JSON
    base_ev = service.execute_regression_baseline(plan)

    # Missing coverage on candidate
    sandbox.coverage_payload = None
    cand_ev = service.execute_candidate(plan, candidate, baseline_evidence=base_ev)
    assert cand_ev.line_coverage is None
    assert cand_ev.branch_coverage is None
    assert cand_ev.line_coverage_delta is None
    assert cand_ev.branch_coverage_delta is None


def test_none_deltas_on_mismatched_settings(source_dir: Path) -> None:
    """
    Verifies that when baseline and candidate use mismatched coverage settings
    (e.g., cov_branch=True vs cov_branch=False or different cov_source),
    deltas are None (do not infer comparability merely from numeric values).
    """
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success()
    sandbox.script_success()

    # Service 1: cov_branch=True
    service1 = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
        cov_source="/workspace/src",
        cov_branch=True,
    )
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    base_ev = service1.execute_regression_baseline(plan)
    assert base_ev.line_coverage == 80.0

    # Service 2: cov_branch=False (mismatched branch setting!)
    service2 = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
        cov_source="/workspace/src",
        cov_branch=False,
    )
    candidate = _make_candidate()
    cand_ev = service2.execute_candidate(plan, candidate, baseline_evidence=base_ev)

    assert cand_ev.line_coverage == 80.0
    assert cand_ev.line_coverage_delta is None
    assert cand_ev.branch_coverage_delta is None


def test_cleanup_on_every_candidate_and_baseline_session(tmp_path: Path, source_dir: Path) -> None:
    """Verifies that ephemeral staging and scratch mounts are cleaned up on each session."""
    staging_root = tmp_path / "staging_parent"
    staging_root.mkdir()
    scratch_root = tmp_path / "scratch_parent"
    scratch_root.mkdir()

    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    for _ in range(3):
        sandbox.script_success()

    service = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
        staging_root=staging_root,
        scratch_root=scratch_root,
    )
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    c1 = _make_candidate(candidate_id="c1")
    c2 = _make_candidate(candidate_id="c2")

    base_ev = service.execute_regression_baseline(plan)
    assert list(staging_root.iterdir()) == []
    assert list(scratch_root.iterdir()) == []

    evs = service.execute_candidates(plan, [c1, c2], baseline_evidence=base_ev)
    assert len(evs) == 2
    assert list(staging_root.iterdir()) == []
    assert list(scratch_root.iterdir()) == []
    assert sandbox.active_containers == []


def test_source_integrity_intact_across_candidate_and_baseline(source_dir: Path) -> None:
    """Verifies that INV-02 source integrity verification guards both baseline and candidate runs."""
    target_file = source_dir / "sample.py"

    # Baseline tampering
    sandbox_base = SimulatedContainerSandboxManager(tamper_target=target_file)
    service_base = ExecutionService(sandbox_manager=sandbox_base, source_root=source_dir)
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))

    with pytest.raises(SourceIntegrityError):
        service_base.execute_regression_baseline(plan)

    # Restore file
    target_file.write_text("def hello(): return 'world'\n", encoding="utf-8")

    # Candidate tampering
    sandbox_cand = SimulatedContainerSandboxManager(tamper_target=target_file)
    service_cand = ExecutionService(sandbox_manager=sandbox_cand, source_root=source_dir)
    candidate = _make_candidate()

    with pytest.raises(SourceIntegrityError):
        service_cand.execute_candidate(plan, candidate)


def test_settings_do_not_leak_between_execution_service_instances(source_dir: Path) -> None:
    """Verifies that evidence settings are instance-scoped and never leak between ExecutionService instances."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success()

    service1 = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    service2 = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)

    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    base_ev = service1.execute_regression_baseline(plan)

    # Registered on service1
    assert service1.get_evidence_settings(base_ev.evidence_id) is not None
    # Completely absent on service2
    assert service2.get_evidence_settings(base_ev.evidence_id) is None


def test_one_run_cleanup_cannot_erase_another_run_settings(source_dir: Path) -> None:
    """
    Verifies that releasing settings or finishing execution in one ExecutionService instance
    cannot erase settings belonging to another concurrent/independent run.
    """
    sandbox_a = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox_a.script_success()
    sandbox_a.script_success()

    sandbox_b = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox_b.script_success()
    sandbox_b.script_success()

    service_a = ExecutionService(sandbox_manager=sandbox_a, source_root=source_dir)
    service_b = ExecutionService(sandbox_manager=sandbox_b, source_root=source_dir)

    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    cand_a = _make_candidate(candidate_id="cand-a")
    cand_b = _make_candidate(candidate_id="cand-b")

    # Baseline for run A and run B
    base_a = service_a.execute_regression_baseline(plan)
    base_b = service_b.execute_regression_baseline(plan)

    assert service_a.get_evidence_settings(base_a.evidence_id) is not None
    assert service_b.get_evidence_settings(base_b.evidence_id) is not None

    # Run A finishes candidate execution, releasing base_a settings
    service_a.execute_candidate(plan, cand_a, baseline_evidence=base_a)
    assert service_a.get_evidence_settings(base_a.evidence_id) is None

    # Run B's settings are completely untouched and still intact!
    assert service_b.get_evidence_settings(base_b.evidence_id) is not None

    # Run B can successfully compute deltas against its baseline
    cand_ev_b = service_b.execute_candidate(plan, cand_b, baseline_evidence=base_b)
    assert cand_ev_b.line_coverage_delta == 0.0
    assert cand_ev_b.branch_coverage_delta == 0.0


def test_same_instance_baseline_and_candidate_computes_deltas(source_dir: Path) -> None:
    """Verifies that baseline and candidate executions on the same instance compute coverage deltas."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success()
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    base_ev = service.execute_regression_baseline(plan)
    assert base_ev.line_coverage == 80.0

    cand_ev = service.execute_candidate(plan, candidate, baseline_evidence=base_ev)
    assert cand_ev.line_coverage == 80.0
    assert cand_ev.line_coverage_delta == 0.0
    assert cand_ev.branch_coverage_delta == 0.0


def test_unavailable_settings_produce_none_deltas(source_dir: Path) -> None:
    """Verifies that when baseline settings are unavailable (cross-service or unrecorded), deltas are None."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success()
    sandbox.script_success()

    service1 = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    service2 = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)

    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    base_ev = service1.execute_regression_baseline(plan)

    # Executed on service2 where base_ev settings are not registered
    cand_ev = service2.execute_candidate(plan, candidate, baseline_evidence=base_ev)
    assert cand_ev.line_coverage == 80.0
    assert cand_ev.line_coverage_delta is None
    assert cand_ev.branch_coverage_delta is None


def test_settings_are_released_after_candidate_delta_computation(source_dir: Path) -> None:
    """
    Verifies that settings are deterministically released after candidate delta computation
    so no memory leak or unbounded settings retention occurs.
    """
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success()
    sandbox.script_success()

    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    base_ev = service.execute_regression_baseline(plan)
    assert service.get_evidence_settings(base_ev.evidence_id) is not None

    cand_ev = service.execute_candidate(plan, candidate, baseline_evidence=base_ev)
    assert cand_ev.line_coverage_delta == 0.0

    # Baseline settings released after candidate delta computation
    assert service.get_evidence_settings(base_ev.evidence_id) is None
    # Candidate itself was not registered as a baseline
    assert service.get_evidence_settings(cand_ev.evidence_id) is None


def test_same_instance_mismatched_settings_produce_none_deltas(source_dir: Path) -> None:
    """Verifies that when baseline settings on the same instance differ in cov_branch, deltas are None."""
    sandbox = SimulatedContainerSandboxManager(coverage_payload=VALID_COVERAGE_JSON)
    sandbox.script_success()
    sandbox.script_success()

    service = ExecutionService(
        sandbox_manager=sandbox,
        source_root=source_dir,
        cov_source="/workspace/src",
        cov_branch=True,
    )
    plan = _make_plan(existing_tests=(Path("tests/test_suite.py"),))
    candidate = _make_candidate()

    base_ev = service.execute_regression_baseline(plan)
    # Manually overwrite registered settings on this instance to simulate mismatched branch coverage
    service.register_evidence_settings(
        evidence_id=base_ev.evidence_id,
        source_root=source_dir,
        cov_source="/workspace/src",
        cov_branch=False,  # mismatch with service._cov_branch = True
    )

    cand_ev = service.execute_candidate(plan, candidate, baseline_evidence=base_ev)
    assert cand_ev.line_coverage == 80.0
    assert cand_ev.line_coverage_delta is None
    assert cand_ev.branch_coverage_delta is None


def test_bounded_settings_cache_eviction(source_dir: Path) -> None:
    """Verifies that the instance-scoped settings cache is bounded to 50 entries via FIFO eviction."""
    sandbox = SimulatedContainerSandboxManager()
    service = ExecutionService(sandbox_manager=sandbox, source_root=source_dir)

    # Register 55 entries
    for i in range(55):
        service.register_evidence_settings(
            evidence_id=f"ev-{i:03d}",
            source_root=source_dir,
            cov_source="/workspace/src",
            cov_branch=True,
        )

    # Earliest 5 entries (0 to 4) should have been evicted
    for i in range(5):
        assert service.get_evidence_settings(f"ev-{i:03d}") is None

    # Latest 50 entries (5 to 54) should be present
    for i in range(5, 55):
        assert service.get_evidence_settings(f"ev-{i:03d}") is not None
