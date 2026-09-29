"""
Gate 3 Isolated Containerized Pytest Collection Validator.
Stage 2 Section 4.3.2.4 (UC-04), Stage 3 Section 4.4.4.4.
Enforces Safety Invariant INV-01 by executing candidate collection strictly within an ephemeral sandbox.
"""

from pathlib import Path
import re
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple
import uuid

from agentic_test.core.models import TestCandidate, ValidationStatus
from agentic_test.core.protocols.sandbox import (
    ExecutionRawResult,
    SandboxConfig,
    SandboxManager,
)
from agentic_test.execution.runner import PytestRunner
from agentic_test.execution.staging import CandidateStagingArea, StagingValidationError
from agentic_test.validation.base import BaseValidator
from agentic_test.validation.models import ValidationResult


class CollectionValidator(BaseValidator):
    """
    Gate 3 isolated containerized pytest collection validator.

    Evaluates candidate importability, fixture resolution, and syntax in an isolated
    container sandbox (INV-01) without executing arbitrary test code on the host.

    Guarantees:
    1. Zero host execution: candidate code is never imported or run on the host environment.
    2. Ephemeral staging: uses dedicated collection staging boundary requiring Gates 1 & 2 pass.
    3. Resource bounded: enforced timeout <= 10.0s, memory ceiling, read-only mounts.
    4. Deterministic outcome parsing: parses test items count and failure diagnostics.
    5. Clean teardown: container and staging directories destroyed on all termination paths.
    """

    def __init__(
        self,
        sandbox_manager: SandboxManager,
        source_root: Optional[Path] = None,
        runner: Optional[PytestRunner] = None,
        staging_root: Optional[Path] = None,
        timeout_sec: float = 10.0,
        image_tag: str = "agentic-runner:0.1.0",
        memory_limit: str = "512m",
        cpu_quota: float = 1.0,
        pids_limit: int = 100,
        environment_vars: Optional[Dict[str, str]] = None,
        keep_artifacts: bool = False,
    ) -> None:
        if timeout_sec <= 0:
            raise ValueError(f"timeout_sec must be positive, got {timeout_sec}")

        self._sandbox_manager = sandbox_manager
        self._source_root = source_root.resolve() if source_root is not None else None
        self._runner = runner or PytestRunner()
        self._staging_root = staging_root.resolve() if staging_root is not None else None
        # Enforce bounded collection timeout ceiling of at most 10 seconds (FR-13, Stage 3)
        self._timeout_sec = min(float(timeout_sec), 10.0)
        self._image_tag = image_tag
        self._memory_limit = memory_limit
        self._cpu_quota = cpu_quota
        self._pids_limit = pids_limit
        self._environment_vars = dict(environment_vars or {})
        self._keep_artifacts = keep_artifacts

    @property
    def gate_name(self) -> str:
        return "GATE_3_COLLECTION"

    @property
    def timeout_sec(self) -> float:
        return self._timeout_sec

    @property
    def sandbox_manager(self) -> SandboxManager:
        return self._sandbox_manager

    @property
    def runner(self) -> PytestRunner:
        return self._runner

    def validate(
        self,
        candidate: TestCandidate,
        static_gates_passed: bool = True,
    ) -> ValidationResult:
        """
        Validates candidate importability and test collection in an isolated container.

        :param candidate: TestCandidate entity to validate.
        :param static_gates_passed: Certifies that Gates 1 and 2 passed (default True).
        :return: ValidationResult with status PASSED or REJECTED_COLLECTION.
        """
        # Reject if static gates were not confirmed
        if not static_gates_passed:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_COLLECTION,
                gate=self.gate_name,
                passed=False,
                error_message="Candidate has not passed static syntax and security gates (Gates 1 & 2).",
            )

        # Short-circuit if candidate was already rejected or quarantined
        if candidate.validation_status in (
            ValidationStatus.REJECTED_SYNTAX,
            ValidationStatus.REJECTED_SECURITY,
            ValidationStatus.QUARANTINED,
        ):
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=candidate.validation_status,
                gate=self.gate_name,
                passed=False,
                error_message=f"Candidate was already rejected/quarantined with status: {candidate.validation_status.value}",
            )

        # 1. Ephemeral run-scoped collection staging
        session_suffix = f"{candidate.candidate_id}_{uuid.uuid4().hex[:8]}"
        if self._staging_root is not None:
            created_staging_dir = self._staging_root / f"coll_staging_{session_suffix}"
        else:
            created_staging_dir = Path(tempfile.mkdtemp(prefix="agentic_coll_staging_"))

        staging_area: Optional[CandidateStagingArea] = None
        container_id: Optional[str] = None
        raw_result: Optional[ExecutionRawResult] = None
        exec_error: Optional[BaseException] = None

        try:
            staging_area = CandidateStagingArea(
                staging_root=created_staging_dir,
                auto_cleanup=False,
            )
            staging_area.stage_candidate_for_collection(
                candidate=candidate,
                static_gates_passed=True,
            )

            # 2. Configure read-only volume mounts (INV-01, INV-02)
            read_only_mounts: Dict[Path, str] = {
                staging_area.tests_dir: "/workspace/tests",
            }
            if self._source_root is not None and self._source_root.exists():
                read_only_mounts[self._source_root] = "/workspace/src"

            # 3. Environment variables (set PYTHONPATH to include source tree and workspace)
            env_vars = dict(self._environment_vars)
            if "PYTHONPATH" not in env_vars:
                env_vars["PYTHONPATH"] = "/workspace/src:/workspace"
            else:
                env_vars["PYTHONPATH"] = f"/workspace/src:/workspace:{env_vars['PYTHONPATH']}"

            # 4. Construct deterministic collection command via PytestRunner
            cmd = self._runner.construct_collection_command("/workspace/tests")

            # 5. Build SandboxConfig
            sandbox_config = SandboxConfig(
                image_tag=self._image_tag,
                timeout_sec=self._timeout_sec,
                memory_limit=self._memory_limit,
                cpu_quota=self._cpu_quota,
                pids_limit=self._pids_limit,
                network_disabled=True,
                read_only_mounts=read_only_mounts,
                read_write_mounts={},
                environment_vars=env_vars,
            )

            # 6. Execute in container
            container_id = self._sandbox_manager.create_environment(sandbox_config)
            raw_result = self._sandbox_manager.execute_command(
                container_id=container_id,
                command=cmd,
                workdir="/workspace",
            )
        except StagingValidationError as s_err:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_COLLECTION,
                gate=self.gate_name,
                passed=False,
                error_message=f"Collection staging failed: {s_err}",
                diagnostics=(str(s_err),),
            )
        except Exception as err:
            exec_error = err
        finally:
            # 7. Guaranteed teardown of container and ephemeral staging mounts
            if container_id is not None:
                try:
                    self._sandbox_manager.cleanup(container_id)
                except Exception:
                    pass

            if not self._keep_artifacts:
                if staging_area is not None:
                    try:
                        staging_area.cleanup()
                    except Exception:
                        pass
                elif created_staging_dir.exists():
                    shutil.rmtree(created_staging_dir, ignore_errors=True)

        # 8. Parse outcomes
        if exec_error is not None:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_COLLECTION,
                gate=self.gate_name,
                passed=False,
                error_message=f"Sandbox execution error during collection: {exec_error}",
                diagnostics=(str(exec_error),),
            )

        if raw_result is None:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_COLLECTION,
                gate=self.gate_name,
                passed=False,
                error_message="Sandbox returned no execution result.",
            )

        if raw_result.timed_out or raw_result.exit_code == 124:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_COLLECTION,
                gate=self.gate_name,
                passed=False,
                error_message=f"Pytest collection timed out after {self._timeout_sec}s.",
                diagnostics=(raw_result.stdout, raw_result.stderr),
            )

        if raw_result.oom_killed or raw_result.exit_code == 137:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_COLLECTION,
                gate=self.gate_name,
                passed=False,
                error_message="Pytest collection terminated due to container out-of-memory (OOM).",
                diagnostics=(raw_result.stdout, raw_result.stderr),
            )

        if raw_result.exit_code != 0:
            err_msg, diags = self._extract_collection_failure_diagnostic(
                raw_result.stdout, raw_result.stderr, raw_result.exit_code
            )
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_COLLECTION,
                gate=self.gate_name,
                passed=False,
                error_message=err_msg,
                diagnostics=diags,
            )

        # Exit code is 0: verify at least one test item was collected
        collected_items = self._extract_collected_items(raw_result.stdout)
        if len(collected_items) == 0:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_COLLECTION,
                gate=self.gate_name,
                passed=False,
                error_message="Pytest collection succeeded with exit code 0 but zero test items were collected.",
                diagnostics=(raw_result.stdout, raw_result.stderr),
            )

        # Successfully collected >= 1 items!
        return ValidationResult(
            candidate_id=candidate.candidate_id,
            status=ValidationStatus.PASSED,
            gate=self.gate_name,
            passed=True,
            error_message=None,
            diagnostics=(f"Collected {len(collected_items)} test item(s)", *collected_items),
        )

    @staticmethod
    def _extract_collected_items(stdout: str) -> List[str]:
        """
        Parses stdout from pytest -q --collect-only to extract collected test item identifiers.
        """
        items: List[str] = []
        if not stdout:
            return items

        for line in stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if "::" in line_str and not line_str.startswith(("ERROR", "FAILED", "WARNING")):
                token = line_str.split()[0]
                if "::" in token:
                    items.append(token)

        if not items:
            count_m = (
                re.search(r"\b([1-9][0-9]*)\s+tests?\s+collected\b", stdout)
                or re.search(r"collected\s+([1-9][0-9]*)\s+items?\b", stdout)
                or re.search(r"\b([1-9][0-9]*)\s+selected\b", stdout)
            )
            if count_m:
                count = int(count_m.group(1))
                items = [f"collected_item_{i+1}" for i in range(count)]

        return items

    @staticmethod
    def _extract_collection_failure_diagnostic(
        stdout: str, stderr: str, exit_code: int
    ) -> Tuple[str, Tuple[str, ...]]:
        """
        Extracts high-fidelity diagnostic error messages and lines from collection failures.
        """
        combined = f"{stdout}\n{stderr}"
        diags: List[str] = [line.strip() for line in combined.splitlines() if line.strip()]

        # 1. ModuleNotFoundError or ImportError
        mod_err = re.search(r"((?:ModuleNotFoundError|ImportError):[^\n]+)", combined)
        if mod_err:
            return f"Pytest collection failed: {mod_err.group(1).strip()}", tuple(diags)

        # 2. SyntaxError during collection
        syn_err = re.search(r"(SyntaxError:[^\n]+)", combined)
        if syn_err:
            return f"Pytest collection failed: {syn_err.group(1).strip()}", tuple(diags)

        # 3. Missing fixture
        fix_err = re.search(r"(fixture\s+'[^']+'\s+not\s+found[^\n]*)", combined, re.IGNORECASE)
        if fix_err:
            return f"Pytest collection failed: {fix_err.group(1).strip()}", tuple(diags)

        # 4. Collection error header
        err_header = re.search(r"ERROR\s+collecting\s+[^\n]+", combined)
        if err_header:
            return f"Pytest collection failed: {err_header.group(0).strip()}", tuple(diags)

        return f"Pytest collection failed with exit code {exit_code}.", tuple(diags)
