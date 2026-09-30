"""
Storage, persistence, and event-sourcing package for the Agentic Test Generation System.
"""

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
from agentic_test.storage.repository import (
    CheckpointRecord,
    RunRecord,
    SQLiteRepository,
)

__all__ = [
    "CheckpointRecord",
    "EventRecord",
    "RunRecord",
    "SCHEMA_DDL",
    "SQLiteEventStore",
    "SQLiteRepository",
    "get_connection",
    "init_database",
    "init_db",
]
