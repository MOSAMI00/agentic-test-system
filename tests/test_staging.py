"""
Offline unit tests for candidate file staging boundary.
Iteration 1.5.1 Slice B.2 Architecture.
"""

from pathlib import Path
import socket
import sys
from typing import Any, Tuple
import pytest

from agentic_test.core.models import TestCandidate, ValidationStatus
from agentic_test.execution.staging import (
    MARKER_FILENAME,
    CandidateStagingArea,
    StagingError,
    StagingPathError,
    StagingValidationError,
    format_candidate_artifact,
)


def _make_candidate(
    candidate_id: str = "cand_001",
    target_symbol_name: str = "toy_math.add",
    test_file_path: Path = Path("tests/test_toy_math.py"),
    imports: Tuple[str, ...] = ("import pytest", "from app.math import add"),
    candidate_code: str = "def test_add():\n    assert add(1, 2) == 3\n",
    validation_status: ValidationStatus = ValidationStatus.PASSED,
) -> TestCandidate:
    return TestCandidate(
        candidate_id=candidate_id,
        run_id="run_test_001",
        target_symbol_name=target_symbol_name,
        test_file_path=test_file_path,
        candidate_code=candidate_code,
        imports=imports,
        validation_status=validation_status,
    )


# =====================================================================
# 1. Normal Staging & Artifact Formatting Tests
# =====================================================================

def test_format_candidate_artifact_combines_imports_and_code() -> None:
    """Verifies that format_candidate_artifact produces valid Python source with imports first."""
    cand = _make_candidate(
        imports=("import pytest", "from mymath import add"),
        candidate_code="def test_add(): assert add(1, 1) == 2",
    )
    artifact = format_candidate_artifact(cand)

    assert artifact.startswith("import pytest\nfrom mymath import add\n\n")
    assert "def test_add(): assert add(1, 1) == 2" in artifact
    assert artifact.endswith("\n")


def test_normal_staging(tmp_path: Path) -> None:
    """Verifies normal staging of a statically cleared candidate under staging_root/tests/."""
    staging_root = tmp_path / "staging"
    staging = CandidateStagingArea(staging_root)

    cand = _make_candidate(
        candidate_id="cand_add",
        test_file_path=Path("tests/test_calc.py"),
        imports=("import pytest",),
        candidate_code="def test_calc(): assert True",
        validation_status=ValidationStatus.PASSED,
    )

    staged_path = staging.stage_candidate(cand)

    assert staged_path.exists()
    assert staged_path.is_file()
    assert staged_path.is_relative_to(staging.tests_dir)
    assert staged_path == staging_root / "tests" / "test_calc.py"

    content = staged_path.read_text(encoding="utf-8")
    assert "import pytest" in content
    assert "def test_calc(): assert True" in content
    assert staging.staged_count == 1
    assert staging.get_staged_path("cand_add") == staged_path


# =====================================================================
# 2. Target Repository Isolation Proof (INV-02, INV-04)
# =====================================================================

def test_target_repository_remains_untouched(tmp_path: Path) -> None:
    """Verifies that staging candidates never writes into the target repository filesystem."""
    target_repo = tmp_path / "fake_repo"
    target_repo.mkdir()
    target_tests_dir = target_repo / "tests"
    target_tests_dir.mkdir()
    existing_file = target_tests_dir / "test_existing.py"
    existing_file.write_text("def test_existing(): pass", encoding="utf-8")
    original_mtime = existing_file.stat().st_mtime

    staging_root = tmp_path / "staging_area"
    staging = CandidateStagingArea(staging_root)

    cand = _make_candidate(test_file_path=Path("tests/test_new.py"))
    staging.stage_candidate(cand)

    # Target repository remains untouched
    assert existing_file.stat().st_mtime == original_mtime
    assert not (target_tests_dir / "test_new.py").exists()
    assert set(target_tests_dir.iterdir()) == {existing_file}


# =====================================================================
# 3. Path Traversal & Absolute/Drive Path Rejection Tests
# =====================================================================

def test_traversal_path_rejection(tmp_path: Path) -> None:
    """Verifies that candidates with relative traversal ('..') are rejected."""
    staging = CandidateStagingArea(tmp_path / "staging")

    traversal_candidates = [
        _make_candidate(test_file_path=Path("../evil.py")),
        _make_candidate(test_file_path=Path("tests/../../evil.py")),
        _make_candidate(test_file_path=Path("sub/../../evil.py")),
    ]

    for cand in traversal_candidates:
        with pytest.raises(StagingPathError, match="Path traversal"):
            staging.stage_candidate(cand)

    assert not (tmp_path / "evil.py").exists()


def test_absolute_and_drive_path_rejection(tmp_path: Path) -> None:
    """Verifies that absolute paths and drive-letter paths are rejected."""
    staging = CandidateStagingArea(tmp_path / "staging")

    # Absolute Unix-style path
    cand_abs = _make_candidate(test_file_path=Path("/etc/passwd"))
    with pytest.raises(StagingPathError, match="Absolute path"):
        staging.stage_candidate(cand_abs)

    # Windows drive-letter path
    cand_drive = _make_candidate(test_file_path=Path("C:/Windows/System32/evil.py"))
    with pytest.raises(StagingPathError, match="(Drive-letter|Absolute path)"):
        staging.stage_candidate(cand_drive)


def test_windows_backslash_separator_normalization(tmp_path: Path) -> None:
    """Verifies that candidate paths with Windows backslashes are parsed consistently."""
    staging = CandidateStagingArea(tmp_path / "staging")

    cand1 = _make_candidate(
        candidate_id="cand_win1",
        test_file_path=Path(r"tests\test_win1.py"),
    )
    cand2 = _make_candidate(
        candidate_id="cand_win2",
        test_file_path=Path(r"tests\unit\test_win2.py"),
    )

    p1 = staging.stage_candidate(cand1)
    p2 = staging.stage_candidate(cand2)

    assert p1.exists()
    assert p1 == staging.tests_dir / "test_win1.py"
    assert p2.exists()
    assert p2 == staging.tests_dir / "unit" / "test_win2.py"


# =====================================================================
# 4. Same-Source-File Collision Avoidance Tests
# =====================================================================

def test_same_source_file_collision_avoidance(tmp_path: Path) -> None:
    """
    Verifies that multiple candidates targeting the same test file path
    do not overwrite each other and are assigned deterministic unique filenames.
    """
    staging = CandidateStagingArea(tmp_path / "staging")

    cand1 = _make_candidate(
        candidate_id="cand_add_01",
        test_file_path=Path("tests/test_math.py"),
        candidate_code="def test_add(): assert 1 + 1 == 2",
    )
    cand2 = _make_candidate(
        candidate_id="cand_sub_02",
        test_file_path=Path("tests/test_math.py"),
        candidate_code="def test_sub(): assert 2 - 1 == 1",
    )

    path1 = staging.stage_candidate(cand1)
    path2 = staging.stage_candidate(cand2)

    assert path1 != path2
    assert path1.exists()
    assert path2.exists()
    assert staging.staged_count == 2

    # Verify both contents are preserved independently without overwrite
    content1 = path1.read_text(encoding="utf-8")
    content2 = path2.read_text(encoding="utf-8")

    assert "def test_add()" in content1
    assert "def test_sub()" not in content1

    assert "def test_sub()" in content2
    assert "def test_add()" not in content2


def test_collision_sanitizes_unsafe_candidate_ids(tmp_path: Path) -> None:
    """Verifies that candidate IDs with traversal sequences or invalid characters are sanitized."""
    staging = CandidateStagingArea(tmp_path / "staging")

    # Prime path
    cand_base = _make_candidate(
        candidate_id="base_cand",
        test_file_path=Path("tests/test_collision.py"),
        candidate_code="def test_base(): pass",
    )
    staging.stage_candidate(cand_base)

    # Collision candidate with path traversal and illegal chars in ID
    cand_unsafe = _make_candidate(
        candidate_id="../../evil:cand*id?",
        test_file_path=Path("tests/test_collision.py"),
        candidate_code="def test_unsafe(): pass",
    )
    staged_path = staging.stage_candidate(cand_unsafe)

    assert staged_path.exists()
    assert staged_path.is_relative_to(staging.tests_dir)
    assert not (staging.staging_root / "evil").exists()
    assert ":" not in staged_path.name
    assert "*" not in staged_path.name
    assert "?" not in staged_path.name


# =====================================================================
# 5. Deterministic Repeated Behavior Tests
# =====================================================================

def test_deterministic_repeated_behavior(tmp_path: Path) -> None:
    """Verifies that re-staging the same candidate is idempotent and preserves the assigned path."""
    staging = CandidateStagingArea(tmp_path / "staging")

    cand = _make_candidate(
        candidate_id="cand_idempotent",
        test_file_path=Path("tests/test_service.py"),
        candidate_code="def test_v1(): pass",
    )

    path1 = staging.stage_candidate(cand)
    assert path1.read_text(encoding="utf-8").strip().endswith("def test_v1(): pass")

    # Update candidate code and re-stage same candidate_id
    cand_updated = _make_candidate(
        candidate_id="cand_idempotent",
        test_file_path=Path("tests/test_service.py"),
        candidate_code="def test_v2(): pass",
    )
    path2 = staging.stage_candidate(cand_updated)

    assert path1 == path2
    assert staging.staged_count == 1
    assert path2.read_text(encoding="utf-8").strip().endswith("def test_v2(): pass")


def test_restaging_after_file_and_parent_removal(tmp_path: Path) -> None:
    """Verifies that re-staging a candidate after its file and parent directory were deleted succeeds."""
    staging = CandidateStagingArea(tmp_path / "staging")

    cand = _make_candidate(
        candidate_id="cand_deleted",
        test_file_path=Path("tests/sub/test_module.py"),
    )
    path1 = staging.stage_candidate(cand)
    assert path1.exists()

    # Remove the staged file and its parent sub-directory
    path1.unlink()
    path1.parent.rmdir()
    assert not path1.exists()
    assert not path1.parent.exists()

    # Re-stage: must safely recreate parent and write file
    path2 = staging.stage_candidate(cand)
    assert path2 == path1
    assert path2.exists()


# =====================================================================
# 6. Cleanup Semantics & Fail-Closed Tests
# =====================================================================

def test_safe_cleanup_semantics(tmp_path: Path) -> None:
    """Verifies that cleanup removes the staging root and is idempotent."""
    staging_root = tmp_path / "staging_run_01"
    staging = CandidateStagingArea(staging_root)

    staging.stage_candidate(_make_candidate())
    assert staging_root.exists()
    assert staging.staged_count == 1

    # First cleanup
    staging.cleanup()
    assert not staging_root.exists()
    assert staging.staged_count == 0

    # Idempotent cleanup: repeated call must not error
    staging.cleanup()


def test_context_manager_auto_cleanup(tmp_path: Path) -> None:
    """Verifies that auto_cleanup triggers automatically upon context manager exit."""
    staging_root = tmp_path / "staging_auto"

    with CandidateStagingArea(staging_root, auto_cleanup=True) as staging:
        staging.stage_candidate(_make_candidate())
        assert staging_root.exists()

    assert not staging_root.exists()


def test_cleanup_refuses_unmarked_directory(tmp_path: Path) -> None:
    """Verifies that pre-existing unmarked non-empty directories cannot be hijacked or deleted."""
    foreign_dir = tmp_path / "foreign_data"
    foreign_dir.mkdir()
    (foreign_dir / "important_data.txt").write_text("critical", encoding="utf-8")

    # Initializing staging on foreign non-empty directory raises StagingError
    with pytest.raises(StagingError, match="pre-existing unmarked non-empty"):
        CandidateStagingArea(foreign_dir)

    assert (foreign_dir / "important_data.txt").exists()


def test_cleanup_refuses_home_directory() -> None:
    """Verifies that CandidateStagingArea rejects Path.home() as a staging root."""
    home = Path.home()
    with pytest.raises(StagingError, match="user home"):
        CandidateStagingArea(home)


# =====================================================================
# 7. Symlink Guards Tests
# =====================================================================

def test_staging_root_symlink_rejected(tmp_path: Path) -> None:
    """Verifies that a staging_root that is a symlink is rejected upon initialization."""
    real_dir = tmp_path / "real_staging"
    real_dir.mkdir()
    link_dir = tmp_path / "link_staging"
    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not permitted in this environment.")

    with pytest.raises(StagingPathError, match="Staging root must not be a symlink"):
        CandidateStagingArea(link_dir)


def test_symlink_destination_or_directory_rejected(tmp_path: Path) -> None:
    """Verifies that existing symlinks along destination paths are rejected before writing."""
    staging_root = tmp_path / "staging"
    staging = CandidateStagingArea(staging_root)

    staging.tests_dir.mkdir(parents=True, exist_ok=True)
    external_target = tmp_path / "external_target.py"
    external_target.write_text("orig", encoding="utf-8")

    symlink_file = staging.tests_dir / "test_symlink.py"
    try:
        symlink_file.symlink_to(external_target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not permitted in this environment.")

    cand = _make_candidate(test_file_path=Path("tests/test_symlink.py"))
    with pytest.raises(StagingPathError, match="symlink"):
        staging.stage_candidate(cand)

    # External target must not have been modified
    assert external_target.read_text(encoding="utf-8") == "orig"


# =====================================================================
# 8. Non-PASSED and Empty Candidate Code Tests
# =====================================================================

@pytest.mark.parametrize(
    "forbidden_status",
    [
        ValidationStatus.PENDING,
        ValidationStatus.REJECTED_SYNTAX,
        ValidationStatus.REJECTED_SECURITY,
        ValidationStatus.REJECTED_COLLECTION,
        ValidationStatus.QUARANTINED,
    ],
)
def test_rejected_and_non_passed_candidates_are_not_staged(
    tmp_path: Path, forbidden_status: ValidationStatus
) -> None:
    """
    Verifies that candidates with status other than PASSED are rejected by staging
    and never written into the executable tests staging directory.
    """
    staging = CandidateStagingArea(tmp_path / "staging")
    cand = _make_candidate(
        candidate_id=f"cand_{forbidden_status.value}",
        validation_status=forbidden_status,
    )

    with pytest.raises(StagingValidationError, match="cannot be staged for execution"):
        staging.stage_candidate(cand)

    assert staging.staged_count == 0
    assert not (tmp_path / "staging" / "tests").exists()


@pytest.mark.parametrize("empty_code", ["", "   \n\t  "])
def test_empty_or_whitespace_candidate_code_rejected(tmp_path: Path, empty_code: str) -> None:
    """Verifies that candidate with empty or whitespace-only candidate_code is rejected."""
    staging = CandidateStagingArea(tmp_path / "staging")
    cand = _make_candidate(candidate_code=empty_code)

    with pytest.raises(StagingValidationError, match="candidate_code is empty or whitespace"):
        staging.stage_candidate(cand)

    assert staging.staged_count == 0


# =====================================================================
# 9. Isolation Proofs: No Network / Docker Imports
# =====================================================================

def test_no_docker_module_in_sys_modules() -> None:
    """Verifies that candidate staging does NOT import or require the Docker SDK."""
    assert "docker" not in sys.modules, "Docker SDK must not be imported in offline staging."


def test_no_network_access_during_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that zero network sockets or connections are attempted during staging operations."""
    network_called = False

    def guard_socket_connect(*args: Any, **kwargs: Any) -> None:
        nonlocal network_called
        network_called = True
        raise AssertionError("Network socket connection attempted during offline candidate staging!")

    monkeypatch.setattr(socket.socket, "connect", guard_socket_connect)
    monkeypatch.setattr(socket, "create_connection", guard_socket_connect)

    staging = CandidateStagingArea(tmp_path / "staging")
    staging.stage_candidate(_make_candidate())
    staging.cleanup()

    assert not network_called, "Zero network or socket operations are permitted in offline staging."
