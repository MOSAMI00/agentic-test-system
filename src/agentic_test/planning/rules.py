"""
Deterministic routing rules and helper calculations for the Planning Subsystem.
Stage 3 Section 4.4.4.2 & Stage 2 Section 4.3.1.2.
Pure Python logic enforcing INV-03 (Zero-LLM Gate).
"""

import hashlib
import json
from pathlib import Path
from typing import Collection, Dict, FrozenSet, List, Sequence, Set, Tuple

from agentic_test.core.models import (
    DiffHunk,
    SymbolContract,
    SymbolType,
    WorkflowRoute,
)

NON_CODE_EXTENSIONS: FrozenSet[str] = frozenset({
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".xml",
    ".csv",
    ".tsv",
    ".ini",
    ".cfg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".lock",
    ".gitignore",
    ".gitattributes",
})


def is_non_code_hunk(hunk: DiffHunk) -> bool:
    """Returns True if the diff hunk modifies a recognized non-code file."""
    path = hunk.file_path
    ext = path.suffix.lower()
    name = path.name.lower()
    if name.startswith("."):
        return True
    return ext in NON_CODE_EXTENSIONS and ext != ".py"


def are_all_hunks_non_code(diff_hunks: Sequence[DiffHunk]) -> bool:
    """Returns True if all diff hunks modify strictly non-code files."""
    if not diff_hunks:
        return False
    return all(is_non_code_hunk(h) for h in diff_hunks)


def symbol_sort_key(s: SymbolContract) -> Tuple[int, str, int, str]:
    """
    Deterministic structural fallback ordering sort key.
    Stage 2 Section 4.3.2.2 UC-02 Step 5 (functions first, followed by classes/methods).
    Ties broken by POSIX file path, starting line, and qualified_name.
    """
    if s.symbol_type == SymbolType.FUNCTION:
        type_priority = 0
    elif s.symbol_type == SymbolType.CLASS:
        type_priority = 1
    elif s.symbol_type == SymbolType.METHOD:
        type_priority = 2
    else:
        type_priority = 3

    return (
        type_priority,
        s.file_path.as_posix(),
        s.line_range[0],
        s.qualified_name,
    )


def order_symbols_structurally(symbols: Collection[SymbolContract]) -> Tuple[SymbolContract, ...]:
    """Sorts symbol contracts deterministically using structural fallback ordering."""
    return tuple(sorted(symbols, key=symbol_sort_key))


def compute_decision_hash(
    source_tree_hash: str,
    route: WorkflowRoute,
    rationale: str,
    target_symbols: Sequence[SymbolContract],
    existing_tests_to_run: Sequence[Path],
) -> str:
    """
    Computes deterministic SHA-256 decision hash over canonical JSON representation.
    Stage 2 [FR-06] & Stage 3 Section 4.4.4.2.
    """
    payload = {
        "existing_tests": sorted(p.as_posix() for p in existing_tests_to_run),
        "rationale": rationale,
        "route": route.value,
        "source_tree_hash": source_tree_hash.lower(),
        "target_symbols": [
            f"{s.qualified_name}:{s.file_path.as_posix()}:{s.line_range[0]}-{s.line_range[1]}"
            for s in target_symbols
        ],
    }
    canonical_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


class RoutingDecision:
    """Container for the result of deterministic precedence evaluation."""

    def __init__(
        self,
        route: WorkflowRoute,
        rationale: str,
        target_symbols: Tuple[SymbolContract, ...],
        existing_tests_to_run: Tuple[Path, ...],
    ) -> None:
        self.route = route
        self.rationale = rationale
        self.target_symbols = target_symbols
        self.existing_tests_to_run = existing_tests_to_run


def evaluate_precedence_rules(
    diff_hunks: Sequence[DiffHunk],
    affected_symbols: Sequence[SymbolContract],
    positively_associated_symbols: Set[str],
    symbol_to_test_files: Dict[str, Set[Path]],
) -> RoutingDecision:
    """
    Evaluates precedence rules in strict authoritative priority order:
    P1 -> P2 -> Decision 1 -> P3 -> P4.

    Args:
        diff_hunks: Sequence of unified diff change hunks.
        affected_symbols: Sequence of SymbolContract records overlapping diff hunks.
        positively_associated_symbols: Set of symbol qualified names with STATICALLY_ASSOCIATED evidence.
        symbol_to_test_files: Mapping from symbol qualified name to distinct test paths with positive evidence.

    Returns:
        RoutingDecision detailing route, rationale, target symbols, and existing tests to run.
    """
    # Rule P1: Clean working tree
    if len(diff_hunks) == 0:
        return RoutingDecision(
            route=WorkflowRoute.ROUTE_NO_OP,
            rationale="Clean working tree: zero modifications detected",
            target_symbols=(),
            existing_tests_to_run=(),
        )

    # Rule P2: Non-code modifications only
    if are_all_hunks_non_code(diff_hunks):
        return RoutingDecision(
            route=WorkflowRoute.ROUTE_NO_OP,
            rationale="Non-code modifications only: changes restricted to documentation or configuration files",
            target_symbols=(),
            existing_tests_to_run=(),
        )

    # Decision 1: Python modifications contain zero affected callable symbols
    if len(affected_symbols) == 0:
        return RoutingDecision(
            route=WorkflowRoute.ROUTE_NO_OP,
            rationale="Python modifications contain no affected callable symbols",
            target_symbols=(),
            existing_tests_to_run=(),
        )

    # Partition affected symbols into covered vs uncovered
    covered: List[SymbolContract] = []
    uncovered: List[SymbolContract] = []

    for sym in affected_symbols:
        if sym.qualified_name in positively_associated_symbols:
            covered.append(sym)
        else:
            uncovered.append(sym)

    # Rule P3: All affected symbols covered by existing tests
    if len(uncovered) == 0:
        # Collect distinct test paths for all covered affected symbols
        tests_to_run: Set[Path] = set()
        for sym in covered:
            tests_to_run.update(symbol_to_test_files.get(sym.qualified_name, set()))

        return RoutingDecision(
            route=WorkflowRoute.ROUTE_TO_DOCKER_EXECUTION,
            rationale=f"All {len(affected_symbols)} affected symbols possess positive static test associations: regression execution",
            target_symbols=(),
            existing_tests_to_run=tuple(sorted(tests_to_run, key=lambda p: p.as_posix())),
        )

    # Rule P4: At least one affected symbol lacks positive static test association
    # Target symbols: only the uncovered affected symbols, ordered structurally
    ordered_target_symbols = order_symbols_structurally(uncovered)

    # Regression baselines: distinct test paths for covered affected symbols
    regression_tests: Set[Path] = set()
    for sym in covered:
        regression_tests.update(symbol_to_test_files.get(sym.qualified_name, set()))

    return RoutingDecision(
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        rationale=f"Uncovered affected symbols detected: test generation required for {len(uncovered)} of {len(affected_symbols)} symbols",
        target_symbols=ordered_target_symbols,
        existing_tests_to_run=tuple(sorted(regression_tests, key=lambda p: p.as_posix())),
    )
