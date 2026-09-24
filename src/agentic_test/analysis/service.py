"""
AnalysisService: High-level facade coordinating Git inspection, static AST traversal,
and test discovery into an immutable RepositorySnapshot.
Stage 3 Section 4.4.4.1 Listing 4.1 & Figures 4.4 / 4.8.
Enforces INV-01 and INV-02: Zero target application code execution during analysis.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from agentic_test.analysis.ast_analyzer import PythonASTAnalyzer
from agentic_test.analysis.discovery import TestDiscovery
from agentic_test.analysis.git_service import GitService
from agentic_test.core.models import RepositorySnapshot, SymbolContract
from agentic_test.core.protocols.analyzer import CodeAnalyzer, SyntaxParsingError


class AnalysisService:
    """
    High-level facade coordinating Git inspection, static AST traversal,
    and test discovery into an immutable RepositorySnapshot.
    Stage 3 Section 4.4.4.1.
    """

    def __init__(
        self,
        git_service: Optional[GitService] = None,
        analyzer: Optional[CodeAnalyzer] = None,
        test_discovery: Optional[TestDiscovery] = None,
    ) -> None:
        """
        Initializes AnalysisService with optional adapter dependencies for testability.

        :param git_service: Optional GitService adapter.
        :param analyzer: Optional CodeAnalyzer adapter (defaults to PythonASTAnalyzer).
        :param test_discovery: Optional TestDiscovery service.
        """
        self._git_service = git_service
        self._analyzer = analyzer
        self._test_discovery = test_discovery

    def analyze(
        self,
        repo_path: Path,
        base_commit: Optional[str] = None,
    ) -> RepositorySnapshot:
        """
        Coordinates Git inspection and static AST analysis to assemble an immutable RepositorySnapshot.
        Catches SyntaxParsingError per file, recording errors into snapshot.syntax_errors
        without halting execution across other valid files.
        Enforces INV-01 and INV-02: pure static analysis; zero target code execution.

        :param repo_path: Absolute or relative path to target repository root.
        :param base_commit: Optional explicit base commit reference.
        :return: Fully populated, immutable RepositorySnapshot.
        """
        git_svc = self._git_service or GitService(repo_path)
        git_svc.validate_repository(repo_path)

        current_commit = git_svc.get_head_commit()
        resolved_base = git_svc.get_base_commit(base_commit)
        branch_name = git_svc.get_branch_name()
        source_tree_hash = git_svc.compute_source_tree_hash()
        diff_hunks = git_svc.compute_diff(resolved_base)
        tracked_files = git_svc.list_tracked_files()
        existing_test_files = git_svc.list_existing_test_files()

        # Configure analyzer with explicit repository root if not pre-configured
        if self._analyzer is not None:
            analyzer = self._analyzer
            if isinstance(analyzer, PythonASTAnalyzer) and analyzer._root_path is None:
                analyzer = PythonASTAnalyzer(root_path=repo_path)
        else:
            analyzer = PythonASTAnalyzer(root_path=repo_path)

        all_symbols: List[SymbolContract] = []
        syntax_errors: List[str] = []

        # Traverse tracked Python files and parse symbols statically
        for rel_path in tracked_files:
            if rel_path.suffix != ".py":
                continue

            abs_path = repo_path / rel_path
            if not abs_path.is_file():
                continue

            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError as err:
                syntax_errors.append(f"{rel_path}: {err}")
                continue

            try:
                symbols = analyzer.parse_symbols(rel_path, content)
                all_symbols.extend(symbols)
            except SyntaxParsingError as err:
                # Per-file syntax isolation: record error and continue to valid files
                syntax_errors.append(f"{rel_path}: {err}")

        # Intersect parsed symbols with diff hunks
        affected_symbols = analyzer.resolve_affected_symbols(all_symbols, diff_hunks)

        return RepositorySnapshot(
            repo_path=repo_path,
            current_commit=current_commit,
            base_commit=resolved_base,
            branch_name=branch_name,
            tracked_files=tuple(tracked_files),
            existing_test_files=tuple(existing_test_files),
            source_tree_hash=source_tree_hash,
            is_valid=True,
            created_at=datetime.now(timezone.utc),
            diff_hunks=tuple(diff_hunks),
            affected_symbols=tuple(affected_symbols),
            syntax_errors=tuple(syntax_errors),
        )
