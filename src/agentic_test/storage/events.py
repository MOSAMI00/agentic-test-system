"""
Append-only event store and telemetry models for the Agentic Test Generation System.
Adheres strictly to Stage 3 Section 4.4.7 (Listing 4.4) and Slice 1.6.2B contracts.
"""

from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any, List, Optional, Union
import uuid

from pydantic import BaseModel, ConfigDict, Field

from agentic_test.storage.database import get_connection


class EventRecord(BaseModel):
    """
    Immutable representation of a persisted workflow telemetry event.
    Satisfies NFR-05 (Audit Logging).
    """
    model_config = ConfigDict(frozen=True)

    event_id: str
    run_id: str
    event_type: str
    node_name: Optional[str] = None
    payload: Any = Field(default_factory=dict)
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SQLiteEventStore:
    """
    Synchronous append-only event store for workflow telemetry and audit logging.
    Guarantees immutable event recording and strict run isolation.
    """

    def __init__(self, target: Union[sqlite3.Connection, str, Path] = ":memory:") -> None:
        if isinstance(target, sqlite3.Connection):
            self._conn = target
        else:
            self._conn = get_connection(target)

    @property
    def connection(self) -> sqlite3.Connection:
        """Access the underlying sqlite3 connection."""
        return self._conn

    def emit_event(
        self,
        run_id: str,
        event_type: Union[str, Enum],
        payload: Any,
        node_name: Optional[str] = None,
        emitted_at: Optional[datetime] = None,
        event_id: Optional[str] = None,
    ) -> EventRecord:
        """
        Appends an event to the immutable audit event log.

        Contract:
        - Validates required parameters (run_id, event_type).
        - Generates a UUID4 event_id if not supplied.
        - Persists timestamps in UTC ISO-8601 format.
        - Serializes payload deterministically to JSON text.
        - Executes parameterized INSERT and commits the transaction.
        - Returns an immutable EventRecord.
        """
        if not run_id or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        event_type_str = event_type.value if isinstance(event_type, Enum) else str(event_type)
        if not event_type_str or not event_type_str.strip():
            raise ValueError("event_type must be a non-empty string")

        if event_id is None:
            final_event_id = str(uuid.uuid4())
        else:
            if not event_id.strip():
                raise ValueError("event_id must not be empty when supplied")
            final_event_id = event_id.strip()

        if emitted_at is None:
            final_emitted_at = datetime.now(timezone.utc)
        else:
            final_emitted_at = (
                emitted_at if emitted_at.tzinfo is not None else emitted_at.replace(tzinfo=timezone.utc)
            )

        payload_json = json.dumps(payload, sort_keys=True)
        emitted_at_str = final_emitted_at.isoformat()

        sql = """
        INSERT INTO events (event_id, run_id, event_type, node_name, payload, emitted_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """
        self._conn.execute(
            sql,
            (
                final_event_id,
                run_id.strip(),
                event_type_str.strip(),
                node_name,
                payload_json,
                emitted_at_str,
            ),
        )
        self._conn.commit()

        return EventRecord(
            event_id=final_event_id,
            run_id=run_id.strip(),
            event_type=event_type_str.strip(),
            node_name=node_name,
            payload=payload,
            emitted_at=final_emitted_at,
        )

    def get_events(
        self,
        run_id: str,
        event_type: Optional[Union[str, Enum]] = None,
    ) -> List[EventRecord]:
        """
        Retrieves events for a given run in deterministic chronological order.

        Contract:
        - Requires non-empty run_id.
        - Never returns events from another run.
        - Supports optional event_type filtering.
        - Orders deterministically: ORDER BY emitted_at ASC, rowid ASC.
        - Deserializes payload JSON to restore original data structures.
        - Returns an empty list when no events match.
        """
        if not run_id or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        query = """
        SELECT event_id, run_id, event_type, node_name, payload, emitted_at
        FROM events
        WHERE run_id = ?
        """
        params: List[Any] = [run_id.strip()]

        if event_type is not None:
            event_type_str = event_type.value if isinstance(event_type, Enum) else str(event_type)
            query += " AND event_type = ?"
            params.append(event_type_str.strip())

        query += " ORDER BY emitted_at ASC, rowid ASC;"

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        records: List[EventRecord] = []
        for row in rows:
            payload_data = json.loads(row["payload"])
            emitted_dt = datetime.fromisoformat(row["emitted_at"])
            records.append(
                EventRecord(
                    event_id=row["event_id"],
                    run_id=row["run_id"],
                    event_type=row["event_type"],
                    node_name=row["node_name"],
                    payload=payload_data,
                    emitted_at=emitted_dt,
                )
            )

        return records
