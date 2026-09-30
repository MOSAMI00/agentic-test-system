"""
Deterministic conditional edge routers for the Agentic Test Generation System.
Adheres strictly to Stage 3 Section 4.4.3 (Listing 4.3) and WBS 1.6.3A.

All routers are pure functions taking an immutable WorkflowState and returning
the exact name of the destination node to execute next.
"""

from agentic_test.core.models import ValidationStatus, WorkflowRoute
from agentic_test.core.state import WorkflowState


def route_after_planning(state: WorkflowState) -> str:
    """
    Evaluates execution plan route to determine next workflow node.

    Transitions:
    - ROUTE_NO_OP -> report_node
    - ROUTE_TO_DOCKER_EXECUTION -> execute_sandbox_node
    - ROUTE_TO_TEST_GENERATION -> generate_tests_node

    :param state: Current immutable WorkflowState containing plan.
    :return: Name of target node.
    :raises ValueError: If state.plan is missing or route is unrecognized.
    """
    if state.plan is None:
        raise ValueError("ExecutionPlan is required for route_after_planning")

    if state.plan.route == WorkflowRoute.ROUTE_NO_OP:
        return "report_node"
    elif state.plan.route == WorkflowRoute.ROUTE_TO_DOCKER_EXECUTION:
        return "execute_sandbox_node"
    elif state.plan.route == WorkflowRoute.ROUTE_TO_TEST_GENERATION:
        return "generate_tests_node"
    else:
        raise ValueError(f"Unrecognized WorkflowRoute: {state.plan.route}")


def route_after_validation(state: WorkflowState) -> str:
    """
    Evaluates multi-gate validation outcomes across test candidates.

    Transitions:
    - At least one candidate has validation_status == PASSED -> execute_sandbox_node
    - Zero candidates passed (all rejected or quarantined) -> report_node

    :param state: Current immutable WorkflowState containing candidates.
    :return: Name of target node.
    """
    if any(c.validation_status == ValidationStatus.PASSED for c in state.candidates):
        return "execute_sandbox_node"
    return "report_node"


def route_after_execution(state: WorkflowState) -> str:
    """
    Evaluates sandbox execution outcomes across baseline and candidate evidences.

    Transitions:
    - Baseline evidence or any candidate evidence has exit_code != 0 -> diagnose_failure_node
    - All evidences succeeded (exit_code == 0) -> report_node

    :param state: Current immutable WorkflowState containing evidences.
    :return: Name of target node.
    """
    if state.baseline_evidence is not None and state.baseline_evidence.exit_code != 0:
        return "diagnose_failure_node"
    if any(ev.exit_code != 0 for ev in state.evidences):
        return "diagnose_failure_node"
    return "report_node"
