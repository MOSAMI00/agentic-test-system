"""
TestDiscovery: Service for discovering pytest test suites and evaluating static symbol associations.
Stage 3 Section 4.4.4.1.
Enforces INV-01, INV-02, and conservative static evidence gating.
"""

import ast
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, TypedDict

from pydantic import BaseModel, ConfigDict

from agentic_test.analysis.git_service import is_excluded_path
from agentic_test.core.models import SymbolContract, SymbolType


class AssociationClassification(str, Enum):
    """
    Deterministic static classification of target symbol association.
    Does NOT assert runtime test coverage, branch execution, or behavioral correctness.
    """
    STATICALLY_ASSOCIATED = "STATICALLY_ASSOCIATED"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class EvidenceKind(str, Enum):
    """Specific nature of deterministic static evidence identified in test AST."""
    # Positive Static Evidence (Candidate for STATICALLY_ASSOCIATED)
    DIRECT_CALL = "DIRECT_CALL"
    MODULE_QUALIFIED_CALL = "MODULE_QUALIFIED_CALL"
    ALIASED_CALL = "ALIASED_CALL"
    METHOD_CALL = "METHOD_CALL"
    AWAITED_CALL = "AWAITED_CALL"

    # Disqualification Evidence (Triggers UNSUPPORTED)
    EXPLICIT_MOCK_DISQUALIFIED = "EXPLICIT_MOCK_DISQUALIFIED"

    # Ambiguity / Absence Evidence (Triggers UNKNOWN)
    EXTERNAL_FIXTURE = "EXTERNAL_FIXTURE"
    DYNAMIC_DISPATCH = "DYNAMIC_DISPATCH"
    UNRESOLVED_ALIAS = "UNRESOLVED_ALIAS"
    EXTERNAL_HELPER = "EXTERNAL_HELPER"
    COMPLEX_RECEIVER = "COMPLEX_RECEIVER"
    NO_STATIC_REFERENCE = "NO_STATIC_REFERENCE"


class _MockInfo(TypedDict):
    type: str
    lineno: Optional[int]
    test_case: Optional[str]


class _FindingInfo(TypedDict):
    classification: AssociationClassification
    evidence_kind: EvidenceKind
    reason: str
    test_case: Optional[str]
    lineno: Optional[int]


class StaticAssociation(BaseModel):
    """
    Immutable audit record of a static association evaluation for a target symbol.
    Layer 3 Analysis domain value object only.
    """
    model_config = ConfigDict(frozen=True)

    target_symbol: str
    test_file_path: Path
    classification: AssociationClassification
    evidence_kind: EvidenceKind
    reason: str
    test_case_name: Optional[str] = None
    call_line_number: Optional[int] = None
    is_path_mirrored: bool = False
    is_name_correlated: bool = False


class _ImportMap:
    """Tracks local identifier bindings to canonical modules and exported symbols."""

    def __init__(self) -> None:
        # local_alias -> full_module_path (e.g., 'm' -> 'math_ops')
        self.modules: Dict[str, str] = {}
        # local_alias -> (module_path, original_symbol_name) (e.g., 'add_fn' -> ('math_ops', 'add'))
        self.symbols: Dict[str, Tuple[str, str]] = {}

    def add_module_import(self, module_path: str, alias: Optional[str] = None) -> None:
        local_name = alias if alias else module_path.split(".")[-1]
        self.modules[local_name] = module_path

    def add_symbol_import(self, module_path: str, symbol_name: str, alias: Optional[str] = None) -> None:
        local_name = alias if alias else symbol_name
        self.symbols[local_name] = (module_path, symbol_name)


class TestDiscovery:
    """
    Discovers pytest test suites and evaluates static associations against target symbols.
    Pure static analysis using Python ast; never imports or executes target application code.
    """

    __test__ = False

    def __init__(self, target_symbols: Optional[Sequence[SymbolContract]] = None) -> None:
        """
        Initializes TestDiscovery with an optional target-symbol index.

        :param target_symbols: Optional sequence of SymbolContract entities used by extract_tested_symbols.
        """
        self._target_symbols: Tuple[SymbolContract, ...] = tuple(target_symbols) if target_symbols else ()

    def discover_test_suites(self, repo_path: Path) -> List[Path]:
        """
        Discovers existing test files matching test_*.py or *_test.py in repo_path.
        Excludes .git, virtual environments, and caches. Preserves Stage 3 interface.

        :param repo_path: Root directory of target repository.
        :return: Sorted list of discovered test file paths.
        """
        test_files: List[Path] = []
        if not repo_path.exists() or not repo_path.is_dir():
            return test_files

        for file_path in repo_path.rglob("*.py"):
            if is_excluded_path(file_path):
                continue
            name = file_path.name
            if name.startswith("test_") or name.endswith("_test.py"):
                test_files.append(file_path)

        return sorted(test_files)

    def extract_tested_symbols(self, test_file: Path) -> List[str]:
        """
        Extracts qualified names of symbols statically associated with tests in this file.
        Stage 3 Section 4.4.4.1 interface.

        Returns only SymbolContract.qualified_name values classified as STATICALLY_ASSOCIATED.
        If no target symbols have been configured on TestDiscovery, returns an empty list.

        :param test_file: Discovered test file path.
        :return: Sorted deduplicated list of statically associated target symbol qualified names.
        """
        if not self._target_symbols:
            return []

        associations = self.analyze_associations(test_file, self._target_symbols)
        statically_associated = {
            assoc.target_symbol
            for assoc in associations
            if assoc.classification == AssociationClassification.STATICALLY_ASSOCIATED
        }
        return sorted(statically_associated)

    def analyze_associations(
        self,
        test_file: Path,
        target_symbols: Sequence[SymbolContract],
    ) -> Tuple[StaticAssociation, ...]:
        """
        Performs conservative static association analysis between target symbols and a test file.
        Emits records only when the test file contains static evidence relating to a specific symbol.

        :param test_file: Discovered test file to analyze.
        :param target_symbols: Target SymbolContract sequence to evaluate.
        :return: Deterministically sorted tuple of StaticAssociation audit records.
        """
        if not test_file.exists() or not target_symbols:
            return ()

        try:
            content = test_file.read_bytes().decode("utf-8", errors="replace")
            tree = ast.parse(content, filename=str(test_file))
        except (SyntaxError, OSError):
            return ()

        # Build imports map
        import_map = self._build_import_map(tree)

        # Build local symbol lookup
        symbol_index = self._index_target_symbols(target_symbols)

        # Detect module-level and function-level mocks
        disqualified_mocks = self._detect_mocks(tree, import_map, symbol_index, test_file)

        # Detect direct calls and ambiguous references
        findings = self._inspect_calls_and_references(tree, import_map, symbol_index, test_file)

        # Synthesize associations according to evidence-triggered emission rule
        associations: List[StaticAssociation] = []

        for symbol in target_symbols:
            qualname = symbol.qualified_name
            simple_name = qualname.split(".")[-1]

            # Priority 1: Was symbol explicitly mocked in this file?
            if qualname in disqualified_mocks:
                mock_info = disqualified_mocks[qualname]
                associations.append(
                    StaticAssociation(
                        target_symbol=qualname,
                        test_file_path=test_file,
                        classification=AssociationClassification.UNSUPPORTED,
                        evidence_kind=EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED,
                        reason=f"Explicit mock/patch detected for target symbol ({mock_info['type']})",
                        test_case_name=mock_info.get("test_case"),
                        call_line_number=mock_info.get("lineno"),
                        is_path_mirrored=self._is_path_mirrored(test_file, symbol.file_path),
                        is_name_correlated=self._is_name_correlated(mock_info.get("test_case"), simple_name),
                    )
                )
                continue

            # Priority 2: Direct call evidence found
            if qualname in findings:
                symbol_findings = findings[qualname]
                for finding in symbol_findings:
                    associations.append(
                        StaticAssociation(
                            target_symbol=qualname,
                            test_file_path=test_file,
                            classification=finding["classification"],
                            evidence_kind=finding["evidence_kind"],
                            reason=finding["reason"],
                            test_case_name=finding.get("test_case"),
                            call_line_number=finding.get("lineno"),
                            is_path_mirrored=self._is_path_mirrored(test_file, symbol.file_path),
                            is_name_correlated=self._is_name_correlated(finding.get("test_case"), simple_name),
                        )
                    )
                continue

            # Priority 3: No evidence in this test file -> emit zero records for this symbol

        # Deterministic sort
        return tuple(
            sorted(
                associations,
                key=lambda a: (a.target_symbol, a.call_line_number or 0, a.test_case_name or ""),
            )
        )

    def _build_import_map(self, tree: ast.AST) -> _ImportMap:
        """Extracts import mappings from AST."""
        imp_map = _ImportMap()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imp_map.add_module_import(alias.name, alias.asname)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for alias in node.names:
                        imp_map.add_symbol_import(node.module, alias.name, alias.asname)
        return imp_map

    def _index_target_symbols(self, target_symbols: Sequence[SymbolContract]) -> Dict[str, SymbolContract]:
        """Indexes target symbols by their qualified names."""
        return {s.qualified_name: s for s in target_symbols}

    def _detect_mocks(
        self,
        tree: ast.AST,
        import_map: _ImportMap,
        symbol_index: Dict[str, SymbolContract],
        test_file: Path,
    ) -> Dict[str, _MockInfo]:
        """
        Detects explicit, recognized mock/patch forms targeting any indexed symbol.
        Returns mapping from target qualified_name to mock metadata.
        """
        disqualified: Dict[str, _MockInfo] = {}

        for node in ast.walk(tree):
            test_case_name: Optional[str] = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                test_case_name = node.name
                # Check decorators
                for dec in node.decorator_list:
                    matched_qualname = self._match_patch_decorator(dec, symbol_index, import_map)
                    if matched_qualname:
                        disqualified[matched_qualname] = {
                            "type": "decorator",
                            "lineno": getattr(dec, "lineno", node.lineno),
                            "test_case": test_case_name,
                        }

            # Check with-block patches
            if isinstance(node, ast.With):
                for item in node.items:
                    matched_qualname = self._match_patch_expr(item.context_expr, symbol_index, import_map)
                    if matched_qualname:
                        disqualified[matched_qualname] = {
                            "type": "with_block",
                            "lineno": getattr(node, "lineno", None),
                            "test_case": test_case_name,
                        }

            # Check monkeypatch.setattr and mocker.patch calls
            if isinstance(node, ast.Call):
                matched_qualname = self._match_patch_call(node, symbol_index, import_map)
                if matched_qualname:
                    disqualified[matched_qualname] = {
                        "type": "patch_call",
                        "lineno": getattr(node, "lineno", None),
                        "test_case": test_case_name,
                    }

        return disqualified

    def _match_patch_decorator(
        self,
        dec: ast.AST,
        symbol_index: Dict[str, SymbolContract],
        import_map: _ImportMap,
    ) -> Optional[str]:
        """Matches @patch(...) or @patch.object(...) decorator."""
        if not isinstance(dec, ast.Call):
            return None
        return self._match_patch_expr(dec, symbol_index, import_map)

    def _match_patch_expr(
        self,
        call: ast.AST,
        symbol_index: Dict[str, SymbolContract],
        import_map: _ImportMap,
    ) -> Optional[str]:
        """Matches patch(...) or patch.object(...) expressions."""
        if not isinstance(call, ast.Call):
            return None

        func_name = self._get_call_func_name(call.func)
        # If func_name is an imported alias (e.g. from unittest.mock import patch as my_patch)
        if func_name in import_map.symbols:
            mod_path, orig = import_map.symbols[func_name]
            if orig in ("patch", "mock.patch", "patch.object") or "mock" in mod_path:
                func_name = orig
        elif "." in func_name:
            base, sub = func_name.split(".", 1)
            if base in import_map.symbols:
                mod_path, orig = import_map.symbols[base]
                if orig in ("patch", "mock.patch") or "mock" in mod_path:
                    func_name = f"{orig}.{sub}"

        # Check standard string patch: patch("pkg.mod.symbol")
        if func_name in ("patch", "mock.patch", "unittest.mock.patch", "mocker.patch"):
            if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                target_str = call.args[0].value
                for qualname in symbol_index:
                    if target_str == qualname or target_str.endswith("." + qualname.split(".")[-1]):
                        return qualname

        # Check patch.object: patch.object(TargetClass, "method")
        if func_name in ("patch.object", "mock.patch.object", "unittest.mock.patch.object", "mocker.patch.object"):
            if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
                target_cls = ast.unparse(call.args[0]) if hasattr(ast, "unparse") else ""
                # If target_cls is an aliased import, resolve to canonical class name
                if target_cls in import_map.symbols:
                    target_cls = import_map.symbols[target_cls][1]
                target_method = call.args[1].value
                for qualname, sym in symbol_index.items():
                    if sym.symbol_type == SymbolType.METHOD and qualname.endswith(f"{target_cls}.{target_method}"):
                        return qualname

        return None

    def _match_patch_call(
        self,
        call: ast.Call,
        symbol_index: Dict[str, SymbolContract],
        import_map: _ImportMap,
    ) -> Optional[str]:
        """Matches monkeypatch.setattr or mocker.patch calls."""
        func_name = self._get_call_func_name(call.func)

        # monkeypatch.setattr
        if func_name == "monkeypatch.setattr":
            if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                target_str = call.args[0].value
                for qualname in symbol_index:
                    if target_str == qualname or target_str.endswith("." + qualname.split(".")[-1]):
                        return qualname
            elif len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
                target_attr = call.args[1].value
                for qualname in symbol_index:
                    if qualname.split(".")[-1] == target_attr:
                        return qualname

        # mocker.patch / mocker.patch.object
        if func_name in ("mocker.patch", "mocker.patch.object"):
            return self._match_patch_expr(call, symbol_index, import_map)

        return None

    def _inspect_calls_and_references(
        self,
        tree: ast.AST,
        import_map: _ImportMap,
        symbol_index: Dict[str, SymbolContract],
        test_file: Path,
    ) -> Dict[str, List[_FindingInfo]]:
        """
        Inspects test functions/methods for direct calls and ambiguous references.
        """
        findings: Dict[str, List[_FindingInfo]] = {}

        # Collect local helper functions and the symbols they reference
        local_helpers = self._find_helper_references(tree, import_map, symbol_index)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            test_case_name = node.name
            if not test_case_name.startswith("test"):
                continue

            # Check for ambiguous fixture parameter matching target symbol name
            for arg in node.args.args:
                for qualname, sym in symbol_index.items():
                    simple_name = qualname.split(".")[-1]
                    if arg.arg == f"{simple_name}_fixture" or arg.arg == simple_name:
                        # Check if test body actually calls the symbol directly
                        if not self._body_calls_symbol(node, qualname, import_map):
                            findings.setdefault(qualname, []).append({
                                "classification": AssociationClassification.UNKNOWN,
                                "evidence_kind": EvidenceKind.EXTERNAL_FIXTURE,
                                "reason": f"Target symbol referenced via test parameter ({arg.arg}); external fixture unverified",
                                "test_case": test_case_name,
                                "lineno": node.lineno,
                            })

            # Track locally instantiated class objects within this test function
            local_instances = self._find_local_instances(node, import_map, symbol_index)

            # Traverse calls inside test body
            for item in ast.walk(node):
                if not isinstance(item, ast.Call):
                    continue

                call_lineno = getattr(item, "lineno", node.lineno)

                # 1. Direct imported function call: func(...)
                if isinstance(item.func, ast.Name):
                    call_name = item.func.id

                    # Check external helper call
                    if call_name in local_helpers:
                        for qualname in local_helpers[call_name]:
                            findings.setdefault(qualname, []).append({
                                "classification": AssociationClassification.UNKNOWN,
                                "evidence_kind": EvidenceKind.EXTERNAL_HELPER,
                                "reason": f"Target symbol referenced through local helper function '{call_name}()'",
                                "test_case": test_case_name,
                                "lineno": call_lineno,
                            })

                    # Check dynamic dispatch or dynamic alias
                    if call_name == "getattr" and len(item.args) >= 2:
                        second_arg = item.args[1]
                        if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str):
                            for qualname in symbol_index:
                                if qualname.split(".")[-1] == second_arg.value:
                                    findings.setdefault(qualname, []).append({
                                        "classification": AssociationClassification.UNKNOWN,
                                        "evidence_kind": EvidenceKind.DYNAMIC_DISPATCH,
                                        "reason": f"Dynamic dispatch via getattr() on '{second_arg.value}'",
                                        "test_case": test_case_name,
                                        "lineno": call_lineno,
                                    })

                    # Match through imports
                    if call_name in import_map.symbols:
                        mod_path, orig_name = import_map.symbols[call_name]
                        for qualname, sym in symbol_index.items():
                            sym_mod = qualname.rsplit(".", 1)[0] if "." in qualname else ""
                            sym_name = qualname.split(".")[-1]
                            if sym_name == orig_name and (not sym_mod or sym_mod.endswith(mod_path) or mod_path.endswith(sym_mod)):
                                evidence = EvidenceKind.ALIASED_CALL if call_name != orig_name else EvidenceKind.DIRECT_CALL
                                is_awaited = self._is_awaited_call(item, node)
                                if is_awaited:
                                    evidence = EvidenceKind.AWAITED_CALL

                                findings.setdefault(qualname, []).append({
                                    "classification": AssociationClassification.STATICALLY_ASSOCIATED,
                                    "evidence_kind": evidence,
                                    "reason": f"Direct static call to imported symbol '{orig_name}'",
                                    "test_case": test_case_name,
                                    "lineno": call_lineno,
                                })
                    else:
                        # Check unresolved local assignment / alias
                        for qualname, sym in symbol_index.items():
                            if sym.qualified_name.split(".")[-1] == call_name:
                                if self._is_locally_assigned(node, call_name):
                                    findings.setdefault(qualname, []).append({
                                        "classification": AssociationClassification.UNKNOWN,
                                        "evidence_kind": EvidenceKind.UNRESOLVED_ALIAS,
                                        "reason": f"Call to locally assigned, unresolvable callable alias '{call_name}'",
                                        "test_case": test_case_name,
                                        "lineno": call_lineno,
                                    })

                # 2. Module-qualified or receiver method call: mod.func(...) or obj.method(...)
                elif isinstance(item.func, ast.Attribute):
                    attr_name = item.func.attr

                    # Check if value is a simple module alias
                    if isinstance(item.func.value, ast.Name):
                        receiver_id = item.func.value.id
                        # Check module qualified: mod.func(...)
                        if receiver_id in import_map.modules:
                            mod_path = import_map.modules[receiver_id]
                            for qualname, sym in symbol_index.items():
                                sym_mod = qualname.rsplit(".", 1)[0] if "." in qualname else ""
                                sym_name = qualname.split(".")[-1]
                                if sym_name == attr_name and (not sym_mod or sym_mod.endswith(mod_path) or mod_path.endswith(sym_mod)):
                                    is_awaited = self._is_awaited_call(item, node)
                                    evidence = EvidenceKind.AWAITED_CALL if is_awaited else EvidenceKind.MODULE_QUALIFIED_CALL
                                    findings.setdefault(qualname, []).append({
                                        "classification": AssociationClassification.STATICALLY_ASSOCIATED,
                                        "evidence_kind": evidence,
                                        "reason": f"Direct module-qualified call '{receiver_id}.{attr_name}'",
                                        "test_case": test_case_name,
                                        "lineno": call_lineno,
                                    })

                        # Check locally instantiated receiver: obj.method(...)
                        elif receiver_id in local_instances:
                            target_cls_qualname = local_instances[receiver_id]
                            target_method_qualname = f"{target_cls_qualname}.{attr_name}"
                            if target_method_qualname in symbol_index:
                                is_awaited = self._is_awaited_call(item, node)
                                evidence = EvidenceKind.AWAITED_CALL if is_awaited else EvidenceKind.METHOD_CALL
                                findings.setdefault(target_method_qualname, []).append({
                                    "classification": AssociationClassification.STATICALLY_ASSOCIATED,
                                    "evidence_kind": evidence,
                                    "reason": f"Direct method call on locally instantiated receiver '{receiver_id}.{attr_name}'",
                                    "test_case": test_case_name,
                                    "lineno": call_lineno,
                                })

                    # Complex receiver (e.g. get_factory().create().method())
                    elif isinstance(item.func.value, ast.Call):
                        for qualname, sym in symbol_index.items():
                            if sym.symbol_type == SymbolType.METHOD and qualname.split(".")[-1] == attr_name:
                                findings.setdefault(qualname, []).append({
                                    "classification": AssociationClassification.UNKNOWN,
                                    "evidence_kind": EvidenceKind.COMPLEX_RECEIVER,
                                    "reason": f"Method call '{attr_name}' on chained/complex receiver expression",
                                    "test_case": test_case_name,
                                    "lineno": call_lineno,
                                })

        return findings

    def _find_local_instances(
        self,
        func_node: ast.AST,
        import_map: _ImportMap,
        symbol_index: Dict[str, SymbolContract],
    ) -> Dict[str, str]:
        """Tracks local variable assignments to simple class instantiations (e.g., c = Calculator())."""
        instances: Dict[str, str] = {}
        for stmt in ast.walk(func_node):
            if isinstance(stmt, ast.Assign):
                if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name):
                    cls_alias = stmt.value.func.id
                    target_cls_qualname: Optional[str] = None
                    if cls_alias in import_map.symbols:
                        mod_path, orig_name = import_map.symbols[cls_alias]
                        for qualname, sym in symbol_index.items():
                            if sym.symbol_type == SymbolType.CLASS and qualname.split(".")[-1] == orig_name:
                                target_cls_qualname = qualname
                                break
                            elif sym.symbol_type == SymbolType.METHOD and "." in qualname:
                                parent_cls = qualname.rsplit(".", 1)[0]
                                if parent_cls.split(".")[-1] == orig_name:
                                    target_cls_qualname = parent_cls
                                    break
                    if target_cls_qualname:
                        for target in stmt.targets:
                            if isinstance(target, ast.Name):
                                instances[target.id] = target_cls_qualname
        return instances

    def _is_awaited_call(self, call_node: ast.Call, func_node: ast.AST) -> bool:
        """Determines if a call node is directly wrapped in an await expression."""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Await) and node.value is call_node:
                return True
        return False

    def _body_calls_symbol(self, func_node: ast.AST, qualname: str, import_map: _ImportMap) -> bool:
        """Helper checking if any direct call in the function matches the target symbol."""
        simple_name = qualname.split(".")[-1]
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in import_map.symbols:
                    if import_map.symbols[node.func.id][1] == simple_name:
                        return True
        return False

    def _get_call_func_name(self, func_node: ast.AST) -> str:
        """Extracts dotted name from call func node."""
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            val_name = self._get_call_func_name(func_node.value)
            return f"{val_name}.{func_node.attr}" if val_name else func_node.attr
        return ""

    @staticmethod
    def _is_path_mirrored(test_file: Path, source_file: Path) -> bool:
        """Diagnostic only: returns True if test filename mirrors source filename."""
        test_stem = test_file.stem
        src_stem = source_file.stem
        return test_stem == f"test_{src_stem}" or test_stem == f"{src_stem}_test"

    @staticmethod
    def _is_name_correlated(test_case_name: Optional[str], simple_name: str) -> bool:
        """Diagnostic only: returns True if test case name contains the target symbol name."""
        if not test_case_name:
            return False
        return simple_name.lower() in test_case_name.lower()

    def _find_helper_references(
        self,
        tree: ast.AST,
        import_map: _ImportMap,
        symbol_index: Dict[str, SymbolContract],
    ) -> Dict[str, Set[str]]:
        """Maps local non-test functions to any target symbols they reference."""
        helpers: Dict[str, Set[str]] = {}
        for top_node in getattr(tree, "body", []):
            if isinstance(top_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not top_node.name.startswith("test_"):
                    refs = self._find_called_symbols_in_node(top_node, import_map, symbol_index)
                    if refs:
                        helpers[top_node.name] = refs
        return helpers

    def _find_called_symbols_in_node(
        self,
        func_node: ast.AST,
        import_map: _ImportMap,
        symbol_index: Dict[str, SymbolContract],
    ) -> Set[str]:
        """Collects target symbol qualified names referenced within an AST node."""
        refs: Set[str] = set()
        for item in ast.walk(func_node):
            if isinstance(item, ast.Call):
                if isinstance(item.func, ast.Name):
                    call_id = item.func.id
                    if call_id in import_map.symbols:
                        orig_name = import_map.symbols[call_id][1]
                        for qualname in symbol_index:
                            if qualname.split(".")[-1] == orig_name:
                                refs.add(qualname)
                elif isinstance(item.func, ast.Attribute):
                    attr = item.func.attr
                    for qualname in symbol_index:
                        if qualname.split(".")[-1] == attr:
                            refs.add(qualname)
        return refs

    def _is_locally_assigned(self, func_node: ast.AST, var_name: str) -> bool:
        """Checks if var_name was locally assigned within func_node."""
        for stmt in ast.walk(func_node):
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == var_name:
                        return True
        return False
