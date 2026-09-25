"""
Gate 2 Security Validator enforcing conservative static safety invariants.
Iteration 1.4 Component Architecture.
"""

import ast
from typing import List, Optional, Set, Tuple

from agentic_test.core.models import TestCandidate, ValidationStatus
from agentic_test.validation.base import BaseValidator
from agentic_test.validation.models import ValidationResult


FORBIDDEN_MODULES: Set[str] = {
    "os",
    "sys",
    "subprocess",
    "shutil",
    "socket",
    "http",
    "urllib",
    "requests",
    "multiprocessing",
    "threading",
    "ctypes",
    "pty",
    "commands",
    "telnetlib",
    "ftplib",
    "poplib",
    "smtplib",
    "importlib",
    "builtins",
}

FORBIDDEN_BUILTINS: Set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
    "delattr",
}

FORBIDDEN_DIRECT_EXECUTION: Set[str] = {
    "system",
    "popen",
    "spawn",
    "fork",
    "execve",
    "execl",
    "execv",
}

SUBPROCESS_EXECUTION_METHODS: Set[str] = {
    "run",
    "call",
    "Popen",
    "check_call",
    "check_output",
}

FORBIDDEN_WRITE_METHODS: Set[str] = {
    "write_text",
    "write_bytes",
    "mkdir",
    "makedirs",
    "rmdir",
    "remove",
    "unlink",
    "rename",
    "replace",
    "chmod",
    "chown",
    "touch",
    "copyfile",
    "copy2",
    "copytree",
    "move",
    "rmtree",
    "truncate",
}


class _SecurityASTVisitor(ast.NodeVisitor):
    """
    AST visitor detecting security violations and statically recognizable filesystem writes.
    """

    def __init__(self) -> None:
        self.violations: List[str] = []
        self._subprocess_symbols: Set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_mod = alias.name.split(".")[0]
            if root_mod in FORBIDDEN_MODULES:
                self.violations.append(
                    f"Forbidden import '{alias.name}' at line {node.lineno}"
                )
            if alias.name == "subprocess":
                self._subprocess_symbols.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root_mod = node.module.split(".")[0]
            if root_mod in FORBIDDEN_MODULES:
                self.violations.append(
                    f"Forbidden from-import '{node.module}' at line {node.lineno}"
                )
            if root_mod == "subprocess":
                for alias in node.names:
                    self._subprocess_symbols.add(alias.asname or alias.name)
        self.generic_visit(node)

    def _is_subprocess_receiver(self, node: ast.expr) -> bool:
        """Determines if the receiver expression statically identifies the subprocess module."""
        if isinstance(node, ast.Name):
            return node.id == "subprocess" or node.id in self._subprocess_symbols
        if isinstance(node, ast.Attribute):
            return node.attr == "subprocess" or self._is_subprocess_receiver(node.value)
        return False

    def visit_Call(self, node: ast.Call) -> None:
        # 1. Direct function call (e.g., eval(), exec(), open(), or imported subprocess call)
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in FORBIDDEN_BUILTINS:
                self.violations.append(
                    f"Forbidden builtin call '{func_name}()' at line {node.lineno}"
                )
            elif func_name in FORBIDDEN_DIRECT_EXECUTION:
                self.violations.append(
                    f"Forbidden execution call '{func_name}()' at line {node.lineno}"
                )
            elif func_name in self._subprocess_symbols:
                self.violations.append(
                    f"Forbidden subprocess call '{func_name}()' at line {node.lineno}"
                )
            elif func_name == "open":
                self._check_open_mode(node)

        # 2. Attribute / method call (e.g., path.write_text(), subprocess.run(), os.system())
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in FORBIDDEN_WRITE_METHODS:
                self.violations.append(
                    f"Forbidden filesystem mutation '{attr_name}()' at line {node.lineno}"
                )
            elif attr_name in FORBIDDEN_DIRECT_EXECUTION:
                self.violations.append(
                    f"Forbidden execution call '{attr_name}()' at line {node.lineno}"
                )
            elif attr_name in SUBPROCESS_EXECUTION_METHODS and self._is_subprocess_receiver(node.func.value):
                self.violations.append(
                    f"Forbidden subprocess execution call '{attr_name}()' at line {node.lineno}"
                )
            elif attr_name in FORBIDDEN_BUILTINS:
                self.violations.append(
                    f"Forbidden builtin call '{attr_name}()' at line {node.lineno}"
                )
            elif attr_name == "open":
                self._check_open_mode(node)

        self.generic_visit(node)

    def _check_open_mode(self, node: ast.Call) -> None:
        """Inspects open() call to reject write/append/mutate modes."""
        mode_val: Optional[str] = None

        # Check positional mode argument (index 1)
        if len(node.args) >= 2:
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                mode_val = arg.value
            else:
                self.violations.append(
                    f"Dynamic or non-constant open mode at line {node.lineno} cannot be verified safe"
                )
                return

        # Check keyword mode argument
        for kw in node.keywords:
            if kw.arg == "mode":
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    mode_val = kw.value.value
                else:
                    self.violations.append(
                        f"Dynamic or non-constant open mode at line {node.lineno} cannot be verified safe"
                    )
                    return

        # If write/append/exclusive/read-write mode detected
        if mode_val is not None:
            if any(m in mode_val for m in ("w", "a", "x", "+")):
                self.violations.append(
                    f"Forbidden filesystem write mode '{mode_val}' in open() at line {node.lineno}"
                )


class SecurityValidator(BaseValidator):
    """
    Gate 2: Static security validator inspecting AST for forbidden operations.
    Enforces zero untrusted execution (INV-01) and rejects all statically recognizable
    filesystem writes (no exceptions for tmp_path or tempfile fixtures).
    """

    @property
    def gate_name(self) -> str:
        return "GATE_2_SECURITY"

    def validate(self, candidate: TestCandidate) -> ValidationResult:
        """
        Parses candidate and visits AST nodes to detect security and write violations.
        """
        combined_source = self._build_source(candidate)

        try:
            tree = ast.parse(combined_source)
        except SyntaxError as err:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_SECURITY,
                gate=self.gate_name,
                passed=False,
                error_message=f"Cannot perform security analysis due to syntax error: {err}",
                diagnostics=(str(err),),
            )

        visitor = _SecurityASTVisitor()
        visitor.visit(tree)

        if visitor.violations:
            return ValidationResult(
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.REJECTED_SECURITY,
                gate=self.gate_name,
                passed=False,
                error_message=f"Security policy violation: {visitor.violations[0]}",
                diagnostics=tuple(visitor.violations),
            )

        return ValidationResult(
            candidate_id=candidate.candidate_id,
            status=ValidationStatus.PASSED,
            gate=self.gate_name,
            passed=True,
        )

    def _build_source(self, candidate: TestCandidate) -> str:
        """Combines imports and candidate code for AST analysis."""
        parts = []
        if candidate.imports:
            parts.append("\n".join(candidate.imports))
        if candidate.candidate_code:
            parts.append(candidate.candidate_code)
        return "\n\n".join(parts)
