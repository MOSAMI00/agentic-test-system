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
from agentic_test.core.models import RepositorySnapshot, SymbolContract, SymbolType


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
