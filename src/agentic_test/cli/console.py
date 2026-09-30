"""
Rich terminal presentation and ANSI formatting for the Agentic Test Generation System CLI.
Adheres strictly to Stage 3 Layer 1 Presentation Layer specifications and WBS 1.6.3C.

Renders execution banners, progress summaries, candidate tables, coverage telemetry,
diagnostic classifications, and prominent APPLICATION_BUG alerts.
"""

from pathlib import Path
from typing import Any, Optional, Sequence, Union

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agentic_test.core.models import FailureCategory, FailureDiagnosis, ValidationStatus
from agentic_test.core.state import WorkflowState


def get_console(console: Optional[Console] = None) -> Console:
    """Returns provided Console instance or default stdout Console."""
    return console if console is not None else Console()


def print_run_header(
    run_id: str,
    repo_path: Union[Path, str],
    db_path: Union[Path, str],
    console: Optional[Console] = None,
) -> None:
    """Renders prominent run startup header panel."""
    c = get_console(console)
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(style="bold cyan", width=16)
    grid.add_column(style="white")

    grid.add_row("Run ID:", str(run_id))
    grid.add_row("Repository:", str(repo_path))
    grid.add_row("Database:", str(db_path))

    panel = Panel(
        grid,
        title="[bold blue]Agentic Test Generation and Maintenance System[/bold blue]",
        border_style="blue",
        padding=(1, 2),
    )
    c.print(panel)


def print_recovery_header(
    run_id: str,
    checkpoint: Any,
    console: Optional[Console] = None,
) -> None:
    """Renders checkpoint recovery notification panel."""
    c = get_console(console)
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(style="bold yellow", width=18)
    grid.add_column(style="white")

    grid.add_row("Resuming Run:", str(run_id))
    grid.add_row("Restored Step:", str(getattr(checkpoint, "step_index", "unknown")))
    grid.add_row("Last Completed Node:", str(getattr(checkpoint, "node_name", "unknown")))

    panel = Panel(
        grid,
        title="[bold yellow]Workflow Checkpoint Recovery[/bold yellow]",
        border_style="yellow",
        padding=(0, 2),
    )
    c.print(panel)


def print_workflow_summary(
    state: WorkflowState,
    console: Optional[Console] = None,
) -> None:
    """
    Renders comprehensive workflow summary including status, route,
    candidates table, coverage telemetry, and failure diagnoses.
    """
    c = get_console(console)

    # 1. Status Overview Table
    overview = Table(title="Execution Summary", border_style="cyan", show_header=True)
    overview.add_column("Property", style="bold cyan")
    overview.add_column("Value")

    status_color = "green" if state.final_status == "COMPLETED" else "red"
    overview.add_row("Final Status", f"[{status_color}]{state.final_status}[/{status_color}]")
    overview.add_row("Selected Route", state.plan.route.value if state.plan else "NONE")

    num_symbols = len(state.plan.target_symbols) if state.plan else 0
    overview.add_row("Target Symbols", str(num_symbols))
    overview.add_row("Generated Candidates", str(len(state.candidates)))
    overview.add_row("Execution Evidences", str(len(state.evidences)))
    overview.add_row("Failure Diagnoses", str(len(state.diagnoses)))

    c.print(overview)

    # 2. Candidates Table (if candidates exist)
    if state.candidates:
        cand_table = Table(title="Test Candidates & Validation Status", border_style="blue")
        cand_table.add_column("Candidate ID", style="bold")
        cand_table.add_column("Target Symbol")
        cand_table.add_column("Validation Status")
        cand_table.add_column("Exit Code", justify="right")
        cand_table.add_column("Duration", justify="right")

        # Map evidences by candidate_id
        ev_map = {ev.candidate_id: ev for ev in state.evidences if ev.candidate_id}

        for cand in state.candidates:
            ev = ev_map.get(cand.candidate_id)
            status_style = (
                "green"
                if cand.validation_status == ValidationStatus.PASSED
                else "yellow"
                if cand.validation_status == ValidationStatus.QUARANTINED
                else "red"
            )

            exit_str = str(ev.exit_code) if ev is not None else "-"
            dur_str = f"{ev.duration_sec:.2f}s" if ev is not None else "-"

            cand_table.add_row(
                cand.candidate_id,
                cand.target_symbol_name,
                f"[{status_style}]{cand.validation_status.value}[/{status_style}]",
                exit_str,
                dur_str,
            )

        c.print(cand_table)

    # 3. Baseline Evidence / Regression Info
    if state.baseline_evidence is not None:
        base_ev = state.baseline_evidence
        b_table = Table(title="Regression Baseline Telemetry", border_style="magenta")
        b_table.add_column("Exit Code", justify="right")
        b_table.add_column("Duration", justify="right")
        b_table.add_column("Line Cov", justify="right")
        b_table.add_column("Branch Cov", justify="right")

        line_str = f"{base_ev.line_coverage:.1f}%" if base_ev.line_coverage is not None else "-"
        branch_str = f"{base_ev.branch_coverage:.1f}%" if base_ev.branch_coverage is not None else "-"

        b_table.add_row(
            str(base_ev.exit_code),
            f"{base_ev.duration_sec:.2f}s",
            line_str,
            branch_str,
        )
        c.print(b_table)

    # 4. Coverage Deltas
    cov_evidences = [ev for ev in state.evidences if ev.line_coverage_delta is not None or ev.line_coverage is not None]
    if cov_evidences:
        cov_table = Table(title="Coverage Telemetry & Deltas", border_style="green")
        cov_table.add_column("Candidate ID", style="bold")
        cov_table.add_column("Line Cov", justify="right")
        cov_table.add_column("Line Delta", justify="right")
        cov_table.add_column("Branch Cov", justify="right")
        cov_table.add_column("Branch Delta", justify="right")

        for ev in cov_evidences:
            line_str = f"{ev.line_coverage:.1f}%" if ev.line_coverage is not None else "-"
            l_delta = f"{ev.line_coverage_delta:+.1f}%" if ev.line_coverage_delta is not None else "-"
            branch_str = f"{ev.branch_coverage:.1f}%" if ev.branch_coverage is not None else "-"
            b_delta = f"{ev.branch_coverage_delta:+.1f}%" if ev.branch_coverage_delta is not None else "-"

            cov_table.add_row(
                ev.candidate_id or "baseline",
                line_str,
                f"[bold green]{l_delta}[/bold green]" if l_delta.startswith("+") else l_delta,
                branch_str,
                f"[bold green]{b_delta}[/bold green]" if b_delta.startswith("+") else b_delta,
            )

        c.print(cov_table)

    # 5. Diagnoses (if any)
    if state.diagnoses:
        diag_table = Table(title="Failure Diagnoses & Triage", border_style="red")
        diag_table.add_column("Diagnosis ID", style="bold")
        diag_table.add_column("Evidence ID")
        diag_table.add_column("Category")
        diag_table.add_column("Engine")
        diag_table.add_column("Confidence", justify="right")
        diag_table.add_column("Is Bug?", justify="center")

        for diag in state.diagnoses:
            cat_color = "red" if diag.canonical_category == FailureCategory.APPLICATION_BUG else "yellow"
            bug_str = "[bold red]YES[/bold red]" if diag.is_application_bug else "no"

            diag_table.add_row(
                diag.diagnosis_id,
                diag.evidence_id,
                f"[{cat_color}]{diag.canonical_category.value}[/{cat_color}]",
                diag.triage_engine.value,
                f"{diag.confidence:.2f}",
                bug_str,
            )

        c.print(diag_table)

    # 6. Prominent Warning if APPLICATION_BUG was detected
    print_application_bug_warning(state.diagnoses, console=c)

    # 7. Error message (if execution halted with failure)
    if state.error_message:
        print_error(state.error_message, console=c)


def print_application_bug_warning(
    diagnoses: Sequence[FailureDiagnosis],
    console: Optional[Console] = None,
) -> None:
    """Renders high-visibility warning banner when APPLICATION_BUG diagnoses are present."""
    app_bugs = [
        d for d in diagnoses
        if d.canonical_category == FailureCategory.APPLICATION_BUG or d.is_application_bug
    ]
    if not app_bugs:
        return

    c = get_console(console)
    lines: list[Union[str, Text]] = [
        Text("CRITICAL: APPLICATION BUG DETECTED", style="bold white on red"),
        "",
        Text(
            f"The triage subsystem classified {len(app_bugs)} failure(s) as genuine application source defects.",
            style="bold yellow",
        ),
        Text("Automatic test mutation is prohibited. Human investigation is required.", style="white"),
        "",
    ]
    for bug in app_bugs:
        lines.append(Text(f"• Diagnosis '{bug.diagnosis_id}' ({bug.evidence_id}): {bug.explanation}", style="red"))

    panel = Panel(
        Text("\n").join([line if isinstance(line, Text) else Text(line) for line in lines]),
        title="[bold red]SAFETY ALERT: APPLICATION DEFECT[/bold red]",
        border_style="red",
        padding=(1, 2),
    )
    c.print(panel)


def print_error(
    message: str,
    console: Optional[Console] = None,
) -> None:
    """Renders formatted error panel."""
    c = get_console(console)
    panel = Panel(
        Text(message, style="bold red"),
        title="[bold red]Execution Error[/bold red]",
        border_style="red",
        padding=(0, 2),
    )
    c.print(panel)
