"""
Unit tests for PythonASTAnalyzer (CodeAnalyzer protocol implementation).
Stage 3 Listing 4.1 & Section 4.4.4.1.
Enforces INV-01 and INV-02: Zero target application code execution during parsing.
"""

from pathlib import Path
import pytest

from agentic_test.analysis.ast_analyzer import PythonASTAnalyzer
from agentic_test.core.models import ChangeType, DiffHunk, SymbolContract, SymbolType
from agentic_test.core.protocols.analyzer import CodeAnalyzer, SyntaxParsingError


def test_python_ast_analyzer_implements_protocol() -> None:
    analyzer = PythonASTAnalyzer()
    assert isinstance(analyzer, CodeAnalyzer)


def test_parse_symbols_functions() -> None:
    analyzer = PythonASTAnalyzer()
    code = (
        'def calculate_tax(subtotal: float, rate: float = 0.05) -> float:\n'
        '    """Calculates sales tax from subtotal."""\n'
        '    return subtotal * rate\n'
    )
    symbols = analyzer.parse_symbols(Path("src/billing/tax.py"), code)

    assert len(symbols) == 1
    sym = symbols[0]
    assert sym.qualified_name == "billing.tax.calculate_tax"
    assert sym.symbol_type == SymbolType.FUNCTION
    assert sym.line_range == (1, 3)
    assert sym.signature == "def calculate_tax(subtotal: float, rate: float=0.05) -> float"
    assert sym.docstring == "Calculates sales tax from subtotal."
    assert sym.is_affected is False


def test_parse_symbols_classes_and_methods() -> None:
    analyzer = PythonASTAnalyzer()
    code = (
        'class BankAccount:\n'
        '    """Represents a simple bank account."""\n'
        '    def __init__(self, initial: float) -> None:\n'
        '        self.balance = initial\n'
        '\n'
        '    def deposit(self, amount: float) -> float:\n'
        '        """Adds funds to the balance."""\n'
        '        self.balance += amount\n'
        '        return self.balance\n'
    )
    symbols = analyzer.parse_symbols(Path("account.py"), code)

    assert len(symbols) == 3
    cls_sym = symbols[0]
    assert cls_sym.qualified_name == "account.BankAccount"
    assert cls_sym.symbol_type == SymbolType.CLASS
    assert cls_sym.line_range == (1, 9)
    assert cls_sym.docstring == "Represents a simple bank account."

    init_sym = symbols[1]
    assert init_sym.qualified_name == "account.BankAccount.__init__"
    assert init_sym.symbol_type == SymbolType.METHOD
    assert init_sym.line_range == (3, 4)

    dep_sym = symbols[2]
    assert dep_sym.qualified_name == "account.BankAccount.deposit"
    assert dep_sym.symbol_type == SymbolType.METHOD
    assert dep_sym.line_range == (6, 9)
    assert dep_sym.docstring == "Adds funds to the balance."


def test_parse_symbols_async_functions() -> None:
    analyzer = PythonASTAnalyzer()
    code = (
        'async def fetch_record(record_id: int) -> dict:\n'
        '    """Fetches a database record asynchronously."""\n'
        '    return {"id": record_id}\n'
    )
    symbols = analyzer.parse_symbols(Path("db/client.py"), code)

    assert len(symbols) == 1
    sym = symbols[0]
    assert sym.qualified_name == "db.client.fetch_record"
    assert sym.symbol_type == SymbolType.FUNCTION
    assert sym.line_range == (1, 3)
    assert "async def fetch_record" in sym.signature


def test_extract_dependencies() -> None:
    analyzer = PythonASTAnalyzer()
    code = (
        'import os\n'
        'import math\n'
        'from typing import List, Optional\n'
        'from pydantic import BaseModel\n'
        'from .local_mod import helper\n'
    )
    deps = analyzer.extract_dependencies(Path("service.py"), code)
    assert "os" in deps
    assert "math" in deps
    assert "typing" in deps
    assert "pydantic" in deps
    assert "local_mod" in deps
    assert deps == sorted(deps)


def test_parse_symbols_syntax_error_raises_syntax_parsing_error() -> None:
    analyzer = PythonASTAnalyzer()
    malformed_code = "def unclosed_function(:\n    pass"

    with pytest.raises(SyntaxParsingError) as exc_info:
        analyzer.parse_symbols(Path("broken.py"), malformed_code)
    assert "Syntax error" in str(exc_info.value)

    with pytest.raises(SyntaxParsingError):
        analyzer.extract_dependencies(Path("broken.py"), malformed_code)


def test_resolve_affected_symbols_overlapping_diff() -> None:
    analyzer = PythonASTAnalyzer()
    sym = SymbolContract(
        qualified_name="calc.add",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("calc.py"),
        line_range=(10, 20),
        signature="def add(a, b)",
        is_affected=False,
    )
    hunk = DiffHunk(
        file_path=Path("calc.py"),
        old_start=12,
        old_lines=2,
        new_start=12,
        new_lines=4,
        change_type=ChangeType.MODIFIED,
        content="@@ -12,2 +12,4 @@",
    )

    affected = analyzer.resolve_affected_symbols([sym], [hunk])
    assert len(affected) == 1
    assert affected[0].qualified_name == "calc.add"
    assert affected[0].is_affected is True


def test_resolve_affected_symbols_non_overlapping_diff() -> None:
    analyzer = PythonASTAnalyzer()
    sym = SymbolContract(
        qualified_name="calc.add",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("calc.py"),
        line_range=(10, 20),
        signature="def add(a, b)",
        is_affected=False,
    )
    hunk = DiffHunk(
        file_path=Path("calc.py"),
        old_start=30,
        old_lines=5,
        new_start=30,
        new_lines=5,
        change_type=ChangeType.MODIFIED,
        content="@@ -30,5 +30,5 @@",
    )

    affected = analyzer.resolve_affected_symbols([sym], [hunk])
    assert len(affected) == 0


def test_resolve_affected_symbols_multi_hunk_selective() -> None:
    analyzer = PythonASTAnalyzer()
    sym1 = SymbolContract(
        qualified_name="service.func1",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("service.py"),
        line_range=(1, 10),
        signature="def func1()",
        is_affected=False,
    )
    sym2 = SymbolContract(
        qualified_name="service.func2",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("service.py"),
        line_range=(20, 30),
        signature="def func2()",
        is_affected=False,
    )
    hunk = DiffHunk(
        file_path=Path("service.py"),
        old_start=5,
        old_lines=2,
        new_start=5,
        new_lines=2,
        change_type=ChangeType.MODIFIED,
        content="@@ -5,2 +5,2 @@",
    )

    affected = analyzer.resolve_affected_symbols([sym1, sym2], [hunk])
    assert len(affected) == 1
    assert affected[0].qualified_name == "service.func1"
    assert affected[0].is_affected is True


def test_zero_target_code_execution_in_ast_analyzer() -> None:
    analyzer = PythonASTAnalyzer()
    malicious_code = (
        'import sys\n'
        'raise RuntimeError("Target application code executed during analysis!")\n'
        'def normal_func():\n'
        '    return 42\n'
    )
    # ast.parse must succeed and NOT execute the RuntimeError statement
    symbols = analyzer.parse_symbols(Path("malicious.py"), malicious_code)
    assert len(symbols) == 1
    assert symbols[0].qualified_name == "malicious.normal_func"


def test_resolve_affected_symbols_adjacent_functions_git_diff(tmp_path: Path) -> None:
    """
    Verifies that context lines in real Git unified diffs do NOT mark
    unmodified adjacent symbols as affected (Corrective Task 1).
    """
    from git import Repo
    from agentic_test.analysis.git_service import GitService

    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    calc_file = repo_dir / "calc.py"
    calc_file.write_text(
        "def func_a(x):\n"
        "    # line 2\n"
        "    # line 3\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def func_b(y):\n"
        "    # line 8\n"
        "    return y * 2\n",
        encoding="utf-8",
    )
    repo.index.add(["calc.py"])
    commit = repo.index.commit("Initial commit")

    # Modify ONLY func_a (line 4)
    calc_file.write_text(
        "def func_a(x):\n"
        "    # line 2\n"
        "    # line 3\n"
        "    return x + 2\n"
        "\n"
        "\n"
        "def func_b(y):\n"
        "    # line 8\n"
        "    return y * 2\n",
        encoding="utf-8",
    )

    git_svc = GitService(repo_dir)
    diff_hunks = git_svc.compute_diff(commit.hexsha)
    assert len(diff_hunks) == 1
    # Note: unified=3 causes new_lines=7, reaching line 7 where func_b begins
    assert diff_hunks[0].new_lines >= 7

    analyzer = PythonASTAnalyzer(root_path=repo_dir)
    symbols = analyzer.parse_symbols(Path("calc.py"), calc_file.read_text(encoding="utf-8"))
    assert len(symbols) == 2

    affected = analyzer.resolve_affected_symbols(symbols, diff_hunks)
    affected_names = [s.qualified_name for s in affected]

    # ONLY func_a should be affected; func_b is in context only
    assert "calc.func_a" in affected_names
    assert "calc.func_b" not in affected_names


def test_resolve_affected_symbols_deletion_in_symbol() -> None:
    """
    Verifies that deleted lines mapped to the containing symbol boundary mark it as affected,
    while deletions between symbols do not mark adjacent symbols as affected.
    """
    analyzer = PythonASTAnalyzer()
    sym_a = SymbolContract(
        qualified_name="mod.func_a",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("mod.py"),
        line_range=(1, 3),
        signature="def func_a()",
        is_affected=False,
    )
    sym_b = SymbolContract(
        qualified_name="mod.func_b",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("mod.py"),
        line_range=(7, 9),
        signature="def func_b()",
        is_affected=False,
    )

    # Deletion inside func_a (deleted lines 2-3 of old file)
    hunk_del_inside = DiffHunk(
        file_path=Path("mod.py"),
        old_start=1,
        old_lines=5,
        new_start=1,
        new_lines=3,
        change_type=ChangeType.MODIFIED,
        content=(
            "@@ -1,5 +1,3 @@\n"
            " def func_a():\n"
            "-    x = 1\n"
            "-    y = 2\n"
            "     return x\n"
        ),
    )
    affected_inside = analyzer.resolve_affected_symbols([sym_a, sym_b], [hunk_del_inside])
    assert [s.qualified_name for s in affected_inside] == ["mod.func_a"]

    # Deletion between functions (e.g. blank line at line 5 deleted)
    hunk_del_between = DiffHunk(
        file_path=Path("mod.py"),
        old_start=3,
        old_lines=4,
        new_start=3,
        new_lines=3,
        change_type=ChangeType.MODIFIED,
        content=(
            "@@ -3,4 +3,3 @@\n"
            "     return x\n"
            "-\n"
            " \n"
            " def func_b():\n"
        ),
    )
    affected_between = analyzer.resolve_affected_symbols([sym_a, sym_b], [hunk_del_between])
    # Neither function was modified
    assert len(affected_between) == 0


def test_derive_module_name_relative_and_absolute_paths(tmp_path: Path) -> None:
    """
    Verifies that PythonASTAnalyzer configured with an explicit root_path derives
    identical qualified names for relative and absolute paths (Corrective Task 3).
    """
    src_dir = tmp_path / "src" / "pkg"
    src_dir.mkdir(parents=True)
    service_file = src_dir / "service.py"
    code = "def run(): pass\n"
    service_file.write_text(code, encoding="utf-8")

    analyzer = PythonASTAnalyzer(root_path=tmp_path)

    # Relative path
    symbols_rel = analyzer.parse_symbols(Path("src/pkg/service.py"), code)
    assert symbols_rel[0].qualified_name == "pkg.service.run"

    # Absolute path
    symbols_abs = analyzer.parse_symbols(service_file, code)
    assert symbols_abs[0].qualified_name == "pkg.service.run"


def test_git_diff_deletion_single_line_inside_function(tmp_path: Path) -> None:
    """Scenario 1: Pure deletion of a single line inside a function body (zero '+' lines)."""
    from git import Repo
    from agentic_test.analysis.git_service import GitService

    repo_dir = tmp_path / "repo1"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    target_file = repo_dir / "mod.py"
    target_file.write_text(
        "def func_a(x):\n"
        "    step_1 = x + 1\n"
        "    step_2 = x + 2\n"
        "    return step_1\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )
    repo.index.add(["mod.py"])
    commit = repo.index.commit("Initial commit")

    # Pure deletion of line 3 (step_2) inside func_a; no lines added
    target_file.write_text(
        "def func_a(x):\n"
        "    step_1 = x + 1\n"
        "    return step_1\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )

    git_svc = GitService(repo_dir)
    diff_hunks = git_svc.compute_diff(commit.hexsha)
    assert len(diff_hunks) == 1
    assert "+" not in [line[0] for line in diff_hunks[0].content.splitlines() if line and not line.startswith("@@")]

    analyzer = PythonASTAnalyzer(root_path=repo_dir)
    symbols = analyzer.parse_symbols(Path("mod.py"), target_file.read_text(encoding="utf-8"))

    affected = [s.qualified_name for s in analyzer.resolve_affected_symbols(symbols, diff_hunks)]
    assert "mod.func_a" in affected
    assert "mod.func_b" not in affected


def test_git_diff_deletion_first_line_of_function_body(tmp_path: Path) -> None:
    """Scenario 2: Pure deletion of the first line of a function body (zero '+' lines)."""
    from git import Repo
    from agentic_test.analysis.git_service import GitService

    repo_dir = tmp_path / "repo2"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    target_file = repo_dir / "mod.py"
    target_file.write_text(
        "def func_a(x):\n"
        "    setup = 1\n"
        "    return x + 1\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )
    repo.index.add(["mod.py"])
    commit = repo.index.commit("Initial commit")

    # Pure deletion of first line of func_a body (setup = 1); no lines added
    target_file.write_text(
        "def func_a(x):\n"
        "    return x + 1\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )

    git_svc = GitService(repo_dir)
    diff_hunks = git_svc.compute_diff(commit.hexsha)
    assert len(diff_hunks) == 1
    assert "+" not in [line[0] for line in diff_hunks[0].content.splitlines() if line and not line.startswith("@@")]

    analyzer = PythonASTAnalyzer(root_path=repo_dir)
    symbols = analyzer.parse_symbols(Path("mod.py"), target_file.read_text(encoding="utf-8"))

    affected = [s.qualified_name for s in analyzer.resolve_affected_symbols(symbols, diff_hunks)]
    assert "mod.func_a" in affected
    assert "mod.func_b" not in affected


def test_git_diff_deletion_entire_function(tmp_path: Path) -> None:
    """
    Scenario 3: Deletion of an entire function preceding another function.
    Documents the deletion boundary: when an entire function is deleted and is absent
    from the new AST, resolve_affected_symbols returns no deleted symbol.
    Deleted-symbol reporting is outside the current SymbolContract/affected_symbols model
    unless separately approved.
    """
    from git import Repo
    from agentic_test.analysis.git_service import GitService

    repo_dir = tmp_path / "repo3"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    target_file = repo_dir / "mod.py"
    target_file.write_text(
        "def func_a(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )
    repo.index.add(["mod.py"])
    commit = repo.index.commit("Initial commit")

    # Delete entire func_a; func_b remains
    target_file.write_text(
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )

    git_svc = GitService(repo_dir)
    diff_hunks = git_svc.compute_diff(commit.hexsha)
    analyzer = PythonASTAnalyzer(root_path=repo_dir)
    symbols = analyzer.parse_symbols(Path("mod.py"), target_file.read_text(encoding="utf-8"))

    # func_a does not exist in the new AST symbols; func_b was NOT modified
    symbol_names = [s.qualified_name for s in symbols]
    assert "mod.func_a" not in symbol_names
    assert "mod.func_b" in symbol_names

    # Under current SymbolContract/affected_symbols model, deleted symbols absent from
    # the new file AST cannot be returned; resolve_affected_symbols filters symbols
    # present in the new AST, so affected list is empty and mod.func_b is not marked affected.
    affected = [s.qualified_name for s in analyzer.resolve_affected_symbols(symbols, diff_hunks)]
    assert "mod.func_b" not in affected
    assert len(affected) == 0


def test_git_diff_deletion_between_adjacent_functions(tmp_path: Path) -> None:
    """Scenario 4: Deletion of comments and whitespace between two adjacent functions."""
    from git import Repo
    from agentic_test.analysis.git_service import GitService

    repo_dir = tmp_path / "repo4"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    target_file = repo_dir / "mod.py"
    target_file.write_text(
        "def func_a(x):\n"
        "    return x + 1\n"
        "\n"
        "# Comment between functions to be removed\n"
        "# Another comment line\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )
    repo.index.add(["mod.py"])
    commit = repo.index.commit("Initial commit")

    # Delete comments between functions
    target_file.write_text(
        "def func_a(x):\n"
        "    return x + 1\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )

    git_svc = GitService(repo_dir)
    diff_hunks = git_svc.compute_diff(commit.hexsha)
    analyzer = PythonASTAnalyzer(root_path=repo_dir)
    symbols = analyzer.parse_symbols(Path("mod.py"), target_file.read_text(encoding="utf-8"))

    affected = [s.qualified_name for s in analyzer.resolve_affected_symbols(symbols, diff_hunks)]
    # Neither function was modified
    assert len(affected) == 0


def test_git_diff_replacement_single_line(tmp_path: Path) -> None:
    """Scenario 5: Replacement represented by one '-' line and one '+' line."""
    from git import Repo
    from agentic_test.analysis.git_service import GitService

    repo_dir = tmp_path / "repo5"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)

    target_file = repo_dir / "mod.py"
    target_file.write_text(
        "def func_a(x):\n"
        "    return x + 1\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )
    repo.index.add(["mod.py"])
    commit = repo.index.commit("Initial commit")

    # Replace line in func_a: x + 1 -> x + 42
    target_file.write_text(
        "def func_a(x):\n"
        "    return x + 42\n"
        "\n"
        "def func_b(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )

    git_svc = GitService(repo_dir)
    diff_hunks = git_svc.compute_diff(commit.hexsha)
    analyzer = PythonASTAnalyzer(root_path=repo_dir)
    symbols = analyzer.parse_symbols(Path("mod.py"), target_file.read_text(encoding="utf-8"))

    affected = [s.qualified_name for s in analyzer.resolve_affected_symbols(symbols, diff_hunks)]
    assert "mod.func_a" in affected
    assert "mod.func_b" not in affected

