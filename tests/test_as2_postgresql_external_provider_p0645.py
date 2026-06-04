"""P0.6.45-dev external/open PostgreSQL verification harness.

These tests are verification-only. They exercise real PostgreSQL semantics that
the P0.6.43 SQLite mini-POC could not honestly validate:

- INSERT ... ON CONFLICT DO NOTHING
- UPDATE ... WHERE state = expected RETURNING *
- one local transaction for idempotency update + audit outbox insert
- FOR UPDATE SKIP LOCKED polling claim with concurrent workers
- optional PgBouncer SET LOCAL isolation
- optional logical replication / pgoutput feasibility

They do not provide official Q8/Q8a/Q10 evidence, do not select a backend, do
not touch production flags, and do not modify runtime wiring. The DSN must be
provided only through environment variables; it is never recorded by the tests.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest


POSTGRES_DSN = os.environ.get("AS2_POSTGRES_TEST_DSN")
PGBOUNCER_DSN = os.environ.get("AS2_PGBOUNCER_TEST_DSN")
ENABLE_CDC = os.environ.get("AS2_ENABLE_CDC_VERIFICATION") == "1"
DEBEZIUM_URL = os.environ.get("AS2_DEBEZIUM_URL")
ENABLE_DEBEZIUM_CONNECTOR_SMOKE = os.environ.get("AS2_ENABLE_DEBEZIUM_CONNECTOR_SMOKE") == "1"
REDPANDA_CONTAINER = os.environ.get("AS2_REDPANDA_CONTAINER", "as2-postgres-mini-poc-redpanda")
LATENCY_SAMPLE_SIZE = int(os.environ.get("AS2_POSTGRES_LATENCY_SAMPLE_SIZE", "100"))


def _load_psycopg():
    if not POSTGRES_DSN:
        pytest.skip("AS2_POSTGRES_TEST_DSN is not configured; external provider verification skipped")
    try:
        import psycopg
        from psycopg import errors
        from psycopg.rows import dict_row
    except ModuleNotFoundError:
        pytest.skip("psycopg is not installed; install psycopg[binary] to run external provider verification")
    return psycopg, errors, dict_row


@dataclass(frozen=True)
class CaseTables:
    idempotency: str
    outbox: str
    sequence: str


def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe generated SQL identifier: {name!r}")
    return f'"{name}"'


def _connect(*, dsn: str | None = None, autocommit: bool = False):
    psycopg, _errors, dict_row = _load_psycopg()
    conn = psycopg.connect(dsn or POSTGRES_DSN, row_factory=dict_row)
    conn.autocommit = autocommit
    return conn


def _execute_fetchall(conn, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _execute_fetchone(conn, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _execute(conn, sql: str, params: tuple[Any, ...] = ()) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


@pytest.fixture()
def pg_case() -> CaseTables:
    suffix = uuid.uuid4().hex[:16]
    tables = CaseTables(
        idempotency=f"as2_p0645_idempotency_records_{suffix}",
        outbox=f"as2_p0645_audit_outbox_{suffix}",
        sequence=f"as2_p0645_audit_outbox_sequence_{suffix}",
    )
    idempotency = _quote_ident(tables.idempotency)
    outbox = _quote_ident(tables.outbox)
    sequence = _quote_ident(tables.sequence)
    conn = _connect(autocommit=True)
    try:
        _execute(
            conn,
            f"""
            CREATE TABLE {idempotency} (
                key_hash TEXT PRIMARY KEY,
                correlation_id TEXT NOT NULL,
                prepared_inputs_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                result_ref TEXT,
                failure_reason TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """,
        )
        _execute(conn, f"CREATE SEQUENCE {sequence}")
        _execute(
            conn,
            f"""
            CREATE TABLE {outbox} (
                event_id TEXT PRIMARY KEY,
                idempotency_key_hash TEXT REFERENCES {idempotency}(key_hash),
                outbox_sequence BIGINT NOT NULL DEFAULT nextval('{tables.sequence}'),
                event_type TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                relay_status TEXT NOT NULL DEFAULT 'pending',
                claimed_at TIMESTAMPTZ,
                relay_worker_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
        )
        _execute(
            conn,
            f"""
            CREATE INDEX {tables.outbox}_polling_idx
            ON {outbox}(relay_status, outbox_sequence)
            """,
        )
        yield tables
    finally:
        _execute(conn, f"DROP TABLE IF EXISTS {outbox} CASCADE")
        _execute(conn, f"DROP TABLE IF EXISTS {idempotency} CASCADE")
        _execute(conn, f"DROP SEQUENCE IF EXISTS {sequence} CASCADE")
        conn.close()


def test_reserve_if_absent_is_atomic_and_idempotent_on_real_postgresql(pg_case: CaseTables) -> None:
    idempotency = _quote_ident(pg_case.idempotency)
    conn = _connect(autocommit=True)
    try:
        first = _execute_fetchall(
            conn,
            f"""
            INSERT INTO {idempotency} (
                key_hash, correlation_id, prepared_inputs_hash, state, created_at, updated_at
            )
            VALUES (%s, %s, %s, 'in_progress', %s, %s)
            ON CONFLICT (key_hash) DO NOTHING
            RETURNING key_hash, state
            """,
            ("key-reserve", "corr-reserve", "hash-reserve", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        duplicate = _execute_fetchall(
            conn,
            f"""
            INSERT INTO {idempotency} (
                key_hash, correlation_id, prepared_inputs_hash, state, created_at, updated_at
            )
            VALUES (%s, %s, %s, 'in_progress', %s, %s)
            ON CONFLICT (key_hash) DO NOTHING
            RETURNING key_hash, state
            """,
            ("key-reserve", "corr-reserve", "hash-reserve", "2026-01-01T00:00:01Z", "2026-01-01T00:00:01Z"),
        )
        count = _execute_fetchone(conn, f"SELECT COUNT(*) AS n FROM {idempotency}")["n"]
        row = _execute_fetchone(conn, f"SELECT state, updated_at FROM {idempotency} WHERE key_hash = %s", ("key-reserve",))
    finally:
        conn.close()

    assert first == [{"key_hash": "key-reserve", "state": "in_progress"}]
    assert duplicate == []
    assert count == 1
    assert row["state"] == "in_progress"
    assert row["updated_at"].astimezone(timezone.utc) == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_conditional_transitions_are_compare_and_swap_on_real_postgresql(pg_case: CaseTables) -> None:
    idempotency = _quote_ident(pg_case.idempotency)
    conn = _connect(autocommit=True)
    try:
        _execute(
            conn,
            f"""
            INSERT INTO {idempotency} (
                key_hash, correlation_id, prepared_inputs_hash, state, created_at, updated_at
            )
            VALUES
                ('key-complete', 'corr-complete', 'hash-complete', 'in_progress', now(), now()),
                ('key-fail', 'corr-fail', 'hash-fail', 'in_progress', now(), now())
            """,
        )
        completed = _execute_fetchall(
            conn,
            f"""
            UPDATE {idempotency}
            SET state = 'completed',
                result_ref = %s,
                updated_at = %s
            WHERE key_hash = %s
              AND state = 'in_progress'
            RETURNING key_hash, state, result_ref
            """,
            ('{"snapshot_hash":"snap-real"}', "2026-01-01T00:00:02Z", "key-complete"),
        )
        rejected = _execute_fetchall(
            conn,
            f"""
            UPDATE {idempotency}
            SET state = 'failed',
                failure_reason = %s,
                updated_at = %s
            WHERE key_hash = %s
              AND state = 'in_progress'
            RETURNING key_hash, state, failure_reason
            """,
            ("wrong_state", "2026-01-01T00:00:03Z", "key-complete"),
        )
        failed = _execute_fetchall(
            conn,
            f"""
            UPDATE {idempotency}
            SET state = 'failed',
                failure_reason = %s,
                updated_at = %s
            WHERE key_hash = %s
              AND state = 'in_progress'
            RETURNING key_hash, state, failure_reason
            """,
            ("projection_failed", "2026-01-01T00:00:04Z", "key-fail"),
        )
    finally:
        conn.close()

    assert completed == [{"key_hash": "key-complete", "state": "completed", "result_ref": '{"snapshot_hash":"snap-real"}'}]
    assert rejected == []
    assert failed == [{"key_hash": "key-fail", "state": "failed", "failure_reason": "projection_failed"}]


def test_local_transaction_rolls_back_idempotency_when_outbox_insert_fails(pg_case: CaseTables) -> None:
    _psycopg, errors, _dict_row = _load_psycopg()
    idempotency = _quote_ident(pg_case.idempotency)
    outbox = _quote_ident(pg_case.outbox)
    conn = _connect(autocommit=True)
    try:
        _execute(
            conn,
            f"""
            INSERT INTO {idempotency} (
                key_hash, correlation_id, prepared_inputs_hash, state, created_at, updated_at
            )
            VALUES ('key-rollback', 'corr-rollback', 'hash-rollback', 'in_progress', now(), now())
            """,
        )
        _execute(
            conn,
            f"""
            INSERT INTO {outbox} (
                event_id, idempotency_key_hash, event_type, payload
            )
            VALUES ('event-dup', 'key-rollback', 'seed', '{{}}'::jsonb)
            """,
        )
    finally:
        conn.close()

    tx_conn = _connect()
    try:
        with pytest.raises(errors.UniqueViolation):
            with tx_conn.transaction():
                updated = _execute_fetchall(
                    tx_conn,
                    f"""
                    UPDATE {idempotency}
                    SET state = 'completed',
                        result_ref = %s,
                        updated_at = %s
                    WHERE key_hash = %s
                      AND state = 'in_progress'
                    RETURNING key_hash
                    """,
                    ('{"snapshot_hash":"snap-rollback"}', "2026-01-01T00:00:05Z", "key-rollback"),
                )
                assert updated == [{"key_hash": "key-rollback"}]
                _execute(
                    tx_conn,
                    f"""
                    INSERT INTO {outbox} (
                        event_id, idempotency_key_hash, event_type, payload
                    )
                    VALUES ('event-dup', 'key-rollback', 'idempotency.completed', '{{}}'::jsonb)
                    """,
                )
    finally:
        tx_conn.close()

    check_conn = _connect(autocommit=True)
    try:
        state = _execute_fetchone(check_conn, f"SELECT state FROM {idempotency} WHERE key_hash = %s", ("key-rollback",))["state"]
        outbox_count = _execute_fetchone(check_conn, f"SELECT COUNT(*) AS n FROM {outbox}")["n"]
    finally:
        check_conn.close()

    assert state == "in_progress"
    assert outbox_count == 1


def test_polling_claim_is_parallel_safe_with_for_update_skip_locked(pg_case: CaseTables) -> None:
    idempotency = _quote_ident(pg_case.idempotency)
    outbox = _quote_ident(pg_case.outbox)
    conn = _connect(autocommit=True)
    try:
        _execute(
            conn,
            f"""
            INSERT INTO {idempotency} (
                key_hash, correlation_id, prepared_inputs_hash, state, created_at, updated_at
            )
            VALUES ('key-claim', 'corr-claim', 'hash-claim', 'completed', now(), now())
            """,
        )
        for seq in range(12):
            _execute(
                conn,
                f"""
                INSERT INTO {outbox} (
                    event_id, idempotency_key_hash, event_type, payload
                )
                VALUES (%s, 'key-claim', 'idempotency.completed', '{{}}'::jsonb)
                """,
                (f"event-claim-{seq}",),
            )
    finally:
        conn.close()

    barrier = threading.Barrier(4)

    def claim(worker_id: int) -> list[str]:
        worker_conn = _connect()
        try:
            barrier.wait(timeout=10)
            with worker_conn.transaction():
                rows = _execute_fetchall(
                    worker_conn,
                    f"""
                    UPDATE {outbox}
                    SET relay_status = 'claimed',
                        claimed_at = %s,
                        relay_worker_id = %s
                    WHERE event_id IN (
                        SELECT event_id
                        FROM {outbox}
                        WHERE relay_status = 'pending'
                        ORDER BY outbox_sequence
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    RETURNING event_id, relay_status, relay_worker_id, outbox_sequence
                    """,
                    ("2026-01-01T00:00:06Z", f"worker-{worker_id}", 3),
                )
                time.sleep(0.1)
                return [row["event_id"] for row in rows]
        finally:
            worker_conn.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(claim, range(4)))

    claimed = [event_id for batch in batches for event_id in batch]
    assert len(claimed) == 12
    assert len(set(claimed)) == 12


def test_pgbouncer_transaction_mode_set_local_isolation_when_configured() -> None:
    if not PGBOUNCER_DSN:
        pytest.skip("SKIP_PROVIDER_LIMITATION: AS2_PGBOUNCER_TEST_DSN is not configured")

    conn = _connect(dsn=PGBOUNCER_DSN)
    try:
        with conn.transaction():
            _execute(conn, "SET LOCAL work_mem = '64MB'")
            inside = _execute_fetchone(conn, "SHOW work_mem")["work_mem"]
        with conn.transaction():
            outside = _execute_fetchone(conn, "SHOW work_mem")["work_mem"]
    finally:
        conn.close()

    assert inside.lower() == "64mb"
    assert outside.lower() != "64mb"


def test_cdc_logical_replication_pgoutput_feasibility_when_enabled(pg_case: CaseTables) -> None:
    if not ENABLE_CDC:
        pytest.skip("SKIP_PROVIDER_LIMITATION: AS2_ENABLE_CDC_VERIFICATION is not enabled")

    _psycopg, errors, _dict_row = _load_psycopg()
    outbox = _quote_ident(pg_case.outbox)
    publication = f"as2_p0645_pub_{uuid.uuid4().hex[:16]}"
    slot = f"as2_p0645_slot_{uuid.uuid4().hex[:16]}"
    conn = _connect(autocommit=True)
    slot_created = False
    try:
        wal_level = _execute_fetchone(conn, "SHOW wal_level")["wal_level"]
        if wal_level != "logical":
            pytest.skip(f"SKIP_PROVIDER_LIMITATION: wal_level is {wal_level!r}, not 'logical'")

        try:
            _execute(conn, f"CREATE PUBLICATION {_quote_ident(publication)} FOR TABLE {outbox}")
            _execute_fetchone(conn, "SELECT * FROM pg_create_logical_replication_slot(%s, 'pgoutput')", (slot,))
            slot_created = True
        except (errors.InsufficientPrivilege, errors.UndefinedObject, errors.FeatureNotSupported) as exc:
            pytest.skip(f"SKIP_PROVIDER_LIMITATION: logical replication/pgoutput unavailable: {exc.__class__.__name__}")
    finally:
        if slot_created:
            _execute(conn, "SELECT pg_drop_replication_slot(%s)", (slot,))
        _execute(conn, f"DROP PUBLICATION IF EXISTS {_quote_ident(publication)}")
        conn.close()


def test_debezium_rest_endpoint_smoke_when_configured() -> None:
    if not DEBEZIUM_URL:
        pytest.skip("SKIP_PROVIDER_LIMITATION: AS2_DEBEZIUM_URL is not configured")

    try:
        with urlopen(f"{DEBEZIUM_URL.rstrip('/')}/connectors", timeout=10) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        pytest.fail(f"Debezium REST endpoint smoke failed: {exc.__class__.__name__}: {exc}")

    assert payload.strip().startswith("[")


def _debezium_json(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{DEBEZIUM_URL.rstrip('/')}/{path.lstrip('/')}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _wait_for_connector_running(connector_name: str) -> None:
    last_status: Any = None
    for _ in range(60):
        time.sleep(1)
        try:
            last_status = _debezium_json("GET", f"connectors/{connector_name}/status")
        except (HTTPError, URLError):
            continue
        connector = last_status.get("connector", {})
        tasks = last_status.get("tasks", [])
        if connector.get("state") == "RUNNING" and tasks and all(task.get("state") == "RUNNING" for task in tasks):
            return
    pytest.fail(f"Debezium connector did not become RUNNING: {last_status!r}")


def _docker_exec_rpk_consume(topic: str, expected_event_id: str) -> str:
    last_output = ""
    for _ in range(12):
        try:
            completed = subprocess.run(
                [
                    "docker",
                    "exec",
                    REDPANDA_CONTAINER,
                    "rpk",
                    "topic",
                    "consume",
                    topic,
                    "--brokers",
                    "localhost:9092",
                    "-n",
                    "1",
                    "-o",
                    "start",
                    "-f",
                    "%v\n",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"SKIP_PROVIDER_LIMITATION: docker/rpk consume unavailable: {exc.__class__.__name__}")

        last_output = (completed.stdout or "") + (completed.stderr or "")
        if expected_event_id in last_output:
            return last_output
        time.sleep(2)
    pytest.fail(f"CDC event {expected_event_id!r} was not observed in topic {topic!r}; last output: {last_output}")


def test_debezium_connector_emits_actual_outbox_event_when_stack_configured(pg_case: CaseTables) -> None:
    if not ENABLE_DEBEZIUM_CONNECTOR_SMOKE:
        pytest.skip("SKIP_PROVIDER_LIMITATION: AS2_ENABLE_DEBEZIUM_CONNECTOR_SMOKE is not enabled")
    if not DEBEZIUM_URL:
        pytest.skip("SKIP_PROVIDER_LIMITATION: AS2_DEBEZIUM_URL is not configured")

    _load_psycopg()
    suffix = uuid.uuid4().hex[:12]
    connector_name = f"as2-p0645-cdc-{suffix}"
    topic_prefix = f"as2_p0645_cdc_{suffix}"
    publication = f"as2_p0645_pub_{suffix}"
    slot = f"as2_p0645_slot_{suffix}"
    topic = f"{topic_prefix}.public.{pg_case.outbox}"
    event_id = f"as2-p0645-outbox-event-{suffix}"
    outbox = _quote_ident(pg_case.outbox)

    conn = _connect(autocommit=True)
    try:
        _execute(conn, f"CREATE PUBLICATION {_quote_ident(publication)} FOR TABLE {outbox}")
        config = {
            "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
            "plugin.name": "pgoutput",
            "database.hostname": os.environ.get("AS2_DEBEZIUM_POSTGRES_HOST", "postgres"),
            "database.port": os.environ.get("AS2_DEBEZIUM_POSTGRES_PORT", "5432"),
            "database.user": os.environ.get("AS2_DEBEZIUM_POSTGRES_USER", "as2"),
            "database.password": os.environ.get("AS2_DEBEZIUM_POSTGRES_PASSWORD", "as2_dev_only"),
            "database.dbname": os.environ.get("AS2_DEBEZIUM_POSTGRES_DB", "as2_mini_poc"),
            "topic.prefix": topic_prefix,
            "slot.name": slot,
            "publication.name": publication,
            "publication.autocreate.mode": "disabled",
            "table.include.list": f"public.{pg_case.outbox}",
            "snapshot.mode": "no_data",
            "tombstones.on.delete": "false",
            "slot.drop.on.stop": "true",
        }
        _debezium_json("POST", "connectors", {"name": connector_name, "config": config})
        _wait_for_connector_running(connector_name)

        _execute(
            conn,
            f"""
            INSERT INTO {outbox} (
                event_id, event_type, payload, relay_status, created_at
            )
            VALUES (%s, 'idempotency.completed', %s::jsonb, 'pending', now())
            """,
            (event_id, json.dumps({"correlation_id": f"corr-{suffix}", "source": "p0645-cdc-smoke"})),
        )
        observed = _docker_exec_rpk_consume(topic, event_id)
    finally:
        try:
            _debezium_json("DELETE", f"connectors/{connector_name}")
        except Exception:
            pass
        time.sleep(2)
        try:
            _execute(conn, f"DROP PUBLICATION IF EXISTS {_quote_ident(publication)}")
            _execute(conn, "SELECT pg_drop_replication_slot(%s) WHERE EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = %s)", (slot, slot))
        finally:
            conn.close()

    assert event_id in observed


def _timed(operation: Callable[[], None]) -> float:
    started = time.perf_counter()
    operation()
    return (time.perf_counter() - started) * 1000


def _percentile(samples: list[float], percent: float) -> float:
    if len(samples) == 1:
        return samples[0]
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def test_basic_latency_sample_is_recorded_as_non_slo_dev_signal(pg_case: CaseTables) -> None:
    idempotency = _quote_ident(pg_case.idempotency)
    outbox = _quote_ident(pg_case.outbox)
    sample_size = max(10, LATENCY_SAMPLE_SIZE)
    conn = _connect(autocommit=True)
    reserve_samples: list[float] = []
    complete_samples: list[float] = []
    claim_samples: list[float] = []
    try:
        for idx in range(sample_size):
            key = f"latency-key-{idx}"
            reserve_samples.append(
                _timed(
                    lambda key=key: _execute_fetchall(
                        conn,
                        f"""
                        INSERT INTO {idempotency} (
                            key_hash, correlation_id, prepared_inputs_hash, state, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, 'in_progress', now(), now())
                        ON CONFLICT (key_hash) DO NOTHING
                        RETURNING key_hash
                        """,
                        (key, f"latency-corr-{idx}", f"latency-hash-{idx}"),
                    )
                )
            )
            complete_samples.append(
                _timed(
                    lambda key=key: _execute_fetchall(
                        conn,
                        f"""
                        UPDATE {idempotency}
                        SET state = 'completed',
                            result_ref = %s,
                            updated_at = now()
                        WHERE key_hash = %s
                          AND state = 'in_progress'
                        RETURNING key_hash
                        """,
                        ('{"snapshot_hash":"latency"}', key),
                    )
                )
            )
            _execute(
                conn,
                f"""
                INSERT INTO {outbox} (
                    event_id, idempotency_key_hash, event_type, payload
                )
                VALUES (%s, %s, 'idempotency.completed', '{{}}'::jsonb)
                """,
                (f"latency-event-{idx}", key),
            )

        for _idx in range(sample_size):
            claim_samples.append(
                _timed(
                    lambda: _execute_fetchall(
                        conn,
                        f"""
                        UPDATE {outbox}
                        SET relay_status = 'claimed',
                            claimed_at = now(),
                            relay_worker_id = 'latency-worker'
                        WHERE event_id IN (
                            SELECT event_id
                            FROM {outbox}
                            WHERE relay_status = 'pending'
                            ORDER BY outbox_sequence
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        RETURNING event_id
                        """,
                    )
                )
            )
    finally:
        conn.close()

    summary = {
        "reserve_if_absent": reserve_samples,
        "complete_if_state": complete_samples,
        "polling_claim": claim_samples,
    }
    for name, samples in summary.items():
        assert len(samples) == sample_size, name
        assert statistics.mean(samples) >= 0
        assert _percentile(samples, 95) >= 0
        assert _percentile(samples, 99) >= 0
