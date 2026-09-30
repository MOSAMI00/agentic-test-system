"""
SQLite database connection management and DDL schema initialization.
Adheres strictly to Stage 3 Section 4.4.7 (Listing 4.4) and Slice 1.6.2A contracts.
"""

from pathlib import Path
import sqlite3
from typing import Union


SCHEMA_DDL = """
-- 1. Pipeline Execution Runs
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    current_commit TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    route_selected TEXT,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    final_status TEXT NOT NULL,
    error_message TEXT
);

-- 2. State Checkpoints for Crash Recovery (NFR-07)
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    node_name TEXT NOT NULL,
    state_payload TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

-- 3. Append-Only Audit Event Log (NFR-05)
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    node_name TEXT,
    payload TEXT NOT NULL,
    emitted_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

-- 4. Test Candidates & Validation States
CREATE TABLE IF NOT EXISTS test_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    target_symbol TEXT NOT NULL,
    test_file_path TEXT NOT NULL,
    candidate_code TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    quarantine_reason TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    UNIQUE (candidate_id, run_id)
);

-- 5. Execution Evidence & Coverage Telemetry
CREATE TABLE IF NOT EXISTS execution_evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    candidate_id TEXT,
    exit_code INTEGER NOT NULL,
    stdout TEXT,
    stderr TEXT,
    duration_sec REAL NOT NULL,
    timed_out BOOLEAN NOT NULL DEFAULT 0,
    line_coverage REAL,
    branch_coverage REAL,
    line_coverage_delta REAL,
    branch_coverage_delta REAL,
    traceback TEXT,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id, run_id) REFERENCES test_candidates(candidate_id, run_id) ON DELETE CASCADE,
    UNIQUE (evidence_id, run_id)
);

-- 6. Failure Diagnoses & Triage Records
CREATE TABLE IF NOT EXISTS failure_diagnoses (
    diagnosis_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    canonical_category TEXT NOT NULL,
    confidence REAL NOT NULL,
    triage_engine TEXT NOT NULL,
    explanation TEXT NOT NULL,
    is_application_bug BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id, run_id) REFERENCES execution_evidence(evidence_id, run_id) ON DELETE CASCADE
);

-- Secondary Indices for High-Performance Querying (NFR-03, NFR-05)
CREATE INDEX IF NOT EXISTS idx_checkpoints_run_id ON checkpoints(run_id);
CREATE INDEX IF NOT EXISTS idx_events_run_id_type ON events(run_id, event_type);
CREATE INDEX IF NOT EXISTS idx_candidates_run_status ON test_candidates(run_id, validation_status);
CREATE INDEX IF NOT EXISTS idx_evidence_run_id ON execution_evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_diagnoses_run_cat ON failure_diagnoses(run_id, canonical_category);
"""


def _is_memory_db(db_path: Union[str, Path]) -> bool:
    """Determine whether the target database is an in-memory database."""
    if isinstance(db_path, Path):
        return False
    path_str = str(db_path).strip()
    if path_str == ":memory:":
        return True
    if path_str.startswith("file:") and ("mode=memory" in path_str or ":memory:" in path_str):
        return True
    return False


def get_connection(db_path: Union[str, Path] = ":memory:") -> sqlite3.Connection:
    """
    Synchronous SQLite connection factory.

    Contract:
    - Executes PRAGMA foreign_keys = ON on every connection.
    - Sets PRAGMA busy_timeout = 5000.
    - Sets conn.row_factory = sqlite3.Row.
    - For on-disk databases:
      - Sets PRAGMA journal_mode = WAL;
      - Sets PRAGMA synchronous = FULL;
    - For :memory: databases:
      - Does not require WAL; accepts journal_mode = memory.
    """
    path_str = str(db_path) if isinstance(db_path, Path) else db_path
    is_uri = isinstance(path_str, str) and path_str.startswith("file:")
    conn = sqlite3.connect(database=path_str, uri=is_uri)
    conn.row_factory = sqlite3.Row

    # Foreign keys must be explicitly enabled on every SQLite connection
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")

    if _is_memory_db(db_path):
        # In-memory database: WAL mode is not supported by SQLite engine; native memory journal is retained
        pass
    else:
        # On-disk database: Configure WAL mode and synchronous=FULL for zero-loss power-failure durability
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = FULL;")

    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """
    Idempotent schema initialization creating all six tables and secondary indices.
    Safe to execute multiple times on an already initialized or populated database.
    """
    conn.executescript(SCHEMA_DDL)


def init_database(db_path: Union[str, Path] = ":memory:") -> sqlite3.Connection:
    """
    Convenience helper creating a connection and initializing the database schema.
    Returns the configured connection.
    """
    conn = get_connection(db_path)
    init_db(conn)
    return conn
