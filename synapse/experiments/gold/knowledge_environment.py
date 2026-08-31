"""Production composition of one connected project's durable Gold world.

A project is connected once and run many times. Connecting creates the six
authority histories and the behavior library under one state root, all sharing
one mutation coordinator, and writes a versioned project record naming the
repository, the declared authority configuration and the trusted head of every
history. Opening reads that record and reopens the same stores against those
exact heads, so a history that was rolled back under the platform is refused
rather than trusted (§19: rollback to an older effective lifecycle snapshot is
forbidden).

One coordinator, not one per store, is the load-bearing part: a per-store
counter cannot tell a reader that lifecycle moved while taint was being read,
and §22's fenced head capture exists to detect exactly that.

Cohesion review (459 eLOC, band 401-700). One responsibility: compose one
project's durable world. It stays whole because opening is one transaction —
the declaration, the coordinator, the seven stores and the recorded heads are
bound together or not at all, and a split would let a partially opened world
escape between two of those bindings. That is the same argument
``create_gold_run_composition`` already makes for staying one linear factory.
The record's schema and the store layout are the only things here that could
change independently, and both are constants rather than logic.

What this root deliberately does not do: publish behaviors into the library.
A freshly connected project has an empty candidate universe, and that is a
valid state — the four gates are defined over a non-empty subject set, so a
project with nothing accumulated cannot run Gold at all. That is not a missing
feature but what the mode means: Gold reuses *accumulated* knowledge. Runs on
such a project take the Baseline path until publication puts the first verified
behavior in the library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json

import hashlib
import re

from synapse.change.workspace import resolve_revision
from synapse.experiments.gold.admission_journal import FileAdmissionJournal, FileSnapshotFence
from synapse.experiments.gold.admission_store import FileAdmissionCausalStore
from synapse.experiments.gold.compatibility_store import FileCompatibilityStore
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    HistoryDomain,
    RepositoryRevision,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
    history_anchor_from_dict,
)
from synapse.experiments.gold.knowledge_store import AuthoritativeKnowledgeStore
from synapse.experiments.gold.library import (
    LIBRARY_PUBLISHER_IDENTITY_V1,
    BehaviorLibrary,
    PublisherIdentity,
)
from synapse.experiments.gold.library_composition import (
    create_program_artifact_behavior_library,
)
from synapse.experiments.gold.lifecycle import open_lifecycle_store
from synapse.experiments.gold.provenance import (
    BUILDER_RUNTIME_IDENTITY_V1,
    BuilderRuntimeIdentity,
    configure_platform_attester,
    open_behavior_attestation_store,
)
from synapse.experiments.gold.taint import open_taint_history_store
from synapse.version import RUNTIME_VERSION

from .runner.vocabulary import GoldRunFailureCode, GoldRunViolation


PROJECT_RECORD_SCHEMA_V1 = "synapse.stage4.gold.project-record/v1"

#: The attesting runtime's *contract* version, which is what §18 records. The
#: build actually running is bound separately, by digest, in ``executable_ref``:
#: a semver alone cannot distinguish two builds of the same release.
_GOLD_RUNTIME_VERSION = "synapse.stage4.gold-runtime/v1"

#: One policy version per project, in the shape every Stage 4 owner accepts.
#: The library's publisher identity validates it more strictly than the gates
#: do, so requiring the strict form here means an operator learns at connect
#: time rather than from a library refusal several owners deep.
_POLICY_VERSION_RE = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*/v[1-9][0-9]*\Z")

#: One directory per authority history, fixed here because the record names
#: heads by history and a relocated store would make an anchor unverifiable.
_FENCE_DIR = "mutation-fence"
_LIBRARY_DIR = "library"
_LIFECYCLE_DIR = "lifecycle"
_ATTESTATION_DIR = "attestations"
_TAINT_DIR = "taint"
_ADMISSION_DIR = "admission"
_CAUSAL_DIR = "admission-causal"
_COMPATIBILITY_DIR = "compatibility"
_SNAPSHOT_DIR = "snapshot"
_RECORD_NAME = "project.json"

_PROJECT_STORES_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class GoldProjectIdentities:
    """The authority configuration an operator declares for one project.

    Declared rather than defaulted: these names decide who may attest, who may
    classify taint, who reviews supersession and revocation. A default would
    make every project share one authority, which is the opposite of what an
    authority identity is for.
    """

    platform_attester_actor: ActorIdentity
    builder_actor: ActorIdentity
    taint_classifier_authority: AuthorityIdentity
    taint_reviewer_authority: AuthorityIdentity
    supersession_reviewer_authority: AuthorityIdentity
    revocation_reviewer_authority: AuthorityIdentity
    lifecycle_writer_actor: ActorIdentity
    governing_human_authority: AuthorityIdentity | None = None

    def __post_init__(self) -> None:
        for name in (
            "platform_attester_actor",
            "builder_actor",
            "lifecycle_writer_actor",
        ):
            if type(getattr(self, name)) is not ActorIdentity:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact actor identity")
        for name in (
            "taint_classifier_authority",
            "taint_reviewer_authority",
            "supersession_reviewer_authority",
            "revocation_reviewer_authority",
        ):
            if type(getattr(self, name)) is not AuthorityIdentity:
                raise _fail(
                    GoldRunFailureCode.TYPE_MISMATCH,
                    f"{name} must be an exact authority identity",
                )
        if self.governing_human_authority is not None and (
            type(self.governing_human_authority) is not AuthorityIdentity
        ):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "governing_human_authority must be exact or None",
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "platform_attester_actor": self.platform_attester_actor.value,
            "builder_actor": self.builder_actor.value,
            "taint_classifier_authority": self.taint_classifier_authority.value,
            "taint_reviewer_authority": self.taint_reviewer_authority.value,
            "supersession_reviewer_authority": self.supersession_reviewer_authority.value,
            "revocation_reviewer_authority": self.revocation_reviewer_authority.value,
            "lifecycle_writer_actor": self.lifecycle_writer_actor.value,
            "governing_human_authority": (
                None if self.governing_human_authority is None else self.governing_human_authority.value
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> "GoldProjectIdentities":
        if type(value) is not dict:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "identities must be an object")
        human = value.get("governing_human_authority")
        return cls(
            platform_attester_actor=ActorIdentity(value["platform_attester_actor"]),
            builder_actor=ActorIdentity(value["builder_actor"]),
            taint_classifier_authority=AuthorityIdentity(value["taint_classifier_authority"]),
            taint_reviewer_authority=AuthorityIdentity(value["taint_reviewer_authority"]),
            supersession_reviewer_authority=AuthorityIdentity(value["supersession_reviewer_authority"]),
            revocation_reviewer_authority=AuthorityIdentity(value["revocation_reviewer_authority"]),
            lifecycle_writer_actor=ActorIdentity(value["lifecycle_writer_actor"]),
            governing_human_authority=None if human is None else AuthorityIdentity(human),
        )


@dataclass(frozen=True)
class GoldProjectEntitlements:
    """The grant an operator issues to this project's Gold runs.

    Absent entitlements are not a default-allow: a project connected without
    them cannot pass the gates, which is §22's fail-closed rule rather than a
    missing implementation. A grant is something someone issues; inventing one
    at composition time would forge it.
    """

    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    oracles: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("scopes", "capabilities", "oracles"):
            value = getattr(self, name)
            if type(value) is not tuple or not value or any(
                type(item) is not str or not item for item in value
            ):
                raise _fail(
                    GoldRunFailureCode.TYPE_MISMATCH,
                    f"{name} must be a non-empty tuple of names",
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "scopes": list(self.scopes),
            "capabilities": list(self.capabilities),
            "oracles": list(self.oracles),
        }

    @classmethod
    def from_dict(cls, value: object) -> "GoldProjectEntitlements":
        if type(value) is not dict:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "entitlements must be an object")
        return cls(
            scopes=tuple(value["scopes"]),
            capabilities=tuple(value["capabilities"]),
            oracles=tuple(value["oracles"]),
        )


@dataclass(frozen=True)
class GoldProjectDeclaration:
    """Everything an operator declares when connecting one repository."""

    repo_root: Path
    state_root: Path
    policy_version: str
    environment_profile_id: str
    identities: GoldProjectIdentities
    entitlements: GoldProjectEntitlements | None = None

    def __post_init__(self) -> None:
        for name in ("repo_root", "state_root"):
            value = getattr(self, name)
            if type(value) is not type(Path()) or not value.is_absolute():
                raise _fail(
                    GoldRunFailureCode.TYPE_MISMATCH,
                    f"{name} must be an exact absolute Path",
                )
        if type(self.environment_profile_id) is not str or not self.environment_profile_id:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "environment_profile_id must be a non-empty string",
            )
        if type(self.policy_version) is not str or _POLICY_VERSION_RE.fullmatch(self.policy_version) is None:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "policy_version must look like name/v1 so every owner accepts it",
            )
        if type(self.identities) is not GoldProjectIdentities:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "identities must be exact")
        if self.entitlements is not None and type(self.entitlements) is not GoldProjectEntitlements:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "entitlements must be exact or None")
        if not self.repo_root.is_dir():
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "the repository must exist before a project is connected to it",
            )


def _builder_runtime_identity(declaration: GoldProjectDeclaration) -> BuilderRuntimeIdentity:
    """The attesting runtime, bound to this repository revision and this build.

    An attester that did not name the revision and the executable it ran as
    would sign behaviors that nobody can later place: §18 makes provenance a
    claim about a specific builder at a specific commit, not about "some
    Synapse".
    """

    raw = f"{RUNTIME_VERSION}\0{BUILDER_RUNTIME_IDENTITY_V1}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return BuilderRuntimeIdentity(
        schema_version=BUILDER_RUNTIME_IDENTITY_V1,
        builder_actor_identity=declaration.identities.builder_actor,
        repository_revision=RepositoryRevision.git_commit(
            resolve_revision(declaration.repo_root, "HEAD")
        ),
        executable_ref=HashBoundRef(
            kind=RefKind.ARTIFACT,
            ref_id=digest,
            schema_id="synapse.stage4.gold.runtime-executable/v1",
            sha256=digest,
            byte_length=len(raw),
            media_type="application/json",
        ),
        runtime_version=_GOLD_RUNTIME_VERSION,
    )


def _record_path(state_root: Path) -> Path:
    return state_root / _RECORD_NAME


def _anchor_of(store: object) -> dict[str, object]:
    """The complete trusted head, not a summary of it.

    Two fields would be enough to notice a truncation and not enough to notice
    a history rewritten under the same length: the anchor's own validator is
    what makes a recorded head checkable, so the whole record is written.
    """

    return store.current_anchor().to_dict()


#: Every recorded head names the history it belongs to, so a lifecycle anchor
#: cannot be replayed as a taint one.
_ANCHOR_DOMAINS = {
    "lifecycle": HistoryDomain.LIFECYCLE,
    "provenance": HistoryDomain.PROVENANCE,
    "taint": HistoryDomain.TAINT,
}


def _trusted_anchor(
    anchors: dict[str, object] | None, name: str, *, configuration_id: object
) -> object | None:
    """Rebuild one recorded head, bound to its domain and this configuration.

    Binding both is what makes the record a trust anchor rather than a hint: an
    anchor from another project's configuration, or from another history of this
    one, is refused here instead of quietly becoming the head a store extends.
    """

    if anchors is None:
        return None
    recorded = anchors.get(name)
    if recorded is None:
        return None
    return history_anchor_from_dict(
        recorded,
        expected_history_domain=_ANCHOR_DOMAINS[name],
        expected_configuration_id=configuration_id,
    )


def connect_gold_project(declaration: GoldProjectDeclaration) -> Path:
    """Create one project's durable Gold world and record its trusted heads.

    Refuses an already-connected state root rather than reopening it: a second
    connect would mint a second authority configuration over histories written
    under the first, and every anchor recorded here would then describe a world
    nobody is using.
    """

    if type(declaration) is not GoldProjectDeclaration:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "declaration must be exact")
    record_path = _record_path(declaration.state_root)
    if record_path.exists():
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "this state root is already connected to a project",
        )
    stores = _open_project_stores(declaration, anchors=None, genesis=True)
    payload = {
        "schema_version": PROJECT_RECORD_SCHEMA_V1,
        "repo_root": str(declaration.repo_root),
        "policy_version": declaration.policy_version,
        "environment_profile_id": declaration.environment_profile_id,
        "identities": declaration.identities.to_dict(),
        "entitlements": (
            None if declaration.entitlements is None else declaration.entitlements.to_dict()
        ),
        "connected_at_utc": datetime.now(timezone.utc).isoformat(),
        "heads": {
            "lifecycle": _anchor_of(stores.lifecycle_store),
            "provenance": _anchor_of(stores.attestation_store),
            "taint": _anchor_of(stores.taint_store),
        },
    }
    declaration.state_root.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return record_path


def read_gold_project_declaration(state_root: Path) -> GoldProjectDeclaration:
    """Read one connected project's declaration back from its state root."""

    if type(state_root) is not type(Path()) or not state_root.is_absolute():
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "state root must be an exact absolute Path")
    record_path = _record_path(state_root)
    if not record_path.exists():
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "this state root holds no connected project",
        )
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    if type(payload) is not dict or payload.get("schema_version") != PROJECT_RECORD_SCHEMA_V1:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "the project record has an unknown schema",
        )
    entitlements = payload.get("entitlements")
    return GoldProjectDeclaration(
        repo_root=Path(payload["repo_root"]),
        state_root=state_root,
        policy_version=payload["policy_version"],
        environment_profile_id=payload["environment_profile_id"],
        identities=GoldProjectIdentities.from_dict(payload["identities"]),
        entitlements=(
            None if entitlements is None else GoldProjectEntitlements.from_dict(entitlements)
        ),
    )


class GoldProjectStores:
    """The six authority histories and the library, sharing one coordinator."""

    __slots__ = (
        "_declaration",
        "_authority_handle",
        "_fence",
        "_library",
        "_lifecycle_store",
        "_attestation_store",
        "_taint_store",
        "_admission_journal",
        "_admission_causal_history",
        "_compatibility_history",
        "_knowledge_store",
        "_trusted_seal",
    )

    def __new__(cls, *args: object, **kwargs: object) -> "GoldProjectStores":
        raise TypeError("GoldProjectStores is factory-created")

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("GoldProjectStores is immutable")

    @property
    def declaration(self) -> GoldProjectDeclaration:
        return self._declaration

    @property
    def authority_handle(self) -> object:
        return self._authority_handle

    @property
    def fence(self) -> FileSnapshotFence:
        return self._fence

    @property
    def library(self) -> BehaviorLibrary:
        return self._library

    @property
    def lifecycle_store(self) -> object:
        return self._lifecycle_store

    @property
    def attestation_store(self) -> object:
        return self._attestation_store

    @property
    def taint_store(self) -> object:
        return self._taint_store

    @property
    def admission_journal(self) -> FileAdmissionJournal:
        return self._admission_journal

    @property
    def admission_causal_history(self) -> FileAdmissionCausalStore:
        return self._admission_causal_history

    @property
    def compatibility_history(self) -> FileCompatibilityStore:
        return self._compatibility_history

    @property
    def knowledge_store(self) -> AuthoritativeKnowledgeStore:
        return self._knowledge_store


def open_gold_project(state_root: Path) -> GoldProjectStores:
    """Reopen a connected project against the heads its record trusts."""

    declaration = read_gold_project_declaration(state_root)
    payload = json.loads(_record_path(state_root).read_text(encoding="utf-8"))
    return _open_project_stores(declaration, anchors=payload["heads"], genesis=False)


def _open_project_stores(
    declaration: GoldProjectDeclaration,
    *,
    anchors: dict[str, object] | None,
    genesis: bool,
) -> GoldProjectStores:
    """Open every store of one project under one shared coordinator.

    Genesis and reopen differ only in what the histories are checked against:
    a new project has no trusted head to extend, a connected one has exactly
    the head its record names.
    """

    root = declaration.state_root
    root.mkdir(parents=True, exist_ok=True)
    for directory in (
        _FENCE_DIR,
        _LIBRARY_DIR,
        _LIFECYCLE_DIR,
        _ATTESTATION_DIR,
        _TAINT_DIR,
        _ADMISSION_DIR,
        _CAUSAL_DIR,
        _COMPATIBILITY_DIR,
        _SNAPSHOT_DIR,
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    fence = FileSnapshotFence(root / _FENCE_DIR)
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=declaration.identities.platform_attester_actor,
        builder_actor=declaration.identities.builder_actor,
        taint_classifier_authority=declaration.identities.taint_classifier_authority,
        taint_reviewer_authority=declaration.identities.taint_reviewer_authority,
        supersession_reviewer_authority=declaration.identities.supersession_reviewer_authority,
        revocation_reviewer_authority=declaration.identities.revocation_reviewer_authority,
        lifecycle_writer_actor=declaration.identities.lifecycle_writer_actor,
        governing_human_authority=declaration.identities.governing_human_authority,
    )
    handle = create_stage4_authority_handle(configuration)
    attester = configure_platform_attester(
        authority_handle=handle,
        builder_runtime_identity=_builder_runtime_identity(declaration),
        trusted_clock=lambda: datetime.now(timezone.utc),
    )
    lifecycle_store = open_lifecycle_store(
        root=root / _LIFECYCLE_DIR,
        authority_handle=handle,
        mutation_fence=fence,
        trusted_anchor=_trusted_anchor(anchors, "lifecycle", configuration_id=configuration.configuration_id),
        allow_genesis=genesis,
    )
    attestation_store = open_behavior_attestation_store(
        root=root / _ATTESTATION_DIR,
        authority_handle=handle,
        platform_attester=attester,
        mutation_fence=fence,
        trusted_anchor=_trusted_anchor(anchors, "provenance", configuration_id=configuration.configuration_id),
        allow_genesis=genesis,
    )
    taint_store = open_taint_history_store(
        root=root / _TAINT_DIR,
        authority_handle=handle,
        mutation_fence=fence,
        trusted_anchor=_trusted_anchor(anchors, "taint", configuration_id=configuration.configuration_id),
        allow_genesis=genesis,
    )
    admission_journal = FileAdmissionJournal(
        root / _ADMISSION_DIR / "decisions.journal", fence
    )
    causal_history = FileAdmissionCausalStore(
        root / _CAUSAL_DIR,
        mutation_fence=fence,
        admission_history=admission_journal,
    )
    compatibility_history = FileCompatibilityStore(
        root / _COMPATIBILITY_DIR, mutation_fence=fence
    )
    library = create_program_artifact_behavior_library(
        root / _LIBRARY_DIR,
        publisher_identity=PublisherIdentity(
            schema_version=LIBRARY_PUBLISHER_IDENTITY_V1,
            component_id="synapse.stage4.gold.publisher",
            policy_version=declaration.policy_version,
        ),
        mutation_fence=fence,
        write_history=admission_journal,
    )
    knowledge_store = AuthoritativeKnowledgeStore(root / _SNAPSHOT_DIR, mutation_fence=fence)

    result = object.__new__(GoldProjectStores)
    for name, item in {
        "_declaration": declaration,
        "_authority_handle": handle,
        "_fence": fence,
        "_library": library,
        "_lifecycle_store": lifecycle_store,
        "_attestation_store": attestation_store,
        "_taint_store": taint_store,
        "_admission_journal": admission_journal,
        "_admission_causal_history": causal_history,
        "_compatibility_history": compatibility_history,
        "_knowledge_store": knowledge_store,
        "_trusted_seal": _PROJECT_STORES_SEAL,
    }.items():
        object.__setattr__(result, name, item)
    return require_gold_project_stores(result)


def require_gold_project_stores(value: object) -> GoldProjectStores:
    """Refuse anything but a sealed store set built by this root."""

    if (
        type(value) is not GoldProjectStores
        or getattr(value, "_trusted_seal", None) is not _PROJECT_STORES_SEAL
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "an exact sealed project store set is required",
        )
    return value


__all__ = [
    "PROJECT_RECORD_SCHEMA_V1",
    "GoldProjectDeclaration",
    "GoldProjectEntitlements",
    "GoldProjectIdentities",
    "GoldProjectStores",
    "connect_gold_project",
    "open_gold_project",
    "read_gold_project_declaration",
    "require_gold_project_stores",
]
