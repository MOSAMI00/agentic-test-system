"""
Unit and integration tests for AnalysisService facade.
Stage 3 Section 4.4.4.1 Listing 4.1 & Figures 4.4 / 4.8.
Verifies FR-01, FR-02, FR-03, FR-04, INV-01, INV-02.
"""

from pathlib import Path
import tempfile
import pytest
from git import Repo

from agentic_test.analysis.ast_analyzer import PythonASTAnalyzer
from agentic_test.analysis.discovery import TestDiscovery
from agentic_test.analysis.git_service import GitService
from agentic_test.analysis.service import AnalysisService
from agentic_test.core.models import (
    ChangeType,
    ExecutionPlan,
    RepositorySnapshot,
    SymbolContract,
    SymbolType,
    WorkflowRoute,
)
from agentic_test.planning.planner import ExecutionPlanner
from agentic_test.planning.rules import compute_decision_hash


@pytest.fixture
def sample_git_repo(tmp_path: Path) -> Path:
    """Creates a valid git repository with valid Python files."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    src_dir = repo_dir / "src" / "pkg"
    src_dir.mkdir(parents=True)
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()

    calc_file = src_dir / "calc.py"
    calc_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b\n",
        encoding="utf-8",
    )

    test_file = tests_dir / "test_calc.py"
    test_file.write_text(
        "from pkg.calc import add\n"
        "\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    repo.index.add(["src/pkg/calc.py", "tests/test_calc.py"])
    repo.index.commit("Initial commit")
    return repo_dir


def test_analysis_service_coordinates_git_ast_and_discovery(sample_git_repo: Path) -> None:
    """
    Verifies that AnalysisService coordinates GitService, PythonASTAnalyzer,
    and TestDiscovery to assemble a fully populated RepositorySnapshot.
    """
    # Modify ONLY 'add' in src/pkg/calc.py
    calc_file = sample_git_repo / "src" / "pkg" / "calc.py"
    calc_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b + 0\n"
        "\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b\n",
        encoding="utf-8",
    )

    service = AnalysisService()
    snapshot = service.analyze(sample_git_repo)

    assert isinstance(snapshot, RepositorySnapshot)
    assert snapshot.is_valid is True
    assert len(snapshot.diff_hunks) == 1
    assert len(snapshot.syntax_errors) == 0

    # Only 'pkg.calc.add' is modified; 'pkg.calc.multiply' is in context only
    affected_names = [s.qualified_name for s in snapshot.affected_symbols]
    assert "pkg.calc.add" in affected_names
    assert "pkg.calc.multiply" not in affected_names

    # Test file discovery
    assert any(p.name == "test_calc.py" for p in snapshot.existing_test_files)


def test_analysis_service_resilience_to_malformed_python_files(tmp_path: Path) -> None:
    """
    Verifies that AnalysisService catches SyntaxParsingError per file, logs it into
    snapshot.syntax_errors, and continues processing other valid files.
    """
    repo_dir = tmp_path / "syntax_repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    src_dir = repo_dir / "src"
    src_dir.mkdir()

    valid_file = src_dir / "valid.py"
    valid_file.write_text("def valid_func() -> int:\n    return 42\n", encoding="utf-8")

    malformed_file = src_dir / "malformed.py"
    malformed_file.write_text("def broken_syntax(:\n", encoding="utf-8")

    repo.index.add(["src/valid.py", "src/malformed.py"])
    repo.index.commit("Initial commit with malformed file")

    service = AnalysisService()
    snapshot = service.analyze(repo_dir)

    # Malformed file error recorded in syntax_errors
    assert len(snapshot.syntax_errors) == 1
    assert "malformed.py" in snapshot.syntax_errors[0]

    # Valid file was processed successfully without halting
    assert snapshot.is_valid is True


def test_analysis_service_zero_target_code_execution(tmp_path: Path) -> None:
    """
    Verifies INV-01 and INV-02: Target code containing top-level runtime exceptions
    is statically analyzed without code execution on the host.
    """
    repo_dir = tmp_path / "poison_repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    src_dir = repo_dir / "src"
    src_dir.mkdir()
    poison_file = src_dir / "poison.py"
    poison_file.write_text(
        "import sys\n"
        "raise RuntimeError('Target application code executed during AnalysisService.analyze()!')\n"
        "def safe_func():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    repo.index.add(["src/poison.py"])
    repo.index.commit("Initial commit with poison file")

    service = AnalysisService()
    # Must complete without triggering RuntimeError
    snapshot = service.analyze(repo_dir)
    assert snapshot.is_valid is True
    assert len(snapshot.syntax_errors) == 0


def test_analysis_service_dependency_injection(sample_git_repo: Path) -> None:
    """
    Verifies that AnalysisService accepts mock/custom adapters via constructor injection.
    """
    custom_git = GitService(sample_git_repo)
    custom_analyzer = PythonASTAnalyzer(root_path=sample_git_repo)
    custom_discovery = TestDiscovery()

    service = AnalysisService(
        git_service=custom_git,
        analyzer=custom_analyzer,
        test_discovery=custom_discovery,
    )
    snapshot = service.analyze(sample_git_repo)
    assert snapshot.is_valid is True


def test_analysis_service_diff_added_untracked_python_files_parsed_and_populates_affected_symbols(
    sample_git_repo: Path,
) -> None:
    """
    Verifies that Python files represented by ADDED diff hunks (untracked in Git)
    are included in AST analysis and populate affected_symbols, while preserving
    strict Git semantics for snapshot.tracked_files (FR-02, FR-03, FR-04).
    """
    new_file = sample_git_repo / "src" / "pkg" / "new_feature.py"
    new_file.write_text(
        "def calculate_tax(amount: float) -> float:\n"
        "    return amount * 0.2\n",
        encoding="utf-8",
    )
    # File is NOT staged (git add) -> untracked in Git index

    service = AnalysisService()
    snapshot = service.analyze(sample_git_repo)

    # 1. Tracked files preserves strict git ls-files semantics
    assert Path("src/pkg/new_feature.py") not in snapshot.tracked_files

    # 2. Diff hunks contains the ADDED hunk
    added_hunks = [
        h for h in snapshot.diff_hunks
        if h.file_path == Path("src/pkg/new_feature.py") and h.change_type == ChangeType.ADDED
    ]
    assert len(added_hunks) == 1

    # 3. Affected symbols contains the callable from the untracked file
    affected_names = [s.qualified_name for s in snapshot.affected_symbols]
    assert "pkg.new_feature.calculate_tax" in affected_names


def test_end_to_end_analysis_to_execution_plan(sample_git_repo: Path) -> None:
    """
    End-to-end integration test:
    Real Git working tree change -> AnalysisService.analyze() -> TestDiscovery -> ExecutionPlanner.plan().
    Verifies pipeline integrity across Layers 1, 2, and 3 without mock fixtures.
    """
    calc_file = sample_git_repo / "src" / "pkg" / "calc.py"
    calc_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b + 0\n"
        "\n"
        "def multiply(a: int, b: int) -> int:\n"
        "    return a * b * 1\n",
        encoding="utf-8",
    )

    # Ingest and analyze working tree
    service = AnalysisService()
    snapshot = service.analyze(sample_git_repo)

    # Plan execution using real TestDiscovery
    planner = ExecutionPlanner(TestDiscovery())
    plan = planner.plan(snapshot)

    # 1. Route verification: uncovered symbol triggers test generation
    assert plan.route == WorkflowRoute.ROUTE_TO_TEST_GENERATION

    # 2. Affected symbols in snapshot: both modified callables detected
    affected_names = {s.qualified_name for s in snapshot.affected_symbols}
    assert "pkg.calc.add" in affected_names
    assert "pkg.calc.multiply" in affected_names

    # 3. Target symbols in plan: only uncovered symbol requiring synthesis
    target_names = [s.qualified_name for s in plan.target_symbols]
    assert "pkg.calc.multiply" in target_names
    assert "pkg.calc.add" not in target_names

    # 4. Existing tests to run: regression baseline for covered symbol
    existing_tests = [p.as_posix() for p in plan.existing_tests_to_run]
    assert any("test_calc.py" in p for p in existing_tests)

    # 5. Cryptographic decision hash verification
    expected_hash = compute_decision_hash(
        source_tree_hash=snapshot.source_tree_hash,
        route=plan.route,
        rationale=plan.rationale,
        target_symbols=plan.target_symbols,
        existing_tests_to_run=plan.existing_tests_to_run,
    )
    assert plan.decision_hash == expected_hash
    assert len(plan.decision_hash) == 64


def test_characterization_deleted_callable_produces_empty_affected_symbols(sample_git_repo: Path) -> None:
    """
    Characterization test documenting existing behavior:
    Deleting an entire callable leaves it absent from working tree AST, resulting in
    zero affected symbols and causing Decision 1 to select ROUTE_NO_OP.
    """
    calc_file = sample_git_repo / "src" / "pkg" / "calc.py"
    calc_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n",
        encoding="utf-8",
    )

    service = AnalysisService()
    snapshot = service.analyze(sample_git_repo)

    # Diff hunk records deletion lines
    assert len(snapshot.diff_hunks) == 1
    # Characterization: working tree AST has no multiply, so affected_symbols is empty
    assert len(snapshot.affected_symbols) == 0

    planner = ExecutionPlanner()
    plan = planner.plan(snapshot)
    assert plan.route == WorkflowRoute.ROUTE_NO_OP
    assert plan.rationale == "Python modifications contain no affected callable symbols"


def test_analysis_service_diff_syntax_error_sets_is_valid_false(sample_git_repo: Path) -> None:
    """
    Verifies that a syntax error in a Python file touched by the working-tree diff
    is recorded in snapshot.syntax_errors and marks snapshot.is_valid = False (Decision A).
    """
    calc_file = sample_git_repo / "src" / "pkg" / "calc.py"
    calc_file.write_text("def broken_syntax(:\n", encoding="utf-8")

    service = AnalysisService()
    snapshot = service.analyze(sample_git_repo)

    # Syntax error recorded in snapshot.syntax_errors
    assert len(snapshot.syntax_errors) == 1
    assert "calc.py" in snapshot.syntax_errors[0]
    # No symbols extracted from malformed file
    assert len(snapshot.affected_symbols) == 0
    # Decision A: snapshot is marked invalid
    assert snapshot.is_valid is False


def test_analysis_service_syntax_error_outside_diff_preserves_is_valid_true(tmp_path: Path) -> None:
    """
    Verifies that syntax errors in Python files outside the working-tree diff
    are treated as non-blocking warnings: recorded in snapshot.syntax_errors,
    while snapshot.is_valid remains True (Decision A).
    """
    repo_dir = tmp_path / "syntax_warn_repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    src_dir = repo_dir / "src"
    src_dir.mkdir()

    valid_file = src_dir / "valid.py"
    valid_file.write_text("def valid_func() -> int:\n    return 42\n", encoding="utf-8")

    malformed_file = src_dir / "malformed.py"
    malformed_file.write_text("def broken_syntax(:\n", encoding="utf-8")

    repo.index.add(["src/valid.py", "src/malformed.py"])
    repo.index.commit("Initial commit with valid and malformed files")

    # Modify ONLY valid.py in working tree
    valid_file.write_text("def valid_func() -> int:\n    return 100\n", encoding="utf-8")

    service = AnalysisService()
    snapshot = service.analyze(repo_dir)

    # 1. Non-blocking warning recorded for malformed.py
    assert len(snapshot.syntax_errors) == 1
    assert "malformed.py" in snapshot.syntax_errors[0]

    # 2. Diff touches ONLY valid.py
    assert len(snapshot.diff_hunks) == 1
    assert snapshot.diff_hunks[0].file_path == Path("src/valid.py")

    # 3. Affected symbols extracted from modified valid file
    affected_names = [s.qualified_name for s in snapshot.affected_symbols]
    assert any("valid_func" in name for name in affected_names)

    # 4. Decision A: syntax error outside diff does NOT invalidate snapshot
    assert snapshot.is_valid is True
