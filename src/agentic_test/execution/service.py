"""
ExecutionService orchestrating sandboxed container execution and telemetry extraction.
Stage 2 Section 4.3.2.5 (UC-05), Stage 3 Section 4.4.4.5 Listing 4.2.
Iteration 1.5 Slice D.
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple, Union
import uuid

from pydantic import BaseModel, ConfigDict

from agentic_test.analysis.git_service import is_excluded_path
from agentic_test.core.models import (
    ExecutionEvidence,
    ExecutionPlan,
    TestCandidate,
    ValidationStatus,
)
from agentic_test.core.protocols.sandbox import (
    ExecutionRawResult,
    SandboxConfig,
    SandboxManager,
)
from agentic_test.execution.coverage import CoverageExtractor, CoverageMetrics
from agentic_test.execution.runner import PytestRunner, TestRunSummary
from agentic_test.execution.staging import CandidateStagingArea, StagingValidationError


class CoverageSettings(BaseModel):
    """
    Cryptographic and telemetry execution settings used for comparability checks.
    Stage 3 Section 4.4.4.5, Stage 2 Section 4.3.2.5.
    """
    model_config = ConfigDict(frozen=True)

    source_root: str
    cov_source: str
    cov_branch: bool


class ExecutionError(Exception):
    """Base exception for execution subsystem errors."""
    pass


class SourceIntegrityError(ExecutionError):
    """
    Raised when Safety Invariant INV-02 is violated:
    Production source files were modified during sandbox execution.
    UC-05 Extension 9a: PRODUCTION_SOURCE_INTEGRITY_VIOLATION.
    """
    pass


# Formal Stage 2 alias for UC-05 Extension 9a
ProductionSourceIntegrityViolation = SourceIntegrityError


def compute_tree_hash(root: Path) -> str:
    """
    Computes a deterministic cryptographic SHA-256 hash of all files under root.
    Excludes caches, virtualenvs, and .git directories (INV-02).

    :param root: Root directory path to hash.
    :return: 64-character lowercase hex digest string.
    """
    hasher = hashlib.sha256()
    if not root.exists():
        return hasher.hexdigest()

    if root.is_file():
        try:
            hasher.update(root.read_bytes())
        except OSError:
            pass
        return hasher.hexdigest()

    # Collect non-excluded files in deterministic sorted order
    files: List[Path] = []
    for p in root.rglob("*"):
        if p.is_file():
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if not is_excluded_path(rel):
                files.append(p)

    files.sort()
    for f in files:
        try:
            rel_str = f.relative_to(root).as_posix()
            content_hash = hashlib.sha256(f.read_bytes()).hexdigest()
            hasher.update(f"{rel_str}:{content_hash}\n".encode("utf-8"))
        except OSError:
            continue

    return hasher.hexdigest()


class ExecutionService:
    """
    High-level facade coordinating container sandbox execution and evidence capture.
    Stage 2 UC-05, Stage 3 Section 4.4.4.5.

    Coordinates:
    1. Validation filtering & candidate staging to isolated ephemeral mount (INV-04).
    2. Pre/post cryptographic SHA-256 source tree hashing (INV-02).
    3. Deterministic CLI command construction via PytestRunner.
    4. Ephemeral container lifecycle confinement via SandboxManager (INV-01).
    5. Test outcome telemetry parsing & failure traceback extraction.
    6. Coverage telemetry extraction via CoverageExtractor.
    7. Fail-closed cleanup of container, staging volume, and scratch mounts in all paths.
    """

    def register_evidence_settings(
        self,
        evidence_id: str,
        source_root: Union[Path, str],
        cov_source: str,
        cov_branch: bool,
    ) -> None:
        """
        Explicitly registers coverage configuration settings for an ExecutionEvidence record
        on this service instance. Ensures comparability invariants can be verified even
        for pre-computed baselines.
        """
        if len(self._evidence_settings) >= 50:
            oldest_key = next(iter(self._evidence_settings))
            del self._evidence_settings[oldest_key]

        self._evidence_settings[evidence_id] = CoverageSettings(
            source_root=str(Path(source_root).resolve()),
            cov_source=cov_source,
            cov_branch=cov_branch,
        )

    def get_evidence_settings(self, evidence_id: str) -> Optional[CoverageSettings]:
        """Returns the registered CoverageSettings for a given evidence record ID on this service instance."""
        return self._evidence_settings.get(evidence_id)

    def release_evidence_settings(self, evidence_id: str) -> None:
        """Releases the registered CoverageSettings for a specific evidence record ID."""
        self._evidence_settings.pop(evidence_id, None)

    def clear_evidence_settings(self) -> None:
        """Clears registered evidence coverage settings owned by this service instance."""
        self._evidence_settings.clear()

    def __init__(
        self,
        sandbox_manager: SandboxManager,
        source_root: Path,
        runner: Optional[PytestRunner] = None,
        coverage_extractor: Optional[CoverageExtractor] = None,
        staging_root: Optional[Path] = None,
        scratch_root: Optional[Path] = None,
        timeout_sec: float = 30.0,
        image_tag: str = "agentic-runner:0.1.0",
        memory_limit: str = "512m",
        cpu_quota: float = 1.0,
        pids_limit: int = 100,
        cov_source: str = "/workspace/src",
        cov_branch: bool = True,
        environment_vars: Optional[Dict[str, str]] = None,
        keep_artifacts: bool = False,
    ) -> None:
        """
        Initializes the ExecutionService orchestrator.

        :param sandbox_manager: Real or mock implementation of SandboxManager protocol.
        :param source_root: Path to repository application source on host (mounted at /workspace/src:ro).
        :param runner: PytestRunner instance for command construction and telemetry parsing.
        :param coverage_extractor: CoverageExtractor instance for JSON coverage parsing.
        :param staging_root: Root path for ephemeral candidate staging (mounted at /workspace/tests:ro).
        :param scratch_root: Root path for ephemeral execution outputs (mounted at /workspace/output:rw).
        :param timeout_sec: Container execution wall-clock timeout ceiling (default 30.0s, FR-13).
        :param image_tag: Hardened runner container image tag (default 'agentic-runner:0.1.0', FR-11).
        :param memory_limit: Confinement memory ceiling (default '512m', FR-14, INV-01).
        :param cpu_quota: Confinement CPU quota ceiling (default 1.0, INV-01).
        :param pids_limit: Confinement process count ceiling (default 100, INV-01).
        :param cov_source: Path inside container to measure coverage on (default '/workspace/src').
        :param cov_branch: Whether to measure branch coverage (default True, FR-15).
        :param environment_vars: Optional container environment variable overrides.
        :param keep_artifacts: If True, preserves staging and scratch directories for debugging.
        :raises ValueError: If source_root is None, does not exist, or is not a directory.
        """
        if source_root is None:
            raise ValueError("'source_root' is mandatory and cannot be None.")
        if not isinstance(source_root, Path):
            try:
                source_root = Path(source_root)
            except Exception as err:
                raise ValueError(f"Invalid 'source_root': {err}") from err

        resolved_source = source_root.resolve()
        if not resolved_source.exists():
            raise ValueError(f"'source_root' path does not exist: {resolved_source}")
        if not resolved_source.is_dir():
            raise ValueError(f"'source_root' must be a directory, but got file: {resolved_source}")

        self._sandbox_manager = sandbox_manager
        self._source_root: Path = resolved_source
        self._runner = runner or PytestRunner()
        self._coverage_extractor = coverage_extractor or CoverageExtractor()
        self._staging_root = staging_root.resolve() if staging_root else None
        self._scratch_root = scratch_root.resolve() if scratch_root else None
        self._timeout_sec = timeout_sec
        self._image_tag = image_tag
        self._memory_limit = memory_limit
        self._cpu_quota = cpu_quota
        self._pids_limit = pids_limit
        self._cov_source = cov_source
        self._cov_branch = bool(cov_branch)
        self._environment_vars = dict(environment_vars or {})
        self._keep_artifacts = keep_artifacts

        self._last_summary: Optional[TestRunSummary] = None
        self._last_coverage: Optional[CoverageMetrics] = None
        self._last_evidence: Optional[ExecutionEvidence] = None
        self._evidence_settings: Dict[str, CoverageSettings] = {}

    @property
    def source_root(self) -> Path:
        """Configured application source root path on host."""
        return self._source_root

    @property
    def sandbox_manager(self) -> SandboxManager:
        """Configured SandboxManager protocol instance."""
        return self._sandbox_manager

    @property
    def runner(self) -> PytestRunner:
        """Configured PytestRunner instance."""
        return self._runner

    @property
    def coverage_extractor(self) -> CoverageExtractor:
        """Configured CoverageExtractor instance."""
        return self._coverage_extractor

    @property
    def cov_source(self) -> str:
        """Configured coverage source path inside container."""
        return self._cov_source

    @property
    def cov_branch(self) -> bool:
        """Whether branch coverage measurement is enabled."""
        return self._cov_branch

    @property
    def last_summary(self) -> Optional[TestRunSummary]:
        """Parsed outcome summary from the most recent execution, if available."""
        return self._last_summary

    @property
    def last_coverage(self) -> Optional[CoverageMetrics]:
        """Parsed coverage metrics from the most recent execution, if available."""
        return self._last_coverage

    @property
    def last_evidence(self) -> Optional[ExecutionEvidence]:
        """Populated ExecutionEvidence record from the most recent execution, if available."""
        return self._last_evidence

    def execute_candidate(
        self,
        plan: ExecutionPlan,
        candidate: TestCandidate,
        run_id: Optional[str] = None,
        baseline_evidence: Optional[ExecutionEvidence] = None,
        release_baseline: bool = True,
    ) -> ExecutionEvidence:
        """
        Convenience execution helper for a single TestCandidate.

        :param plan: ExecutionPlan defining the run context.
        :param candidate: Single TestCandidate entity.
        :param run_id: Optional explicit run identifier.
        :param baseline_evidence: Optional baseline evidence for coverage delta computation.
        :param release_baseline: Whether to release baseline evidence settings after execution (default True).
        :return: Populated ExecutionEvidence record.
        """
        evs = self.execute_candidates(
            plan=plan,
            candidates=(candidate,),
            baseline_evidence=baseline_evidence,
            run_id=run_id,
            release_baseline=release_baseline,
        )
        return evs[0]

    def execute_regression_baseline(
        self,
        plan: ExecutionPlan,
        run_id: Optional[str] = None,
    ) -> ExecutionEvidence:
        """
        Executes planned regression suite without candidates to capture baseline telemetry.

        :param plan: ExecutionPlan defining the existing regression tests.
        :param run_id: Optional explicit run identifier.
        :return: ExecutionEvidence with candidate_id=None.
        :raises ValueError: If plan.existing_tests_to_run is empty.
        """
        if not plan.existing_tests_to_run:
            raise ValueError(
                "ExecutionPlan.existing_tests_to_run cannot be empty for regression baseline execution."
            )
        return self.execute(
            plan=plan,
            candidates=(),
            run_id=run_id,
            candidate_id=None,
            baseline_evidence=None,
            release_baseline=False,
        )

    def execute_candidates(
        self,
        plan: ExecutionPlan,
        candidates: Sequence[TestCandidate],
        baseline_evidence: Optional[ExecutionEvidence] = None,
        run_id: Optional[str] = None,
        release_baseline: bool = True,
    ) -> Tuple[ExecutionEvidence, ...]:
        """
        Executes candidates in isolated per-candidate container sessions (INV-01, INV-04).
        Preserves deterministic input order.

        :param plan: ExecutionPlan defining target symbols and routes.
        :param candidates: Sequence of TestCandidate entities to execute.
        :param baseline_evidence: Optional baseline evidence for coverage delta computation.
        :param run_id: Optional explicit run identifier.
        :param release_baseline: Whether to release baseline evidence settings after execution (default True).
        :return: Tuple of ExecutionEvidence records corresponding to each candidate in input order.
        :raises StagingValidationError: If any candidate has validation_status other than PASSED.
        """
        if not candidates:
            return ()

        # Fail fast if any candidate is not PASSED (INV-04)
        for cand in candidates:
            if cand.validation_status != ValidationStatus.PASSED:
                raise StagingValidationError(
                    f"Candidate '{cand.candidate_id}' has validation_status={cand.validation_status}. "
                    "Only candidates with validation_status == PASSED can be executed (INV-04)."
                )

        evidences: List[ExecutionEvidence] = []
        try:
            for cand in candidates:
                ev = self.execute(
                    plan=plan,
                    candidates=(cand,),
                    run_id=run_id,
                    candidate_id=cand.candidate_id,
                    baseline_evidence=baseline_evidence,
                    release_baseline=False,
                )
                evidences.append(ev)
            return tuple(evidences)
        finally:
            if release_baseline and baseline_evidence is not None:
                self.release_evidence_settings(baseline_evidence.evidence_id)

    def execute(
        self,
        plan: ExecutionPlan,
        candidates: Sequence[TestCandidate] = (),
        run_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        baseline_evidence: Optional[ExecutionEvidence] = None,
        release_baseline: bool = True,
    ) -> ExecutionEvidence:
        """
        Orchestrates full sandboxed container execution and returns ExecutionEvidence.
        Stage 2 UC-05, Stage 3 Section 4.4.4.5.

        :param plan: ExecutionPlan defining target symbols and routes.
        :param candidates: Sequence of TestCandidate entities to execute.
        :param run_id: Optional explicit run identifier; defaults to candidate.run_id or plan.plan_id.
        :param candidate_id: Optional explicit candidate identifier for evidence linking.
        :param baseline_evidence: Optional baseline evidence for coverage delta computation.
        :return: Immutable ExecutionEvidence containing exit codes, outputs, duration, and coverage.
        :raises SourceIntegrityError: If production source files were modified (INV-02).
        :raises StagingValidationError: If candidates were provided but none passed validation.
        :raises ValueError: If neither candidates nor existing tests are available for execution,
                            or if multiple candidates are batched into a single execution session.
        """
        # Batch execution of multiple candidates into a single container session is strictly prohibited (INV-04)
        if len(candidates) > 1:
            raise ValueError(
                "Batch execution of multiple candidates in a single container session is prohibited. "
                "Use execute_candidates() to execute candidates in isolated per-candidate sessions."
            )

        # 1. Resolve effective identifiers
        effective_run_id = (
            run_id
            or (candidates[0].run_id if candidates else None)
            or plan.plan_id
        )

        # 2. Filter candidates for PASSED validation status (INV-04)
        passed_candidates = [
            c for c in candidates if c.validation_status == ValidationStatus.PASSED
        ]

        # Fail closed if candidates were passed but none were validated
        if len(candidates) > 0 and len(passed_candidates) == 0:
            raise StagingValidationError(
                "None of the provided test candidates have validation_status == PASSED. "
                "Unvalidated or rejected candidates must never be staged or executed (INV-04)."
            )

        # Fail closed if there is nothing to execute
        if len(passed_candidates) == 0 and not plan.existing_tests_to_run:
            raise ValueError(
                "No validated test candidates or existing tests available for execution."
            )

        # Resolve effective candidate ID: non-None for candidates, None for baseline
        if candidate_id is not None:
            effective_candidate_id: Optional[str] = candidate_id
        elif passed_candidates:
            effective_candidate_id = passed_candidates[0].candidate_id
        else:
            effective_candidate_id = None

        # 3. Step 1 of UC-05: Source integrity pre-hash recording (INV-02)
        try:
            pre_source_hash = compute_tree_hash(self._source_root)
        except Exception as err:
            raise SourceIntegrityError(
                f"Safety Invariant INV-02 violated: Failed to compute pre-execution source hash "
                f"for '{self._source_root}': {err}"
            ) from err

        # 4. Set up ephemeral staging area and scratch output directory
        staging_area: Optional[CandidateStagingArea] = None
        scratch_dir: Optional[Path] = None
        created_staging_dir: Optional[Path] = None
        integrity_verified = False

        try:
            # Create run-scoped staging area
            session_suffix = f"{effective_run_id}_{uuid.uuid4().hex[:8]}"
            if self._staging_root is not None:
                created_staging_dir = self._staging_root / f"staging_{session_suffix}"
            else:
                created_staging_dir = Path(tempfile.mkdtemp(prefix="agentic_staging_"))

            staging_area = CandidateStagingArea(
                staging_root=created_staging_dir,
                auto_cleanup=False,
            )

            # Stage validated candidates to staging_root/tests/
            if passed_candidates:
                staging_area.stage_candidates(passed_candidates)

            # Create run-scoped scratch output directory
            if self._scratch_root is not None:
                scratch_dir = self._scratch_root / f"scratch_{session_suffix}"
                scratch_dir.mkdir(parents=True, exist_ok=True)
            else:
                scratch_dir = Path(tempfile.mkdtemp(prefix="agentic_scratch_"))

            # 5. Build volume isolation mounts (Stage 3 Section 4.4.4.5)
            read_only_mounts: Dict[Path, str] = {
                self._source_root: "/workspace/src",
            }

            if passed_candidates:
                read_only_mounts[staging_area.tests_dir] = "/workspace/tests"

            read_write_mounts: Dict[Path, str] = {
                scratch_dir: "/workspace/output",
            }

            # 6. Construct deterministic pytest CLI tokens via PytestRunner
            cov_report_path = "/workspace/output/coverage.json"
            tests_paths: List[str] = []
            if passed_candidates:
                tests_paths.append("/workspace/tests")

            for existing_test in plan.existing_tests_to_run:
                p_str = existing_test.as_posix()
                if not p_str.startswith("/"):
                    tests_paths.append(f"/workspace/src/{p_str}")
                else:
                    tests_paths.append(p_str)

            cmd = self._runner.construct_command_for_paths(
                tests_paths=tests_paths,
                cov_source=self._cov_source,
                cov_report_path=cov_report_path,
                cov_branch=self._cov_branch,
            )

            # 7. Configure sandbox confinement (INV-01)
            sandbox_config = SandboxConfig(
                image_tag=self._image_tag,
                timeout_sec=self._timeout_sec,
                memory_limit=self._memory_limit,
                cpu_quota=self._cpu_quota,
                pids_limit=self._pids_limit,
                network_disabled=True,
                read_only_mounts=read_only_mounts,
                read_write_mounts=read_write_mounts,
                environment_vars=self._environment_vars,
            )

            # 8. Container lifecycle execution with guaranteed cleanup
            container_id: Optional[str] = None
            raw_result: Optional[ExecutionRawResult] = None
            op_error: Optional[BaseException] = None

            try:
                container_id = self._sandbox_manager.create_environment(sandbox_config)
                raw_result = self._sandbox_manager.execute_command(
                    container_id=container_id,
                    command=cmd,
                    workdir="/workspace",
                )
            except Exception as e:
                op_error = e
            finally:
                if container_id is not None:
                    try:
                        self._sandbox_manager.cleanup(container_id)
                    except Exception as e:
                        if op_error is None:
                            op_error = e

            # If container execution or cleanup failed, verify source integrity immediately
            if op_error is not None:
                self._verify_source_integrity(pre_source_hash, chained_cause=op_error)
                integrity_verified = True
                raise op_error

            # 9. Parse test outcomes & captured telemetry
            assert raw_result is not None
            try:
                summary = self._runner.parse_test_outcomes(
                    stdout=raw_result.stdout,
                    stderr=raw_result.stderr,
                    exit_code=raw_result.exit_code,
                )

                # 10. Parse coverage telemetry from generated coverage.json
                coverage_file = scratch_dir / "coverage.json"
                coverage_metrics: CoverageMetrics
                if coverage_file.is_file():
                    coverage_metrics = self._coverage_extractor.parse_coverage_json(
                        coverage_file,
                        strict=False,
                    )
                else:
                    coverage_metrics = CoverageMetrics(
                        line_coverage=None,
                        branch_coverage=None,
                        is_valid=False,
                        error_message=f"Coverage report file '{coverage_file}' not found.",
                    )
            except Exception as e:
                op_error = e

            # 11. Step 9 of UC-05: Source integrity post-hash verification (INV-02)
            # Verify after container cleanup and before staging/artifact cleanup on all termination paths
            self._verify_source_integrity(pre_source_hash, chained_cause=op_error)
            integrity_verified = True

            if op_error is not None:
                raise op_error

            # 12. Compute coverage metrics & deltas against baseline
            if coverage_metrics.is_valid:
                cand_line_cov = coverage_metrics.line_coverage
                cand_branch_cov = coverage_metrics.branch_coverage if self._cov_branch else None
            else:
                cand_line_cov = None
                cand_branch_cov = None

            line_cov_delta: Optional[float] = None
            branch_cov_delta: Optional[float] = None

            if (
                baseline_evidence is not None
                and baseline_evidence.exit_code == 0
                and raw_result.exit_code == 0
                and coverage_metrics.is_valid
                and cand_line_cov is not None
                and baseline_evidence.line_coverage is not None
                and self._verify_settings_comparable(baseline_evidence)
            ):
                line_cov_delta = self._coverage_extractor.calculate_deltas(
                    pre_cov=baseline_evidence.line_coverage,
                    post_cov=cand_line_cov,
                )
                if (
                    self._cov_branch
                    and cand_branch_cov is not None
                    and baseline_evidence.branch_coverage is not None
                ):
                    branch_cov_delta = self._coverage_extractor.calculate_deltas(
                        pre_cov=baseline_evidence.branch_coverage,
                        post_cov=cand_branch_cov,
                    )

            # Package immutable ExecutionEvidence entity
            evidence_id = f"ev-{uuid.uuid4().hex[:12]}"
            evidence = ExecutionEvidence(
                evidence_id=evidence_id,
                run_id=effective_run_id,
                candidate_id=effective_candidate_id,
                exit_code=raw_result.exit_code,
                stdout=raw_result.stdout,
                stderr=raw_result.stderr,
                duration_sec=raw_result.duration_sec,
                timed_out=summary.timed_out,
                line_coverage=cand_line_cov,
                branch_coverage=cand_branch_cov,
                line_coverage_delta=line_cov_delta,
                branch_coverage_delta=branch_cov_delta,
                traceback=summary.traceback,
                created_at=datetime.now(timezone.utc),
            )

            # Register coverage settings for valid baseline executions
            if coverage_metrics.is_valid and effective_candidate_id is None:
                self.register_evidence_settings(
                    evidence_id=evidence.evidence_id,
                    source_root=self._source_root,
                    cov_source=self._cov_source,
                    cov_branch=self._cov_branch,
                )

            # Cache detailed telemetry for caller introspection
            self._last_summary = summary
            self._last_coverage = coverage_metrics
            self._last_evidence = evidence

            return evidence

        except SourceIntegrityError:
            raise
        except Exception as err:
            if not integrity_verified:
                self._verify_source_integrity(pre_source_hash, chained_cause=err)
            raise
        finally:
            if release_baseline and baseline_evidence is not None:
                self.release_evidence_settings(baseline_evidence.evidence_id)

            # 13. Fail-closed ephemeral mount teardown
            if not self._keep_artifacts:
                if staging_area is not None:
                    try:
                        staging_area.cleanup()
                    except Exception:
                        pass
                elif created_staging_dir is not None and created_staging_dir.exists():
                    shutil.rmtree(created_staging_dir, ignore_errors=True)

                if scratch_dir is not None and scratch_dir.exists():
                    shutil.rmtree(scratch_dir, ignore_errors=True)

    def _verify_source_integrity(
        self,
        pre_source_hash: str,
        chained_cause: Optional[BaseException] = None,
    ) -> None:
        """
        Verifies that source_root files were not modified during execution (INV-02).

        :param pre_source_hash: SHA-256 tree hash recorded before container provisioning.
        :param chained_cause: Any operational exception encountered prior to verification.
        :raises SourceIntegrityError: If post-hash cannot be computed or differs from pre-hash.
        """
        try:
            if not self._source_root.exists() or not self._source_root.is_dir():
                raise OSError(f"Source root '{self._source_root}' is missing or not a directory.")
            post_source_hash = compute_tree_hash(self._source_root)
        except Exception as err:
            msg = (
                f"Safety Invariant INV-02 violated: Failed to compute post-execution source tree hash "
                f"for '{self._source_root}': {err}"
            )
            if chained_cause is not None:
                raise SourceIntegrityError(msg) from chained_cause
            raise SourceIntegrityError(msg) from err

        if post_source_hash != pre_source_hash:
            msg = (
                f"Safety Invariant INV-02 violated: Production source files under "
                f"'{self._source_root}' were modified during test execution! "
                f"Pre-execution SHA-256: {pre_source_hash}, "
                f"Post-execution SHA-256: {post_source_hash}. "
                f"Halting immediately (UC-05 Extension 9a)."
            )
            if chained_cause is not None:
                raise SourceIntegrityError(msg) from chained_cause
            raise SourceIntegrityError(msg)

    def _verify_settings_comparable(self, baseline_evidence: ExecutionEvidence) -> bool:
        """
        Verifies that baseline and candidate runs satisfy Identical Telemetry Invariants:
        1. Same source_root
        2. Same cov_source
        3. Same cov_branch

        :param baseline_evidence: The baseline ExecutionEvidence record.
        :return: True if both runs used identical settings, False otherwise.
        """
        base_settings = self._evidence_settings.get(baseline_evidence.evidence_id)
        if base_settings is None:
            # Cannot infer comparability merely from numeric values (Requirement 6)
            return False
        current_settings = CoverageSettings(
            source_root=str(self._source_root.resolve()),
            cov_source=self._cov_source,
            cov_branch=self._cov_branch,
        )
        return base_settings == current_settings
