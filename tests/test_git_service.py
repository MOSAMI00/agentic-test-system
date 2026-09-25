"""
Unit and integration tests for GitService.
Verifies FR-01, FR-02, NFR-01, and exclusion filtering using isolated temporary Git repositories.
"""

from datetime import datetime
from pathlib import Path
import pytest
import git

from agentic_test.analysis.git_service import GitService, is_excluded_path
from agentic_test.core.models import ChangeType, RepositoryValidationError


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """
    Creates an isolated temporary Git repository with an initial commit.
    This is a transient test fixture completely separate from the project repository.
    """
    repo_dir = tmp_path / "target_repo"
    repo_dir.mkdir()
    repo = git.Repo.init(str(repo_dir))

    # Configure user for commits
    repo.config_writer().set_value("user", "name", "Test Engineer").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()

    # Create initial files
    app_file = repo_dir / "calculator.py"
    app_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def subtract(a: int, b: int) -> int:\n"
        "    return a - b\n",
        encoding="utf-8",
    )

    test_file = repo_dir / "test_calculator.py"
    test_file.write_text(
        "from calculator import add\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    repo.index.add(["calculator.py", "test_calculator.py"])
    repo.index.commit("Initial commit")

    return repo_dir


def test_validate_repository_rejects_nonexistent_path(tmp_path: Path) -> None:
    """FR-01: validate_repository rejects non-existent paths with RepositoryValidationError."""
    non_existent = tmp_path / "does_not_exist"
    service = GitService(non_existent)
    with pytest.raises(RepositoryValidationError, match="does not exist"):
        service.validate_repository(non_existent)


def test_validate_repository_rejects_non_git_dir(tmp_path: Path) -> None:
    """FR-01: validate_repository rejects regular directories that are not Git repositories."""
    non_git_dir = tmp_path / "regular_dir"
    non_git_dir.mkdir()
    service = GitService(non_git_dir)
    with pytest.raises(RepositoryValidationError, match="not a valid Git repository"):
        service.validate_repository(non_git_dir)


def test_validate_repository_accepts_valid_repo(temp_git_repo: Path) -> None:
    """FR-01: validate_repository accepts valid Git repositories and returns True."""
    service = GitService(temp_git_repo)
    assert service.validate_repository(temp_git_repo) is True


def test_metadata_extraction(temp_git_repo: Path) -> None:
    """FR-01: Correct extraction of HEAD commit SHA, branch name, and base commit."""
    service = GitService(temp_git_repo)
    head_sha = service.get_head_commit()
    assert len(head_sha) == 40
    assert all(c in "0123456789abcdef" for c in head_sha)

    branch = service.get_branch_name()
    assert isinstance(branch, str)
    assert len(branch) > 0

    base_sha = service.get_base_commit()
    # For initial commit with no parent, base resolves to head_sha
    assert len(base_sha) == 40


def test_tracked_files_and_test_discovery(temp_git_repo: Path) -> None:
    """Discovers tracked files and test files matching pytest naming conventions."""
    service = GitService(temp_git_repo)
    tracked = service.list_tracked_files()
    assert Path("calculator.py") in tracked
    assert Path("test_calculator.py") in tracked

    test_files = service.list_existing_test_files()
    assert test_files == [Path("test_calculator.py")]


def test_source_tree_hash_determinism(temp_git_repo: Path) -> None:
    """INV-02: Cryptographic source tree hash is deterministic and 64-hex SHA-256."""
    service = GitService(temp_git_repo)
    hash1 = service.compute_source_tree_hash()
    hash2 = service.compute_source_tree_hash()

    assert len(hash1) == 64
    assert hash1 == hash2

    # Modifying a file alters the source tree hash
    (temp_git_repo / "calculator.py").write_text("# modified\n", encoding="utf-8")
    hash3 = service.compute_source_tree_hash()
    assert hash3 != hash1


def test_compute_diff_clean_tree(temp_git_repo: Path) -> None:
    """FR-02: Clean working tree against HEAD produces zero diff hunks."""
    service = GitService(temp_git_repo)
    head = service.get_head_commit()
    diff_hunks = service.compute_diff(base_commit=head)
    assert len(diff_hunks) == 0


def test_compute_diff_modified_file(temp_git_repo: Path) -> None:
    """FR-02: Modified file produces structured DiffHunk with exact line spans."""
    app_file = temp_git_repo / "calculator.py"

    # Append new function
    content = app_file.read_text(encoding="utf-8")
    content += "\ndef multiply(a: int, b: int) -> int:\n    return a * b\n"
    app_file.write_text(content, encoding="utf-8")

    service = GitService(temp_git_repo)
    head = service.get_head_commit()
    hunks = service.compute_diff(base_commit=head)

    assert len(hunks) >= 1
    modified_hunk = next(h for h in hunks if h.file_path == Path("calculator.py"))
    assert modified_hunk.change_type == ChangeType.MODIFIED
    assert modified_hunk.new_lines > 0
    assert "multiply" in modified_hunk.content


def test_compute_diff_added_file(temp_git_repo: Path) -> None:
    """FR-02: Newly added file produces DiffHunk with ChangeType.ADDED."""
    new_file = temp_git_repo / "analytics.py"
    new_file.write_text("def track():\n    pass\n", encoding="utf-8")

    service = GitService(temp_git_repo)
    head = service.get_head_commit()
    hunks = service.compute_diff(base_commit=head)

    added_hunk = next(h for h in hunks if h.file_path == Path("analytics.py"))
    assert added_hunk.change_type == ChangeType.ADDED
    assert added_hunk.new_start == 1
    assert "track" in added_hunk.content


def test_compute_diff_deleted_file(temp_git_repo: Path) -> None:
    """FR-02: Deleted file produces DiffHunk with ChangeType.DELETED."""
    app_file = temp_git_repo / "calculator.py"
    app_file.unlink()

    service = GitService(temp_git_repo)
    head = service.get_head_commit()
    hunks = service.compute_diff(base_commit=head)

    deleted_hunk = next(h for h in hunks if h.file_path == Path("calculator.py"))
    assert deleted_hunk.change_type == ChangeType.DELETED


def test_exclusion_filtering(temp_git_repo: Path) -> None:
    """
    FR-02: Excludes .git, virtual environments (.venv, venv), caches (__pycache__),
    and binary files from diff extraction and file tracking.
    """
    # Create excluded virtual environment files and caches
    venv_dir = temp_git_repo / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "venv_module.py").write_text("# virtualenv file\n", encoding="utf-8")

    cache_dir = temp_git_repo / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "compiled.cpython-312.pyc").write_bytes(b"\x00\x01\x02")

    binary_file = temp_git_repo / "data.bin"
    binary_file.write_bytes(b"\x00\xff\xfe\xfd")

    service = GitService(temp_git_repo)
    head = service.get_head_commit()
    hunks = service.compute_diff(base_commit=head)

    # Verify that NONE of the excluded files appear in diff hunks
    hunk_paths = [h.file_path.as_posix() for h in hunks]
    assert not any(".venv" in p for p in hunk_paths)
    assert not any("__pycache__" in p for p in hunk_paths)
    assert not any(p.endswith(".bin") for p in hunk_paths)

    # Verify helper function
    assert is_excluded_path(Path(".venv/bin/activate")) is True
    assert is_excluded_path(Path("venv/lib/site.py")) is True
    assert is_excluded_path(Path("src/__pycache__/app.cpython-312.pyc")) is True
    assert is_excluded_path(Path("assets/logo.png")) is True
    assert is_excluded_path(Path("src/agentic_test/models.py")) is False


def test_create_snapshot_full_flow(temp_git_repo: Path) -> None:
    """
    Full integration test for GitService.create_snapshot():
    Verifies valid snapshot population including Option A fields.
    """
    service = GitService(temp_git_repo)
    snapshot = service.create_snapshot()

    assert snapshot.is_valid is True
    assert snapshot.repo_path == temp_git_repo
    assert len(snapshot.current_commit) == 40
    assert len(snapshot.base_commit) == 40
    assert snapshot.branch_name in ("master", "main")
    assert len(snapshot.source_tree_hash) == 64
    assert Path("calculator.py") in snapshot.tracked_files
    assert Path("test_calculator.py") in snapshot.existing_test_files
    assert isinstance(snapshot.created_at, datetime)
    assert isinstance(snapshot.tracked_files, tuple)
    assert isinstance(snapshot.existing_test_files, tuple)
    assert isinstance(snapshot.diff_hunks, tuple)
    assert snapshot.affected_symbols == ()  # Empty in Iteration 1.1
    assert snapshot.syntax_errors == ()     # Empty in Iteration 1.1


def test_zero_target_code_execution(temp_git_repo: Path) -> None:
    """
    NFR-01 / SEC-01: Static repository inspection must NEVER execute target application code.
    Injects a runtime poison pill into target repository and verifies zero execution.
    """
    poison_file = temp_git_repo / "poison_pill.py"
    poison_file.write_text(
        "raise RuntimeError('Target application code was executed during static analysis!')\n",
        encoding="utf-8",
    )

    # Running GitService operations must not trigger the exception
    service = GitService(temp_git_repo)
    assert service.validate_repository(temp_git_repo) is True
    head = service.get_head_commit()
    hunks = service.compute_diff(base_commit=head)
    snapshot = service.create_snapshot()

    assert snapshot.is_valid is True
    # The poison pill file was added and extracted without being executed
    assert any(h.file_path == Path("poison_pill.py") for h in hunks)


def test_empty_repo_without_commits_raises_validation_error(tmp_path: Path) -> None:
    """get_head_commit raises RepositoryValidationError when repository has no commits."""
    empty_dir = tmp_path / "empty_repo"
    empty_dir.mkdir()
    git.Repo.init(str(empty_dir))

    service = GitService(empty_dir)
    assert service.validate_repository(empty_dir) is True
    with pytest.raises(RepositoryValidationError, match="has no commits"):
        service.get_head_commit()


def test_explicit_base_commit_resolution(temp_git_repo: Path) -> None:
    """get_base_commit resolves explicit commit SHA and raises on non-existent commit."""
    service = GitService(temp_git_repo)
    head_sha = service.get_head_commit()

    resolved = service.get_base_commit(head_sha)
    assert resolved == head_sha

    with pytest.raises(RepositoryValidationError, match="not found"):
        service.get_base_commit("deadbeef" * 5)


def test_detached_head_branch_name(temp_git_repo: Path) -> None:
    """get_branch_name returns 'HEAD' when in detached HEAD state."""
    repo = git.Repo(str(temp_git_repo))
    head_sha = repo.head.commit.hexsha
    repo.git.checkout(head_sha)

    service = GitService(temp_git_repo)
    branch = service.get_branch_name()
    assert branch == "HEAD"


def test_compute_diff_staged_changes(temp_git_repo: Path) -> None:
    """
    FR-02: Diff extraction captures staged changes in Git index.
    Verifies both staged modifications to tracked files and staged newly added files.
    """
    repo = git.Repo(str(temp_git_repo))
    service = GitService(temp_git_repo)
    head_sha = service.get_head_commit()

    # 1. Modify existing tracked file and stage it
    calc_file = temp_git_repo / "calculator.py"
    calc_content = calc_file.read_text(encoding="utf-8")
    calc_file.write_text(calc_content + "\ndef staged_mul(a: int, b: int) -> int:\n    return a * b\n", encoding="utf-8")
    repo.index.add(["calculator.py"])

    # 2. Create brand new file and stage it
    staged_new = temp_git_repo / "staged_module.py"
    staged_new.write_text("def staged_init():\n    return True\n", encoding="utf-8")
    repo.index.add(["staged_module.py"])

    hunks = service.compute_diff(base_commit=head_sha)

    # Verify staged modified file
    mod_hunk = next((h for h in hunks if h.file_path == Path("calculator.py")), None)
    assert mod_hunk is not None
    assert mod_hunk.change_type == ChangeType.MODIFIED
    assert "staged_mul" in mod_hunk.content

    # Verify staged added file
    add_hunk = next((h for h in hunks if h.file_path == Path("staged_module.py")), None)
    assert add_hunk is not None
    assert add_hunk.change_type == ChangeType.ADDED
    assert "staged_init" in add_hunk.content


def test_compute_diff_committed_changes_and_implicit_parent(temp_git_repo: Path) -> None:
    """
    FR-01 & FR-02: Multi-commit repository inspection:
    - Resolves implicit baseline commit to HEAD~1 (first parent) when explicit_base is None.
    - Computes diff against committed changes across commits without working tree modifications.
    """
    repo = git.Repo(str(temp_git_repo))
    commit1_sha = repo.head.commit.hexsha

    # Create a second commit
    feature_file = temp_git_repo / "feature.py"
    feature_file.write_text("def new_feature() -> str:\n    return 'v2'\n", encoding="utf-8")
    repo.index.add(["feature.py"])
    commit2 = repo.index.commit("feat: add new_feature")
    commit2_sha = commit2.hexsha

    service = GitService(temp_git_repo)

    # 1. Verify HEAD commit is commit2
    assert service.get_head_commit() == commit2_sha

    # 2. Verify implicit base resolution resolves to commit1 (HEAD~1)
    implicit_base = service.get_base_commit()
    assert implicit_base == commit1_sha
    assert implicit_base != commit2_sha

    # 3. Verify compute_diff() with implicit base captures committed changes
    hunks = service.compute_diff()  # implicit base: commit1_sha
    assert len(hunks) >= 1
    feat_hunk = next((h for h in hunks if h.file_path == Path("feature.py")), None)
    assert feat_hunk is not None
    assert feat_hunk.change_type == ChangeType.ADDED
    assert "new_feature" in feat_hunk.content

    # 4. Verify snapshot creation captures the multi-commit metadata
    snapshot = service.create_snapshot()
    assert snapshot.current_commit == commit2_sha
    assert snapshot.base_commit == commit1_sha
    assert any(h.file_path == Path("feature.py") for h in snapshot.diff_hunks)


def test_get_remote_origin_present(temp_git_repo: Path) -> None:
    """FR-01: get_remote_origin returns URL when 'origin' remote is configured."""
    repo = git.Repo(str(temp_git_repo))
    repo.create_remote("origin", "https://github.com/example/project.git")

    service = GitService(temp_git_repo)
    assert service.get_remote_origin() == "https://github.com/example/project.git"


def test_get_remote_origin_no_remotes(temp_git_repo: Path) -> None:
    """FR-01: get_remote_origin returns None when no remotes are configured."""
    service = GitService(temp_git_repo)
    assert service.get_remote_origin() is None


def test_get_remote_origin_upstream_only(temp_git_repo: Path) -> None:
    """
    FR-01: get_remote_origin returns None when only non-origin remotes
    (e.g., 'upstream') exist, without substituting them.
    """
    repo = git.Repo(str(temp_git_repo))
    repo.create_remote("upstream", "https://github.com/upstream/project.git")

    service = GitService(temp_git_repo)
    assert service.get_remote_origin() is None


def test_get_remote_origin_simulated_read_error(temp_git_repo: Path) -> None:
    """
    FR-01: get_remote_origin raises explicit RepositoryValidationError
    when reading the Git configuration fails, rather than silently returning None.
    """
    config_path = temp_git_repo / ".git" / "config"
    # Overwrite .git/config with corrupted syntax
    config_path.write_text("[corrupted_section_header\nkey = val\n", encoding="utf-8")

    service = GitService(temp_git_repo)
    with pytest.raises(RepositoryValidationError, match="Failed to read Git remote configuration"):
        service.get_remote_origin()


def test_list_tracked_files_git_failure_raises_validation_error(temp_git_repo: Path) -> None:
    """FR-01: list_tracked_files raises RepositoryValidationError on Git failure rather than returning empty list."""
    from unittest.mock import patch
    from git.exc import GitCommandError

    service = GitService(temp_git_repo)
    with patch.object(git.cmd.Git, "_call_process", side_effect=GitCommandError("ls-files", 1)):
        with pytest.raises(RepositoryValidationError, match="Failed to list tracked files"):
            service.list_tracked_files()


def test_compute_diff_git_failure_raises_validation_error(temp_git_repo: Path) -> None:
    """FR-02: compute_diff raises RepositoryValidationError on Git diff failure rather than returning empty diff."""
    from unittest.mock import patch
    from git.exc import GitCommandError

    service = GitService(temp_git_repo)
    head_sha = service.get_head_commit()
    with patch.object(git.cmd.Git, "_call_process", side_effect=GitCommandError("diff", 1)):
        with pytest.raises(RepositoryValidationError, match="Failed to compute diff against baseline"):
            service.compute_diff(base_commit=head_sha)


def test_compute_diff_excludes_binary_files(temp_git_repo: Path) -> None:
    """
    FR-02: compute_diff excludes real binary files without executing or altering target code.
    Tests committed/modified, staged, and untracked binary files with raw null-byte payloads.
    """
    repo = git.Repo(str(temp_git_repo))
    service = GitService(temp_git_repo)
    head_sha = service.get_head_commit()

    # 1. Create a committed binary file (e.g. simulated compiled asset or binary blob)
    binary_content_v1 = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x3e\x00"
    committed_bin = temp_git_repo / "module.so"
    committed_bin.write_bytes(binary_content_v1)
    repo.index.add(["module.so"])
    repo.index.commit("chore: add binary module")
    base_sha = repo.head.commit.hexsha

    # 2. Modify the committed binary file in working tree
    binary_content_v2 = binary_content_v1 + b"\x90\x90\xcc\x00\xff"
    committed_bin.write_bytes(binary_content_v2)

    # 3. Add a staged binary file (real PNG magic header with null bytes)
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    staged_bin = temp_git_repo / "icon.png"
    staged_bin.write_bytes(png_bytes)
    repo.index.add(["icon.png"])

    # 4. Add an untracked binary file without standard image/compiled extension
    raw_bin = temp_git_repo / "payload.dat"
    raw_bin_bytes = b"\x00\x01\x02\x03\xff\xfe\x00\xaa\xbb\xcc"
    raw_bin.write_bytes(raw_bin_bytes)

    # 5. Add a canary Python text file to verify text changes are still captured
    canary_py = temp_git_repo / "canary.py"
    canary_py.write_text("def canary() -> bool:\n    return True\n", encoding="utf-8")
    repo.index.add(["canary.py"])

    # Compute diff against the new baseline
    hunks = service.compute_diff(base_commit=base_sha)

    # Verify binary files are completely excluded from diff hunks
    diff_file_paths = {h.file_path for h in hunks}
    assert Path("module.so") not in diff_file_paths
    assert Path("icon.png") not in diff_file_paths
    assert Path("payload.dat") not in diff_file_paths

    # Verify that text changes are captured properly
    assert Path("canary.py") in diff_file_paths

    # Verify binary files on disk were not altered
    assert committed_bin.read_bytes() == binary_content_v2
    assert staged_bin.read_bytes() == png_bytes
    assert raw_bin.read_bytes() == raw_bin_bytes


def test_get_file_content_at_commit_success(temp_git_repo: Path) -> None:
    """
    Verifies that get_file_content_at_commit retrieves valid file content
    at a specific commit SHA without host execution (INV-01).
    """
    service = GitService(temp_git_repo)
    head_sha = service.get_head_commit()
    content = service.get_file_content_at_commit(head_sha, Path("calculator.py"))
    assert "def add(" in content


def test_get_file_content_at_commit_invalid_file_raises_error(temp_git_repo: Path) -> None:
    """
    Verifies that attempting to read a non-existent file at a commit SHA
    raises RepositoryValidationError.
    """
    service = GitService(temp_git_repo)
    head_sha = service.get_head_commit()
    with pytest.raises(RepositoryValidationError, match="Failed to retrieve file"):
        service.get_file_content_at_commit(head_sha, Path("non_existent_file.py"))
