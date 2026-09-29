"""
Offline unit tests for DockerSandboxManager adapter using mocked Docker SDK behavior.
Iteration 1.5.1 Slice B.3.2.
"""

from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock
import pytest

from agentic_test.core.protocols.sandbox import (
    ExecutionRawResult,
    SandboxConfig,
    SandboxInitializationError,
    SandboxManager,
)
from agentic_test.execution.docker_sandbox import (
    DockerSandboxManager,
    is_docker_sdk_available,
)


class DummyExecResult:
    """Mock result emitted by container.exec_run in Docker SDK."""
    def __init__(self, exit_code: int, output: Tuple[bytes, bytes]) -> None:
        self.exit_code = exit_code
        self.output = output


class DummyContainer:
    """Mock Docker SDK Container object."""
    def __init__(self, container_id: str = "mock-cont-12345") -> None:
        self.id = container_id
        self.attrs: Dict[str, Any] = {"State": {"OOMKilled": False}}
        self.started = False
        self.killed = False
        self.removed = False
        self.remove_kwargs: Dict[str, Any] = {}
        self.exec_calls: List[Dict[str, Any]] = []
        self.exec_result = DummyExecResult(0, (b"tests passed\n", b""))
        self.exec_delay: float = 0.0
        self.stop_event: Optional[threading.Event] = None

    def start(self) -> None:
        self.started = True

    def reload(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True

    def remove(self, **kwargs: Any) -> None:
        self.removed = True
        self.remove_kwargs = kwargs

    def exec_run(self, cmd: List[str], workdir: str = "/workspace", demux: bool = True) -> DummyExecResult:
        self.exec_calls.append({"cmd": list(cmd), "workdir": workdir, "demux": demux})
        if self.exec_delay > 0:
            if self.stop_event is not None:
                self.stop_event.wait(timeout=self.exec_delay)
            else:
                time.sleep(self.exec_delay)
        return self.exec_result


class DummyContainersManager:
    """Mock Docker SDK client.containers manager."""
    def __init__(self) -> None:
        self.created_kwargs: List[Dict[str, Any]] = []
        self.containers: Dict[str, DummyContainer] = {}

    def create(self, **kwargs: Any) -> DummyContainer:
        self.created_kwargs.append(kwargs)
        cid = f"cont-{len(self.containers) + 1:04d}"
        container = DummyContainer(container_id=cid)
        self.containers[cid] = container
        return container

    def get(self, container_id: str) -> DummyContainer:
        if container_id not in self.containers:
            raise KeyError(f"Container '{container_id}' not found")
        return self.containers[container_id]


class DummyDockerClient:
    """Mock Docker SDK client."""
    def __init__(self) -> None:
        self.containers = DummyContainersManager()


# =====================================================================
# 1. Protocol Conformance & Lazy Import Tests
# =====================================================================

def test_docker_sandbox_manager_conforms_to_protocol() -> None:
    """Verifies that DockerSandboxManager satisfies runtime-checkable SandboxManager."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    assert isinstance(manager, SandboxManager)


def test_absent_docker_sdk_raises_initialization_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that instantiating DockerSandboxManager without SDK and without client raises clear error."""
    # Force is_docker_sdk_available to False
    monkeypatch.setattr("agentic_test.execution.docker_sandbox.is_docker_sdk_available", lambda: False)

    with pytest.raises(SandboxInitializationError, match="The 'docker' Python package is required"):
        DockerSandboxManager()


def test_no_daemon_calls_during_construction_or_import() -> None:
    """Verifies that importing and instantiating with injected client does not connect to real daemon."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    assert manager.active_containers == []


# =====================================================================
# 2. Container Creation Parameter Translation Tests
# =====================================================================

def test_create_environment_translates_sandbox_config_kwargs() -> None:
    """Verifies exact translation of SandboxConfig into Docker SDK container creation kwargs."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)

    config = SandboxConfig(
        image_tag="agentic-runner:0.1.0",
        timeout_sec=30.0,
        memory_limit="512m",
        cpu_quota=1.0,
        pids_limit=100,
        network_disabled=True,
        read_only_mounts={
            Path("/host/app"): "/workspace/src",
            Path("/host/staged"): "/workspace/tests",
        },
        read_write_mounts={
            Path("/host/scratch"): "/workspace/scratch",
        },
        environment_vars={"PYTHONPATH": "/workspace/src", "TESTING": "1"},
    )

    container_id = manager.create_environment(config)

    assert container_id == "cont-0001"
    assert container_id in manager.active_containers

    assert len(client.containers.created_kwargs) == 1
    kwargs = client.containers.created_kwargs[0]

    # Verify security confinement parameters
    assert kwargs["image"] == "agentic-runner:0.1.0"
    assert kwargs["network_mode"] == "none"
    assert kwargs["user"] == "10001:10001"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["mem_limit"] == "512m"
    assert kwargs["memswap_limit"] == "512m"
    assert kwargs["nano_cpus"] == 1_000_000_000
    assert kwargs["pids_limit"] == 100
    assert kwargs["detach"] is True
    assert kwargs["command"] == ["tail", "-f", "/dev/null"]
    assert kwargs["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=64m"}
    assert kwargs["environment"] == {"PYTHONPATH": "/workspace/src", "TESTING": "1"}

    # Verify exact volume mount modes
    volumes = kwargs["volumes"]
    assert volumes[str(Path("/host/app"))] == {"bind": "/workspace/src", "mode": "ro"}
    assert volumes[str(Path("/host/staged"))] == {"bind": "/workspace/tests", "mode": "ro"}
    assert volumes[str(Path("/host/scratch"))] == {"bind": "/workspace/scratch", "mode": "rw"}


# =====================================================================
# 3. Command Execution & Result Mapping Tests
# =====================================================================

def test_execute_command_success_mapping() -> None:
    """Verifies that command output is captured and demuxed into ExecutionRawResult."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    cid = manager.create_environment(SandboxConfig())

    container = client.containers.get(cid)
    container.exec_result = DummyExecResult(
        exit_code=0,
        output=(b"5 passed in 0.15s\n", b""),
    )

    res = manager.execute_command(cid, ["pytest", "tests/"])

    assert res.exit_code == 0
    assert res.stdout == "5 passed in 0.15s\n"
    assert res.stderr == ""
    assert res.timed_out is False
    assert res.oom_killed is False
    assert res.duration_sec >= 0.0

    assert len(container.exec_calls) == 1
    assert container.exec_calls[0]["cmd"] == ["pytest", "tests/"]
    assert container.exec_calls[0]["workdir"] == "/workspace"


def test_execute_command_failure_mapping() -> None:
    """Verifies non-zero exit code and stderr capture."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    cid = manager.create_environment(SandboxConfig())

    container = client.containers.get(cid)
    container.exec_result = DummyExecResult(
        exit_code=1,
        output=(b"1 failed\n", b"AssertionError: assert 1 == 2\n"),
    )

    res = manager.execute_command(cid, ["pytest", "tests/"])

    assert res.exit_code == 1
    assert res.stdout == "1 failed\n"
    assert "AssertionError" in res.stderr
    assert res.timed_out is False
    assert res.oom_killed is False


# =====================================================================
# 4. Timeout & OOM Mapping Tests
# =====================================================================

def test_execute_command_timeout_maps_to_exit_124() -> None:
    """Verifies that execution timeout triggers SIGKILL and returns exit code 124."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)

    # Configure small timeout for deterministic test speed
    cid = manager.create_environment(SandboxConfig(timeout_sec=0.05))

    container = client.containers.get(cid)
    container.exec_delay = 0.5  # Simulate long command

    res = manager.execute_command(cid, ["python", "-c", "while True: pass"])

    assert res.exit_code == 124
    assert res.timed_out is True
    assert res.oom_killed is False
    assert "timed out" in res.stderr
    assert container.killed is True


def test_execute_command_timeout_returns_promptly_without_blocking() -> None:
    """
    Verifies that execute_command returns within wall-clock timeout bounds (<0.35s)
    even when container.exec_run hangs for at least 5.0s in a worker thread.
    """
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    cid = manager.create_environment(SandboxConfig(timeout_sec=0.05))

    container = client.containers.get(cid)
    stop_event = threading.Event()
    container.stop_event = stop_event
    container.exec_delay = 5.0  # Simulate exec_run hanging for at least 5 seconds

    try:
        t0 = time.perf_counter()
        res = manager.execute_command(cid, ["python", "-c", "import time; time.sleep(10)"])
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.35, f"execute_command blocked for {elapsed:.3f}s; expected < 0.35s"
        assert res.exit_code == 124
        assert res.timed_out is True
        assert res.oom_killed is False
        assert "timed out" in res.stderr
        assert container.killed is True
    finally:
        stop_event.set()


def test_execute_command_worker_exception_propagates() -> None:
    """Verifies that exceptions raised within the worker thread propagate deterministically."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    cid = manager.create_environment(SandboxConfig())

    container = client.containers.get(cid)

    def faulty_exec_run(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("exec socket failed")

    container.exec_run = faulty_exec_run  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="exec socket failed"):
        manager.execute_command(cid, ["pytest"])


def test_execute_command_oom_killed_maps_to_exit_137() -> None:
    """Verifies that OOM termination sets oom_killed=True and exit code 137."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    cid = manager.create_environment(SandboxConfig())

    container = client.containers.get(cid)
    container.attrs = {"State": {"OOMKilled": True}}
    container.exec_result = DummyExecResult(
        exit_code=137,
        output=(b"", b"Killed: Out of memory\n"),
    )

    res = manager.execute_command(cid, ["pytest"])

    assert res.exit_code == 137
    assert res.oom_killed is True
    assert res.timed_out is False
    assert "Out of memory" in res.stderr


# =====================================================================
# 5. Cleanup Lifecycle Tests
# =====================================================================

def test_cleanup_kills_and_removes_container() -> None:
    """Verifies cleanup lifecycle kills and removes container without removing host volumes."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    cid = manager.create_environment(SandboxConfig())

    container = client.containers.get(cid)
    manager.cleanup(cid)

    assert container.killed is True
    assert container.removed is True
    assert container.remove_kwargs == {"force": True, "v": False}
    assert cid not in manager.active_containers

    # Idempotent cleanup on already cleaned up container
    manager.cleanup(cid)


# =====================================================================
# 6. Isolation Proofs: No Network / Daemon Sockets
# =====================================================================

def test_no_network_access_during_mocked_docker_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that zero network sockets are created when using DockerSandboxManager with mock client."""
    network_called = False

    def guard_socket(*args: Any, **kwargs: Any) -> None:
        nonlocal network_called
        network_called = True
        raise AssertionError("Network socket called during offline Docker sandbox test!")

    monkeypatch.setattr(socket.socket, "connect", guard_socket)
    monkeypatch.setattr(socket, "create_connection", guard_socket)

    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)
    cid = manager.create_environment(SandboxConfig())
    manager.execute_command(cid, ["pytest"])
    manager.cleanup(cid)

    assert not network_called, "Zero network or daemon socket connections allowed in offline tests."


# =====================================================================
# 7. Partial Creation & Fail-Closed Cleanup Tests (Slice 3)
# =====================================================================

def test_partial_container_start_failure_removes_container() -> None:
    """Verifies that container creation followed by start() failure forces container removal."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)

    original_create = client.containers.create
    created_container: Optional[DummyContainer] = None

    def failing_create(**kwargs: Any) -> DummyContainer:
        nonlocal created_container
        cont = original_create(**kwargs)
        created_container = cont

        def fail_start() -> None:
            raise RuntimeError("Docker daemon start failure: out of resources")

        cont.start = fail_start  # type: ignore[method-assign]
        return cont

    client.containers.create = failing_create  # type: ignore[method-assign]

    with pytest.raises(SandboxInitializationError) as exc_info:
        manager.create_environment(SandboxConfig())

    assert "Docker daemon start failure" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
    assert "out of resources" in str(exc_info.value.__cause__)
    assert created_container is not None
    assert created_container.removed is True
    assert created_container.remove_kwargs == {"force": True}
    assert manager.active_containers == []


def test_partial_container_registration_failure_removes_container() -> None:
    """Verifies that container creation followed by internal registration failure forces container removal."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)

    class FailingDict(Dict[str, float]):
        def __setitem__(self, key: str, val: float) -> None:
            raise RuntimeError("Internal state registration error")

    manager._container_timeouts = FailingDict()

    with pytest.raises(SandboxInitializationError) as exc_info:
        manager.create_environment(SandboxConfig())

    assert "Internal state registration error" in str(exc_info.value)
    assert manager.active_containers == []
    assert len(client.containers.containers) == 1
    created = list(client.containers.containers.values())[0]
    assert created.removed is True
    assert created.remove_kwargs == {"force": True}


def test_cleanup_failure_does_not_mask_initialization_failure() -> None:
    """Verifies that a failure during container.remove() does not mask primary start failure."""
    client = DummyDockerClient()
    manager = DockerSandboxManager(client=client)

    original_create = client.containers.create
    created_container: Optional[DummyContainer] = None

    def failing_create(**kwargs: Any) -> DummyContainer:
        nonlocal created_container
        cont = original_create(**kwargs)
        created_container = cont

        def fail_start() -> None:
            raise RuntimeError("Primary start failure")

        def fail_remove(**remove_kwargs: Any) -> None:
            raise RuntimeError("Secondary remove failure: daemon socket disconnected")

        cont.start = fail_start  # type: ignore[method-assign]
        cont.remove = fail_remove  # type: ignore[method-assign]
        return cont

    client.containers.create = failing_create  # type: ignore[method-assign]

    with pytest.raises(SandboxInitializationError) as exc_info:
        manager.create_environment(SandboxConfig())

    assert "Primary start failure" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
    assert "Primary start failure" in str(exc_info.value.__cause__)
    assert "Secondary remove failure" not in str(exc_info.value)
    assert manager.active_containers == []
