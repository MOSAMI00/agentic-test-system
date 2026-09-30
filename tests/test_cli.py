"""
Unit and integration tests for the Typer CLI and Rich terminal presentation.
Adheres strictly to WBS 1.6.3C.
"""

from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock
import git
import pytest
from typer.testing import CliRunner

from agentic_test.cli.app import app
from agentic_test.cli.service_factory import set_engine_factory
from agentic_test.core.models import (
    ExecutionEvidence,
    ExecutionPlan,
    FailureCategory,
    FailureDiagnosis,
    RepositorySnapshot,
    TestCandidate,
    TriageEngine,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.core.state import WorkflowState
from agentic_test.storage.database import init_database
from agentic_test.storage.events import SQLiteEventStore
from agentic_test.storage.repository import SQLiteRepository
from agentic_test.workflow.engine import WorkflowEngine


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "target_repo"
    repo_dir.mkdir()
    repo = git.Repo.init(str(repo_dir))
    (repo_dir / "app.py").write_text("def hello(): return 'world'\n", encoding="utf-8")
    repo.index.add(["app.py"])
    repo.index.commit("Initial commit")
    return repo_dir


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_cli.db"
    init_database(db_file)
    return db_file


@pytest.fixture(autouse=True)
def reset_engine_factory() -> Generator[None, None, None]:
    set_engine_factory(None)
    yield
    set_engine_factory(None)


# ============================================================================
# 1. CLI Help and Argument Validation Tests
# ============================================================================

def test_cli_help(runner: CliRunner) -> None:
    """Verifies top-level help text renders and displays subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "resume" in result.stdout


def test_cli_run_help(runner: CliRunner) -> None:
    """Verifies `run --help` lists required options."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.stdout
    assert "--resume" in result.stdout
    assert "--db" in result.stdout


def test_cli_run_missing_args(runner: CliRunner) -> None:
    """Verifies that invoking run without --repo or --resume exits with code 2."""
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert "Either --repo <path> or --resume <run_id>" in result.stdout


def test_cli_run_nonexistent_repo(runner: CliRunner, tmp_path: Path) -> None:
    """Verifies that a non-existent repo path returns exit code 2."""
    non_existent = tmp_path / "does_not_exist"
    result = runner.invoke(app, ["run", "--repo", str(non_existent)])
    assert result.exit_code == 2
    assert "Target repository path does not exist" in result.stdout


def test_cli_run_file_instead_of_dir(runner: CliRunner, tmp_path: Path) -> None:
    """Verifies that pointing --repo to a file instead of directory returns exit code 2."""
    file_path = tmp_path / "regular_file.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    result = runner.invoke(app, ["run", "--repo", str(file_path)])
    assert result.exit_code == 2
    assert "Target repository path is not a directory" in result.stdout


def test_cli_run_not_a_git_repo(runner: CliRunner, tmp_path: Path) -> None:
    """Verifies that an uninitialized directory returns exit code 2."""
    plain_dir = tmp_path / "plain_dir"
    plain_dir.mkdir()
    result = runner.invoke(app, ["run", "--repo", str(plain_dir)])
    assert result.exit_code == 2
    assert "Invalid Git repository path" in result.stdout


# ============================================================================
# 2. End-to-End Pipeline Execution Tests
# ============================================================================

def test_cli_run_success_exit_0(runner: CliRunner, git_repo: Path, db_path: Path) -> None:
    """Verifies successful run completes with exit code 0 and renders Rich summary."""
    plan = ExecutionPlan(
        plan_id="plan-test",
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        target_symbols=(),
        existing_tests_to_run=(),
        rationale="Nominal synthesis",
        decision_hash="hash-123",
    )
    cand = TestCandidate(
        candidate_id="cand-1",
        run_id="run-cli",
        target_symbol_name="app.hello",
        test_file_path=Path("tests/test_app.py"),
        candidate_code="def test_hello(): assert hello() == 'world'\n",
        validation_status=ValidationStatus.PASSED,
    )
    ev = ExecutionEvidence(
        evidence_id="ev-1",
        run_id="run-cli",
        candidate_id="cand-1",
        exit_code=0,
        stdout="1 passed\n",
        stderr="",
        duration_sec=0.15,
        line_coverage=85.0,
        line_coverage_delta=5.0,
    )

    def mock_factory(db_path: Path, repo_path: Path, **kwargs: object) -> MagicMock:
        mock_engine = MagicMock(spec=WorkflowEngine)
        mock_engine.run.return_value = WorkflowState(
            run_id="run-cli-001",
            repo_path=repo_path,
            plan=plan,
            candidates=(cand,),
            evidences=(ev,),
            final_status="COMPLETED",
        )
        return mock_engine

    set_engine_factory(mock_factory)

    result = runner.invoke(app, ["run", "--repo", str(git_repo), "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Execution Summary" in result.stdout
    assert "COMPLETED" in result.stdout
    assert "cand-1" in result.stdout
    assert "85.0%" in result.stdout


def test_cli_run_workflow_failed_exit_1(runner: CliRunner, git_repo: Path, db_path: Path) -> None:
    """Verifies workflow failure returns exit code 1."""
    def mock_factory(db_path: Path, repo_path: Path, **kwargs: object) -> MagicMock:
        mock_engine = MagicMock(spec=WorkflowEngine)
        mock_engine.run.side_effect = RuntimeError("Container execution timeout")
        return mock_engine

    set_engine_factory(mock_factory)

    result = runner.invoke(app, ["run", "--repo", str(git_repo), "--db", str(db_path)])
    assert result.exit_code == 1
    assert "Workflow execution failed: Container execution timeout" in result.stdout


def test_cli_run_workflow_state_failed_exit_1(runner: CliRunner, git_repo: Path, db_path: Path) -> None:
    """Verifies that if WorkflowState.final_status is FAILED, CLI exits with code 1."""
    def mock_factory(db_path: Path, repo_path: Path, **kwargs: object) -> MagicMock:
        mock_engine = MagicMock(spec=WorkflowEngine)
        mock_engine.run.return_value = WorkflowState(
            run_id="run-fail-state",
            repo_path=repo_path,
            final_status="FAILED",
            error_message="Multi-gate validation rejected all candidates",
        )
        return mock_engine

    set_engine_factory(mock_factory)

    result = runner.invoke(app, ["run", "--repo", str(git_repo), "--db", str(db_path)])
    assert result.exit_code == 1
    assert "FAILED" in result.stdout
    assert "Multi-gate validation rejected all candidates" in result.stdout


def test_cli_run_application_bug_warning(runner: CliRunner, git_repo: Path, db_path: Path) -> None:
    """Verifies that diagnoses with APPLICATION_BUG render a prominent warning banner."""
    diag = FailureDiagnosis(
        diagnosis_id="diag-bug",
        evidence_id="ev-fail",
        canonical_category=FailureCategory.APPLICATION_BUG,
        confidence=0.98,
        triage_engine=TriageEngine.DETERMINISTIC_RULE,
        explanation="Assertion failure represents genuine application source bug",
        is_application_bug=True,
    )

    def mock_factory(db_path: Path, repo_path: Path, **kwargs: object) -> MagicMock:
        mock_engine = MagicMock(spec=WorkflowEngine)
        mock_engine.run.return_value = WorkflowState(
            run_id="run-app-bug",
            repo_path=repo_path,
            diagnoses=(diag,),
            final_status="COMPLETED",
        )
        return mock_engine

    set_engine_factory(mock_factory)

    result = runner.invoke(app, ["run", "--repo", str(git_repo), "--db", str(db_path)])
    assert result.exit_code == 0
    assert "SAFETY ALERT: APPLICATION DEFECT" in result.stdout
    assert "CRITICAL: APPLICATION BUG DETECTED" in result.stdout
    assert "diag-bug" in result.stdout


# ============================================================================
# 3. Checkpoint Resumption CLI Tests
# ============================================================================

def test_cli_run_resume_missing_run(runner: CliRunner, db_path: Path) -> None:
    """Verifies that resuming a non-existent run ID exits with code 2."""
    result = runner.invoke(app, ["run", "--resume", "run-missing", "--db", str(db_path)])
    assert result.exit_code == 2
    assert "No checkpoint found for run 'run-missing'" in result.stdout


def test_cli_subcommand_resume_missing_run(runner: CliRunner, db_path: Path) -> None:
    """Verifies that `resume <run_id>` for non-existent run ID exits with code 2."""
    result = runner.invoke(app, ["resume", "run-missing", "--db", str(db_path)])
    assert result.exit_code == 2
    assert "No checkpoint found for run 'run-missing'" in result.stdout


def test_cli_run_resume_terminal_run(runner: CliRunner, db_path: Path) -> None:
    """Verifies that attempting to resume an already completed run exits with code 2."""
    conn = init_database(db_path)
    repo = SQLiteRepository(conn)
    state = WorkflowState(run_id="run-terminal-ok", repo_path=Path("/test/repo"))
    repo.save_run(
        run_id="run-terminal-ok",
        repo_path=state.repo_path,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="COMPLETED",
    )
    repo.save_checkpoint(state=state, step_index=2, node_name="plan_execution_node")

    result = runner.invoke(app, ["run", "--resume", "run-terminal-ok", "--db", str(db_path)])
    assert result.exit_code == 2
    assert "already in terminal status" in result.stdout
    assert "COMPLETED" in result.stdout


def test_cli_run_resume_success(runner: CliRunner, git_repo: Path, db_path: Path) -> None:
    """Verifies that `run --resume <run_id>` successfully resumes and renders header."""
    conn = init_database(db_path)
    repo = SQLiteRepository(conn)
    event_store = SQLiteEventStore(conn)

    state = WorkflowState(
        run_id="run-resume-me",
        repo_path=git_repo,
        snapshot=RepositorySnapshot(
            repo_path=git_repo,
            current_commit="c1",
            base_commit="c0",
            branch_name="main",
            source_tree_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
            is_valid=True,
        ),
        plan=ExecutionPlan(
            plan_id="plan-1",
            route=WorkflowRoute.ROUTE_NO_OP,
            decision_hash="dhash",
            rationale="No op",
        ),
    )
    repo.save_run(
        run_id="run-resume-me",
        repo_path=git_repo,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state, step_index=2, node_name="plan_execution_node")

    # In factory, return real WorkflowEngine wired to the same test db
    def mock_factory(db_path: Path, **kwargs: object) -> WorkflowEngine:
        return WorkflowEngine(repository=repo, event_store=event_store)

    set_engine_factory(mock_factory)

    result = runner.invoke(app, ["run", "--resume", "run-resume-me", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Workflow Checkpoint Recovery" in result.stdout
    assert "Resuming Run:" in result.stdout
    assert "run-resume-me" in result.stdout
    assert "COMPLETED" in result.stdout


def test_cli_subcommand_resume_success(runner: CliRunner, git_repo: Path, db_path: Path) -> None:
    """Verifies that `resume <run_id>` subcommand successfully resumes."""
    conn = init_database(db_path)
    repo = SQLiteRepository(conn)
    event_store = SQLiteEventStore(conn)

    state = WorkflowState(
        run_id="run-resume-subcmd",
        repo_path=git_repo,
        snapshot=RepositorySnapshot(
            repo_path=git_repo,
            current_commit="c1",
            base_commit="c0",
            branch_name="main",
            source_tree_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
            is_valid=True,
        ),
        plan=ExecutionPlan(
            plan_id="plan-sub",
            route=WorkflowRoute.ROUTE_NO_OP,
            decision_hash="dhash",
            rationale="No op",
        ),
    )
    repo.save_run(
        run_id="run-resume-subcmd",
        repo_path=git_repo,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state, step_index=2, node_name="plan_execution_node")

    def mock_factory(db_path: Path, **kwargs: object) -> WorkflowEngine:
        return WorkflowEngine(repository=repo, event_store=event_store)

    set_engine_factory(mock_factory)

    result = runner.invoke(app, ["resume", "run-resume-subcmd", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Workflow Checkpoint Recovery" in result.stdout
    assert "run-resume-subcmd" in result.stdout
    assert "COMPLETED" in result.stdout


def test_cli_resume_failure_exit_1(runner: CliRunner, git_repo: Path, db_path: Path) -> None:
    """Verifies that an unhandled error during resumption exits with code 1."""
    conn = init_database(db_path)
    repo = SQLiteRepository(conn)
    event_store = SQLiteEventStore(conn)

    state = WorkflowState(
        run_id="run-resume-fail",
        repo_path=git_repo,
        snapshot=RepositorySnapshot(
            repo_path=git_repo,
            current_commit="c1",
            base_commit="c0",
            branch_name="main",
            source_tree_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
            is_valid=True,
        ),
    )
    repo.save_run(
        run_id="run-resume-fail",
        repo_path=git_repo,
        current_commit="c1",
        base_commit="c0",
        branch_name="main",
        final_status="RUNNING",
    )
    repo.save_checkpoint(state=state, step_index=1, node_name="ingest_and_analyze_node")

    mock_planner = MagicMock()
    mock_planner.plan.side_effect = RuntimeError("Fatal planner exception")

    def mock_factory(db_path: Path, **kwargs: object) -> WorkflowEngine:
        return WorkflowEngine(
            repository=repo,
            event_store=event_store,
            planner=mock_planner,
        )

    set_engine_factory(mock_factory)

    result = runner.invoke(app, ["resume", "run-resume-fail", "--db", str(db_path)])
    assert result.exit_code == 1
    assert "Fatal planner exception" in result.stdout
