"""
Run artifact exporter and Markdown report generator.
Persists runnable test candidates and run-scoped execution summary reports under:
<repository-root>/.agentic_test/runs/<run_id>/
├── candidates/
│   └── test_<target>_<candidate-id>.py
└── report.md
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple

from agentic_test.core.models import (
    ExecutionEvidence,
    FailureCategory,
    FailureDiagnosis,
    TestCandidate,
    ValidationStatus,
)
from agentic_test.core.state import WorkflowState


def sanitize_identifier(name: str) -> str:
    """Sanitizes qualified symbol names into safe filesystem string identifiers."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_")


def export_run_artifacts(state: WorkflowState) -> Optional[Path]:
    """
    Exports all generated candidate test files and a comprehensive run summary report.
    Returns the path to the created run directory, or None if repo_path is invalid.
    """
    if not state.repo_path:
        return None

    try:
        repo_root = Path(state.repo_path).resolve()
        if not repo_root.exists() or not repo_root.is_dir():
            return None
    except Exception:
        return None

    run_id = state.run_id
    agentic_root = repo_root / ".agentic_test"
    run_dir = agentic_root / "runs" / run_id
    candidates_dir = run_dir / "candidates"
    quarantine_dir = run_dir / "quarantine"

    # 1. Export candidate files based on validation status
    # Passed candidates go to candidates/ with test_*.py
    # Quarantined / rejected candidates with code go to quarantine/ with quarantined_*.py.txt
    passed_files: List[Tuple[str, Path, TestCandidate]] = []
    quarantined_files: List[Tuple[str, Path, TestCandidate]] = []

    for candidate in state.candidates:
        if candidate.candidate_code and candidate.candidate_code.strip():
            clean_target = sanitize_identifier(candidate.target_symbol_name)
            file_content = _assemble_candidate_file(candidate, run_id)

            if candidate.validation_status == ValidationStatus.PASSED:
                candidates_dir.mkdir(parents=True, exist_ok=True)
                file_name = f"test_{clean_target}_{candidate.candidate_id}.py"
                file_path = candidates_dir / file_name
                file_path.write_text(file_content, encoding="utf-8")
                passed_files.append((f"candidates/{file_name}", file_path, candidate))
            else:
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                file_name = f"quarantined_{clean_target}_{candidate.candidate_id}.py.txt"
                file_path = quarantine_dir / file_name
                file_path.write_text(file_content, encoding="utf-8")
                quarantined_files.append((f"quarantine/{file_name}", file_path, candidate))

    # 2. Generate and write report.md
    report_path = run_dir / "report.md"
    report_content = _generate_report_markdown(state, passed_files, quarantined_files)
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_content, encoding="utf-8")

    return run_dir


def _assemble_candidate_file(candidate: TestCandidate, run_id: str) -> str:
    """Assembles Python test or quarantined artifact file with metadata docstring, imports, and test code."""
    header = [
        '"""',
        "Auto-generated unit test candidate by Agentic Test Generation System.",
        f"Run ID:            {run_id}",
        f"Candidate ID:      {candidate.candidate_id}",
        f"Target Symbol:     {candidate.target_symbol_name}",
        f"Validation Status: {candidate.validation_status.value}",
    ]
    if candidate.quarantine_reason:
        header.append(f"Quarantine Reason: {candidate.quarantine_reason}")
    if candidate.validation_status != ValidationStatus.PASSED:
        header.append("WARNING: This candidate was quarantined/rejected from execution.")
        header.append("Saved as a non-executable artifact (.py.txt) for manual audit only.")
    header.append('"""\n')

    parts = ["\n".join(header)]

    if candidate.imports:
        parts.append("\n".join(candidate.imports) + "\n")

    parts.append(candidate.candidate_code.strip() + "\n")
    return "\n".join(parts)


def _format_evidence_snippet(title: str, ev: ExecutionEvidence) -> List[str]:
    """Formats traceback, stderr, or stdout code fence snippets for a failed execution evidence."""
    to_str = " (TIMED OUT)" if (ev.timed_out or ev.exit_code == 124) else ""
    snippet_lines = [
        f"#### {title} (Exit Code: `{ev.exit_code}`{to_str}, Duration: `{ev.duration_sec:.2f}s`)",
        "",
    ]
    if ev.traceback:
        snippet_lines.extend([
            "```text",
            ev.traceback.strip(),
            "```",
            "",
        ])
    elif ev.stderr:
        snippet_lines.extend([
            "```text",
            ev.stderr.strip(),
            "```",
            "",
        ])
    elif ev.stdout:
        snippet_lines.extend([
            "```text",
            ev.stdout.strip()[:1000],
            "```",
            "",
        ])
    return snippet_lines


def _generate_report_markdown(
    state: WorkflowState,
    passed_files: List[Tuple[str, Path, TestCandidate]],

    quarantined_files: List[Tuple[str, Path, TestCandidate]],
) -> str:
    """Generates the run execution summary Markdown report."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ev_map: Dict[str, ExecutionEvidence] = {
        ev.candidate_id: ev for ev in state.evidences if ev.candidate_id
    }
    all_exported = passed_files + quarantined_files
    file_link_by_cand = {cand.candidate_id: rel_link for rel_link, _, cand in all_exported}
    passed_candidates = [c for c in state.candidates if c.validation_status == ValidationStatus.PASSED]

    lines = [
        f"# Execution Report: `{state.run_id}`",
        "",
        f"- **Generated At**: {now_str}",
        f"- **Repository**: `{state.repo_path}`",
        f"- **Final Status**: `{state.final_status}`",
        f"- **Selected Route**: `{state.plan.route.value if state.plan else 'NONE'}`",
        "",
        "---",
        "",
        "## 1. Executive Summary",
        "",
        "| Property | Value |",
        "| :--- | :--- |",
        f"| **Final Status** | `{state.final_status}` |",
        f"| **Selected Route** | `{state.plan.route.value if state.plan else 'NONE'}` |",
        f"| **Target Symbols** | {len(state.plan.target_symbols) if state.plan else 0} |",
        f"| **Total Candidates** | {len(state.candidates)} |",
        f"| **Passed (Validated) Candidates** | {len(passed_candidates)} |",
        f"| **Exported Executable Tests** | {len(passed_files)} |",
        f"| **Quarantined Review Artifacts** | {len(quarantined_files)} |",
        f"| **Execution Evidences** | {len(state.evidences)} |",
        f"| **Failure Diagnoses** | {len(state.diagnoses)} |",
        "",
        "---",
        "",
        "## 2. Exported Test Candidate Files",
        "",
        "### Validated & Executable Tests (`candidates/`)",
        "",
    ]

    # 2. Exported Candidates List (Placed immediately after Executive Summary)
    if passed_files:
        lines.append("The following test files passed all validation gates and are runnable:")
        for rel_link, fp, c in passed_files:
            ev = ev_map.get(c.candidate_id)
            status_parts = []
            if ev is not None:
                status_parts.append(f"Exit Code: `{ev.exit_code}`")
                status_parts.append(f"Duration: `{ev.duration_sec:.2f}s`")
                if ev.timed_out or ev.exit_code == 124:
                    status_parts.append("**TIMED OUT**")
            else:
                status_parts.append("Status: " + c.validation_status.value)
            status_desc = ", ".join(status_parts)
            lines.append(f"- [`{rel_link}`](./{rel_link}) — Target: `{c.target_symbol_name}` ({status_desc})")
    else:
        lines.append("No executable test candidate files passed validation for this run.")
    lines.append("")

    lines.extend([
        "### Quarantined Artifacts for Review (`quarantine/`)",
        "",
    ])
    if quarantined_files:
        lines.append(
            "The following candidate files were rejected during generation or validation gates and are preserved for audit with a non-executable `.py.txt` extension to prevent accidental test collection:"
        )
        for rel_link, fp, c in quarantined_files:
            reason = c.quarantine_reason or "Validation rejected"
            lines.append(f"- [`{rel_link}`](./{rel_link}) — Target: `{c.target_symbol_name}` (Status: `{c.validation_status.value}`, Reason: {reason})")
    else:
        lines.append("No candidate files were quarantined.")
    lines.append("")

    # 3. Process Errors & Diagnostics Alerts
    lines.extend([
        "---",
        "",
        "## 3. Process Errors & Diagnostics Alerts",
        "",
    ])

    errors_found = False

    # A. Global error message
    if state.error_message:
        errors_found = True
        lines.extend([
            "> [!CAUTION]",
            f"> **Pipeline Execution Error**: {state.error_message}",
            "",
        ])

    # B. Static syntax errors from repository analysis
    if state.snapshot and state.snapshot.syntax_errors:
        errors_found = True
        lines.extend([
            "> [!WARNING]",
            "> **Source Syntax Errors Detected in Target Repository**:",
        ])
        for err in state.snapshot.syntax_errors:
            lines.append(f"> - `{err}`")
        lines.append("")

    # C. Application Defect Alert (Critical)
    app_bugs = [d for d in state.diagnoses if d.canonical_category == FailureCategory.APPLICATION_BUG or d.is_application_bug]
    if app_bugs:
        errors_found = True
        lines.extend([
            "> [!CAUTION]",
            "> ### CRITICAL: APPLICATION DEFECT DETECTED",
            "> The triage subsystem classified genuine defect(s) in application source code. Automatic test mutation is prohibited.",
            "",
        ])
        for bug in app_bugs:
            lines.append(f"> - **Diagnosis `{bug.diagnosis_id}`** (Evidence `{bug.evidence_id}`): {bug.explanation}")
        lines.append("")

    # D. Generation & Validation Failures
    quarantined_cands = [c for c in state.candidates if c.validation_status != ValidationStatus.PASSED]
    if quarantined_cands:
        errors_found = True
        lines.extend([
            "### Generation & Validation Quarantine Details",
            "",
            "| Candidate ID | Target Symbol | Validation Status | Quarantine / Failure Reason | Quarantined Artifact |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ])
        for c in quarantined_cands:
            link_str = (
                f"[`{file_link_by_cand[c.candidate_id]}`](./{file_link_by_cand[c.candidate_id]})"
                if c.candidate_id in file_link_by_cand
                else "-"
            )
            lines.append(
                f"| `{c.candidate_id}` | `{c.target_symbol_name}` | `{c.validation_status.value}` | {c.quarantine_reason or '-'} | {link_str} |"
            )
        lines.append("")

    # E. Execution Failures (including Baseline Regression Suite and Candidate Tests)
    failed_baseline = (
        state.baseline_evidence
        if state.baseline_evidence is not None and state.baseline_evidence.exit_code != 0
        else None
    )
    failed_candidates = [ev for ev in state.evidences if ev.exit_code != 0]

    if failed_baseline is not None or failed_candidates:
        errors_found = True
        lines.extend([
            "### Execution Failures & Traceback Snippets",
            "",
        ])
        if failed_baseline is not None:
            lines.extend(_format_evidence_snippet("Baseline Regression Tests (Existing Tests)", failed_baseline))
        for ev in failed_candidates:
            cand_id = ev.candidate_id or "unknown"
            lines.extend(_format_evidence_snippet(f"Candidate `{cand_id}`", ev))

    if not errors_found:
        lines.extend([
            "> [!NOTE]",
            "> No process errors, validation rejections, or execution failures were encountered during this run.",
            "",
        ])

    # 4. Test Candidates Table (with clickable file links and Timed Out column)
    lines.extend([
        "---",
        "",
        "## 4. Test Candidates & Validation Status",
        "",
        "| Candidate ID | Target Symbol | Validation Status | Details / Reason | Exit Code | Timed Out? | Duration | File Link |",
        "| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- |",
    ])

    for c in state.candidates:
        ev = ev_map.get(c.candidate_id)
        exit_code_str = str(ev.exit_code) if ev is not None else "-"
        timed_out_str = "**YES**" if (ev is not None and (ev.timed_out or ev.exit_code == 124)) else ("No" if ev is not None else "-")
        dur_str = f"{ev.duration_sec:.2f}s" if ev is not None else "-"
        if c.candidate_id in file_link_by_cand:
            rel_link = file_link_by_cand[c.candidate_id]
            fn_str = f"[`{rel_link}`](./{rel_link})"
        else:
            fn_str = "-"
        reason_str = c.quarantine_reason or "-"

        lines.append(
            f"| `{c.candidate_id}` | `{c.target_symbol_name}` | `{c.validation_status.value}` | {reason_str} | {exit_code_str} | {timed_out_str} | {dur_str} | {fn_str} |"
        )
    lines.append("")

    # 5. Coverage Telemetry & Deltas (including Baseline Regression)
    has_baseline_cov = state.baseline_evidence is not None and (
        state.baseline_evidence.line_coverage is not None or state.baseline_evidence.branch_coverage is not None
    )
    cov_evidences = [ev for ev in state.evidences if ev.line_coverage is not None or ev.line_coverage_delta is not None]

    if has_baseline_cov or cov_evidences:
        lines.extend([
            "---",
            "",
            "## 5. Coverage Telemetry & Deltas",
            "",
            "| Target / Candidate | Line Cov | Line Delta | Branch Cov | Branch Delta | Exit Code | Duration |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])

        # Add Baseline row first if present
        if has_baseline_cov and state.baseline_evidence is not None:
            base = state.baseline_evidence
            b_line = f"{base.line_coverage:.1f}%" if base.line_coverage is not None else "-"
            b_branch = f"{base.branch_coverage:.1f}%" if base.branch_coverage is not None else "-"
            lines.append(
                f"| **Baseline** (Existing Tests) | {b_line} | - | {b_branch} | - | `{base.exit_code}` | {base.duration_sec:.2f}s |"
            )

        # Add Candidates coverage rows
        for ev in cov_evidences:
            line_str = f"{ev.line_coverage:.1f}%" if ev.line_coverage is not None else "-"
            l_delta = f"{ev.line_coverage_delta:+.1f}%" if ev.line_coverage_delta is not None else "-"
            branch_str = f"{ev.branch_coverage:.1f}%" if ev.branch_coverage is not None else "-"
            b_delta = f"{ev.branch_coverage_delta:+.1f}%" if ev.branch_coverage_delta is not None else "-"
            exit_str = f"`{ev.exit_code}`"
            dur_str = f"{ev.duration_sec:.2f}s"
            lines.append(f"| `{ev.candidate_id or 'unknown'}` | {line_str} | {l_delta} | {branch_str} | {b_delta} | {exit_str} | {dur_str} |")
        lines.append("")

    # 6. Failure Diagnoses Table (with Category & Confidence highlighted clearly)
    if state.diagnoses:
        lines.extend([
            "---",
            "",
            "## 6. Failure Diagnoses & Triage Details",
            "",
            "| Diagnosis ID | Evidence ID | Category | Confidence | Engine | Is App Bug? | Explanation |",
            "| :--- | :--- | :--- | :---: | :--- | :---: | :--- |",
        ])
        for d in state.diagnoses:
            bug_flag = "**YES**" if d.is_application_bug else "No"
            conf_str = f"`{d.confidence:.2f}` ({int(d.confidence * 100)}%)"
            lines.append(
                f"| `{d.diagnosis_id}` | `{d.evidence_id}` | `{d.canonical_category.value}` | {conf_str} | `{d.triage_engine.value}` | {bug_flag} | {d.explanation} |"
            )
        lines.append("")

    return "\n".join(lines)
