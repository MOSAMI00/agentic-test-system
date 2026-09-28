"""
Private execution-subsystem candidate file staging helper.
Iteration 1.5.1 Slice B.2 Architecture.

DISCLAIMER ON EXECUTION BOUNDARY:
Candidate staging serializes statically validated candidates to an isolated,
ephemeral staging directory (fulfilling Safety Invariant INV-04). Staging does
NOT equal execution authorization and does NOT bypass Gate 3 collection screening.
Container collection and dynamic test execution require separate, explicit
downstream orchestrator gates.
"""

from pathlib import Path
import re
import shutil
from typing import Any, Dict, Iterable, List, Optional

from agentic_test.core.models import TestCandidate, ValidationStatus

MARKER_FILENAME = ".agentic_staging_marker"


class StagingError(Exception):
    """Base exception for candidate staging errors."""
    pass


class StagingPathError(StagingError):
    """Raised when a candidate test_file_path attempts traversal or is absolute."""
    pass


class StagingValidationError(StagingError):
    """Raised when attempting to stage a candidate that does not have PASSED status or has invalid code."""
    pass


def format_candidate_artifact(candidate: TestCandidate) -> str:
    """
    Combines candidate imports and test code into a single Python source artifact.

    :param candidate: Statically validated TestCandidate entity.
    :return: Formatted Python source string with imports followed by test body.
    """
    sections: List[str] = []

    # 1. Imports block
    if candidate.imports:
        import_lines = [imp.strip() for imp in candidate.imports if imp.strip()]
        if import_lines:
            sections.append("\n".join(import_lines))

    # 2. Candidate code
    code = candidate.candidate_code.strip()
    if code:
        sections.append(code)

    return "\n\n".join(sections) + "\n"


class CandidateStagingArea:
    """
    Private execution-subsystem staging manager.

    Maintains run-scoped ephemeral staging under `staging_root / "tests"` for
    read-only volume mounting into the runner container at `/workspace/tests:ro`.

    Enforces:
    1. Writes strictly beneath run-scoped staging_root.
    2. Path traversal, symlink, and absolute/drive path rejection.
    3. Deterministic candidate destination paths.
    4. Collision avoidance for multiple candidates targeting the same file stem.
    5. Exclusion of non-PASSED and empty/whitespace candidates.
    6. Fail-closed and idempotent cleanup semantics via ownership marker.
    """

    def __init__(self, staging_root: Path, auto_cleanup: bool = False) -> None:
        """
        Initializes the candidate staging area.

        :param staging_root: Root directory for run artifacts (e.g. .agentic_test/runs/<run_id>/).
        :param auto_cleanup: If True, cleans up staging directory on context exit.
        :raises StagingPathError: If staging_root is a symlink.
        :raises StagingError: If staging_root is a home directory, root anchor, or pre-existing unmarked directory.
        """
        if staging_root.is_symlink():
            raise StagingPathError(f"Staging root must not be a symlink: {staging_root}")

        self._staging_root: Path = staging_root.resolve()

        # Fail-closed safety guards: protect system roots and user home
        try:
            home = Path.home().resolve()
            if self._staging_root == home or self._staging_root in home.parents:
                raise StagingError(f"Staging root cannot be user home or system root: {self._staging_root}")
        except RuntimeError:
            pass  # Handle environment without user home if applicable

        if len(self._staging_root.parts) <= 1 or self._staging_root == Path(self._staging_root.anchor):
            raise StagingError(f"Staging root cannot be a system drive or root anchor: {self._staging_root}")

        self._marker_file: Path = self._staging_root / MARKER_FILENAME
        self._tests_dir: Path = self._staging_root / "tests"
        self._auto_cleanup: bool = auto_cleanup

        self._candidate_to_path: Dict[str, Path] = {}
        self._path_to_candidate: Dict[Path, str] = {}

        # Establish staging root ownership with marker
        if self._staging_root.exists():
            if not self._marker_file.exists():
                # Directory exists; only accept if completely empty, otherwise reject as foreign
                if any(self._staging_root.iterdir()):
                    raise StagingError(
                        f"Staging root '{self._staging_root}' is a pre-existing unmarked non-empty directory."
                    )
                self._marker_file.write_text("agentic-staging-area\n", encoding="utf-8")
        else:
            self._staging_root.mkdir(parents=True, exist_ok=True)
            self._marker_file.write_text("agentic-staging-area\n", encoding="utf-8")

    @property
    def staging_root(self) -> Path:
        """Absolute resolved path to the staging root."""
        return self._staging_root

    @property
    def tests_dir(self) -> Path:
        """Absolute resolved path to the staged tests directory."""
        return self._tests_dir

    @property
    def staged_count(self) -> int:
        """Total number of uniquely staged candidates."""
        return len(self._candidate_to_path)

    def _normalize_relative_path(self, path: Path) -> Path:
        """
        Validates and normalizes candidate relative path.

        :param path: candidate.test_file_path.
        :return: Normalized relative Path under tests directory.
        :raises StagingPathError: If path is absolute, has a drive letter, or traverses '../'.
        """
        # Convert separators to forward slashes for uniform cross-platform parsing
        path_str = str(path).replace("\\", "/")

        # 1. Drive-letter check (Windows compatibility)
        if path.drive or re.match(r"^[a-zA-Z]:", str(path)) or re.match(r"^[a-zA-Z]:", path_str):
            raise StagingPathError(f"Drive-letter path rejected: {path}")

        # 2. Absolute or root-prefixed path check
        if path.is_absolute() or path_str.startswith("/"):
            raise StagingPathError(f"Absolute path rejected: {path}")

        # 3. Path traversal check on normalized string
        normalized_path = Path(path_str)
        if ".." in normalized_path.parts:
            raise StagingPathError(f"Path traversal ('..') rejected: {path}")

        # 4. Strip redundant leading 'tests' folder if already specified by generator
        parts = list(normalized_path.parts)
        if parts and parts[0] == "tests":
            parts = parts[1:]

        if not parts:
            raise StagingPathError("Path must specify a test filename, not an empty path.")

        return Path(*parts)

    def _assert_no_symlink_components(self, target_path: Path) -> None:
        """
        Verifies that neither target_path nor any of its existing parent directories are symlinks.

        :param target_path: Destination path to inspect.
        :raises StagingPathError: If any path component is a symlink.
        """
        curr: Path = target_path
        while curr != self._tests_dir and curr != self._staging_root and curr != curr.parent:
            if curr.is_symlink():
                raise StagingPathError(f"Refusing to write to or through symlink: {curr}")
            curr = curr.parent

    def stage_candidate(self, candidate: TestCandidate) -> Path:
        """
        Stages a single statically validated test candidate to disk.

        :param candidate: TestCandidate with validation_status == PASSED.
        :return: Absolute Path to the staged Python test file.
        :raises StagingValidationError: If candidate is not PASSED or has empty code.
        :raises StagingPathError: If candidate.test_file_path violates isolation boundaries or is a symlink.
        """
        # Gate check 1: rejected or unvalidated candidates must NEVER be staged for execution
        if candidate.validation_status != ValidationStatus.PASSED:
            raise StagingValidationError(
                f"Candidate '{candidate.candidate_id}' cannot be staged for execution: "
                f"validation_status is '{candidate.validation_status.value}', "
                f"expected '{ValidationStatus.PASSED.value}'."
            )

        # Gate check 2: reject empty or whitespace-only candidate code
        if not candidate.candidate_code or not candidate.candidate_code.strip():
            raise StagingValidationError(
                f"Candidate '{candidate.candidate_id}' cannot be staged: candidate_code is empty or whitespace."
            )

        # Idempotent re-staging: if candidate was already assigned a path, re-use it
        if candidate.candidate_id in self._candidate_to_path:
            target_path = self._candidate_to_path[candidate.candidate_id]
            self._assert_no_symlink_components(target_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_content = format_candidate_artifact(candidate)
            target_path.write_text(artifact_content, encoding="utf-8")
            return target_path

        # Validate and normalize path
        rel_path = self._normalize_relative_path(candidate.test_file_path)
        candidate_dest = (self._tests_dir / rel_path).resolve()

        # Enforce boundary: must reside strictly under staging_root / tests
        if not candidate_dest.is_relative_to(self._tests_dir):
            raise StagingPathError(
                f"Resolved destination '{candidate_dest}' escapes staging root '{self._staging_root}'."
            )

        # Collision avoidance: if path is already claimed by another candidate, append sanitized candidate_id
        if candidate_dest in self._path_to_candidate:
            stem = candidate_dest.stem
            suffix = candidate_dest.suffix or ".py"
            # Sanitize candidate_id to strictly alphanumeric/underscore characters
            safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", candidate.candidate_id)
            candidate_dest = candidate_dest.parent / f"{stem}_{safe_id}{suffix}"

            # Re-verify disambiguated path boundary
            if not candidate_dest.resolve().is_relative_to(self._tests_dir):
                raise StagingPathError(
                    f"Disambiguated destination '{candidate_dest}' escapes staging tests directory."
                )

        # Reject any symlinks along the path
        self._assert_no_symlink_components(candidate_dest)

        # Ensure parent directory exists under staging root
        candidate_dest.parent.mkdir(parents=True, exist_ok=True)

        # Write single artifact (imports + code)
        artifact_content = format_candidate_artifact(candidate)
        candidate_dest.write_text(artifact_content, encoding="utf-8")

        # Register mappings
        self._candidate_to_path[candidate.candidate_id] = candidate_dest
        self._path_to_candidate[candidate_dest] = candidate.candidate_id

        return candidate_dest

    def stage_candidates(self, candidates: Iterable[TestCandidate]) -> List[Path]:
        """
        Stages multiple test candidates sequentially, avoiding collisions.

        :param candidates: Iterable of TestCandidate entities.
        :return: List of absolute Paths to staged test files.
        """
        return [self.stage_candidate(cand) for cand in candidates]

    def get_staged_path(self, candidate_id: str) -> Optional[Path]:
        """Returns the staged path for a candidate ID, or None if not staged."""
        return self._candidate_to_path.get(candidate_id)

    def cleanup(self) -> None:
        """
        Safely and idempotently removes the staging root and all its contents.

        Fails closed:
        - Refuses to delete if staging root is unmarked (missing marker file).
        - Refuses to delete system root, user home, or drive root.
        """
        if not self._staging_root.exists():
            return

        resolved = self._staging_root.resolve()
        # Verify marker presence before deleting
        if not (resolved / MARKER_FILENAME).exists():
            raise StagingError(f"Refusing to delete unmarked directory: {resolved}")

        # Safety guard: refuse to delete if path is root or single-part anchor
        if len(resolved.parts) <= 1 or resolved == Path(resolved.anchor):
            raise StagingError(f"Refusing to delete unsafe root path: {resolved}")

        # Protect user home
        try:
            home = Path.home().resolve()
            if resolved == home or resolved in home.parents:
                raise StagingError(f"Refusing to delete home directory path: {resolved}")
        except RuntimeError:
            pass

        shutil.rmtree(resolved, ignore_errors=True)
        self._candidate_to_path.clear()
        self._path_to_candidate.clear()

    def __enter__(self) -> "CandidateStagingArea":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._auto_cleanup:
            self.cleanup()
