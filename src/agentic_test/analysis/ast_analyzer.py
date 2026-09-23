"""
PythonASTAnalyzer: Concrete implementation of CodeAnalyzer protocol using Python standard library ast.
Stage 3 Section 4.4.4.1 & Listing 4.1.
Enforces INV-01 and INV-02: Zero target application code execution during parsing.
"""

import ast
from pathlib import Path
from typing import List, Optional, Set

from agentic_test.core.models import DiffHunk, SymbolContract, SymbolType
from agentic_test.core.protocols.analyzer import CodeAnalyzer, SyntaxParsingError


def _derive_module_name(file_path: Path) -> str:
    """Derives a normalized Python module name from a file path."""
    parts = list(file_path.parts)
    if not parts:
        return file_path.stem

    # Strip known root prefixes like 'src' if present at start
    if parts and parts[0] == "src":
        parts = parts[1:]

    if not parts:
        return file_path.stem

    # If the file is __init__.py, the module name is the enclosing directory
    if parts[-1] == "__init__.py" or file_path.stem == "__init__":
        if len(parts) > 1:
            return ".".join(parts[:-1])
        return file_path.parent.name or "package"

    # Replace last element with its stem
    stem = Path(parts[-1]).stem
    dotted_parts = parts[:-1] + [stem]
    return ".".join(dotted_parts)


def _format_signature(node: ast.AST) -> str:
    """Reconstructs the function/method signature string from an AST node."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""

    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    name = node.name

    # Build argument list using ast.unparse for annotations and defaults
    args_str = ""
    try:
        args_str = ast.unparse(node.args)
    except Exception:
        # Fallback manual reconstruction if unparse fails
        arg_names = [arg.arg for arg in node.args.args]
        args_str = ", ".join(arg_names)

    return_annot = ""
    if node.returns:
        try:
            return_annot = f" -> {ast.unparse(node.returns)}"
        except Exception:
            return_annot = ""

    return f"{prefix}{name}({args_str}){return_annot}"


class PythonASTAnalyzer(CodeAnalyzer):
    """
    Concrete realization of the CodeAnalyzer protocol.
    Parses Python source files using the standard library ast module without executing target code.
    """

    def parse_symbols(self, file_path: Path, content: str) -> List[SymbolContract]:
        """
        Statically parses Python source code content into structured SymbolContract entities.
        Does NOT execute or import the parsed code.

        :param file_path: Path to the target source file.
        :param content: Raw UTF-8 source code string.
        :return: List of parsed callable symbols (functions, methods, classes).
        :raises SyntaxParsingError: If source contains unrecoverable syntax errors.
        """
        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as err:
            raise SyntaxParsingError(f"Syntax error parsing {file_path}: {err}") from err

        module_name = _derive_module_name(file_path)
        symbols: List[SymbolContract] = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_qualname = f"{module_name}.{node.name}" if module_name else node.name
                line_range = (node.lineno, getattr(node, "end_lineno", node.lineno))
                sig = _format_signature(node)
                doc = ast.get_docstring(node)
                symbols.append(
                    SymbolContract(
                        qualified_name=func_qualname,
                        symbol_type=SymbolType.FUNCTION,
                        file_path=file_path,
                        line_range=line_range,
                        signature=sig,
                        docstring=doc,
                        is_affected=False,
                    )
                )

            elif isinstance(node, ast.ClassDef):
                cls_qualname = f"{module_name}.{node.name}" if module_name else node.name
                cls_line_range = (node.lineno, getattr(node, "end_lineno", node.lineno))
                cls_doc = ast.get_docstring(node)

                # Class contract
                symbols.append(
                    SymbolContract(
                        qualified_name=cls_qualname,
                        symbol_type=SymbolType.CLASS,
                        file_path=file_path,
                        line_range=cls_line_range,
                        signature=f"class {node.name}",
                        docstring=cls_doc,
                        is_affected=False,
                    )
                )

                # Methods inside class
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_qualname = f"{cls_qualname}.{item.name}"
                        method_line_range = (item.lineno, getattr(item, "end_lineno", item.lineno))
                        method_sig = _format_signature(item)
                        method_doc = ast.get_docstring(item)
                        symbols.append(
                            SymbolContract(
                                qualified_name=method_qualname,
                                symbol_type=SymbolType.METHOD,
                                file_path=file_path,
                                line_range=method_line_range,
                                signature=method_sig,
                                docstring=method_doc,
                                is_affected=False,
                            )
                        )

        return symbols

    def extract_dependencies(self, file_path: Path, content: str) -> List[str]:
        """
        Extracts imported module dependencies, packages, and standard libraries.

        :param file_path: Path to the target source file.
        :param content: Raw source code string.
        :return: Sorted list of imported module names (e.g., ['math', 'os', 'typing']).
        """
        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as err:
            raise SyntaxParsingError(f"Syntax error extracting dependencies from {file_path}: {err}") from err

        dependencies: Set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg:
                        dependencies.add(root_pkg)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg:
                        dependencies.add(root_pkg)
                elif node.level and node.level > 0:
                    # Relative import without module (e.g., 'from . import foo')
                    for alias in node.names:
                        dependencies.add(alias.name.split(".")[0])

        return sorted(dependencies)

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
        :return: Subset of symbols directly intersected by modified lines, with is_affected=True.
        """
        affected_symbols: List[SymbolContract] = []

        for symbol in symbols:
            is_affected = False
            for hunk in diff_hunks:
                # Compare file paths by path object or relative posix match
                if not self._paths_match(symbol.file_path, hunk.file_path):
                    continue

                # Compute modified line interval in new file
                hunk_start = hunk.new_start
                hunk_end = hunk.new_start + max(hunk.new_lines - 1, 0) if hunk.new_lines > 0 else hunk.new_start

                # Interval overlap test: max(start1, start2) <= min(end1, end2)
                sym_start, sym_end = symbol.line_range
                if max(sym_start, hunk_start) <= min(sym_end, hunk_end):
                    is_affected = True
                    break

            if is_affected:
                # Create an updated SymbolContract with is_affected=True
                affected_symbols.append(
                    SymbolContract(
                        qualified_name=symbol.qualified_name,
                        symbol_type=symbol.symbol_type,
                        file_path=symbol.file_path,
                        line_range=symbol.line_range,
                        signature=symbol.signature,
                        docstring=symbol.docstring,
                        dependencies=symbol.dependencies,
                        is_affected=True,
                    )
                )

        return affected_symbols

    @staticmethod
    def _paths_match(path1: Path, path2: Path) -> bool:
        """Determines whether two paths refer to the same relative file."""
        if path1 == path2:
            return True
        p1_posix = path1.as_posix()
        p2_posix = path2.as_posix()
        return p1_posix == p2_posix or p1_posix.endswith("/" + p2_posix) or p2_posix.endswith("/" + p1_posix)
