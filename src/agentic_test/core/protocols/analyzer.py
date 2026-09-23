"""
CodeAnalyzer Protocol Definition.
Stage 3 Listing 4.1 & Section 4.4.2.1.
Decouples static source-code parsing and symbol extraction from underlying parser implementations.
"""

from pathlib import Path
from typing import List, Protocol, runtime_checkable

from agentic_test.core.models import DiffHunk, SymbolContract


class SyntaxParsingError(Exception):
    """Raised when source code contains unrecoverable syntax errors."""
    pass


@runtime_checkable
class CodeAnalyzer(Protocol):
    """
    Formal protocol decoupling static source-code parsing and symbol extraction
    from underlying parser implementations (Python ast, Tree-sitter, etc.).
    """

    def parse_symbols(self, file_path: Path, content: str) -> List[SymbolContract]:
        """
        Statically parses source code content into structured SymbolContract entities.
        Must operate deterministically and NEVER execute the parsed code.

        :param file_path: Absolute or relative path to the source file.
        :param content: Raw UTF-8 source code string.
        :return: List of parsed callable symbols (functions, methods, classes).
        :raises SyntaxParsingError: If source contains unrecoverable syntax errors.
        """
        ...

    def extract_dependencies(self, file_path: Path, content: str) -> List[str]:
        """
        Extracts imported module dependencies, symbols, and standard libraries.

        :param file_path: Path to the target source file.
        :param content: Raw source code string.
        :return: List of imported module names (e.g., ['math', 'typing', 'pydantic']).
        """
        ...

    def resolve_affected_symbols(
        self,
        symbols: List[SymbolContract],
        diff_hunks: List[DiffHunk],
    ) -> List[SymbolContract]:
        """
        Intersects unified diff line ranges with AST symbol spans to identify
        all callable symbols modified or introduced by the working tree diff.

        :param symbols: Full list of extracted symbols for the repository.
        :param diff_hunks: Parsed unified diff hunks.
        :return: Subset of symbols directly intersected by modified lines.
        """
        ...
