"""
Formal protocol defining isolated ephemeral container sandboxes.
Stage 3 Section 4.4.2.2 Listing 4.2.
"""

from pathlib import Path
from typing import Dict, List, Optional, Protocol, runtime_checkable
from pydantic import BaseModel, Field


class SandboxError(Exception):
    """Base exception for sandbox infrastructure errors."""
    pass


class SandboxInitializationError(SandboxError):
    """Raised when environment or container provisioning fails."""
    pass


class SandboxExecutionError(SandboxError):
    """Raised when command execution fails due to container or infrastructure error."""
    pass


class SandboxConfig(BaseModel):
    """
    Sandbox confinement and mounting configuration.
    Stage 3 Section 4.4.2.2 Listing 4.2.
    """
    image_tag: str = "agentic-runner:0.1.0"
    timeout_sec: float = 30.0
    memory_limit: str = "512m"
    cpu_quota: float = 1.0
    pids_limit: int = 100
    network_disabled: bool = True
    read_only_mounts: Dict[Path, str] = Field(default_factory=dict)
    read_write_mounts: Dict[Path, str] = Field(default_factory=dict)
    environment_vars: Dict[str, str] = Field(default_factory=dict)


class ExecutionRawResult(BaseModel):
    """
    Low-level process execution outcomes and captured telemetry.
    Stage 3 Section 4.4.2.2 Listing 4.2.
    """
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool
    oom_killed: bool


@runtime_checkable
class SandboxManager(Protocol):
    """
    Formal protocol managing isolated ephemeral container sandboxes.
    Enforces security confinement, network disallowance, and resource boundaries.
    Stage 3 Section 4.4.2.2 Listing 4.2.
    """

    def create_environment(self, config: SandboxConfig) -> str:
        """
        Provisions an isolated container sandbox adhering strictly to SandboxConfig.

        :param config: Sandbox confinement and mounting configuration.
        :return: Unique container identifier string.
        :raises SandboxInitializationError: If daemon fails to spawn container.
        """
        ...

    def execute_command(
        self,
        container_id: str,
        command: List[str],
        workdir: str = "/workspace",
    ) -> ExecutionRawResult:
        """
        Executes a command inside the container under enforced timeout limits.

        :param container_id: Active container identifier.
        :param command: Command tokens (e.g., ['pytest', 'tests/']).
        :param workdir: Execution directory within container.
        :return: ExecutionRawResult containing exit codes and captured telemetry.
        """
        ...

    def cleanup(self, container_id: str) -> None:
        """
        Forcibly destroys and removes the container and associated ephemeral mounts.

        :param container_id: Identifier of container to terminate.
        """
        ...
