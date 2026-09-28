"""
Offline unit tests for SandboxManager protocol, models, and MockSandboxManager.
Iteration 1.5.1 Slice A Contract Verification.
"""

from pathlib import Path
import socket
import sys
from typing import Any, Dict, List
import pytest
from pydantic import ValidationError

from agentic_test.core.protocols.sandbox import (
    ExecutionRawResult,
    SandboxConfig,
    SandboxError,
    SandboxExecutionError,
    SandboxInitializationError,
    SandboxManager,
)
from agentic_test.execution.mock_sandbox import MockSandboxManager


# =====================================================================
# 1. Contract Conformance Tests
# =====================================================================

def test_mock_sandbox_manager_conforms_to_protocol() -> None:
    """Verifies that MockSandboxManager satisfies the runtime-checkable SandboxManager protocol."""
    mock = MockSandboxManager()
    assert isinstance(mock, SandboxManager)


def test_protocol_rejects_non_conforming_classes() -> None:
    """Verifies that classes missing any required protocol method fail runtime check."""
    class IncompleteSandboxA:
        def create_environment(self, config: SandboxConfig) -> str:
            return "cid"

    class IncompleteSandboxB:
        def create_environment(self, config: SandboxConfig) -> str:
            return "cid"
        def execute_command(
            self, container_id: str, command: List[str], workdir: str = "/workspace"
        ) -> ExecutionRawResult:
            return ExecutionRawResult(
                exit_code=0, stdout="", stderr="", duration_sec=0.1, timed_out=False, oom_killed=False
            )

    assert not isinstance(IncompleteSandboxA(), SandboxManager)
    assert not isinstance(IncompleteSandboxB(), SandboxManager)


# =====================================================================
# 2. Exact Model Defaults Tests
# =====================================================================

def test_sandbox_config_exact_defaults() -> None:
    """Verifies that SandboxConfig default field values match Stage 3 Listing 4.2 character-for-character."""
    config = SandboxConfig()

    assert config.image_tag == "agentic-runner:0.1.0"
    assert config.timeout_sec == 30.0
    assert config.memory_limit == "512m"
    assert config.cpu_quota == 1.0
    assert config.pids_limit == 100
    assert config.network_disabled is True
    assert config.read_only_mounts == {}
    assert config.read_write_mounts == {}
    assert config.environment_vars == {}


def test_sandbox_config_custom_initialization() -> None:
    """Verifies that custom parameters can be supplied and validated in SandboxConfig."""
    ro_mounts = {Path("/repo/src"): "/workspace/src"}
    rw_mounts = {Path("/repo/tmp"): "/tmp"}
    env_vars = {"PYTHONPATH": "/workspace/src", "TEST_ENV": "1"}

    config = SandboxConfig(
        image_tag="custom-runner:v2",
        timeout_sec=45.5,
        memory_limit="1024m",
        cpu_quota=2.0,
        pids_limit=250,
        network_disabled=False,
        read_only_mounts=ro_mounts,
        read_write_mounts=rw_mounts,
        environment_vars=env_vars,
    )

    assert config.image_tag == "custom-runner:v2"
    assert config.timeout_sec == 45.5
    assert config.memory_limit == "1024m"
    assert config.cpu_quota == 2.0
    assert config.pids_limit == 250
    assert config.network_disabled is False
    assert config.read_only_mounts == ro_mounts
    assert config.read_write_mounts == rw_mounts
    assert config.environment_vars == env_vars


# =====================================================================
# 3. Validation and Serialization of ExecutionRawResult
# =====================================================================

def test_execution_raw_result_valid_instantiation_and_serialization() -> None:
    """Verifies field types, dict dump, JSON serialization, and round-trip deserialization."""
    result = ExecutionRawResult(
        exit_code=0,
        stdout="5 passed in 0.12s\n",
        stderr="",
        duration_sec=0.12,
        timed_out=False,
        oom_killed=False,
    )

    assert result.exit_code == 0
    assert result.stdout == "5 passed in 0.12s\n"
    assert result.stderr == ""
    assert result.duration_sec == 0.12
    assert result.timed_out is False
    assert result.oom_killed is False

    # Dict serialization
    dumped = result.model_dump()
    assert dumped == {
        "exit_code": 0,
        "stdout": "5 passed in 0.12s\n",
        "stderr": "",
        "duration_sec": 0.12,
        "timed_out": False,
        "oom_killed": False,
    }

    # JSON serialization and round-trip
    json_repr = result.model_dump_json()
    reconstituted = ExecutionRawResult.model_validate_json(json_repr)
    assert reconstituted == result


def test_execution_raw_result_rejects_missing_or_invalid_fields() -> None:
    """Verifies that missing required fields or invalid types trigger ValidationError."""
    with pytest.raises(ValidationError):
        # Missing exit_code and duration_sec
        ExecutionRawResult.model_validate({"stdout": "", "stderr": "", "timed_out": False, "oom_killed": False})

    with pytest.raises(ValidationError):
        # Invalid type for exit_code
        ExecutionRawResult.model_validate(
            {
                "exit_code": "not_an_int",
                "stdout": "",
                "stderr": "",
                "duration_sec": 1.0,
                "timed_out": False,
                "oom_killed": False,
            }
        )

    with pytest.raises(ValidationError):
        # Invalid type for timed_out
        ExecutionRawResult.model_validate(
            {
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "duration_sec": 1.0,
                "timed_out": "not_a_bool",
                "oom_killed": False,
            }
        )


# =====================================================================
# 4. Lifecycle Behavior and Cleanup Policy Tests
# =====================================================================

def test_lifecycle_creation_and_execution() -> None:
    """Verifies standard lifecycle: create_environment produces active ID, execute_command succeeds."""
    mock = MockSandboxManager()
    config = SandboxConfig()

    container_id = mock.create_environment(config)
    assert isinstance(container_id, str)
    assert len(container_id) > 0
    assert container_id in mock.active_containers
    assert mock.created_configs[container_id] == config

    result = mock.execute_command(container_id, ["pytest", "tests/"])
    assert result.exit_code == 0
    assert mock.call_count == 1
    assert mock.calls[0] == {
        "container_id": container_id,
        "command": ["pytest", "tests/"],
        "workdir": "/workspace",
    }


def test_lifecycle_cleanup_policy_and_post_cleanup_rejection() -> None:
    """
    Verifies the documented cleanup policy:
    1. cleanup transitions container from ACTIVE to TERMINATED.
    2. subsequent command execution on terminated container raises RuntimeError.
    3. repeated cleanup on already terminated or unknown container is idempotent (no-op).
    """
    mock = MockSandboxManager()
    container_id = mock.create_environment(SandboxConfig())

    # Active execution works
    mock.execute_command(container_id, ["echo", "hello"])

    # Perform cleanup
    mock.cleanup(container_id)
    assert container_id not in mock.active_containers
    assert container_id in mock.terminated_containers

    # Subsequent command execution MUST raise RuntimeError
    with pytest.raises(RuntimeError, match="already been cleaned up"):
        mock.execute_command(container_id, ["pytest"])

    # Idempotent cleanup: repeated cleanup must succeed safely without error
    mock.cleanup(container_id)
    assert container_id in mock.terminated_containers

    # Cleanup on non-existent container ID must also succeed safely (idempotent)
    mock.cleanup("non-existent-container-id")


def test_execution_on_unregistered_container_raises_error() -> None:
    """Verifies that attempting execution on an unregistered container raises RuntimeError."""
    mock = MockSandboxManager()
    with pytest.raises(RuntimeError, match="unknown or not active"):
        mock.execute_command("unknown-cid-12345", ["pytest"])


# =====================================================================
# 5. Deterministic Scripted Outcomes Tests
# =====================================================================

def test_scripted_success_outcome() -> None:
    """Verifies deterministic scripted success response."""
    mock = MockSandboxManager()
    cid = mock.create_environment(SandboxConfig())

    mock.script_success(stdout="ALL PASS", duration_sec=0.45)
    res = mock.execute_command(cid, ["pytest"])

    assert res.exit_code == 0
    assert res.stdout == "ALL PASS"
    assert res.stderr == ""
    assert res.duration_sec == 0.45
    assert res.timed_out is False
    assert res.oom_killed is False


def test_scripted_failure_outcome() -> None:
    """Verifies deterministic scripted test failure (exit code 1)."""
    mock = MockSandboxManager()
    cid = mock.create_environment(SandboxConfig())

    mock.script_failure(exit_code=1, stdout="1 failed", stderr="AssertionError: 2 != 3", duration_sec=0.8)
    res = mock.execute_command(cid, ["pytest"])

    assert res.exit_code == 1
    assert res.stdout == "1 failed"
    assert "AssertionError" in res.stderr
    assert res.duration_sec == 0.8
    assert res.timed_out is False
    assert res.oom_killed is False


def test_scripted_timeout_outcome() -> None:
    """Verifies deterministic scripted timeout (exit code 124, timed_out=True)."""
    mock = MockSandboxManager()
    cid = mock.create_environment(SandboxConfig())

    mock.script_timeout(duration_sec=30.0, stderr="Execution timed out at 30.0s")
    res = mock.execute_command(cid, ["pytest"])

    assert res.exit_code == 124
    assert res.timed_out is True
    assert res.oom_killed is False
    assert res.duration_sec == 30.0
    assert "timed out" in res.stderr


def test_scripted_oom_outcome() -> None:
    """Verifies deterministic scripted OOM kill (exit code 137, oom_killed=True)."""
    mock = MockSandboxManager()
    cid = mock.create_environment(SandboxConfig())

    mock.script_oom(duration_sec=1.1, stderr="Out of memory: Killed process 42")
    res = mock.execute_command(cid, ["pytest"])

    assert res.exit_code == 137
    assert res.oom_killed is True
    assert res.timed_out is False
    assert "Out of memory" in res.stderr


def test_scripted_fifo_queue_ordering() -> None:
    """Verifies that multiple scripted outcomes are returned in strict FIFO sequence."""
    mock = MockSandboxManager()
    cid = mock.create_environment(SandboxConfig())

    mock.script_success(stdout="pass 1")
    mock.script_failure(exit_code=1, stdout="fail 2")
    mock.script_timeout(stderr="timeout 3")

    r1 = mock.execute_command(cid, ["pytest", "test_1.py"])
    r2 = mock.execute_command(cid, ["pytest", "test_2.py"])
    r3 = mock.execute_command(cid, ["pytest", "test_3.py"])
    r4 = mock.execute_command(cid, ["pytest", "test_4.py"])  # Queue empty: returns default

    assert r1.exit_code == 0
    assert r1.stdout == "pass 1"

    assert r2.exit_code == 1
    assert r2.stdout == "fail 2"

    assert r3.exit_code == 124
    assert r3.timed_out is True

    assert r4.exit_code == 0  # default fallback


def test_simulated_error_queue_raises_on_creation_and_execution() -> None:
    """Verifies that enqueued exceptions are raised predictably during lifecycle calls."""
    mock = MockSandboxManager()

    # Enqueue initialization error
    mock.enqueue_error(SandboxInitializationError("Docker daemon connection failed"))
    with pytest.raises(SandboxInitializationError, match="Docker daemon connection failed"):
        mock.create_environment(SandboxConfig())

    # Create valid environment
    cid = mock.create_environment(SandboxConfig())

    # Enqueue execution error
    mock.enqueue_error(SandboxExecutionError("Container unexpected exit"))
    with pytest.raises(SandboxExecutionError, match="Container unexpected exit"):
        mock.execute_command(cid, ["pytest"])


# =====================================================================
# 6. Isolation Proof: No Docker Client, Socket, or Network Access
# =====================================================================

def test_no_docker_module_in_sys_modules() -> None:
    """Verifies that importing and using MockSandboxManager does NOT load the docker SDK."""
    # Assert 'docker' package is not in sys.modules
    assert "docker" not in sys.modules, "Docker SDK must not be imported in Slice A offline tests."


def test_no_socket_or_network_access_during_mock_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Verifies that zero network sockets or connections are attempted during
    the complete lifecycle of MockSandboxManager.
    """
    network_called = False

    def guard_socket_connect(*args: Any, **kwargs: Any) -> None:
        nonlocal network_called
        network_called = True
        raise AssertionError("Network socket connect attempted during offline mock sandbox execution!")

    def guard_create_connection(*args: Any, **kwargs: Any) -> socket.socket:
        nonlocal network_called
        network_called = True
        raise AssertionError("socket.create_connection attempted during offline mock sandbox execution!")

    monkeypatch.setattr(socket.socket, "connect", guard_socket_connect)
    monkeypatch.setattr(socket, "create_connection", guard_create_connection)

    # Execute complete lifecycle and multiple commands
    mock = MockSandboxManager()
    cid = mock.create_environment(SandboxConfig())
    mock.script_success(stdout="all tests passed")
    res1 = mock.execute_command(cid, ["pytest", "tests/"])
    assert res1.exit_code == 0

    mock.script_timeout()
    res2 = mock.execute_command(cid, ["pytest", "tests/"])
    assert res2.timed_out is True

    mock.cleanup(cid)

    assert not network_called, "Zero network or socket operations are permitted in Slice A."


def test_no_target_filesystem_mutation(tmp_path: Path) -> None:
    """Verifies that MockSandboxManager does not create or write files to disk."""
    initial_items = set(tmp_path.iterdir())

    mock = MockSandboxManager()
    config = SandboxConfig(
        read_only_mounts={tmp_path: "/workspace/src"},
    )
    cid = mock.create_environment(config)
    mock.execute_command(cid, ["pytest", "tests/"])
    mock.cleanup(cid)

    # Target directory remains completely untouched
    final_items = set(tmp_path.iterdir())
    assert initial_items == final_items
