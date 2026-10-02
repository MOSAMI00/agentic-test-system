"""
Unit tests for run artifact exporter and execution report generator.
"""

from pathlib import Path
import pytest

from agentic_test.core.models import (
    ExecutionEvidence,
    ExecutionPlan,
    FailureCategory,
    FailureDiagnosis,
    RepositorySnapshot,
    SymbolContract,
    SymbolType,
    TestCandidate,
    TriageEngine,
    ValidationStatus,
    WorkflowRoute,
)
from agentic_test.core.state import WorkflowState
from agentic_test.reporting.exporter import export_run_artifacts, sanitize_identifier


def test_sanitize_identifier() -> None:
    assert sanitize_identifier("slugify.special.reverse_char_mapping") == "slugify_special_reverse_char_mapping"
    assert sanitize_identifier("func-name!123") == "func_name_123"
    assert sanitize_identifier("___already_clean___") == "already_clean"


def test_export_run_artifacts_success(tmp_path: Path) -> None:
    repo_dir = tmp_path / "mock_repo"
    repo_dir.mkdir()

    sym = SymbolContract(
        symbol_type=SymbolType.FUNCTION,
        name="calc_sum",
        qualified_name="math_module.calc_sum",
        file_path=Path("math_module.py"),
        signature="calc_sum(a: int, b: int) -> int",
        docstring="Adds two numbers.",
        line_range=(1, 10),
    )
    plan = ExecutionPlan(
        plan_id="plan-1",
        route=WorkflowRoute.ROUTE_TO_TEST_GENERATION,
        target_symbols=(sym,),
        rationale="Plan rationale",
        decision_hash="hash1",
    )
    cand1 = TestCandidate(
        candidate_id="tc-1",
        run_id="run-test-123",
        target_symbol_name="math_module.calc_sum",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_calc_sum(): assert calc_sum(1, 2) == 3",
        imports=("import pytest", "from math_module import calc_sum"),
        validation_status=ValidationStatus.PASSED,
    )
    cand2 = TestCandidate(
        candidate_id="tc-2",
        run_id="run-test-123",
        target_symbol_name="math_module.calc_sum",
        test_file_path=Path("tests/test_calc.py"),
        candidate_code="def test_calc_fail(): assert calc_sum(1, 2) == 4",
        imports=("import pytest", "from math_module import calc_sum"),
        validation_status=ValidationStatus.PASSED,
    )
    ev1 = ExecutionEvidence(
        evidence_id="ev-1",
        run_id="run-test-123",
        candidate_id="tc-1",
        exit_code=0,
        stdout="1 passed in 0.1s",
        stderr="",
        duration_sec=0.15,
        line_coverage=85.0,
        branch_coverage=75.0,
        line_coverage_delta=5.0,
        branch_coverage_delta=2.5,
    )
    ev2 = ExecutionEvidence(
        evidence_id="ev-2",
        run_id="run-test-123",
        candidate_id="tc-2",
        exit_code=1,
        stdout="",
        stderr="",
        traceback="def test_calc_fail(): > assert calc_sum(1, 2) == 4\nE AssertionError",
        duration_sec=0.20,
    )
    diag = FailureDiagnosis(
        diagnosis_id="diag-1",
        evidence_id="ev-2",
        canonical_category=FailureCategory.INVALID_GENERATED_TEST,
        confidence=0.95,
        triage_engine=TriageEngine.COGNITIVE_LLM,
        explanation="The test expects 4 instead of 3, making it an invalid test assertion.",
        is_application_bug=False,
    )

    base_ev = ExecutionEvidence(
        evidence_id="ev-base",
        run_id="run-test-123",
        candidate_id=None,
        exit_code=0,
        stdout="all base tests passed",
        stderr="",
        duration_sec=0.50,
        line_coverage=50.0,
        branch_coverage=40.0,
    )

    state = WorkflowState(
        run_id="run-test-123",
        repo_path=repo_dir,
        plan=plan,
        candidates=(cand1, cand2),
        baseline_evidence=base_ev,
        evidences=(ev1, ev2),
        diagnoses=(diag,),
        final_status="COMPLETED",
    )

    run_dir = export_run_artifacts(state)
    assert run_dir is not None
    assert run_dir.exists()

    expected_dir = repo_dir / ".agentic_test" / "runs" / "run-test-123"
    assert run_dir == expected_dir

    candidates_dir = run_dir / "candidates"
    assert candidates_dir.exists()

    # Check candidate 1 file
    f1 = candidates_dir / "test_math_module_calc_sum_tc-1.py"
    assert f1.exists()
    content1 = f1.read_text(encoding="utf-8")
    assert "import pytest" in content1
    assert "from math_module import calc_sum" in content1
    assert "def test_calc_sum(): assert calc_sum(1, 2) == 3" in content1
    assert "Run ID:            run-test-123" in content1

    # Check candidate 2 file
    f2 = candidates_dir / "test_math_module_calc_sum_tc-2.py"
    assert f2.exists()
    content2 = f2.read_text(encoding="utf-8")
    assert "def test_calc_fail(): assert calc_sum(1, 2) == 4" in content2

    # Check report.md
    report_file = run_dir / "report.md"
    assert report_file.exists()
    report_text = report_file.read_text(encoding="utf-8")

    assert "# Execution Report: `run-test-123`" in report_text
    assert "Executive Summary" in report_text
    # 1. Exported candidates placed right after executive summary
    assert report_text.find("## 2. Exported Test Candidate Files") < report_text.find("## 3. Process Errors & Diagnostics Alerts")
    # 2. Clickable link in candidates table
    assert "[`candidates/test_math_module_calc_sum_tc-1.py`](./candidates/test_math_module_calc_sum_tc-1.py)" in report_text
    # 3. Baseline regression included in coverage table
    assert "**Baseline** (Existing Tests)" in report_text
    assert "50.0%" in report_text
    assert "Coverage Telemetry & Deltas" in report_text
    assert "85.0%" in report_text
    # 4. Timed Out column present
    assert "Timed Out?" in report_text
    # 5. Diagnoses shows category, engine, and confidence percentage
    assert "Failure Diagnoses & Triage Details" in report_text
    assert "The test expects 4 instead of 3" in report_text
    assert "(95%)" in report_text
    assert "Process Errors & Diagnostics Alerts" in report_text
    assert "Execution Failures & Traceback Snippets" in report_text
    assert "Candidate `tc-2`" in report_text
    assert "AssertionError" in report_text


def test_export_run_artifacts_with_quarantine_and_app_bug(tmp_path: Path) -> None:
    repo_dir = tmp_path / "app_bug_repo"
    repo_dir.mkdir()

    cand = TestCandidate(
        candidate_id="tc-quarantined",
        run_id="run-bug-456",
        target_symbol_name="broken_sym",
        test_file_path=Path("test.py"),
        candidate_code="",
        validation_status=ValidationStatus.QUARANTINED,
        quarantine_reason="EXHAUSTED_GENERATION_REPAIRS",
    )
    ev = ExecutionEvidence(
        evidence_id="ev-bug",
        run_id="run-bug-456",
        candidate_id=None,
        exit_code=1,
        stdout="",
        stderr="MemoryError",
        duration_sec=1.5,
    )
    diag = FailureDiagnosis(
        diagnosis_id="diag-app-bug",
        evidence_id="ev-bug",
        canonical_category=FailureCategory.APPLICATION_BUG,
        confidence=0.92,
        triage_engine=TriageEngine.COGNITIVE_LLM,
        explanation="Detected genuine memory leak defect in target application.",
        is_application_bug=True,
    )

    state = WorkflowState(
        run_id="run-bug-456",
        repo_path=repo_dir,
        candidates=(cand,),
        evidences=(ev,),
        diagnoses=(diag,),
        final_status="COMPLETED",
        error_message="Global pipeline warning",
    )

    run_dir = export_run_artifacts(state)
    assert run_dir is not None

    report_text = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "CRITICAL: APPLICATION DEFECT DETECTED" in report_text
    assert "Detected genuine memory leak defect" in report_text
    assert "Global pipeline warning" in report_text
    assert "EXHAUSTED_GENERATION_REPAIRS" in report_text


def test_export_run_artifacts_graceful_on_invalid_repo() -> None:
    state = WorkflowState(
        run_id="run-invalid",
        repo_path=Path("/non_existent_directory_987654321"),
        final_status="COMPLETED",
    )
    result = export_run_artifacts(state)
    assert result is None


def test_export_run_artifacts_quarantine_isolation_and_reporting(tmp_path: Path) -> None:
    repo_dir = tmp_path / "quarantine_repo"
    repo_dir.mkdir()

    # 1. Valid passed candidate
    cand_passed = TestCandidate(
        candidate_id="tc-pass",
        run_id="run-quarantine-test",
        target_symbol_name="safe_module.safe_func",
        test_file_path=Path("tests/test_safe.py"),
        candidate_code="def test_safe_func(): assert safe_func() is True",
        imports=("from safe_module import safe_func",),
        validation_status=ValidationStatus.PASSED,
    )

    # 2. Rejected candidate (e.g. security rejection containing dangerous imports)
    cand_quarantined = TestCandidate(
        candidate_id="tc-quarantine",
        run_id="run-quarantine-test",
        target_symbol_name="dangerous_module.exec_cmd",
        test_file_path=Path("tests/test_danger.py"),
        candidate_code="def test_eval(): import os; os.system('echo dangerous')",
        imports=("import os",),
        validation_status=ValidationStatus.REJECTED_SECURITY,
        quarantine_reason="Gate 2 Security: Prohibited call to os.system",
    )

    state = WorkflowState(
        run_id="run-quarantine-test",
        repo_path=repo_dir,
        candidates=(cand_passed, cand_quarantined),
        final_status="COMPLETED",
    )

    run_dir = export_run_artifacts(state)
    assert run_dir is not None

    candidates_dir = run_dir / "candidates"
    quarantine_dir = run_dir / "quarantine"
    assert candidates_dir.exists()
    assert quarantine_dir.exists()

    # Passed candidate MUST be in candidates/ with test_*.py
    passed_file = candidates_dir / "test_safe_module_safe_func_tc-pass.py"
    assert passed_file.exists()
    assert "def test_safe_func():" in passed_file.read_text(encoding="utf-8")

    # Quarantined candidate MUST NOT be in candidates/
    assert not (candidates_dir / "test_dangerous_module_exec_cmd_tc-quarantine.py").exists()

    # Quarantined candidate MUST be in quarantine/ with .py.txt extension
    quarantined_file = quarantine_dir / "quarantined_dangerous_module_exec_cmd_tc-quarantine.py.txt"
    assert quarantined_file.exists()
    q_content = quarantined_file.read_text(encoding="utf-8")
    assert "Gate 2 Security: Prohibited call to os.system" in q_content
    assert "WARNING: This candidate was quarantined/rejected from execution." in q_content
    assert "def test_eval():" in q_content

    # The quarantined file must NOT match pytest test discovery patterns
    assert not quarantined_file.name.startswith("test_")
    assert not quarantined_file.name.endswith(".py")
    assert quarantined_file.name.endswith(".py.txt")

    # Check report.md content
    report_text = (run_dir / "report.md").read_text(encoding="utf-8")

    # Executive Summary distinction
    assert "Passed (Validated) Candidates" in report_text
    assert "Exported Executable Tests" in report_text
    assert "Quarantined Review Artifacts" in report_text

    # Section 2 separation
    assert "### Validated & Executable Tests (`candidates/`)" in report_text
    assert "[`candidates/test_safe_module_safe_func_tc-pass.py`](./candidates/test_safe_module_safe_func_tc-pass.py)" in report_text

    assert "### Quarantined Artifacts for Review (`quarantine/`)" in report_text
    assert "[`quarantine/quarantined_dangerous_module_exec_cmd_tc-quarantine.py.txt`](./quarantine/quarantined_dangerous_module_exec_cmd_tc-quarantine.py.txt)" in report_text

    # Section 3 quarantine details table link
    assert "Quarantined Artifact" in report_text
    assert "Gate 2 Security: Prohibited call to os.system" in report_text

    # Section 4 table link
    assert "[`quarantine/quarantined_dangerous_module_exec_cmd_tc-quarantine.py.txt`](./quarantine/quarantined_dangerous_module_exec_cmd_tc-quarantine.py.txt)" in report_text


def test_export_run_artifacts_reports_failed_baseline_regression_in_errors_section(tmp_path: Path) -> None:
    repo_dir = tmp_path / "baseline_fail_repo"
    repo_dir.mkdir()

    # 1. Baseline evidence has failed exit_code (e.g. exit_code=1 from existing regression tests)
    base_ev = ExecutionEvidence(
        evidence_id="ev-base-fail",
        run_id="run-base-fail",
        candidate_id=None,
        exit_code=1,
        stdout="collected 10 items, 1 failed",
        stderr="",
        traceback="def test_existing():\n>   assert 1 == 2\nE   AssertionError: assert 1 == 2",
        duration_sec=0.85,
    )

    # 2. State has no candidate execution failures and no quarantined candidates
    cand_passed = TestCandidate(
        candidate_id="tc-pass-only",
        run_id="run-base-fail",
        target_symbol_name="mod.func",
        test_file_path=Path("tests/test_mod.py"),
        candidate_code="def test_func(): assert True",
        validation_status=ValidationStatus.PASSED,
    )
    ev_passed = ExecutionEvidence(
        evidence_id="ev-pass",
        run_id="run-base-fail",
        candidate_id="tc-pass-only",
        exit_code=0,
        stdout="1 passed",
        stderr="",
        duration_sec=0.2,
    )

    state = WorkflowState(
        run_id="run-base-fail",
        repo_path=repo_dir,
        candidates=(cand_passed,),
        baseline_evidence=base_ev,
        evidences=(ev_passed,),
        final_status="COMPLETED",
    )

    run_dir = export_run_artifacts(state)
    assert run_dir is not None

    report_text = (run_dir / "report.md").read_text(encoding="utf-8")

    # The errors section MUST include the baseline execution failure
    assert "### Execution Failures & Traceback Snippets" in report_text
    assert "#### Baseline Regression Tests (Existing Tests) (Exit Code: `1`, Duration: `0.85s`)" in report_text
    assert "AssertionError: assert 1 == 2" in report_text

    # The clean execution note must NOT appear because baseline failed
    assert "No process errors, validation rejections, or execution failures were encountered" not in report_text


