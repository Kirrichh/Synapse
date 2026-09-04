"""Operator-issued, run-bound approval grants and auditable automatic reviews.

The configured store is a local operator authority boundary, outside worker
worktrees. Requests are proposals; only the explicit CLI grant creates authority.
This does not sandbox an adversarial process running as the operator's OS user.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Callable

from ..canonicalization import HashBoundRef, RefKind
from ..contracts import ActorIdentity, AuthorityIdentity
from .context_codec import encode_canonical
from .intent import IntentCandidate
from .planning import OperationPlanCandidate, validate_operation_plan_against_intent


APPROVAL_REQUEST_SCHEMA_V1 = "synapse.stage4.gold.stage10.approval-request/v1"
APPROVAL_GRANT_SCHEMA_V1 = "synapse.stage4.gold.stage10.approval-grant/v1"
APPROVAL_RECEIPT_SCHEMA_V1 = "synapse.stage4.gold.stage10.approval-receipt/v1"
_MAX_BYTES = 131072


class ApprovalRequired(ValueError):
    def __init__(self, request_path: Path) -> None:
        self.request_path = request_path
        super().__init__(f"Operator approval required: {request_path}")


def _digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("approval digest is invalid")
    return value


def _time(value: object) -> int:
    if type(value) is not int or not 0 <= value < 2**53:
        raise ValueError("approval clock must be a nonnegative safe integer")
    return value


def _now() -> int:
    return time.time_ns() // 1_000_000


def _read(path: Path) -> tuple[dict[str, object], bytes]:
    if path.is_symlink():
        raise ValueError("approval records cannot be symlinks")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("approval record exceeds its envelope")
    value = json.loads(raw)
    if type(value) is not dict or encode_canonical(value) != raw:
        raise ValueError("approval record is not a canonical object")
    return value, raw


def _write(path: Path, value: dict[str, object]) -> None:
    raw = encode_canonical(value)
    if len(raw) > _MAX_BYTES:
        raise ValueError("approval record exceeds its envelope")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(parent.is_symlink() for parent in (path.parent, *path.parent.parents)):
        raise ValueError("approval store cannot use symlink directories")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if _read(path)[1] != raw:
            raise ValueError("approval record already exists with different bytes")
        return
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "posix":
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _record_ref(value: dict[str, object]) -> HashBoundRef:
    raw = encode_canonical(value)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.CONTRACT_CONDITION, ref_id=digest,
        schema_id=value["schema_version"], sha256=digest,
        byte_length=len(raw), media_type="application/json",
    )


def _request(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != {
        "schema_version", "run_manifest_sha256", "governing_human_authority",
        "policy_sha256", "executor", "intent_contract", "plan_contract",
    } or value["schema_version"] != APPROVAL_REQUEST_SCHEMA_V1:
        raise ValueError("unknown approval request")
    _digest(value["run_manifest_sha256"])
    _digest(value["policy_sha256"])
    AuthorityIdentity.from_dict(value["governing_human_authority"])
    if value["executor"] is not None:
        ActorIdentity.from_dict(value["executor"])
    if any(type(value[field]) is not dict for field in ("intent_contract", "plan_contract")):
        raise ValueError("approval request requires exact contracts")
    return value


def grant_approval(*, request_path: Path, store_root: Path, duration_seconds: int,
                   observed_at_unix_ms: int | None = None) -> HashBoundRef:
    """Explicit operator action. Never called by a planning or worker owner."""
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= 604800:
        raise ValueError("approval duration must be between 1 second and 7 days")
    request = _request(_read(request_path)[0])
    now = _time(_now() if observed_at_unix_ms is None else observed_at_unix_ms)
    grant = {
        "schema_version": APPROVAL_GRANT_SCHEMA_V1, "request": request,
        "issued_at_unix_ms": now,
        "expires_at_unix_ms": _time(now + duration_seconds * 1000),
    }
    ref = _record_ref(grant)
    request_sha = _record_ref(request).sha256
    _write(store_root / "grants" / request_sha / (ref.sha256 + ".json"), grant)
    return ref


def revoke_approval(*, store_root: Path, grant_sha256: str,
                    observed_at_unix_ms: int | None = None) -> None:
    """Append a permanent revocation; existing decision evidence is retained."""
    digest = _digest(grant_sha256)
    matches = list((store_root / "grants").glob(f"*/{digest}.json"))
    if len(matches) != 1:
        raise ValueError("approval grant was not found unambiguously in this store")
    grant, _ = _read(matches[0])
    if grant.get("schema_version") != APPROVAL_GRANT_SCHEMA_V1 or _record_ref(grant).sha256 != digest:
        raise ValueError("approval grant bytes do not match the requested revocation")
    if matches[0].parent.name != _record_ref(_request(grant.get("request"))).sha256:
        raise ValueError("approval grant is stored under another request")
    path = store_root / "revocations" / (digest + ".json")
    if path.exists():
        value, _ = _read(path)
        if set(value) != {"grant_sha256", "revoked_at_unix_ms"} or value["grant_sha256"] != digest:
            raise ValueError("invalid existing revocation")
        _time(value["revoked_at_unix_ms"])
        return
    _write(path, {"grant_sha256": digest, "revoked_at_unix_ms":
                 _time(_now() if observed_at_unix_ms is None else observed_at_unix_ms)})


@dataclass(frozen=True)
class RunApprovalPolicy:
    store_root: Path
    run_manifest_sha256: str
    governing_human_authority: AuthorityIdentity
    trusted_clock: Callable[[], int] = _now

    def __post_init__(self) -> None:
        if not isinstance(self.store_root, Path) or not self.store_root.is_absolute():
            raise ValueError("approval store must be an absolute operator-owned path")
        _digest(self.run_manifest_sha256)
        if type(self.governing_human_authority) is not AuthorityIdentity or not callable(self.trusted_clock):
            raise ValueError("approval requires a governing human and a clock")

    def request_for(self, *, plan: OperationPlanCandidate, intent: IntentCandidate,
                    policy_sha256: str, executor: ActorIdentity | None) -> dict[str, object]:
        validate_operation_plan_against_intent(plan, intent=intent)
        intent_contract = dict(intent.to_dict()["payload"])
        plan_contract = dict(plan.to_dict()["payload"])
        # Only attempt-local provenance and explicitly data-only observations
        # vary under a grant. Every permission, condition and uncertainty stays.
        for key in ("knowledge_snapshot_ref", "execution_feedback"):
            intent_contract.pop(key)
        for key in ("knowledge_snapshot_ref", "intent_proposal_id", "intent_sha256"):
            plan_contract.pop(key)
        return self.request_for_contract(
            intent_contract=intent_contract, plan_contract=plan_contract,
            policy_sha256=policy_sha256, executor=executor,
        )

    def request_for_contract(self, *, intent_contract: dict, plan_contract: dict,
                            policy_sha256: str, executor: ActorIdentity | None) -> dict[str, object]:
        return _request({
            "schema_version": APPROVAL_REQUEST_SCHEMA_V1,
            "run_manifest_sha256": self.run_manifest_sha256,
            "governing_human_authority": self.governing_human_authority.to_dict(),
            "policy_sha256": policy_sha256,
            "executor": None if executor is None else executor.to_dict(),
            "intent_contract": intent_contract, "plan_contract": plan_contract,
        })

    def _grant_at(self, ref: HashBoundRef, request: dict[str, object], moment: int) -> None:
        request_sha = _record_ref(request).sha256
        grant, raw = _read(self.store_root / "grants" / request_sha / (_digest(ref.sha256) + ".json"))
        if set(grant) != {"schema_version", "request", "issued_at_unix_ms", "expires_at_unix_ms"}:
            raise ValueError("unknown approval grant fields")
        if grant["schema_version"] != APPROVAL_GRANT_SCHEMA_V1 or _record_ref(grant) != ref or grant["request"] != request:
            raise ValueError("grant does not match the exact approval request")
        issued, expires = _time(grant["issued_at_unix_ms"]), _time(grant["expires_at_unix_ms"])
        if not issued <= moment < expires or expires - issued > 604800000:
            raise ValueError("approval expired or clock regressed")
        revoked = self.store_root / "revocations" / (ref.sha256 + ".json")
        if revoked.exists():
            value, _ = _read(revoked)
            if set(value) != {"grant_sha256", "revoked_at_unix_ms"} or value["grant_sha256"] != ref.sha256:
                raise ValueError("invalid approval revocation")
            if _time(value["revoked_at_unix_ms"]) <= moment:
                raise ValueError("approval was revoked")

    def review(self, **inputs: object) -> HashBoundRef:
        return self.review_request(self.request_for(**inputs))

    def review_request(self, request: dict[str, object]) -> HashBoundRef:
        _request(request)
        if request["run_manifest_sha256"] != self.run_manifest_sha256 or request["governing_human_authority"] != self.governing_human_authority.to_dict():
            raise ValueError("request is outside the configured operator authority")
        request_sha = _record_ref(request).sha256
        now = _time(self.trusted_clock())
        directory = self.store_root / "grants" / request_sha
        for path in sorted(directory.glob("*.json")):
            grant, _ = _read(path)
            ref = _record_ref(grant)
            if path.stem != ref.sha256:
                raise ValueError("grant filename does not match its bytes")
            try:
                self._grant_at(ref, request, now)
            except ValueError:
                continue
            receipt = {
                "schema_version": APPROVAL_RECEIPT_SCHEMA_V1,
                "request_sha256": request_sha, "grant_ref": ref.to_dict(),
                "checked_at_unix_ms": now,
            }
            receipt_ref = _record_ref(receipt)
            _write(self.store_root / "receipts" / (receipt_ref.sha256 + ".json"), receipt)
            return receipt_ref
        path = self.store_root / "requests" / (request_sha + ".json")
        _write(path, request)
        raise ApprovalRequired(path)

    def validate(self, ref: HashBoundRef, *, current: bool, **inputs: object) -> None:
        if type(ref) is not HashBoundRef or ref.schema_id != APPROVAL_RECEIPT_SCHEMA_V1:
            raise ValueError("human approval requires a recorded review receipt")
        request = self.request_for(**inputs)
        receipt, _ = _read(self.store_root / "receipts" / (_digest(ref.sha256) + ".json"))
        if set(receipt) != {"schema_version", "request_sha256", "grant_ref", "checked_at_unix_ms"} or _record_ref(receipt) != ref:
            raise ValueError("approval receipt differs from its exact reference")
        if receipt["request_sha256"] != _record_ref(request).sha256:
            raise ValueError("approval belongs to different plan conditions or run")
        checked = _time(receipt["checked_at_unix_ms"])
        grant_ref = HashBoundRef.from_dict(receipt["grant_ref"])
        self._grant_at(grant_ref, request, checked)
        if current:
            now = _time(self.trusted_clock())
            if now < checked:
                raise ValueError("approval clock regressed")
            self._grant_at(grant_ref, request, now)
