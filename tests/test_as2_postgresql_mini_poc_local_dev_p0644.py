"""P0.6.44-dev static checks for the local PostgreSQL mini-POC verification stack.

These tests do not require Docker. They verify that the local/open-source stack is
explicitly marked verification-only and that it preserves the approved P0.6.44-dev
scope without implying production evidence or production activation.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.as2-postgres-mini-poc.yml"
POC_DOC = PROJECT_ROOT / "docs" / "AS2-POSTGRESQL-MINI-POC-P0644-DEV.md"


def _compose_text() -> str:
    return COMPOSE_FILE.read_text(encoding="utf-8")


def test_local_compose_stack_is_explicitly_verification_only() -> None:
    source = _compose_text()
    assert "verification-only" in source
    assert "as2.production: \"false\"" in source
    assert "P0.6.44-dev" in source
    assert "no production ENABLED activation" in source
    assert "no official infra sign-off" in source


def test_local_compose_declares_postgres_logical_replication_controls() -> None:
    source = _compose_text()
    assert "image: postgres:16" in source
    assert "wal_level=logical" in source
    assert "max_replication_slots=4" in source
    assert "max_wal_senders=4" in source
    assert "max_slot_wal_keep_size=10GB" in source
    assert "55432:5432" in source


def test_local_compose_declares_pgbouncer_transaction_pooling_rehearsal() -> None:
    source = _compose_text()
    assert "pgbouncer" in source
    assert "PGBOUNCER_POOL_MODE: transaction" in source
    assert "PGBOUNCER_MAX_CLIENT_CONN" in source
    assert "PGBOUNCER_DEFAULT_POOL_SIZE" in source
    assert "56432:6432" in source


def test_local_compose_declares_optional_cdc_verification_stack() -> None:
    source = _compose_text()
    assert "redpanda" in source
    assert "debezium/connect" in source
    assert "BOOTSTRAP_SERVERS: redpanda:9092" in source
    assert "AS2_CDC_DECODER_PREFERRED: pgoutput" in source
    assert "58083:8083" in source


def test_p0644_dev_document_marks_stack_as_non_production_evidence() -> None:
    doc = POC_DOC.read_text(encoding="utf-8")
    assert "VERIFICATION_ONLY" in doc
    assert "not official Q8 / Q8a / Q10 evidence" in doc
    assert "Production ENABLED remains LOCKED" in doc
    assert "docker-compose.as2-postgres-mini-poc.yml" in doc
    assert "P0.6.44-dev" in doc
