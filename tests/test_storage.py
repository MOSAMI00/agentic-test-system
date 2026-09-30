"""
Unit tests for SQLite database connection policy, DDL schema, and relational integrity.
Adheres strictly to Stage 3 Section 4.4.7 (Listing 4.4) and Slice 1.6.2A contracts.
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Set

from pydantic import ValidationError
import pytest

from agentic_test.storage.database import (
    SCHEMA_DDL,
    get_connection,
    init_database,
    init_db,
)
from agentic_test.storage.events import (
    EventRecord,
    SQLiteEventStore,
)


def _get_table_names(conn: sqlite3.Connection) -> Set[str]:
    """Helper returning set of non-sqlite table names in database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
    )
    return {row[0] for row in cursor.fetchall()}


def _get_index_names(conn: sqlite3.Connection) -> Set[str]:
    """Helper returning set of non-autoindex index names in database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%';"
    )
    return {row[0] for row in cursor.fetchall()}


def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> Dict[str, Dict[str, object]]:
    """Helper returning column metadata for a given table."""
    cursor = conn.execute(f"PRAGMA table_info({table_name});")
    return {
        row["name"]: {
            "type": row["type"],
            "notnull": bool(row["notnull"]),
            "dflt_value": row["dflt_value"],
            "pk": bool(row["pk"]),
        }
        for row in cursor.fetchall()
    }


# ============================================================================
# 1. Connection Policy & Pragmas Tests
# ============================================================================

def test_memory_connection_pragmas() -> None:
    """Verifies that in-memory database sets FK=ON, busy_timeout=5000, and journal_mode=memory."""
    conn = get_connection(":memory:")
    try:
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]

        assert fk == 1, "Foreign keys must be enabled"
        assert timeout == 5000, "Busy timeout must be 5000ms"
        assert journal == "memory", "In-memory database should retain native memory journal"

        # Verify row_factory is sqlite3.Row
        assert conn.row_factory == sqlite3.Row
        row = conn.execute("SELECT 1 AS col_val;").fetchone()
        assert row["col_val"] == 1
    finally:
        conn.close()


def test_memory_uri_connection_pragmas() -> None:
    """Verifies that URI-based in-memory connections accept memory journal mode without error."""
    conn = get_connection("file:mem_test?mode=memory&cache=shared")
    try:
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]

        assert fk == 1
        assert journal == "memory"
    finally:
        conn.close()


def test_disk_connection_pragmas(tmp_path: Path) -> None:
    """Verifies that on-disk database sets FK=ON, busy_timeout=5000, WAL mode, and synchronous=FULL."""
    db_file = tmp_path / "persistence.db"
    conn = get_connection(db_file)
    try:
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        sync = conn.execute("PRAGMA synchronous;").fetchone()[0]

        assert fk == 1, "Foreign keys must be enabled on disk"
        assert timeout == 5000, "Busy timeout must be 5000ms"
        assert journal.lower() == "wal", "On-disk database must configure WAL journal mode"
        assert sync == 2, "On-disk database must configure synchronous=FULL (code 2) for power durability"
    finally:
        conn.close()


def test_disk_connection_with_path_string(tmp_path: Path) -> None:
    """Verifies that string path arguments are handled properly for on-disk databases."""
    db_file_str = str(tmp_path / "str_path.db")
    conn = get_connection(db_file_str)
    try:
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal.lower() == "wal"
    finally:
        conn.close()


# ============================================================================
# 2. Schema DDL & Index Verification Tests
# ============================================================================

def test_all_six_tables_created() -> None:
    """Verifies that init_db creates all six required normalized tables."""
    conn = get_connection(":memory:")
    try:
        init_db(conn)
        tables = _get_table_names(conn)
        expected = {
            "runs",
            "checkpoints",
            "events",
            "test_candidates",
            "execution_evidence",
            "failure_diagnoses",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"
    finally:
        conn.close()


def test_all_required_indices_created() -> None:
    """Verifies that all 5 secondary indices are created in sqlite_master."""
    conn = get_connection(":memory:")
    try:
        init_db(conn)
        indices = _get_index_names(conn)
        expected = {
            "idx_checkpoints_run_id",
            "idx_events_run_id_type",
            "idx_candidates_run_status",
            "idx_evidence_run_id",
            "idx_diagnoses_run_cat",
        }
        assert expected.issubset(indices), f"Missing indices: {expected - indices}"
    finally:
        conn.close()


def test_table_columns_and_data_types() -> None:
    """Verifies column names, nullability, and coverage delta definitions."""
    conn = get_connection(":memory:")
    try:
        init_db(conn)

        # 1. execution_evidence checks
        evidence_cols = _get_table_columns(conn, "execution_evidence")
        assert "evidence_id" in evidence_cols and evidence_cols["evidence_id"]["pk"]
        assert "candidate_id" in evidence_cols
        assert evidence_cols["candidate_id"]["notnull"] is False, "candidate_id must be nullable for baseline runs"
        assert "line_coverage_delta" in evidence_cols
        assert "branch_coverage_delta" in evidence_cols
        assert "line_coverage" in evidence_cols
        assert "branch_coverage" in evidence_cols
        assert "duration_sec" in evidence_cols
        assert "timed_out" in evidence_cols

        # 2. test_candidates checks
        candidate_cols = _get_table_columns(conn, "test_candidates")
        assert "candidate_id" in candidate_cols and candidate_cols["candidate_id"]["pk"]
        assert "target_symbol" in candidate_cols
        assert "retry_count" in candidate_cols
        assert candidate_cols["retry_count"]["dflt_value"] in ("0", 0)

        # 3. failure_diagnoses checks
        diag_cols = _get_table_columns(conn, "failure_diagnoses")
        assert "diagnosis_id" in diag_cols and diag_cols["diagnosis_id"]["pk"]
        assert "canonical_category" in diag_cols
        assert "triage_engine" in diag_cols
        assert "is_application_bug" in diag_cols
    finally:
        conn.close()


# ============================================================================
# 3. Foreign Key Enforcement & Orphan Rejection Tests
# ============================================================================

def test_orphan_checkpoints_rejected() -> None:
    """Verifies that inserting a checkpoint with a non-existent run_id is rejected."""
    conn = init_database(":memory:")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO checkpoints (checkpoint_id, run_id, step_index, node_name, state_payload, created_at) "
                "VALUES ('chk-1', 'nonexistent-run', 0, 'ingest_node', '{}', '2026-01-01T00:00:00Z');"
            )
    finally:
        conn.close()


def test_orphan_events_rejected() -> None:
    """Verifies that inserting an event with a non-existent run_id is rejected."""
    conn = init_database(":memory:")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (event_id, run_id, event_type, node_name, payload, emitted_at) "
                "VALUES ('evt-1', 'nonexistent-run', 'RUN_STARTED', 'init', '{}', '2026-01-01T00:00:00Z');"
            )
    finally:
        conn.close()


def test_orphan_candidates_rejected() -> None:
    """Verifies that inserting a candidate with a non-existent run_id is rejected."""
    conn = init_database(":memory:")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
                "VALUES ('cand-1', 'nonexistent-run', 'foo', 'tests/test_foo.py', 'def test(): pass', 'PASSED', '2026-01-01T00:00:00Z');"
            )
    finally:
        conn.close()


def test_orphan_evidence_rejected() -> None:
    """Verifies that inserting evidence with a non-existent run_id or candidate_id is rejected."""
    conn = init_database(":memory:")
    try:
        # Non-existent run_id
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
                "VALUES ('ev-1', 'nonexistent-run', NULL, 0, 1.0, '2026-01-01T00:00:00Z');"
            )

        # Create valid run
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )

        # Non-existent candidate_id (when candidate_id is provided)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
                "VALUES ('ev-2', 'run-1', 'nonexistent-candidate', 0, 1.0, '2026-01-01T00:00:00Z');"
            )
    finally:
        conn.close()


def test_orphan_diagnosis_rejected() -> None:
    """Verifies that inserting a diagnosis with a non-existent evidence_id or run_id is rejected."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO failure_diagnoses (diagnosis_id, run_id, evidence_id, canonical_category, confidence, triage_engine, explanation, created_at) "
                "VALUES ('diag-1', 'run-1', 'nonexistent-evidence', 'APPLICATION_BUG', 1.0, 'DETERMINISTIC_RULE', 'Bug', '2026-01-01T00:00:00Z');"
            )
    finally:
        conn.close()


# ============================================================================
# 4. Cascading Deletion Tests (Run, Candidate, Evidence)
# ============================================================================

def test_run_level_cascading_deletion() -> None:
    """Verifies that deleting a run row cascades across all five child tables."""
    conn = init_database(":memory:")
    try:
        # Populate full execution hierarchy
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        conn.execute(
            "INSERT INTO checkpoints (checkpoint_id, run_id, step_index, node_name, state_payload, created_at) "
            "VALUES ('chk-1', 'run-1', 0, 'ingest', '{}', '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO events (event_id, run_id, event_type, node_name, payload, emitted_at) "
            "VALUES ('evt-1', 'run-1', 'RUN_STARTED', 'init', '{}', '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
            "VALUES ('cand-1', 'run-1', 'sym', 'test.py', 'code', 'PASSED', '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
            "VALUES ('ev-1', 'run-1', 'cand-1', 1, 0.5, '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO failure_diagnoses (diagnosis_id, run_id, evidence_id, canonical_category, confidence, triage_engine, explanation, created_at) "
            "VALUES ('diag-1', 'run-1', 'ev-1', 'APPLICATION_BUG', 1.0, 'DETERMINISTIC_RULE', 'Bug', '2026-01-01T00:00:00Z');"
        )

        # Verify all records inserted
        assert conn.execute("SELECT COUNT(*) FROM checkpoints WHERE run_id = 'run-1';").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events WHERE run_id = 'run-1';").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM test_candidates WHERE run_id = 'run-1';").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM execution_evidence WHERE run_id = 'run-1';").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM failure_diagnoses WHERE run_id = 'run-1';").fetchone()[0] == 1

        # Delete parent run
        conn.execute("DELETE FROM runs WHERE run_id = 'run-1';")

        # Verify all child tables cleanly cascaded to 0
        assert conn.execute("SELECT COUNT(*) FROM checkpoints;").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events;").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM test_candidates;").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM execution_evidence;").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM failure_diagnoses;").fetchone()[0] == 0
    finally:
        conn.close()


def test_candidate_level_cascading_deletion() -> None:
    """Verifies that deleting a candidate cascades to its execution evidence and failure diagnoses."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        # Candidate 1 with evidence and diagnosis
        conn.execute(
            "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
            "VALUES ('cand-1', 'run-1', 'sym1', 'test1.py', 'code1', 'PASSED', '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
            "VALUES ('ev-1', 'run-1', 'cand-1', 1, 0.5, '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO failure_diagnoses (diagnosis_id, run_id, evidence_id, canonical_category, confidence, triage_engine, explanation, created_at) "
            "VALUES ('diag-1', 'run-1', 'ev-1', 'APPLICATION_BUG', 1.0, 'DETERMINISTIC_RULE', 'Bug', '2026-01-01T00:00:00Z');"
        )

        # Candidate 2 with separate evidence and diagnosis
        conn.execute(
            "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
            "VALUES ('cand-2', 'run-1', 'sym2', 'test2.py', 'code2', 'PASSED', '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
            "VALUES ('ev-2', 'run-1', 'cand-2', 1, 0.7, '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO failure_diagnoses (diagnosis_id, run_id, evidence_id, canonical_category, confidence, triage_engine, explanation, created_at) "
            "VALUES ('diag-2', 'run-1', 'ev-2', 'TEST_OUTDATED', 0.9, 'COGNITIVE_LLM', 'Outdated', '2026-01-01T00:00:00Z');"
        )

        # Delete cand-1
        conn.execute("DELETE FROM test_candidates WHERE candidate_id = 'cand-1';")

        # Verify cand-1 artifacts are deleted
        assert conn.execute("SELECT COUNT(*) FROM execution_evidence WHERE candidate_id = 'cand-1';").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM failure_diagnoses WHERE diagnosis_id = 'diag-1';").fetchone()[0] == 0

        # Verify cand-2 artifacts remain intact
        assert conn.execute("SELECT COUNT(*) FROM execution_evidence WHERE candidate_id = 'cand-2';").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM failure_diagnoses WHERE diagnosis_id = 'diag-2';").fetchone()[0] == 1
    finally:
        conn.close()


def test_evidence_level_cascading_deletion() -> None:
    """Verifies that deleting an execution evidence cascades to its failure diagnosis while candidate is preserved."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        conn.execute(
            "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
            "VALUES ('cand-1', 'run-1', 'sym1', 'test1.py', 'code1', 'PASSED', '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
            "VALUES ('ev-1', 'run-1', 'cand-1', 1, 0.5, '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO failure_diagnoses (diagnosis_id, run_id, evidence_id, canonical_category, confidence, triage_engine, explanation, created_at) "
            "VALUES ('diag-1', 'run-1', 'ev-1', 'APPLICATION_BUG', 1.0, 'DETERMINISTIC_RULE', 'Bug', '2026-01-01T00:00:00Z');"
        )

        # Delete evidence
        conn.execute("DELETE FROM execution_evidence WHERE evidence_id = 'ev-1';")

        # Diagnosis must be cascade-deleted
        assert conn.execute("SELECT COUNT(*) FROM failure_diagnoses WHERE diagnosis_id = 'diag-1';").fetchone()[0] == 0
        # Candidate must remain preserved
        assert conn.execute("SELECT COUNT(*) FROM test_candidates WHERE candidate_id = 'cand-1';").fetchone()[0] == 1
    finally:
        conn.close()


# ============================================================================
# 5. Baseline Evidence & Multi-Evidence Tests
# ============================================================================

def test_baseline_evidence_nullable_candidate_id() -> None:
    """Verifies that execution_evidence accepts candidate_id = NULL for baseline regression evidence."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )

        # Insert baseline evidence (no candidate)
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, line_coverage, branch_coverage, created_at) "
            "VALUES ('ev-base-1', 'run-1', NULL, 0, 2.5, 75.0, 70.0, '2026-01-01T00:00:00Z');"
        )

        # Insert second baseline evidence (e.g. repeated baseline check)
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, line_coverage, branch_coverage, created_at) "
            "VALUES ('ev-base-2', 'run-1', NULL, 0, 2.3, 75.0, 70.0, '2026-01-01T00:00:05Z');"
        )

        rows = conn.execute(
            "SELECT evidence_id, candidate_id, exit_code FROM execution_evidence WHERE candidate_id IS NULL;"
        ).fetchall()

        assert len(rows) == 2
        assert rows[0]["candidate_id"] is None
        assert rows[1]["candidate_id"] is None
    finally:
        conn.close()


def test_multiple_evidence_rows_per_candidate_allowed() -> None:
    """Verifies that multiple evidence rows for the same candidate are permitted by schema."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        conn.execute(
            "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
            "VALUES ('cand-1', 'run-1', 'sym', 'test.py', 'code', 'PASSED', '2026-01-01T00:00:00Z');"
        )

        # Execution attempt 1
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
            "VALUES ('ev-attempt-1', 'run-1', 'cand-1', 1, 0.4, '2026-01-01T00:00:01Z');"
        )
        # Execution attempt 2
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
            "VALUES ('ev-attempt-2', 'run-1', 'cand-1', 0, 0.5, '2026-01-01T00:00:05Z');"
        )

        ev_count = conn.execute(
            "SELECT COUNT(*) FROM execution_evidence WHERE candidate_id = 'cand-1';"
        ).fetchone()[0]
        assert ev_count == 2, "Multiple execution evidence records must be allowed per candidate"
    finally:
        conn.close()


# ============================================================================
# 6. Composite Foreign Key / Cross-Run Reference Rejection Tests
# ============================================================================

def test_cross_run_candidate_reference_rejected() -> None:
    """Verifies that execution_evidence cannot reference a candidate belonging to a different run."""
    conn = init_database(":memory:")
    try:
        # Create Run A and Candidate A
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-A', '/repoA', 'cA', 'bA', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        conn.execute(
            "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
            "VALUES ('cand-A', 'run-A', 'symA', 'testA.py', 'codeA', 'PASSED', '2026-01-01T00:00:00Z');"
        )

        # Create Run B
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-B', '/repoB', 'cB', 'bB', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )

        # Attempt to insert evidence in Run B referencing Candidate A -> Must be rejected by composite FK!
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
                "VALUES ('ev-cross', 'run-B', 'cand-A', 0, 1.0, '2026-01-01T00:00:00Z');"
            )
    finally:
        conn.close()


def test_cross_run_evidence_reference_rejected() -> None:
    """Verifies that failure_diagnoses cannot reference evidence belonging to a different run."""
    conn = init_database(":memory:")
    try:
        # Create Run A, Candidate A, Evidence A
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-A', '/repoA', 'cA', 'bA', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        conn.execute(
            "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
            "VALUES ('cand-A', 'run-A', 'symA', 'testA.py', 'codeA', 'PASSED', '2026-01-01T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO execution_evidence (evidence_id, run_id, candidate_id, exit_code, duration_sec, created_at) "
            "VALUES ('ev-A', 'run-A', 'cand-A', 1, 0.5, '2026-01-01T00:00:00Z');"
        )

        # Create Run B
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-B', '/repoB', 'cB', 'bB', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )

        # Attempt to insert diagnosis in Run B referencing Evidence A -> Must be rejected by composite FK!
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO failure_diagnoses (diagnosis_id, run_id, evidence_id, canonical_category, confidence, triage_engine, explanation, created_at) "
                "VALUES ('diag-cross', 'run-B', 'ev-A', 'APPLICATION_BUG', 1.0, 'DETERMINISTIC_RULE', 'Bug', '2026-01-01T00:00:00Z');"
            )
    finally:
        conn.close()


# ============================================================================
# 7. Idempotence & Data Preservation Tests
# ============================================================================

def test_init_db_idempotent_and_preserves_data() -> None:
    """Verifies that calling init_db repeatedly does not raise errors and preserves existing rows."""
    conn = get_connection(":memory:")
    try:
        # First call
        init_db(conn)

        # Insert baseline data
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        conn.execute(
            "INSERT INTO test_candidates (candidate_id, run_id, target_symbol, test_file_path, candidate_code, validation_status, created_at) "
            "VALUES ('cand-1', 'run-1', 'sym', 'test.py', 'code', 'PASSED', '2026-01-01T00:00:00Z');"
        )

        # Second call to init_db
        init_db(conn)

        # Third call to init_db
        init_db(conn)

        # Assert data is preserved
        run_row = conn.execute("SELECT run_id, current_commit FROM runs WHERE run_id = 'run-1';").fetchone()
        assert run_row["run_id"] == "run-1"
        assert run_row["current_commit"] == "c1"

        cand_row = conn.execute("SELECT candidate_id, target_symbol FROM test_candidates WHERE candidate_id = 'cand-1';").fetchone()
        assert cand_row["candidate_id"] == "cand-1"
        assert cand_row["target_symbol"] == "sym"
    finally:
        conn.close()


# ============================================================================
# 8. Append-Only Event Store & Telemetry Tests (Slice 1.6.2B)
# ============================================================================

class MockWorkflowEventType(str, Enum):
    """Sample enum for testing event_type enum handling."""
    RUN_STARTED = "RUN_STARTED"
    NODE_COMPLETED = "NODE_COMPLETED"
    RUN_FAILED = "RUN_FAILED"


def test_event_record_fields_and_immutability() -> None:
    """Verifies EventRecord fields, defaults, and immutability."""
    now = datetime.now(timezone.utc)
    rec = EventRecord(
        event_id="evt-123",
        run_id="run-456",
        event_type="STAGE_START",
        node_name="ingest_node",
        payload={"symbols_count": 42},
        emitted_at=now,
    )

    assert rec.event_id == "evt-123"
    assert rec.run_id == "run-456"
    assert rec.event_type == "STAGE_START"
    assert rec.node_name == "ingest_node"
    assert rec.payload == {"symbols_count": 42}
    assert rec.emitted_at == now

    # Verify frozen immutability
    with pytest.raises(ValidationError):
        setattr(rec, "event_type", "MUTATED")

    with pytest.raises(ValidationError):
        setattr(rec, "payload", {})


def test_emit_event_and_get_events_basic() -> None:
    """Verifies standard emission and retrieval of events."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        store = SQLiteEventStore(conn)

        rec = store.emit_event(
            run_id="run-1",
            event_type="RUN_STARTED",
            payload={"repo": "/repo", "head": "c1"},
            node_name="init",
        )

        assert rec.run_id == "run-1"
        assert rec.event_type == "RUN_STARTED"
        assert rec.node_name == "init"
        assert rec.payload == {"repo": "/repo", "head": "c1"}
        assert rec.event_id is not None
        assert rec.emitted_at is not None

        # Retrieve events
        events = store.get_events("run-1")
        assert len(events) == 1
        retrieved = events[0]
        assert retrieved.event_id == rec.event_id
        assert retrieved.run_id == "run-1"
        assert retrieved.event_type == "RUN_STARTED"
        assert retrieved.node_name == "init"
        assert retrieved.payload == {"repo": "/repo", "head": "c1"}
        assert retrieved.emitted_at == rec.emitted_at
    finally:
        conn.close()


def test_emit_event_generates_unique_event_id() -> None:
    """Verifies that omitted event_id generates unique UUID4 values."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        store = SQLiteEventStore(conn)

        rec1 = store.emit_event(run_id="run-1", event_type="EVT_1", payload={})
        rec2 = store.emit_event(run_id="run-1", event_type="EVT_2", payload={})

        assert rec1.event_id != rec2.event_id
        assert len(rec1.event_id) >= 32
        assert len(rec2.event_id) >= 32
    finally:
        conn.close()


def test_emit_event_supplied_event_id_preservation() -> None:
    """Verifies that explicitly supplied event_id is preserved exactly."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        store = SQLiteEventStore(conn)

        custom_id = "custom-uuid-0000-1111-2222"
        rec = store.emit_event(
            run_id="run-1",
            event_type="CUSTOM_EVENT",
            payload={"key": "val"},
            event_id=custom_id,
        )

        assert rec.event_id == custom_id
        retrieved = store.get_events("run-1")[0]
        assert retrieved.event_id == custom_id
    finally:
        conn.close()


def test_event_payload_json_roundtrip_nested_and_scalars() -> None:
    """Verifies JSON payload round-trip fidelity for nested dicts, lists, and scalar types."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        store = SQLiteEventStore(conn)

        test_payloads: List[Any] = [
            # 1. Complex nested dict
            {"nested": {"inner_list": [1, 2, "3", {"deep": True}]}, "null_val": None, "pi": 3.14159},
            # 2. List payload
            ["alpha", 123, {"flag": False}, None],
            # 3. Scalar string
            "plain string payload",
            # 4. Scalar integer
            987654321,
            # 5. Scalar float
            42.42,
            # 6. Scalar boolean
            False,
        ]

        for idx, payload in enumerate(test_payloads):
            store.emit_event(
                run_id="run-1",
                event_type=f"PAYLOAD_TEST_{idx}",
                payload=payload,
            )

        events = store.get_events("run-1")
        assert len(events) == len(test_payloads)

        for idx, expected_payload in enumerate(test_payloads):
            assert events[idx].payload == expected_payload, f"Payload mismatch at index {idx}"
    finally:
        conn.close()


def test_event_ordering_chronological_with_tiebreak() -> None:
    """Verifies events are returned in chronological order with deterministic insertion tie-breaking."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        store = SQLiteEventStore(conn)

        t1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 1, 10, 5, 0, tzinfo=timezone.utc)
        t_same = datetime(2026, 1, 1, 10, 10, 0, tzinfo=timezone.utc)

        # Emit in non-chronological order to test SQL ordering
        store.emit_event(run_id="run-1", event_type="SECOND", payload={}, emitted_at=t2)
        store.emit_event(run_id="run-1", event_type="FIRST", payload={}, emitted_at=t1)
        # Emit two events with the exact same timestamp to verify insertion order tie-breaking
        store.emit_event(run_id="run-1", event_type="SAME_TIME_A", payload={"seq": 1}, emitted_at=t_same)
        store.emit_event(run_id="run-1", event_type="SAME_TIME_B", payload={"seq": 2}, emitted_at=t_same)

        events = store.get_events("run-1")
        assert len(events) == 4
        assert [e.event_type for e in events] == ["FIRST", "SECOND", "SAME_TIME_A", "SAME_TIME_B"]
    finally:
        conn.close()


def test_event_type_filtering_string_and_enum() -> None:
    """Verifies filtering events by event_type using both string and Enum representations."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        store = SQLiteEventStore(conn)

        store.emit_event(run_id="run-1", event_type=MockWorkflowEventType.RUN_STARTED, payload={})
        store.emit_event(run_id="run-1", event_type=MockWorkflowEventType.NODE_COMPLETED, payload={"node": "A"})
        store.emit_event(run_id="run-1", event_type=MockWorkflowEventType.NODE_COMPLETED, payload={"node": "B"})
        store.emit_event(run_id="run-1", event_type=MockWorkflowEventType.RUN_FAILED, payload={"err": "fail"})

        # Filter by Enum
        node_events_enum = store.get_events("run-1", event_type=MockWorkflowEventType.NODE_COMPLETED)
        assert len(node_events_enum) == 2
        assert all(e.event_type == "NODE_COMPLETED" for e in node_events_enum)

        # Filter by String
        node_events_str = store.get_events("run-1", event_type="NODE_COMPLETED")
        assert len(node_events_str) == 2
        assert node_events_str[0].payload == {"node": "A"}
        assert node_events_str[1].payload == {"node": "B"}

        # Filter with no matching type
        empty_filter = store.get_events("run-1", event_type="NON_EXISTENT_TYPE")
        assert empty_filter == []
    finally:
        conn.close()


def test_run_isolation() -> None:
    """Verifies that events from one run never bleed into or return during queries for another run."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-A', '/repoA', 'cA', 'bA', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-B', '/repoB', 'cB', 'bB', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        store = SQLiteEventStore(conn)

        store.emit_event(run_id="run-A", event_type="EVT_A1", payload={"owner": "A"})
        store.emit_event(run_id="run-A", event_type="EVT_A2", payload={"owner": "A"})
        store.emit_event(run_id="run-B", event_type="EVT_B1", payload={"owner": "B"})

        events_a = store.get_events("run-A")
        events_b = store.get_events("run-B")

        assert len(events_a) == 2
        assert all(e.run_id == "run-A" for e in events_a)
        assert [e.event_type for e in events_a] == ["EVT_A1", "EVT_A2"]

        assert len(events_b) == 1
        assert events_b[0].run_id == "run-B"
        assert events_b[0].event_type == "EVT_B1"
    finally:
        conn.close()


def test_empty_result_behavior() -> None:
    """Verifies that querying a run with zero events returns an empty list, not None or exception."""
    conn = init_database(":memory:")
    try:
        store = SQLiteEventStore(conn)
        events = store.get_events("nonexistent-run-id")
        assert events == []
        assert isinstance(events, list)
    finally:
        conn.close()


def test_foreign_key_rejection_unknown_run_id() -> None:
    """Verifies that emitting an event for a non-existent run raises sqlite3.IntegrityError."""
    conn = init_database(":memory:")
    try:
        store = SQLiteEventStore(conn)
        with pytest.raises(sqlite3.IntegrityError):
            store.emit_event(
                run_id="orphan-run-does-not-exist",
                event_type="SHOULD_FAIL",
                payload={},
            )
    finally:
        conn.close()


def test_append_only_surface_no_mutation_methods() -> None:
    """Verifies that SQLiteEventStore exposes strictly append and read methods, with zero mutation APIs."""
    conn = init_database(":memory:")
    try:
        store = SQLiteEventStore(conn)

        # Must have emit_event and get_events
        assert callable(getattr(store, "emit_event", None))
        assert callable(getattr(store, "get_events", None))

        # Prohibited mutation or deletion methods
        prohibited_methods = [
            "update_event",
            "delete_event",
            "delete_events",
            "purge_events",
            "clear_events",
            "modify_event",
            "remove_event",
            "drop_events",
        ]
        for method_name in prohibited_methods:
            assert not hasattr(store, method_name), f"Prohibited mutation method '{method_name}' found on event store"
    finally:
        conn.close()


def test_validation_empty_run_id_and_event_type() -> None:
    """Verifies that emit_event and get_events validate parameters and reject empty strings."""
    conn = init_database(":memory:")
    try:
        store = SQLiteEventStore(conn)

        with pytest.raises(ValueError, match="run_id must be a non-empty string"):
            store.emit_event(run_id="", event_type="TYPE", payload={})

        with pytest.raises(ValueError, match="run_id must be a non-empty string"):
            store.emit_event(run_id="   ", event_type="TYPE", payload={})

        with pytest.raises(ValueError, match="event_type must be a non-empty string"):
            store.emit_event(run_id="run-1", event_type="", payload={})

        with pytest.raises(ValueError, match="event_id must not be empty"):
            store.emit_event(run_id="run-1", event_type="TYPE", payload={}, event_id="   ")

        with pytest.raises(ValueError, match="run_id must be a non-empty string"):
            store.get_events(run_id="")
    finally:
        conn.close()


def test_idempotent_schema_init_after_events() -> None:
    """Verifies that re-running init_db preserves existing events in SQLiteEventStore."""
    conn = init_database(":memory:")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
            "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
        )
        store = SQLiteEventStore(conn)
        store.emit_event(run_id="run-1", event_type="PERSISTED_EVT", payload={"data": 123})

        # Re-initialize schema
        init_db(conn)

        # Retrieve and verify
        events = store.get_events("run-1")
        assert len(events) == 1
        assert events[0].event_type == "PERSISTED_EVT"
        assert events[0].payload == {"data": 123}
    finally:
        conn.close()


def test_event_store_instantiation_with_path_and_connection(tmp_path: Path) -> None:
    """Verifies that SQLiteEventStore can be instantiated via existing connection or disk path."""
    db_path = tmp_path / "event_store.db"
    conn = init_database(db_path)
    conn.execute(
        "INSERT INTO runs (run_id, repo_path, current_commit, base_commit, branch_name, started_at, final_status) "
        "VALUES ('run-1', '/repo', 'c1', 'b1', 'main', '2026-01-01T00:00:00Z', 'INITIALIZED');"
    )
    conn.commit()
    conn.close()

    # Instantiate store passing the Path directly
    store = SQLiteEventStore(db_path)
    try:
        rec = store.emit_event(run_id="run-1", event_type="DISK_EVENT", payload={"on_disk": True})
        assert rec.event_type == "DISK_EVENT"

        events = store.get_events("run-1")
        assert len(events) == 1
        assert events[0].payload == {"on_disk": True}
    finally:
        store.connection.close()
