"""
In-memory mock sandbox manager for deterministic offline testing.
Iteration 1.5.1 Slice A Architecture.
"""

from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from agentic_test.core.protocols.sandbox import (
    ExecutionRawResult,
    SandboxConfig,
    SandboxInitializationError,
    SandboxManager,
)


class MockSandboxManager(SandboxManager):
    """
    Offline, deterministic in-memory realization of the SandboxManager protocol.

    Simulates container lifecycle state transitions and scripted command outcomes
    without requiring Docker, virtualization daemons, or network access.

    Lifecycle Behavior & Cleanup Policy:
    -----------------------------------
    - State Transitions: UNREGISTERED -> ACTIVE -> TERMINATED.
    - create_environment(config):
        Validates config and provisions a unique container ID. Sets container state
        to ACTIVE.
    - execute_command(container_id, command, workdir):
        Permitted only when container is ACTIVE. If container_id is unknown or has
        been TERMINATED, raises RuntimeError.
    - cleanup(container_id):
        Idempotent termination policy. If container_id is ACTIVE, transitions to
        TERMINATED. If container_id is already TERMINATED or unknown, completes
        safely as a no-op without raising errors.

    DISCLAIMER & NON-SECURITY CLAIM:
    --------------------------------
    This mock is strictly an offline software test double and lifecycle simulator.
    It does NOT execute or prove:
    - Docker daemon interaction or container virtualization;
    - Linux kernel cgroups enforcement (memory, CPU, PID limits);
    - Host or container network namespace isolation;
    - Non-root user ID (UID 10001) execution privilege dropping;
    - Read-only filesystem mount enforcement (EROFS);
    - Real OS-level SIGKILL process timeout termination.
    Real security guarantees and confinement validation require live container
    execution in Slice B and cannot be certified by this mock.
    """

    def __init__(
        self,
        default_result: Optional[ExecutionRawResult] = None,
    ) -> None:
        self._default_result: ExecutionRawResult = default_result or ExecutionRawResult(
            exit_code=0,
            stdout="",
            stderr="",
            duration_sec=0.1,
            timed_out=False,
            oom_killed=False,
        )
        self._containers: Dict[str, SandboxConfig] = {}
        self._active_containers: Set[str] = set()
        self._terminated_containers: Set[str] = set()
        self._calls: List[Dict[str, Any]] = []
        self._outcomes_queue: List[ExecutionRawResult] = []
        self._errors_queue: List[Exception] = []

    @property
    def calls(self) -> List[Dict[str, Any]]:
        """List of all execute_command invocations and their arguments."""
        return list(self._calls)

    @property
    def call_count(self) -> int:
        """Total number of execute_command invocations."""
        return len(self._calls)

    @property
    def active_containers(self) -> List[str]:
        """List of currently active container identifiers."""
        return sorted(list(self._active_containers))

    @property
    def terminated_containers(self) -> List[str]:
        """List of terminated container identifiers."""
        return sorted(list(self._terminated_containers))

    @property
    def created_configs(self) -> Dict[str, SandboxConfig]:
        """Mapping of container identifiers to their provisioning configurations."""
        return dict(self._containers)

    def enqueue_result(self, result: ExecutionRawResult) -> None:
        """Appends a scripted ExecutionRawResult to the FIFO outcome queue."""
        self._outcomes_queue.append(result)

    def enqueue_error(self, error: Exception) -> None:
        """Appends an exception to be raised on the next operation."""
        self._errors_queue.append(error)

    def set_default_result(self, result: ExecutionRawResult) -> None:
        """Overrides the default fallback ExecutionRawResult when outcome queue is empty."""
        self._default_result = result

    def script_success(
        self,
        stdout: str = "",
        stderr: str = "",
        duration_sec: float = 0.1,
    ) -> None:
        """Convenience helper to enqueue a successful outcome."""
        self.enqueue_result(
            ExecutionRawResult(
                exit_code=0,
                stdout=stdout,
                stderr=stderr,
                duration_sec=duration_sec,
                timed_out=False,
                oom_killed=False,
            )
        )

    def script_failure(
        self,
        exit_code: int = 1,
        stdout: str = "",
        stderr: str = "",
        duration_sec: float = 0.1,
    ) -> None:
        """Convenience helper to enqueue a non-zero exit outcome."""
        self.enqueue_result(
            ExecutionRawResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_sec=duration_sec,
                timed_out=False,
                oom_killed=False,
            )
        )

    def script_timeout(
        self,
        duration_sec: float = 30.0,
        stdout: str = "",
        stderr: str = "Command execution timed out",
    ) -> None:
        """Convenience helper to enqueue a timeout outcome."""
        self.enqueue_result(
            ExecutionRawResult(
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                duration_sec=duration_sec,
                timed_out=True,
                oom_killed=False,
            )
        )

    def script_oom(
        self,
        duration_sec: float = 0.5,
        stdout: str = "",
        stderr: str = "Out of memory: Killed process",
    ) -> None:
        """Convenience helper to enqueue an OOM outcome."""
        self.enqueue_result(
            ExecutionRawResult(
                exit_code=137,
                stdout=stdout,
                stderr=stderr,
                duration_sec=duration_sec,
                timed_out=False,
                oom_killed=True,
            )
        )

    def create_environment(self, config: SandboxConfig) -> str:
        """
        Provisions an in-memory simulated container environment.

        :param config: Sandbox configuration parameters.
        :return: Unique container identifier.
        :raises SandboxInitializationError: If an error was enqueued or config is invalid.
        """
        if self._errors_queue:
            err = self._errors_queue.pop(0)
            if isinstance(err, SandboxInitializationError):
                raise err
            raise SandboxInitializationError(f"Failed to initialize mock sandbox: {err}") from err

        if config.timeout_sec <= 0:
            raise SandboxInitializationError("Sandbox timeout must be positive.")

        container_id = f"mock-container-{uuid4().hex[:12]}"
        self._containers[container_id] = config
        self._active_containers.add(container_id)
        return container_id

    def execute_command(
        self,
        container_id: str,
        command: List[str],
        workdir: str = "/workspace",
    ) -> ExecutionRawResult:
        """
        Simulates executing a command inside the container.

        :param container_id: Active container identifier.
        :param command: Command tokens.
        :param workdir: Execution directory.
        :return: Scripted or default ExecutionRawResult.
        :raises RuntimeError: If container is unknown or already terminated.
        """
        if container_id in self._terminated_containers:
            raise RuntimeError(
                f"Container '{container_id}' has already been cleaned up and is terminated."
            )
        if container_id not in self._active_containers:
            raise RuntimeError(
                f"Container '{container_id}' is unknown or not active."
            )

        if self._errors_queue:
            raise self._errors_queue.pop(0)

        self._calls.append(
            {
                "container_id": container_id,
                "command": list(command),
                "workdir": workdir,
            }
        )

        if self._outcomes_queue:
            return self._outcomes_queue.pop(0)

        return self._default_result

    def cleanup(self, container_id: str) -> None:
        """
        Idempotent termination of the container environment.

        Transitions an active container to terminated. If container is already
        terminated or unknown, completes safely without raising errors.
        """
        if container_id in self._active_containers:
            self._active_containers.remove(container_id)
            self._terminated_containers.add(container_id)
