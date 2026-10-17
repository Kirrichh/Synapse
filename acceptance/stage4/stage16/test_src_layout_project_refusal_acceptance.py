"""What a src-layout project refuses before any effect.

A claim written under the other binding contract than the project's is refused before the operation is
journaled: a project that declares import roots binds under contract v2 only, one that declares none under
v1 only. Import roots that cannot name modules at the connected commit refuse the connection, and no project
record is written.
"""
import json
from pathlib import Path

import pytest

from acceptance.stage4.stage16 import _src_layout as layout
from acceptance.stage4.stage16._source_inputs import learn, write_learn_input
from synapse.experiments.gold.bindings import BINDING_CONTRACT_VERSION_V1, BINDING_CONTRACT_VERSION_V2
from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity
from synapse.experiments.gold.knowledge_environment import (
    PROJECT_RECORD_SCHEMA_V1, PROJECT_RECORD_SCHEMA_V2, GoldProjectDeclaration, GoldProjectIdentities,
)
from synapse.experiments.gold.runner.vocabulary import GoldRunViolation
from synapse.experiments.gold.source_operation_journal import read_source_operations


@pytest.mark.parametrize(
    ("roots", "module", "contract"),
    [
        (["src"], "src.shapes.area", BINDING_CONTRACT_VERSION_V1),
        (None, "shapes.area", BINDING_CONTRACT_VERSION_V2),
    ],
)
def test_a_claim_under_the_other_binding_contract_is_refused_before_it_is_journaled(
    tmp_path: Path, roots: list[str] | None, module: str, contract: str
) -> None:
    repo, revision = layout.repository(tmp_path, {"src/shapes/__init__.py": "", "src/shapes/area.py": layout.AREA})
    connected = layout.connect(tmp_path, repo, **({} if roots is None else {"python_roots": roots}))
    assert connected.returncode == 0, connected.stderr
    state = tmp_path / "state"
    assert json.loads((state / "project.json").read_text(encoding="utf-8"))["schema_version"] == (
        PROJECT_RECORD_SCHEMA_V1 if roots is None else PROJECT_RECORD_SCHEMA_V2)

    code, result = learn(state, write_learn_input(tmp_path, layout.claim(revision, module, contract)))

    assert (code, result) == (2, {"status": "REFUSED",
                                  "reason": "source claim names modules under another binding contract than the project's"})
    assert read_source_operations(state) == ()


@pytest.mark.parametrize(
    ("files", "roots", "reason"),
    [
        ({"lib/shapes.py": ""}, ["src"], "an import root is not a directory of the commit"),
        ({"src/__init__.py": "", "src/shapes.py": ""}, ["src"], "an import root is a package or lies inside one"),
        ({"src/shapes.py": ""}, ["src/../lib"], "a python root is not a canonical repository directory"),
        ({"src/shapes.py": ""}, ["src", "src"], "a python root is declared twice"),
    ],
)
def test_roots_that_cannot_name_modules_refuse_the_connection(
    tmp_path: Path, files: dict[str, str], roots: list[str], reason: str
) -> None:
    repo, _ = layout.repository(tmp_path, files)
    connected = layout.connect(tmp_path, repo, python_roots=roots)
    assert connected.returncode != 0
    assert reason in connected.stderr
    assert not (tmp_path / "state" / "project.json").exists()


def test_a_project_record_that_lost_its_import_roots_is_refused(tmp_path: Path) -> None:
    repo, _ = layout.repository(tmp_path, {"src/shapes/__init__.py": "", "src/shapes/area.py": layout.AREA})
    assert layout.connect(tmp_path, repo, python_roots=["src"]).returncode == 0
    record_path = tmp_path / "state" / "project.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    del record["python_roots"]  # A record v2 binds under its roots; without them it cannot be read as v1.
    record_path.write_text(json.dumps(record), encoding="utf-8")

    status = layout.cli("project", "status", "--state-dir", str(tmp_path / "state"))

    assert status.returncode != 0
    assert "the project record is missing required fields: ['python_roots']" in status.stderr


def test_a_declaration_holds_its_import_roots_canonical(tmp_path: Path) -> None:
    repo, _ = layout.repository(tmp_path, {"src/shapes.py": "", "tests/test_shapes.py": ""})
    identities = GoldProjectIdentities(ActorIdentity("a"), ActorIdentity("b"), AuthorityIdentity("c"),
                                       AuthorityIdentity("d"), AuthorityIdentity("e"), AuthorityIdentity("f"),
                                       ActorIdentity("g"), AuthorityIdentity("h"))

    def declaration(roots):
        return GoldProjectDeclaration(repo, tmp_path / "state", "source-policy/v1", "source-env", identities,
                                      python_roots=roots)

    assert declaration(("src", "tests")).python_roots == ("src", "tests")
    for roots in (("tests", "src"), ("src/",), ["src"]):
        with pytest.raises(GoldRunViolation):
            declaration(roots)
