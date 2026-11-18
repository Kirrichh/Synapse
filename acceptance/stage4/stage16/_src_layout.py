"""A src-layout repository connected through the canonical CLI, and claims about its one module.

``src/shapes/area.py`` is imported as ``shapes.area``. A project that declares the import root ``src``
binds it under binding contract v2 by that name; a project that declares none binds it under v1 by its
repository path (``src.shapes.area``).
"""
import json
from pathlib import Path
import subprocess
import sys

from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity
from synapse.experiments.gold.knowledge_environment import GoldProjectIdentities
from synapse.experiments.gold.source_verification import SOURCE_CLAIM_V1

REPO_ROOT = Path(__file__).resolve().parents[3]
AREA = "def circle(radius):\n    return 3 * radius * radius\n"


def cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", "-m", "synapse", *argv], cwd=REPO_ROOT, text=True,
                          capture_output=True, timeout=180)


def repository(root: Path, files: dict[str, str]) -> tuple[Path, str]:
    """A Git repository holding ``files`` in one commit, and that commit."""
    repo = root / "repo"
    repo.mkdir()
    for argv in (("init", "-q"), ("config", "user.name", "Acceptance"),
                 ("config", "user.email", "acceptance@example.invalid")):
        subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "src layout"], cwd=repo, check=True, capture_output=True)
    return repo, subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def connect(root: Path, repo: Path, **declared: object) -> subprocess.CompletedProcess:
    """``project connect`` of ``repo`` into ``root/state``, with the declaration's extra fields."""
    identities = GoldProjectIdentities(ActorIdentity("src-attester"), ActorIdentity("src-builder"),
        AuthorityIdentity("src-classifier"), AuthorityIdentity("src-reviewer"), AuthorityIdentity("src-supersession"),
        AuthorityIdentity("src-revocation"), ActorIdentity("src-lifecycle"), AuthorityIdentity("src-operator"))
    declaration = root / "declaration.json"
    declaration.write_text(json.dumps({
        "policy_version": "source-policy/v1", "environment_profile_id": "source-env",
        "identities": identities.to_dict(),
        "entitlements": {"scopes": ["src", "tests"], "capabilities": ["read"], "oracles": ["change-oracle", "source-verifier"]},
        **declared}), encoding="utf-8")
    return cli("project", "connect", "--repo", str(repo), "--state-dir", str(root / "state"),
               "--declaration", str(declaration))


def claim(revision: str, module: str, contract: str) -> dict:
    """A fact check of ``shapes.area.circle`` naming its module as ``module`` under binding ``contract``."""
    return {"schema_version": SOURCE_CLAIM_V1, "operation_id": "learn-area", "revision": revision,
            "kind": "REPOSITORY_FACT_CHECK", "sources": ["src/shapes/area.py"], "replay_gas_budget": 10_000,
            "symbols": [{"path": "src/shapes/area.py", "module": module, "qualname": "circle",
                         "symbol_kind": "FUNCTION", "contract_version": contract}], "recipe": None}
