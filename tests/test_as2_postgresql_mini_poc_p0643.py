"""P0.6.43 PostgreSQL Mini-POC Phase 1 using SQLite as a dev backend.

These tests exercise SQL semantics that are required by the future PostgreSQL
backend ADR while staying intentionally local and dependency-free. SQLite is
used only as a dev backend for executable proof of SQL shape, transaction
rollback, and polling-claim behavior. PostgreSQL-only concerns are represented
as explicit skipped tests with reasons recorded in the P0.6.43 report.
"""

from __future__ import annotations

import sqlite3

import pytest


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE idempotency_records (
            key_hash TEXT PRIMARY KEY,
            correlation_id TEXT NOT NULL,
            prepared_inputs_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            result_ref TEXT,
            reason_code TEXT,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE audit_outbox (
            event_id TEXT PRIMARY KEY,
            idempotency_key_hash TEXT NOT NULL,
            outbox_sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            relay_status TEXT NOT NULL DEFAULT 'pending',
            claimed_at INTEGER,
            relay_worker_id TEXT,
            FOREIGN KEY (idempotency_key_hash)
                REFERENCES idempotency_records(key_hash)
        );

        CREATE INDEX idx_audit_outbox_polling_claim
            ON audit_outbox(relay_status, outbox_sequence);
        CREATE INDEX idx_audit_outbox_claimed_at
            ON audit_outbox(claimed_at);
        """
    )
    return conn


def test_reserve_if_absent_uses_insert_on_conflict_do_nothing_idempotently() -> None:
    """reserve_if_absent shape: INSERT ... ON CONFLICT DO NOTHING."""

    conn = _connect()

    first = conn.execute(
        """
        INSERT INTO idempotency_records (
            key_hash, correlation_id, prepared_inputs_hash, state, updated_at
        )
        VALUES (?, ?, ?, 'in_progress', ?)
        ON CONFLICT(key_hash) DO NOTHING
        RETURNING key_hash, state
        """,
        ("key-1", "corr-1", "hash-1", 10),
    ).fetchall()

    duplicate = conn.execute(
        """
        INSERT INTO idempotency_records (
            key_hash, correlation_id, prepared_inputs_hash, state, updated_at
        )
        VALUES (?, ?, ?, 'in_progress', ?)
        ON CONFLICT(key_hash) DO NOTHING
        RETURNING key_hash, state
        """,
        ("key-1", "corr-1", "hash-1", 11),
    ).fetchall()

    assert [dict(row) for row in first] == [
        {"key_hash": "key-1", "state": "in_progress"}
    ]
    assert duplicate == []
    assert conn.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0] == 1
    row = conn.execute(
        "SELECT state, updated_at FROM idempotency_records WHERE key_hash = ?",
        ("key-1",),
    ).fetchone()
    assert (row["state"], row["updated_at"]) == ("in_progress", 10)


def test_conditional_complete_and_fail_use_update_where_state_returning() -> None:
    """complete_if_state / fail_if_state shape: UPDATE ... WHERE state = expected RETURNING."""

    conn = _connect()
    conn.execute(
        """
        INSERT INTO idempotency_records (
            key_hash, correlation_id, prepared_inputs_hash, state, updated_at
        )
        VALUES ('key-2', 'corr-2', 'hash-2', 'in_progress', 20)
        """
    )

    completed = conn.execute(
        """
        UPDATE idempotency_records
        SET state = 'completed', result_ref = ?, updated_at = ?
        WHERE key_hash = ? AND state = 'in_progress'
        RETURNING key_hash, state, result_ref
        """,
        ('{"snapshot_hash":"snap-1"}', 21, "key-2"),
    ).fetchall()

    rejected_fail = conn.execute(
        """
        UPDATE idempotency_records
        SET state = 'failed', reason_code = 'projection_failed', updated_at = ?
        WHERE key_hash = ? AND state = 'in_progress'
        RETURNING key_hash, state, reason_code
        """,
        (22, "key-2"),
    ).fetchall()

    assert [dict(row) for row in completed] == [
        {
            "key_hash": "key-2",
            "state": "completed",
            "result_ref": '{"snapshot_hash":"snap-1"}',
        }
    ]
    assert rejected_fail == []
    assert conn.execute("SELECT state FROM idempotency_records").fetchone()[0] == "completed"


def test_local_transaction_rolls_back_idempotency_update_when_outbox_insert_fails() -> None:
    """idempotency update + audit outbox insert must be one rollback-safe local unit."""

    conn = _connect()
    conn.execute(
        """
        INSERT INTO idempotency_records (
            key_hash, correlation_id, prepared_inputs_hash, state, updated_at
        )
        VALUES ('key-3', 'corr-3', 'hash-3', 'in_progress', 30)
        """
    )
    conn.execute(
        """
        INSERT INTO audit_outbox (
            event_id, idempotency_key_hash, outbox_sequence, event_type
        )
        VALUES ('event-dup', 'key-3', 1, 'seed')
        """
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            updated = conn.execute(
                """
                UPDATE idempotency_records
                SET state = 'completed', result_ref = ?, updated_at = ?
                WHERE key_hash = ? AND state = 'in_progress'
                RETURNING key_hash
                """,
                ('{"snapshot_hash":"snap-rollback"}', 31, "key-3"),
            ).fetchall()
            assert len(updated) == 1
            conn.execute(
                """
                INSERT INTO audit_outbox (
                    event_id, idempotency_key_hash, outbox_sequence, event_type
                )
                VALUES ('event-dup', 'key-3', 2, 'projection_completed')
                """
            )

    assert conn.execute("SELECT state FROM idempotency_records").fetchone()[0] == "in_progress"
    assert conn.execute("SELECT COUNT(*) FROM audit_outbox").fetchone()[0] == 1


def test_polling_claim_uses_single_round_trip_update_with_subquery_returning() -> None:
    """Polling claim shape: UPDATE ... WHERE id IN (SELECT ... LIMIT N) RETURNING."""

    conn = _connect()
    conn.execute(
        """
        INSERT INTO idempotency_records (
            key_hash, correlation_id, prepared_inputs_hash, state, updated_at
        )
        VALUES ('key-4', 'corr-4', 'hash-4', 'completed', 40)
        """
    )
    for seq in range(1, 6):
        conn.execute(
            """
            INSERT INTO audit_outbox (
                event_id, idempotency_key_hash, outbox_sequence, event_type
            )
            VALUES (?, 'key-4', ?, 'audit_event')
            """,
            (f"event-{seq}", seq),
        )

    claimed = conn.execute(
        """
        UPDATE audit_outbox
        SET relay_status = 'claimed',
            claimed_at = ?,
            relay_worker_id = ?
        WHERE event_id IN (
            SELECT event_id
            FROM audit_outbox
            WHERE relay_status = 'pending'
            ORDER BY outbox_sequence
            LIMIT ?
        )
        RETURNING event_id, relay_status, relay_worker_id, outbox_sequence
        """,
        (50, "worker-1", 3),
    ).fetchall()

    assert [row["event_id"] for row in claimed] == ["event-1", "event-2", "event-3"]
    assert {row["relay_status"] for row in claimed} == {"claimed"}
    assert {row["relay_worker_id"] for row in claimed} == {"worker-1"}
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_outbox WHERE relay_status = 'pending'"
    ).fetchone()[0] == 2


@pytest.mark.skip(reason="P0.6.43 SQLite dev backend limitation: PgBouncer SET LOCAL isolation requires PostgreSQL + PgBouncer/Odyssey.")
def test_pgbouncer_set_local_isolation_requires_real_postgresql_pool() -> None:
    pass


@pytest.mark.skip(reason="P0.6.43 SQLite dev backend limitation: CDC/logical replication requires PostgreSQL wal_level=logical and replication slots.")
def test_cdc_logical_replication_requires_real_postgresql() -> None:
    pass


@pytest.mark.skip(reason="P0.6.43 SQLite dev backend limitation: p99 concurrent-load validation requires the target PostgreSQL environment.")
def test_p99_concurrent_load_requires_target_postgresql_environment() -> None:
    pass
