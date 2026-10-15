"""Build a real joint-source publication for worker scope acceptance."""
import json

from acceptance.stage4.stage16._source_inputs import prepare, learn
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.project_agents import read_active_memory
from synapse.experiments.gold.source_delivery import source_experience_delivery
from synapse.experiments.gold.source_snapshot import (
    capture_project_source_snapshot, memory_source_basis, read_source_snapshot,
)
from synapse.experiments.gold.source_verification import canonical, source_ref
from synapse.experiments.gold.stage10.context_codec import decode_base64url
from synapse.experiments.gold.stage10.intent import (
    AcceptanceCriterion, AcceptanceKind, EffectConstraint, EffectDisposition, EffectKind,
)
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract, TASK_CONTRACT_SCHEMA_V3
from synapse.experiments.gold.task_targets import resolve_task_targets
from synapse.memory_consolidation.project_port import ProjectMemoryCourt


SOURCES = {
    "routing/ring.py": (
        "from consensus.quorum import majority\n"
        "def route_owner(partition, epoch, voters):\n"
        "    if not majority(voters, 24):\n        raise ValueError('quorum unavailable')\n"
        "    return (partition * 17 + epoch) % 24\n"
    ),
    "consensus/quorum.py": (
        "def majority(voters, nodes):\n"
        "    return len(set(voters)) > nodes // 2\n"
    ),
    "billing/ledger.py": (
        "BILLING_RULE = 'IRRELEVANT_SETTLEMENT_CURRENCY_SENTINEL'\n"
        "def settle(amount, rate):\n    return amount * rate\n"
    ),
}


def publish_case(root, *, recipe=None):
    repo, state, path = prepare(root, execute=recipe is not None, extra_sources=SOURCES)
    value = json.loads(path.read_text())
    claim = value["claim"]
    version = claim["symbols"][0]["contract_version"]
    claim.update(operation_id="routing-quorum-billing", sources=sorted(SOURCES), symbols=[
        {"path": name, "module": name[:-3].replace("/", "."), "qualname": symbol,
         "symbol_kind": "FUNCTION", "contract_version": version}
        for name, symbol in (("routing/ring.py", "route_owner"),
                             ("consensus/quorum.py", "majority"), ("billing/ledger.py", "settle"))])
    if recipe is not None:
        claim.update(kind="VERIFICATION_RECIPE", recipe=recipe)
    path.write_text(json.dumps(value))
    code, publication = learn(state, path)
    assert code == 0 and publication["status"] == "PUBLISHED", publication
    return root, repo, state, claim["revision"]


def capture(published_sources, label, paths):
    root, repo, state, revision = published_sources
    condition = source_ref(b"1536 partitions, 24 nodes, quorum and epoch fencing",
                           "acceptance.partition-migration/v1", RefKind.CONTRACT_CONDITION)
    task = GoverningTaskContract(task_id=label,
        task_statement="Repair route_owner epoch fencing across 1536 partitions with majority quorum.",
        repository_revision_sha256=revision, allowed_scope=create_repository_scope(paths),
        required_capabilities=("source:patch",), target_bindings=(),
        effects=(EffectConstraint("routing", EffectDisposition.EXPECTED,
                                 EffectKind.PATH_MODIFIED, "routing/ring.py", condition),),
        acceptance=(AcceptanceCriterion("migration", AcceptanceKind.CONTRACT_CONDITION, condition),),
        schema_version=TASK_CONTRACT_SCHEMA_V3)
    resolution = json.loads(resolve_task_targets(task=task, repository_root=repo))
    snapshot, _, _ = capture_project_source_snapshot(project=open_gold_project(state), task=task, limit=64,
        target_resolution=resolution, run_root=root / label, run_id=label, court=ProjectMemoryCourt())
    path = root / (label + ".json")
    path.write_bytes(canonical(snapshot))
    restored = json.loads(path.read_bytes())
    assert read_source_snapshot(restored, task=task) == snapshot["recall"]
    held = read_active_memory(restored["project_memory"], source_snapshot=memory_source_basis(restored),
                              run_memory_selection="ALL")
    return restored, held


def source_records(snapshot):
    delivery = source_experience_delivery(snapshot)
    records = [json.loads(decode_base64url(item["content_base64url"]))
               for item in delivery["information"]["items"]]
    return [item for item in records if "sources" in item]
