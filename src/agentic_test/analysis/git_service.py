"""
Git repository inspection and unified diff extraction service.
Adheres strictly to FR-01, FR-02, NFR-01, and Stage 3 Section 4.4.4.1.
"""

import configparser
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import List, Optional, Set, Tuple

import git
from git.exc import BadName, BadObject, GitError, InvalidGitRepositoryError, NoSuchPathError

from agentic_test.core.models import (
    ChangeType,
    DiffHunk,
    RepositorySnapshot,
    RepositoryValidationError,
)

# Standard directory names and file patterns excluded from static ingestion (FR-01, FR-02)
EXCLUDED_DIR_NAMES: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".eggs",
    "build",
    "dist",
}

BINARY_EXTENSIONS: Set[str] = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip",
    ".tar", ".gz", ".db", ".sqlite", ".bin"
}

HUNK_HEADER_REGEX = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$", re.MULTILINE
)


def is_excluded_path(path: Path) -> bool:
    """
    Checks whether a file path falls into an excluded directory or has a binary extension.
    Excludes .git, virtual environments, caches, and binary files.
    """
    for part in path.parts:
        if part in EXCLUDED_DIR_NAMES or part.endswith(".egg-info"):
            return True
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    return False


class GitService:
    """
    Deterministic Git repository inspection service.
    Extracts Git metadata and unified diff hunks without importing or executing application code.
    """

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = Path(repo_path).resolve()
        self._repo: Optional[git.Repo] = None

    def _get_repo(self) -> git.Repo:
        """Lazily obtains and caches the git.Repo instance, verifying repository validity."""
        if self._repo is None:
            self.validate_repository(self.repo_path)
            try:
                self._repo = git.Repo(str(self.repo_path))
            except (InvalidGitRepositoryError, NoSuchPathError) as exc:
                raise RepositoryValidationError(
                    f"Path '{self.repo_path}' is not a valid Git repository: {exc}"
                ) from exc
        return self._repo

    def validate_repository(self, path: Path) -> bool:
        """
        Validates that target directory exists and is an initialized Git repository.
        FR-01: Rejects non-existent or uninitialized directories with RepositoryValidationError.
        """
        p = Path(path).resolve()
        if not p.exists():
            raise RepositoryValidationError(f"Repository path does not exist: {p}")
        if not p.is_dir():
            raise RepositoryValidationError(f"Repository path is not a directory: {p}")
        try:
            repo = git.Repo(str(p))
            # Test that git directory exists
            if not repo.git_dir or not Path(repo.git_dir).exists():
                raise RepositoryValidationError(f"Directory is not a Git repository: {p}")
            return True
        except (InvalidGitRepositoryError, NoSuchPathError) as exc:
            raise RepositoryValidationError(
                f"Directory is not a valid Git repository: {p}"
            ) from exc

    def get_head_commit(self) -> str:
        """
        Retrieves the 40-character SHA-1 hex string of the current HEAD commit.
        FR-01.
        """
        repo = self._get_repo()
        try:
            head_commit = repo.head.commit.hexsha
            if len(head_commit) != 40:
                raise RepositoryValidationError(f"Invalid HEAD commit SHA format: {head_commit}")
            return head_commit
        except (ValueError, AttributeError) as exc:
            raise RepositoryValidationError(
                f"Repository at '{self.repo_path}' has no commits: {exc}"
            ) from exc

    def get_branch_name(self) -> str:
        """
        Retrieves active branch name, or 'HEAD' if in detached HEAD state.
        FR-01.
        """
        repo = self._get_repo()
        try:
            return repo.active_branch.name
        except TypeError:
            # Detached HEAD state
            return "HEAD"

    def get_remote_origin(self) -> Optional[str]:
        """
        Retrieves the URL of the remote named 'origin'.
        FR-01: Returns None if no 'origin' remote is configured.
        Raises RepositoryValidationError if reading the Git configuration fails.
        """
        repo = self._get_repo()
        try:
            for remote in repo.remotes:
                if remote.name == "origin":
                    return str(remote.url)
            return None
        except (GitError, configparser.Error, OSError) as exc:
            raise RepositoryValidationError(
                f"Failed to read Git remote configuration from '{self.repo_path}': {exc}"
            ) from exc

    def get_base_commit(self, explicit_base: Optional[str] = None) -> str:
        """
        Resolves the baseline comparison commit SHA.
        If not explicitly specified, defaults to the first parent of HEAD (HEAD~1),
        or HEAD itself if HEAD has no parents (initial commit).
        """
        repo = self._get_repo()
        if explicit_base:
            try:
                commit_obj = repo.commit(explicit_base)
                return commit_obj.hexsha
            except (GitError, BadName, BadObject, ValueError) as exc:
                raise RepositoryValidationError(
                    f"Specified base commit '{explicit_base}' not found: {exc}"
                ) from exc

        # Automatic base resolution
        head = repo.head.commit
        if head.parents:
            return head.parents[0].hexsha
        return head.hexsha

    def get_file_content_at_commit(self, commit_sha: str, rel_path: Path) -> str:
        """
        Retrieves UTF-8 content of a tracked file at a specific Git commit SHA.
        FR-01 / FR-02: Reads from git show <commit_sha>:<posix_path> without executing code.
        """
        repo = self._get_repo()
        posix_path = rel_path.as_posix()
        try:
            content: str = repo.git.show(f"{commit_sha}:{posix_path}")
            return content
        except GitError as exc:
            raise RepositoryValidationError(
                f"Failed to retrieve file '{rel_path}' at commit '{commit_sha}': {exc}"
            ) from exc


    def list_tracked_files(self) -> List[Path]:
        """
        Lists all files currently tracked by Git in the repository, excluding virtualenvs, caches, etc.
        Raises RepositoryValidationError if reading tracked files from Git fails.
        """
        repo = self._get_repo()
        tracked: List[Path] = []
        try:
            for item in repo.git.ls_files().splitlines():
                if not item.strip():
                    continue
                file_rel = Path(item.strip())
                if not is_excluded_path(file_rel):
                    tracked.append(file_rel)
        except GitError as exc:
            raise RepositoryValidationError(
                f"Failed to list tracked files in '{self.repo_path}': {exc}"
            ) from exc
        return sorted(tracked)

    def list_existing_test_files(self) -> List[Path]:
        """
        Discovers existing test files matching pytest conventions (test_*.py or *_test.py).
        Excludes caches and virtualenvs.
        """
        repo = self._get_repo()
        test_files: List[Path] = []
        # Search tracked files first
        for f in self.list_tracked_files():
            if f.suffix == ".py" and (f.name.startswith("test_") or f.name.endswith("_test.py")):
                test_files.append(f)

        # Also search untracked files on filesystem
        for p in self.repo_path.rglob("*.py"):
            try:
                rel = p.relative_to(self.repo_path)
            except ValueError:
                continue
            if is_excluded_path(rel):
                continue
            if (rel.name.startswith("test_") or rel.name.endswith("_test.py")) and rel not in test_files:
                test_files.append(rel)

        return sorted(test_files)

    def compute_source_tree_hash(self) -> str:
        """
        Computes a deterministic cryptographic SHA-256 hash of all tracked source files.
        Enforces INV-02 baseline recording.
        """
        hasher = hashlib.sha256()
        for rel_path in self.list_tracked_files():
            full_path = self.repo_path / rel_path
            if full_path.is_file():
                try:
                    file_bytes = full_path.read_bytes()
                    file_hash = hashlib.sha256(file_bytes).hexdigest()
                    hasher.update(f"{rel_path.as_posix()}:{file_hash}\n".encode("utf-8"))
                except OSError:
                    continue
        return hasher.hexdigest()

    def compute_diff(self, base_commit: Optional[str] = None) -> List[DiffHunk]:
        """
        Computes unified diff between working tree and baseline reference commit.
        FR-02: Extracts file paths, change types (ADDED, MODIFIED, DELETED),
        and individual change hunks with exact line spans.
        Ignores binary files, virtualenvs, caches, and .git.
        """
        repo = self._get_repo()
        resolved_base = self.get_base_commit(base_commit)

        diff_hunks: List[DiffHunk] = []

        # 1. Extract diff against resolved base including working tree changes
        # Use repo.git.diff(resolved_base, unified=3)
        try:
            diff_text = repo.git.diff(resolved_base, unified=3)
        except GitError as exc:
            raise RepositoryValidationError(
                f"Failed to compute diff against baseline commit '{resolved_base}': {exc}"
            ) from exc

        if diff_text:
            diff_hunks.extend(self._parse_unified_diff(diff_text))

        # 2. Check for untracked new files that are not yet in git index
        untracked = repo.untracked_files
        for item in untracked:
            rel_path = Path(item)
            if is_excluded_path(rel_path):
                continue
            full_path = self.repo_path / rel_path
            if not full_path.is_file():
                continue
            # Treat untracked file as an ADDED hunk, ignoring binary files
            try:
                with full_path.open("rb") as bf:
                    if b"\x00" in bf.read(8192):
                        continue
                content = full_path.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines(keepends=True)
                line_count = len(lines) if lines else 1
                hunk_header = f"@@ -0,0 +1,{line_count} @@"
                hunk_content = hunk_header + "\n" + "".join(f"+{l}" if not l.startswith("+") else l for l in lines)
                diff_hunks.append(
                    DiffHunk(
                        file_path=rel_path,
                        old_start=0,
                        old_lines=0,
                        new_start=1,
                        new_lines=line_count,
                        change_type=ChangeType.ADDED,
                        content=hunk_content,
                    )
                )
            except OSError:
                continue

        return diff_hunks

    def _parse_unified_diff(self, diff_text: str) -> List[DiffHunk]:
        """
        Parses raw git unified diff output into structured DiffHunk value objects.
        """
        hunks: List[DiffHunk] = []
        if not diff_text or not diff_text.strip():
            return hunks

        # Split diff by file chunks: 'diff --git a/'
        file_chunks = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)

        for chunk in file_chunks:
            chunk = chunk.strip()
            if not chunk or not chunk.startswith("diff --git"):
                continue

            # Parse file headers
            file_match = re.search(r"^diff --git a/(.*?) b/(.*?)$", chunk, re.MULTILINE)
            if not file_match:
                continue

            old_path_str = file_match.group(1)
            new_path_str = file_match.group(2)
            rel_path = Path(new_path_str if new_path_str != "/dev/null" else old_path_str)

            # Check exclusion filtering
            if is_excluded_path(rel_path):
                continue

            # Skip binary diff chunks reported by Git
            if "Binary files " in chunk or "GIT binary patch" in chunk:
                continue

            # Determine change type
            if "new file mode" in chunk or old_path_str == "/dev/null" or "--- /dev/null" in chunk:
                change_type = ChangeType.ADDED
            elif "deleted file mode" in chunk or new_path_str == "/dev/null" or "+++ /dev/null" in chunk:
                change_type = ChangeType.DELETED
            else:
                change_type = ChangeType.MODIFIED

            # Split into individual hunks starting with @@
            hunk_matches = list(HUNK_HEADER_REGEX.finditer(chunk))
            for i, match in enumerate(hunk_matches):
                old_start = int(match.group(1))
                old_lines = int(match.group(2)) if match.group(2) is not None else 1
                new_start = int(match.group(3))
                new_lines = int(match.group(4)) if match.group(4) is not None else 1

                # Extract hunk body from this header to next header or end of chunk
                start_pos = match.start()
                end_pos = hunk_matches[i + 1].start() if i + 1 < len(hunk_matches) else len(chunk)
                hunk_content = chunk[start_pos:end_pos].strip()

                hunks.append(
                    DiffHunk(
                        file_path=rel_path,
                        old_start=old_start,
                        old_lines=old_lines,
                        new_start=new_start,
                        new_lines=new_lines,
                        change_type=change_type,
                        content=hunk_content,
                    )
                )

        return hunks

    def create_snapshot(self, base_commit: Optional[str] = None) -> RepositorySnapshot:
        """
        Coordinates Git inspection to assemble an immutable RepositorySnapshot.
        Enforces FR-01 and Option A contract clarification.
        """
        self.validate_repository(self.repo_path)
        current_commit = self.get_head_commit()
        resolved_base = self.get_base_commit(base_commit)
        branch_name = self.get_branch_name()
        tracked_files = self.list_tracked_files()
        test_files = self.list_existing_test_files()
        source_hash = self.compute_source_tree_hash()
        diff_hunks = self.compute_diff(resolved_base)

        return RepositorySnapshot(
            repo_path=self.repo_path,
            current_commit=current_commit,
            base_commit=resolved_base,
            branch_name=branch_name,
            tracked_files=tuple(tracked_files),
            existing_test_files=tuple(test_files),
            source_tree_hash=source_hash,
            is_valid=True,
            created_at=datetime.now(timezone.utc),
            diff_hunks=tuple(diff_hunks),
            affected_symbols=(),  # Populated during Iteration 1.2
            syntax_errors=(),     # Populated during Iteration 1.2
        )
