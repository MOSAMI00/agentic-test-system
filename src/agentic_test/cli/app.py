"""
Typer command-line interface application for the Agentic Test Generation System.
Adheres strictly to Stage 3 Layer 1 Presentation Layer specifications and WBS 1.6.3C.

Provides `agentic-test run` and `agentic-test resume` commands with Rich terminal formatting,
checkpoint recovery, and deterministic exit-code contracts.
"""

from pathlib import Path
from typing import Optional
import uuid

import typer

from agentic_test.cli.console import (
    print_error,
    print_recovery_header,
    print_run_header,
    print_workflow_summary,
)
from agentic_test.cli.service_factory import create_workflow_engine
from agentic_test.core.state import WorkflowState
from agentic_test.workflow.recovery import RecoveryError, resume_run

app = typer.Typer(
    name="agentic-test",
    help="Agentic Test Generation and Maintenance System CLI",
    add_completion=False,
    no_args_is_help=True,
)


def validate_repo_path(repo_path: Path) -> Path:
    """
    Validates that the target repository path exists, is a directory,
    and is an initialized Git repository (FR-01, UC-01 Extension 2a).
    """
    if not repo_path.exists():
        print_error(f"Target repository path does not exist: {repo_path}")
        raise typer.Exit(code=2)
    if not repo_path.is_dir():
        print_error(f"Target repository path is not a directory: {repo_path}")
        raise typer.Exit(code=2)

    try:
        from agentic_test.analysis.git_service import GitService
        from agentic_test.core.models import RepositoryValidationError

        git_svc = GitService(repo_path)
        git_svc.validate_repository(repo_path)
    except Exception as exc:
        print_error(f"Invalid Git repository path: {exc}")
        raise typer.Exit(code=2)

    return repo_path.resolve()


def _execute_new_run(repo_path: Path, db_path: Path) -> None:
    """Executes a new end-to-end test generation run."""
    resolved_repo = validate_repo_path(repo_path)
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    print_run_header(run_id=run_id, repo_path=resolved_repo, db_path=db_path)

    engine = create_workflow_engine(db_path=db_path, repo_path=resolved_repo)
    initial_state = WorkflowState(run_id=run_id, repo_path=resolved_repo)

    try:
        final_state = engine.run(initial_state)
    except Exception as exc:
        print_error(f"Workflow execution failed: {exc}")
        raise typer.Exit(code=1)

    print_workflow_summary(final_state)

    if final_state.final_status != "COMPLETED":
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


def _execute_resume(run_id: str, db_path: Path) -> None:
    """Resumes an interrupted workflow run from its latest SQLite checkpoint."""
    if not run_id or not run_id.strip():
        print_error("Invalid run_id: run_id cannot be empty.")
        raise typer.Exit(code=2)

    clean_run_id = run_id.strip()
    engine = create_workflow_engine(db_path=db_path)

    checkpoint = engine.repository.get_latest_checkpoint(clean_run_id)
    if checkpoint is not None:
        print_recovery_header(clean_run_id, checkpoint)

    try:
        final_state = resume_run(run_id=clean_run_id, engine=engine)
    except RecoveryError as exc:
        print_error(str(exc))
        raise typer.Exit(code=2)
    except Exception as exc:
        print_error(f"Workflow resumption failed: {exc}")
        raise typer.Exit(code=1)

    print_workflow_summary(final_state)

    if final_state.final_status != "COMPLETED":
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command(name="run", help="Execute test generation pipeline or resume an existing run.")
def run_command(
    repo: Optional[Path] = typer.Option(
        None,
        "--repo",
        "-r",
        help="Path to the target Git repository root.",
    ),
    resume: Optional[str] = typer.Option(
        None,
        "--resume",
        help="Run ID of an interrupted execution run to resume.",
    ),
    db: Path = typer.Option(
        Path(".agentic_test.db"),
        "--db",
        help="Path to the SQLite database persistence file.",
    ),
) -> None:
    """Executes a new pipeline run or resumes an interrupted run."""
    if resume is not None:
        _execute_resume(run_id=resume, db_path=db)
    elif repo is not None:
        _execute_new_run(repo_path=repo, db_path=db)
    else:
        print_error("Missing required parameter: Either --repo <path> or --resume <run_id> must be specified.")
        raise typer.Exit(code=2)


@app.command(name="resume", help="Resume an interrupted workflow run from its latest checkpoint.")
def resume_command(
    run_id: str = typer.Argument(
        ...,
        help="Run ID of the execution run to resume.",
    ),
    db: Path = typer.Option(
        Path(".agentic_test.db"),
        "--db",
        help="Path to the SQLite database persistence file.",
    ),
) -> None:
    """Subcommand to resume an interrupted workflow run by run_id."""
    _execute_resume(run_id=run_id, db_path=db)


def main() -> None:
    """Entry point for console script executions."""
    app()


if __name__ == "__main__":
    main()
