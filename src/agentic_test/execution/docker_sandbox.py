"""
Docker Sandbox Manager implementing isolated ephemeral container execution.
Stage 3 Section 4.4.2.2 Listing 4.2 and Section 4.4.4.5.
Iteration 1.5.1 Slice B.3.2.
"""

import importlib.util
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Set

from agentic_test.core.protocols.sandbox import (
    ExecutionRawResult,
    SandboxConfig,
    SandboxInitializationError,
    SandboxManager,
)


def is_docker_sdk_available() -> bool:
    """
    Checks whether the 'docker' Python package is installed.

    :return: True if 'docker' spec is found, False otherwise.
    """
    return importlib.util.find_spec("docker") is not None


class DockerSandboxManager(SandboxManager):
    """
    Concrete adapter realizing the SandboxManager protocol via the Docker Python SDK.

    Enforces:
    - Kernel network isolation (--network none).
    - Read-only source and candidate volume mounts (:ro).
    - Hardened security context (cap_drop ALL, no-new-privileges).
    - cgroups v2 resource ceilings (memory, cpu quota, pids limit).
    - Dedicated non-root user execution (10001:10001).
    - Host-side watchdog timeout with SIGKILL (exit code 124).
    - OOM kill detection (exit code 137).
    """

    def __init__(self, client: Optional[Any] = None) -> None:
        """
        Initializes the Docker sandbox manager.

        :param client: Optional pre-configured or mocked docker.DockerClient.
        :raises SandboxInitializationError: If Docker SDK is absent and no client was provided.
        """
        self._client: Optional[Any] = client
        self._active_containers: Set[str] = set()
        self._container_timeouts: Dict[str, float] = {}

        if client is None and not is_docker_sdk_available():
            raise SandboxInitializationError(
                "The 'docker' Python package is required to use DockerSandboxManager. "
                "Install it with: pip install 'agentic-test[docker]'"
            )

    def _get_client(self) -> Any:
        """
        Returns active Docker client, lazily initializing from environment if needed.

        :return: DockerClient instance.
        :raises SandboxInitializationError: If daemon connection fails.
        """
        if self._client is not None:
            return self._client

        try:
            import importlib
            docker_mod = importlib.import_module("docker")
            self._client = docker_mod.from_env()
            return self._client
        except Exception as err:
            raise SandboxInitializationError(
                f"Failed to connect to Docker daemon: {err}"
            ) from err

    @property
    def active_containers(self) -> List[str]:
        """List of active container identifiers managed by this instance."""
        return sorted(list(self._active_containers))

    def create_environment(self, config: SandboxConfig) -> str:
        """
        Provisions an isolated ephemeral Docker container according to SandboxConfig.

        :param config: Sandbox configuration parameters.
        :return: Unique container identifier string.
        :raises SandboxInitializationError: If container creation fails.
        """
        client = self._get_client()

        # 1. Translate volume mounts
        volumes: Dict[str, Dict[str, str]] = {}
        for host_path, container_bind in config.read_only_mounts.items():
            volumes[str(host_path)] = {"bind": container_bind, "mode": "ro"}
        for host_path, container_bind in config.read_write_mounts.items():
            volumes[str(host_path)] = {"bind": container_bind, "mode": "rw"}

        # 2. Translate resource quotas
        nano_cpus = int(config.cpu_quota * 1_000_000_000)

        # 3. Assemble Docker container creation parameters
        create_kwargs: Dict[str, Any] = {
            "image": config.image_tag,
            "command": ["tail", "-f", "/dev/null"],
            "detach": True,
            "network_mode": "none" if config.network_disabled else "bridge",
            "user": "10001:10001",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": config.memory_limit,
            "memswap_limit": config.memory_limit,
            "nano_cpus": nano_cpus,
            "pids_limit": config.pids_limit,
            "volumes": volumes,
            "tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
        }

        if config.environment_vars:
            create_kwargs["environment"] = dict(config.environment_vars)

        try:
            container = client.containers.create(**create_kwargs)
            container.start()
            container_id = str(container.id)
            self._active_containers.add(container_id)
            self._container_timeouts[container_id] = config.timeout_sec
            return container_id
        except Exception as err:
            raise SandboxInitializationError(
                f"Docker daemon failed to provision container: {err}"
            ) from err

    def execute_command(
        self,
        container_id: str,
        command: List[str],
        workdir: str = "/workspace",
    ) -> ExecutionRawResult:
        """
        Executes a command inside the container under enforced timeout limits.

        :param container_id: Active container identifier.
        :param command: Command tokens (e.g. ['pytest', 'tests/']).
        :param workdir: Execution directory within container.
        :return: ExecutionRawResult containing exit codes and captured telemetry.
        :raises RuntimeError: If container is unknown or unreachable.
        """
        client = self._get_client()
        try:
            container = client.containers.get(container_id)
        except Exception as err:
            raise RuntimeError(f"Container '{container_id}' not found or unreachable: {err}") from err

        start_time = time.perf_counter()
        timeout_sec = self._container_timeouts.get(container_id, 30.0)

        exec_result: Any = None
        worker_error: Optional[BaseException] = None

        def _exec_worker() -> None:
            nonlocal exec_result, worker_error
            try:
                exec_result = container.exec_run(
                    cmd=command,
                    workdir=workdir,
                    demux=True,
                )
            except BaseException as err:
                worker_error = err

        # Run command execution in a dedicated daemon thread.
        #
        # Note on Docker SDK cancellation:
        # A daemon thread running container.exec_run cannot be forcibly interrupted
        # in Python. When timeout occurs, container.kill() is attempted to terminate the
        # container process, followed by a brief bounded grace period. If the underlying
        # Docker SDK call or socket read remains blocked, the daemon worker thread may
        # remain alive until process termination, but it will never block this method's
        # return or the Python interpreter's shutdown.
        worker = threading.Thread(
            target=_exec_worker,
            name=f"docker-exec-{container_id}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout=timeout_sec)

        timed_out = False
        if worker.is_alive():
            timed_out = True
            try:
                container.kill()
            except Exception:
                pass
            # Bounded brief grace period allowing container.kill() signal propagation
            # to unblock the socket read, without risking an unbounded wait.
            worker.join(timeout=0.1)

        duration = time.perf_counter() - start_time

        if timed_out:
            return ExecutionRawResult(
                exit_code=124,
                stdout="",
                stderr=f"Command execution timed out after {timeout_sec:.1f}s (SIGKILL)",
                duration_sec=duration,
                timed_out=True,
                oom_killed=False,
            )

        if worker_error is not None:
            raise worker_error

        # Demultiplex stdout and stderr
        raw_out = exec_result.output
        if isinstance(raw_out, tuple):
            stdout_bytes, stderr_bytes = raw_out
        else:
            stdout_bytes, stderr_bytes = raw_out, b""

        stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")

        # Check for OOM termination
        oom_killed = False
        try:
            container.reload()
            state = getattr(container, "attrs", {}).get("State", {})
            if state.get("OOMKilled") or exec_result.exit_code == 137:
                oom_killed = True
        except Exception:
            if exec_result.exit_code == 137:
                oom_killed = True

        exit_code = 137 if oom_killed else int(exec_result.exit_code)

        return ExecutionRawResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_sec=duration,
            timed_out=False,
            oom_killed=oom_killed,
        )

    def cleanup(self, container_id: str) -> None:
        """
        Forcibly destroys and removes the container environment.
        Idempotent: completes safely if container is already terminated or removed.

        :param container_id: Identifier of container to terminate.
        """
        client = self._get_client()
        try:
            container = client.containers.get(container_id)
            try:
                container.kill()
            except Exception:
                pass
            try:
                container.remove(force=True, v=False)
            except Exception:
                pass
        except Exception:
            pass

        self._active_containers.discard(container_id)
        self._container_timeouts.pop(container_id, None)
