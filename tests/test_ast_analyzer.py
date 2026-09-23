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
