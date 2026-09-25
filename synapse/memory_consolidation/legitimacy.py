"""The court's port to Gold legitimacy for learned-habit behaviors.

A birth becomes a claim Gold verifies itself (record identities, asserted
criteria, retained bytes of the recorded results of its basis) and a behavior
Gold's INGESTION and PUBLICATION gates admit through its one project writer;
a refusal or quarantine cancels the birth with its reason. The status of an
admitted behavior is Gold's lifecycle state for its context and the equality of
its tool binding with the current configuration's. A successor's supersession
is proposed to Gold's supersession authority. Nothing here decides
effectiveness, and nothing bypasses a gate.
"""
from __future__ import annotations

import subprocess
from typing import Any, Mapping

from synapse.experiments.gold.admission_journal import JournalAdapterViolation
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.experiments.gold.knowledge_environment import _builder_runtime_identity, open_gold_project
from synapse.experiments.gold.learned_habit_lifecycle import learned_habit_status, supersede_learned_habit
from synapse.experiments.gold.learned_habit_profile import (
    LEARNED_HABIT_CLAIM_V1,
    MEMORY_EVIDENCE_V1,
    verify_learned_habit,
)
from synapse.experiments.gold.source_verification import source_ref
from synapse.experiments.gold.stage12.reusable import ReusableVerificationAuthority
from synapse.experiments.gold.stage13.publication import PublicationAuthority
from synapse.experiments.gold.stage13.publication_store import PublicationResult, PublicationStore

from .configuration import MemoryConfiguration
from .owner import MemoryOwner
from .tools.evidence import EvidenceStore


class GoldLegitimacy:
    """Gold admission of the learned behaviors of one memory owner."""

    def __init__(self, owner: MemoryOwner, evidence: EvidenceStore, executor: str,
                 configuration: MemoryConfiguration) -> None:
        self.owner = owner
        self.evidence = evidence
        self.executor = executor
        self.configuration = configuration
        self._project = None

    @property
    def project(self):
        if self._project is None:
            self._project = open_gold_project(self.owner.state_root)
        return self._project

    def _revision(self) -> str:
        return subprocess.run(["git", "-C", str(self.project.declaration.repo_root), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()

    def _evidence(self, refs) -> tuple[list[dict], dict]:
        claimed, retained = [], {}
        for ref_id in sorted(set(refs)):
            raw = (self.evidence.root / f"{ref_id}.json").read_bytes()
            ref = source_ref(raw, MEMORY_EVIDENCE_V1, RefKind.SOURCE_EVIDENCE)
            if ref.sha256 != ref_id:
                raise ValueError("recorded evidence no longer matches its address")
            claimed.append(ref.to_dict())
            retained[ref] = raw
        return claimed, retained

    def claim(self, birth: Mapping[str, Any], configuration: MemoryConfiguration) -> tuple[dict, dict]:
        """The court's birth claim and the bytes Gold retains with it."""
        evidence, retained = self._evidence(birth.get("evidence", []))
        predecessor = (birth.get("boundary") or {}).get("predecessor")
        claim = {"schema_version": LEARNED_HABIT_CLAIM_V1, "project_identity": self.owner.identity,
                 "revision": self._revision(), "consolidation_id": birth["habit"]["born_from"]["consolidation"],
                 "habit": birth["habit"], "trigger": birth["trigger"], "criteria": birth.get("criteria"),
                 "independence": birth.get("independence") or {"verdict": None}, "successor_of": predecessor,
                 "tool_binding_sha256": configuration.tool_binding_sha256,
                 "tool_contracts": [contract.canonical() for _, contract in sorted(configuration.tools.tools.items())],
                 "policy": configuration.policy,
                 "environment": {"configuration_sha256": configuration.configuration_sha256,
                                 "executor": self.executor, "element": configuration.element},
                 "evidence": evidence}
        return claim, retained

    def _publisher(self):
        project = self.project
        owners = ReusableVerificationAuthority(
            project.declaration.repo_root, project.declaration.environment_profile_id, project.authority_handle,
            project.library, project.attestation_store, project.lifecycle_store, project.admission_journal,
            project.fence, None, project.compatibility_history)
        authority = PublicationAuthority(owners, project.taint_store, _builder_runtime_identity(project.declaration), ())
        return authority, PublicationStore(root=self.owner.state_root / "publications", authority=authority)

    def publish(self, birth: Mapping[str, Any], configuration: MemoryConfiguration) -> dict[str, Any]:
        """Gold's gates for one birth: admitted with its publication, or refused with a reason."""
        try:
            claim, retained = self.claim(birth, configuration)
            verification = verify_learned_habit(claim, retained)
            authority, store = self._publisher()
            request = authority.prepare_learned_habit(verification)
            published = store.publish(request)
        except (ValueError, TypeError, OSError, subprocess.CalledProcessError, PersistenceViolation,
                JournalAdapterViolation) as exc:
            # Gold refused, quarantined or could not complete: no admission, and the reason is reported.
            return {"admitted": False, "reason": f"gold_refused:{type(exc).__name__}:{str(exc)[:200]}",
                    "publication": None}
        if published is None:
            return {"admitted": False, "reason": "gold_quarantined", "publication": None}
        result = published.payload()
        registration = result["registration"]
        publication = {"transaction_id": result["transaction_id"],
                       "result_ref": PublicationResult(store.root, result["transaction_id"]).reference.to_dict(),
                       "attestation_ref": request.payload()["attestation_ref"],
                       "lifecycle_context": registration["lifecycle_context"],
                       "verification_ref": verification.reference.to_dict(),
                       "tool_binding_sha256": configuration.tool_binding_sha256, "habit_id": birth["habit"]["id"]}
        status = self.status(birth["habit"]["id"], {"publication": publication})
        if not status["admitted"]:
            return {"admitted": False, "reason": f"gold_not_consumable:{status['state']}", "publication": None}
        return {"admitted": True, "reason": None, "publication": publication, "status": status,
                "gates": {name: registration[name]["ref"] for name in ("ingestion", "publication")}}

    def status(self, habit_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        return learned_habit_status(self.project, metadata["publication"],
                                    tool_binding_sha256=self.configuration.tool_binding_sha256)

    def supersede(self, predecessor, successor) -> None:
        if predecessor is not None:
            supersede_learned_habit(self.project, predecessor, successor)

