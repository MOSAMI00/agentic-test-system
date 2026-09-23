"""
Deterministic ExecutionPlanner service.
Stage 3 Section 4.4.4.2 & Stage 2 Section 4.3.1.2.
Pure Python domain business service enforcing INV-03 (Zero-LLM Gate).
"""

from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional, Set
import uuid

from agentic_test.analysis.discovery import (
    AssociationClassification,
    TestDiscovery,
)
from agentic_test.core.models import (
    ExecutionPlan,
    RepositorySnapshot,
)
from agentic_test.planning.rules import (
    compute_decision_hash,
    evaluate_precedence_rules,
)


class ExecutionPlanner:
    """
    Deterministic planning and decision routing gate.
    Stage 3 Section 4.4.4.2 & Stage 2 Section 4.3.1.2.
    Pure Python domain service enforcing INV-03 (Zero-LLM Gate).
    """

    def __init__(self, test_discovery: Optional[TestDiscovery] = None) -> None:
        """
        Initializes ExecutionPlanner with optional TestDiscovery dependency.
        Enables constructor dependency injection for testing while preserving
        the public plan(snapshot) method signature.
        """
        self._test_discovery = test_discovery or TestDiscovery()

    def plan(self, snapshot: RepositorySnapshot) -> ExecutionPlan:
        """
        Synthesizes an immutable ExecutionPlan from a RepositorySnapshot.
        Stage 3 Section 4.4.4.2 Part 2 public contract.

        Args:
            snapshot: Immutable point-in-time repository snapshot.

        Returns:
            Frozen ExecutionPlan containing selected route, target symbols,
            existing regression tests to run, rationale, and decision hash.
        """
        positively_associated_symbols: Set[str] = set()
        symbol_to_test_files: Dict[str, Set[Path]] = defaultdict(set)

        # Collect static associations only when affected symbols and test files exist
        if snapshot.affected_symbols and snapshot.existing_test_files:
            for test_file in snapshot.existing_test_files:
                resolved_test_path = (
                    test_file
                    if test_file.is_absolute()
                    else (snapshot.repo_path / test_file)
                )

                associations = self._test_discovery.analyze_associations(
                    resolved_test_path,
                    snapshot.affected_symbols,
                )

                for assoc in associations:
                    if assoc.classification == AssociationClassification.STATICALLY_ASSOCIATED:
                        positively_associated_symbols.add(assoc.target_symbol)
                        symbol_to_test_files[assoc.target_symbol].add(test_file)

        # Evaluate rules P1 -> P2 -> Decision 1 -> P3 -> P4
        decision = evaluate_precedence_rules(
            diff_hunks=snapshot.diff_hunks,
            affected_symbols=snapshot.affected_symbols,
            positively_associated_symbols=positively_associated_symbols,
            symbol_to_test_files=symbol_to_test_files,
        )

        # Calculate cryptographic decision hash
        decision_hash = compute_decision_hash(
            source_tree_hash=snapshot.source_tree_hash,
            route=decision.route,
            rationale=decision.rationale,
            target_symbols=decision.target_symbols,
            existing_tests_to_run=decision.existing_tests_to_run,
        )

        return ExecutionPlan(
            plan_id=str(uuid.uuid4()),
            route=decision.route,
            target_symbols=decision.target_symbols,
            existing_tests_to_run=decision.existing_tests_to_run,
            rationale=decision.rationale,
            decision_hash=decision_hash,
        )
