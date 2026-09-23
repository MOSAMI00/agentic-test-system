"""
Unit and integration tests for ExecutionPlanner and routing precedence rules.
Stage 1 Table 4.1, Stage 2 Section 4.3.1.2, Stage 3 Section 4.4.4.2.
Verifies FR-05, FR-06, NFR-02, NFR-06, INV-01, INV-02, and INV-03.
"""

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Tuple
import pytest

from agentic_test.analysis.discovery import (
    AssociationClassification,
    EvidenceKind,
    StaticAssociation,
    TestDiscovery,
)
from agentic_test.core.models import (
    ChangeType,
    DiffHunk,
    ExecutionPlan,
    RepositorySnapshot,
    SymbolContract,
    SymbolType,
    WorkflowRoute,
)
from agentic_test.planning.planner import ExecutionPlanner
from agentic_test.planning.rules import (
    compute_decision_hash,
    order_symbols_structurally,
)


class MockTestDiscovery(TestDiscovery):
    """Mock TestDiscovery for isolated deterministic rule testing."""

    def __init__(self, associations_to_return: Sequence[StaticAssociation]) -> None:
        super().__init__()
        self._associations = tuple(associations_to_return)

    def analyze_associations(
        self,
        test_file: Path,
        target_symbols: Sequence[SymbolContract],
    ) -> Tuple[StaticAssociation, ...]:
        # Return only associations matching the requested test_file (if specified)
        return tuple(
            a for a in self._associations
            if a.test_file_path.name == test_file.name or a.test_file_path == test_file
        )


def _make_diff_hunk(
    file_path: Path,
    old_start: int = 1,
    old_lines: int = 1,
    new_start: int = 1,
    new_lines: int = 1,
    change_type: ChangeType = ChangeType.MODIFIED,
    content: str = "@@ -1,1 +1,1 @@\n+sample change\n",
) -> DiffHunk:
    """Helper to create a validated DiffHunk."""
    return DiffHunk(
        file_path=file_path,
        old_start=old_start,
        old_lines=old_lines,
        new_start=new_start,
        new_lines=new_lines,
        content=content,
        change_type=change_type,
    )


def _make_snapshot(
    repo_path: Path,
    diff_hunks: Tuple[DiffHunk, ...] = (),
    affected_symbols: Tuple[SymbolContract, ...] = (),
    existing_test_files: Tuple[Path, ...] = (),
    source_tree_hash: str = "a" * 64,
) -> RepositorySnapshot:
    """Helper to create a validated RepositorySnapshot."""
    return RepositorySnapshot(
        repo_path=repo_path,
        current_commit="1" * 40,
        base_commit="0" * 40,
        branch_name="main",
        tracked_files=(),
        existing_test_files=existing_test_files,
        source_tree_hash=source_tree_hash,
        is_valid=True,
        created_at=datetime.now(timezone.utc),
        diff_hunks=diff_hunks,
        affected_symbols=affected_symbols,
        syntax_errors=(),
    )


# ---------------------------------------------------------------------------
# Rule P1: Clean Working Tree
# ---------------------------------------------------------------------------


def test_rule_p1_clean_working_tree_selects_route_no_op(tmp_path: Path) -> None:
    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(),
        affected_symbols=(),
        existing_test_files=(Path("tests/test_foo.py"),),
    )
    planner = ExecutionPlanner()
    plan = planner.plan(snapshot)

    assert plan.route == WorkflowRoute.ROUTE_NO_OP
    assert "Clean working tree" in plan.rationale
    assert plan.target_symbols == ()
    assert plan.existing_tests_to_run == ()
    assert len(plan.decision_hash) == 64


# ---------------------------------------------------------------------------
# Rule P2: Non-Code Modifications Only
# ---------------------------------------------------------------------------


def test_rule_p2_non_code_modifications_selects_route_no_op(tmp_path: Path) -> None:
    diff_md = _make_diff_hunk(file_path=Path("README.md"))
    diff_yaml = _make_diff_hunk(file_path=Path("config.yaml"))

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff_md, diff_yaml),
        affected_symbols=(),
        existing_test_files=(Path("tests/test_foo.py"),),
    )
    planner = ExecutionPlanner()
    plan = planner.plan(snapshot)

    assert plan.route == WorkflowRoute.ROUTE_NO_OP
    assert "Non-code modifications only" in plan.rationale
    assert plan.target_symbols == ()
    assert plan.existing_tests_to_run == ()


# ---------------------------------------------------------------------------
# Decision 1: Python Modifications with Zero Affected Callable Symbols
# ---------------------------------------------------------------------------


def test_decision_1_python_modifications_zero_affected_symbols_selects_route_no_op(
    tmp_path: Path,
) -> None:
    # A diff touching a Python file (e.g. comment/whitespace edit), but zero affected symbols
    diff_py = _make_diff_hunk(file_path=Path("src/calc.py"))
    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff_py,),
        affected_symbols=(),  # Zero callable symbols affected
        existing_test_files=(Path("tests/test_calc.py"),),
    )
    planner = ExecutionPlanner()
    plan = planner.plan(snapshot)

    assert plan.route == WorkflowRoute.ROUTE_NO_OP
    assert plan.rationale == "Python modifications contain no affected callable symbols"
    assert plan.target_symbols == ()
    assert plan.existing_tests_to_run == ()


# ---------------------------------------------------------------------------
# Rule P3: All Affected Symbols Covered by Existing Tests
# ---------------------------------------------------------------------------


def test_rule_p3_all_affected_symbols_covered_selects_docker_execution(tmp_path: Path) -> None:
    sym1 = SymbolContract(
        qualified_name="calc.add",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/calc.py"),
        line_range=(1, 5),
        signature="def add(a, b)",
        is_affected=True,
    )
    sym2 = SymbolContract(
        qualified_name="calc.sub",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/calc.py"),
        line_range=(6, 10),
        signature="def sub(a, b)",
        is_affected=True,
    )
    diff = _make_diff_hunk(file_path=Path("src/calc.py"), old_lines=10, new_lines=10)

    test_file = Path("tests/test_calc.py")
    assoc1 = StaticAssociation(
        target_symbol="calc.add",
        test_file_path=test_file,
        classification=AssociationClassification.STATICALLY_ASSOCIATED,
        evidence_kind=EvidenceKind.DIRECT_CALL,
        reason="Direct call",
    )
    assoc2 = StaticAssociation(
        target_symbol="calc.sub",
        test_file_path=test_file,
        classification=AssociationClassification.STATICALLY_ASSOCIATED,
        evidence_kind=EvidenceKind.DIRECT_CALL,
        reason="Direct call",
    )

    mock_discovery = MockTestDiscovery([assoc1, assoc2])
    planner = ExecutionPlanner(test_discovery=mock_discovery)

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff,),
        affected_symbols=(sym1, sym2),
        existing_test_files=(test_file,),
    )
    plan = planner.plan(snapshot)

    assert plan.route == WorkflowRoute.ROUTE_TO_DOCKER_EXECUTION
    assert "All 2 affected symbols possess positive static test associations" in plan.rationale
    assert plan.target_symbols == ()
    assert plan.existing_tests_to_run == (test_file,)


# ---------------------------------------------------------------------------
# Rule P4: Uncovered Affected Symbols Detected
# ---------------------------------------------------------------------------


def test_rule_p4_all_uncovered_symbols_selects_test_generation(tmp_path: Path) -> None:
    sym = SymbolContract(
        qualified_name="core.process",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/core.py"),
        line_range=(10, 20),
        signature="def process()",
        is_affected=True,
    )
    diff = _make_diff_hunk(file_path=Path("src/core.py"), old_start=10, new_start=10)

    # Empty mock discovery: no associations exist
    mock_discovery = MockTestDiscovery([])
    planner = ExecutionPlanner(test_discovery=mock_discovery)

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff,),
        affected_symbols=(sym,),
        existing_test_files=(Path("tests/test_other.py"),),
    )
    plan = planner.plan(snapshot)

    assert plan.route == WorkflowRoute.ROUTE_TO_TEST_GENERATION
    assert "Uncovered affected symbols detected" in plan.rationale
    assert plan.target_symbols == (sym,)
    assert plan.existing_tests_to_run == ()


def test_rule_p4_mixed_covered_and_uncovered_symbols_selects_test_generation(
    tmp_path: Path,
) -> None:
    sym_covered = SymbolContract(
        qualified_name="calc.add",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/calc.py"),
        line_range=(1, 5),
        signature="def add(a, b)",
        is_affected=True,
    )
    sym_uncovered = SymbolContract(
        qualified_name="calc.mul",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/calc.py"),
        line_range=(10, 15),
        signature="def mul(a, b)",
        is_affected=True,
    )
    diff = _make_diff_hunk(file_path=Path("src/calc.py"), old_lines=15, new_lines=15)

    test_file_add = Path("tests/test_add.py")
    assoc_add = StaticAssociation(
        target_symbol="calc.add",
        test_file_path=test_file_add,
        classification=AssociationClassification.STATICALLY_ASSOCIATED,
        evidence_kind=EvidenceKind.DIRECT_CALL,
        reason="Direct call",
    )

    mock_discovery = MockTestDiscovery([assoc_add])
    planner = ExecutionPlanner(test_discovery=mock_discovery)

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff,),
        affected_symbols=(sym_covered, sym_uncovered),
        existing_test_files=(test_file_add,),
    )
    plan = planner.plan(snapshot)

    assert plan.route == WorkflowRoute.ROUTE_TO_TEST_GENERATION
    # target_symbols must contain ONLY the uncovered symbol
    assert plan.target_symbols == (sym_uncovered,)
    # existing_tests_to_run must contain the covered symbol's test as a regression baseline
    assert plan.existing_tests_to_run == (test_file_add,)


# ---------------------------------------------------------------------------
# Safety Rules: UNKNOWN and UNSUPPORTED are NEVER Positive Evidence
# ---------------------------------------------------------------------------


def test_safety_unknown_association_treated_as_uncovered(tmp_path: Path) -> None:
    sym = SymbolContract(
        qualified_name="db.query",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/db.py"),
        line_range=(1, 10),
        signature="def query()",
        is_affected=True,
    )
    diff = _make_diff_hunk(file_path=Path("src/db.py"))

    test_file = Path("tests/test_db.py")
    assoc_unknown = StaticAssociation(
        target_symbol="db.query",
        test_file_path=test_file,
        classification=AssociationClassification.UNKNOWN,
        evidence_kind=EvidenceKind.EXTERNAL_FIXTURE,
        reason="Ambiguous fixture parameter",
    )

    mock_discovery = MockTestDiscovery([assoc_unknown])
    planner = ExecutionPlanner(test_discovery=mock_discovery)

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff,),
        affected_symbols=(sym,),
        existing_test_files=(test_file,),
    )
    plan = planner.plan(snapshot)

    # UNKNOWN must NOT satisfy Rule P3; must fall through to P4
    assert plan.route == WorkflowRoute.ROUTE_TO_TEST_GENERATION
    assert plan.target_symbols == (sym,)
    assert plan.existing_tests_to_run == ()


def test_safety_unsupported_mock_treated_as_uncovered(tmp_path: Path) -> None:
    sym = SymbolContract(
        qualified_name="net.fetch",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/net.py"),
        line_range=(1, 10),
        signature="def fetch()",
        is_affected=True,
    )
    diff = _make_diff_hunk(file_path=Path("src/net.py"))

    test_file = Path("tests/test_net.py")
    assoc_unsupported = StaticAssociation(
        target_symbol="net.fetch",
        test_file_path=test_file,
        classification=AssociationClassification.UNSUPPORTED,
        evidence_kind=EvidenceKind.EXPLICIT_MOCK_DISQUALIFIED,
        reason="Mock detected via patch",
    )

    mock_discovery = MockTestDiscovery([assoc_unsupported])
    planner = ExecutionPlanner(test_discovery=mock_discovery)

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff,),
        affected_symbols=(sym,),
        existing_test_files=(test_file,),
    )
    plan = planner.plan(snapshot)

    # UNSUPPORTED must NOT satisfy Rule P3; must fall through to P4
    assert plan.route == WorkflowRoute.ROUTE_TO_TEST_GENERATION
    assert plan.target_symbols == (sym,)
    assert plan.existing_tests_to_run == ()


# ---------------------------------------------------------------------------
# Decision 2 Option A: Targeted Regression Existing Tests
# ---------------------------------------------------------------------------


def test_decision_2_targeted_regression_excludes_unrelated_tests(tmp_path: Path) -> None:
    sym = SymbolContract(
        qualified_name="auth.login",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/auth.py"),
        line_range=(1, 10),
        signature="def login()",
        is_affected=True,
    )
    diff = _make_diff_hunk(file_path=Path("src/auth.py"))

    test_auth = Path("tests/test_auth.py")
    test_unrelated = Path("tests/test_unrelated.py")

    assoc = StaticAssociation(
        target_symbol="auth.login",
        test_file_path=test_auth,
        classification=AssociationClassification.STATICALLY_ASSOCIATED,
        evidence_kind=EvidenceKind.DIRECT_CALL,
        reason="Direct call",
    )

    mock_discovery = MockTestDiscovery([assoc])
    planner = ExecutionPlanner(test_discovery=mock_discovery)

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff,),
        affected_symbols=(sym,),
        existing_test_files=(test_auth, test_unrelated),
    )
    plan = planner.plan(snapshot)

    assert plan.route == WorkflowRoute.ROUTE_TO_DOCKER_EXECUTION
    # Targeted regression: contains ONLY test_auth, NOT test_unrelated
    assert plan.existing_tests_to_run == (test_auth,)


# ---------------------------------------------------------------------------
# Deterministic Structural Fallback Symbol Ordering
# ---------------------------------------------------------------------------


def test_deterministic_structural_ordering() -> None:
    sym_method = SymbolContract(
        qualified_name="srv.Service.start",
        symbol_type=SymbolType.METHOD,
        file_path=Path("src/srv.py"),
        line_range=(20, 30),
        signature="def start(self)",
    )
    sym_class = SymbolContract(
        qualified_name="srv.Service",
        symbol_type=SymbolType.CLASS,
        file_path=Path("src/srv.py"),
        line_range=(10, 40),
        signature="class Service",
    )
    sym_func2 = SymbolContract(
        qualified_name="srv.zeta_func",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/srv.py"),
        line_range=(50, 60),
        signature="def zeta_func()",
    )
    sym_func1 = SymbolContract(
        qualified_name="srv.alpha_func",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/srv.py"),
        line_range=(5, 9),
        signature="def alpha_func()",
    )

    # Intentionally scrambled input order
    scrambled = [sym_method, sym_class, sym_func2, sym_func1]
    ordered = order_symbols_structurally(scrambled)

    # Functions (priority 0) first: sorted by line range / name
    assert ordered[0].qualified_name == "srv.alpha_func"
    assert ordered[1].qualified_name == "srv.zeta_func"
    # Classes (priority 1) next
    assert ordered[2].qualified_name == "srv.Service"
    # Methods (priority 2) last
    assert ordered[3].qualified_name == "srv.Service.start"


# ---------------------------------------------------------------------------
# Decision Hash Determinism, Path Normalization, and Variance
# ---------------------------------------------------------------------------


def test_decision_hash_determinism_and_posix_normalization() -> None:
    sym = SymbolContract(
        qualified_name="calc.add",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/calc.py"),
        line_range=(1, 5),
        signature="def add(a, b)",
    )
    h1 = compute_decision_hash(
        source_tree_hash="abcdef" * 10 + "1234",
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        rationale="Test generation required",
        target_symbols=[sym],
        existing_tests_to_run=[Path("tests/test_a.py"), Path("tests/test_b.py")],
    )

    # Calling again with identical logical inputs and Windows backslashes produces identical hash
    h2 = compute_decision_hash(
        source_tree_hash="ABCDEF" * 10 + "1234",  # Uppercase source tree hash normalized to lowercase
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        rationale="Test generation required",
        target_symbols=[sym],
        existing_tests_to_run=[Path("tests\\test_b.py"), Path("tests\\test_a.py")],  # Different order and separators
    )

    assert h1 == h2
    assert len(h1) == 64


def test_decision_hash_variance_on_input_changes() -> None:
    sym = SymbolContract(
        qualified_name="calc.add",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/calc.py"),
        line_range=(1, 5),
        signature="def add(a, b)",
    )
    h_base = compute_decision_hash(
        source_tree_hash="0" * 64,
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        rationale="Rationale A",
        target_symbols=[sym],
        existing_tests_to_run=[Path("tests/test_a.py")],
    )

    # Different route
    h_diff_route = compute_decision_hash(
        source_tree_hash="0" * 64,
        route=WorkflowRoute.ROUTE_NO_OP,
        rationale="Rationale A",
        target_symbols=[sym],
        existing_tests_to_run=[Path("tests/test_a.py")],
    )
    assert h_base != h_diff_route

    # Different target symbols
    h_diff_sym = compute_decision_hash(
        source_tree_hash="0" * 64,
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        rationale="Rationale A",
        target_symbols=[],
        existing_tests_to_run=[Path("tests/test_a.py")],
    )
    assert h_base != h_diff_sym


# ---------------------------------------------------------------------------
# Invariant INV-03: Zero LLM or Network Imports
# ---------------------------------------------------------------------------


def test_zero_llm_imports_enforces_inv_03() -> None:
    """Verifies statically that agentic_test.planning imports zero LLM or network modules."""
    planning_dir = Path(__file__).parent.parent / "src" / "agentic_test" / "planning"
    disallowed_modules = {
        "openai",
        "anthropic",
        "litellm",
        "google",
        "requests",
        "urllib",
        "http",
        "socket",
        "langchain",
        "langgraph",
    }

    for py_file in planning_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    assert root_mod not in disallowed_modules, (
                        f"Disallowed import '{alias.name}' found in {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    assert root_mod not in disallowed_modules, (
                        f"Disallowed import from '{node.module}' found in {py_file}"
                    )


# ---------------------------------------------------------------------------
# Invariant INV-01, INV-02: Zero Target-Code Execution
# ---------------------------------------------------------------------------


def test_zero_target_code_execution_during_planning(tmp_path: Path) -> None:
    """
    Verifies that ExecutionPlanner.plan() operates purely statically on
    structural metadata and does NOT execute target code on the host.
    """
    # Create test file containing explosive code
    explosive_test = tmp_path / "test_explosive.py"
    explosive_test.write_text(
        "raise RuntimeError('Target application code executed on host during planning!')\n"
        "from safe_mod import safe_func\n"
        "def test_safe():\n"
        "    safe_func()\n",
        encoding="utf-8",
    )

    sym = SymbolContract(
        qualified_name="safe_mod.safe_func",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("safe_mod.py"),
        line_range=(1, 5),
        signature="def safe_func()",
        is_affected=True,
    )
    diff = _make_diff_hunk(
        file_path=Path("safe_mod.py"),
        old_start=1,
        old_lines=5,
        new_start=1,
        new_lines=5,
        change_type=ChangeType.MODIFIED,
    )

    # Use real TestDiscovery (pure ast.parse)
    real_discovery = TestDiscovery()
    planner = ExecutionPlanner(test_discovery=real_discovery)

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(diff,),
        affected_symbols=(sym,),
        existing_test_files=(explosive_test,),
    )

    # Pure static planning must NOT execute top-level raise RuntimeError
    plan = planner.plan(snapshot)
    assert plan.route == WorkflowRoute.ROUTE_TO_DOCKER_EXECUTION
    assert plan.existing_tests_to_run == (explosive_test,)


# ---------------------------------------------------------------------------
# Immutability and State Invariants (NFR-06)
# ---------------------------------------------------------------------------


def test_execution_plan_immutability(tmp_path: Path) -> None:
    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(),
        affected_symbols=(),
    )
    planner = ExecutionPlanner()
    plan = planner.plan(snapshot)

    # ExecutionPlan is frozen
    with pytest.raises(Exception):
        setattr(plan, "route", WorkflowRoute.ROUTE_TO_TEST_GENERATION)

    # Collections are immutable tuples
    assert isinstance(plan.target_symbols, tuple)
    assert isinstance(plan.existing_tests_to_run, tuple)


def test_rule_p2_dotfile_modifications_and_symbol_type_module(tmp_path: Path) -> None:
    from agentic_test.planning.rules import are_all_hunks_non_code, is_non_code_hunk

    assert are_all_hunks_non_code([]) is False

    hunk_dotfile = _make_diff_hunk(file_path=Path(".gitignore"))
    assert is_non_code_hunk(hunk_dotfile) is True

    snapshot = _make_snapshot(
        repo_path=tmp_path,
        diff_hunks=(hunk_dotfile,),
        affected_symbols=(),
    )
    planner = ExecutionPlanner()
    plan = planner.plan(snapshot)
    assert plan.route == WorkflowRoute.ROUTE_NO_OP

    # SymbolType.MODULE ordering
    sym_mod = SymbolContract(
        qualified_name="srv",
        symbol_type=SymbolType.MODULE,
        file_path=Path("src/srv.py"),
        line_range=(1, 100),
        signature="module srv",
    )
    sym_fn = SymbolContract(
        qualified_name="srv.func",
        symbol_type=SymbolType.FUNCTION,
        file_path=Path("src/srv.py"),
        line_range=(10, 20),
        signature="def func()",
    )
    ordered = order_symbols_structurally([sym_mod, sym_fn])
    assert ordered[0].qualified_name == "srv.func"
    assert ordered[1].qualified_name == "srv"

