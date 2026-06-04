from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "AS2-POSTGRESQL-MINI-POC-P0645-DEV-EXECUTION.md"


def test_p0645_execution_report_exists_and_is_verification_only() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "OPEN_PROVIDER_EXECUTION_ATTEMPT_RECORDED" in text
    assert "verification-only" in text.lower()
    assert "not official Q8 / Q8a / Q10 evidence" in text
    assert "Production ENABLED remains **LOCKED**" in text


def test_p0645_execution_report_records_docker_unavailable_status() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "docker: command not found" in text
    assert "OPEN_PROVIDER_RUNTIME_EXECUTION_BLOCKED_BY_LOCAL_RUNTIME_NO_DOCKER" in text
    assert "STATIC_VERIFICATION_CONFIRMED" in text


def test_p0645_execution_report_records_external_provider_harness() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "EXTERNAL_PROVIDER_VERIFICATION_HARNESS_ADDED" in text
    assert "OPEN_PROVIDER_SQL_RUNTIME_VERIFIED" in text
    assert "LOCAL_PORTABLE_POSTGRES_RUNTIME_VERIFIED" in text
    assert "tests/test_as2_postgresql_external_provider_p0645.py" in text
    assert "AS2_POSTGRES_TEST_DSN" in text
    assert "AS2_PGBOUNCER_TEST_DSN" in text
    assert "AS2_ENABLE_CDC_VERIFICATION=1" in text
    assert "AS2_DEBEZIUM_URL" in text
    assert "AS2_ENABLE_DEBEZIUM_CONNECTOR_SMOKE=1" in text
    assert "AS2_REDPANDA_CONTAINER" in text
    assert "Actual outbox event -> emitted CDC event" in text
    assert ".github/workflows/as2-postgres-open-provider-verification.yml" in text
    assert "DSN, password, token, or provider secret is recorded" in text


def test_p0645_execution_report_preserves_required_future_checks() -> None:
    text = REPORT.read_text(encoding="utf-8")
    required = [
        "INSERT ... ON CONFLICT DO NOTHING",
        "UPDATE ... WHERE state = expected RETURNING *",
        "FOR UPDATE SKIP LOCKED polling claim",
        "PgBouncer transaction-mode SET LOCAL isolation",
        "CDC smoke through pgoutput / Debezium / Redpanda",
    ]
    for item in required:
        assert item in text
