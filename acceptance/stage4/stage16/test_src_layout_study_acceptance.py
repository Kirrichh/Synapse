"""A study of a src-layout project names its modules by their import names and binds no ambiguous one.

A project that declares the import root ``src`` studies its sources under binding contract v2: the
structural identities discovered in ``src/shapes/area.py`` are the module ``shapes.area`` and its function
``circle``. A module name that two paths derive (``src/tools.py`` beside the package ``src/tools/``) binds
nothing and is reported uninterpreted with ``MODULE_AMBIGUOUS``.
"""
import json
from pathlib import Path

from acceptance.stage4.stage16 import _src_layout as layout
from acceptance.stage4.stage16._source_inputs import learn, recall, write_learn_input
from synapse.experiments.gold.bindings import BINDING_CONTRACT_VERSION_V2
from synapse.experiments.gold.source_ingestion import SOURCE_INGESTION_V2
from synapse.experiments.gold.source_verification import SOURCE_CLAIM_V2


def test_a_rooted_project_studies_its_modules_by_their_import_names(tmp_path: Path) -> None:
    repo, revision = layout.repository(tmp_path, {"src/shapes/__init__.py": "", "src/shapes/area.py": layout.AREA,
                                                  "src/tools.py": "", "src/tools/__init__.py": ""})
    connected = layout.connect(tmp_path, repo, python_roots=["src"])
    assert connected.returncode == 0, connected.stderr
    state = tmp_path / "state"
    study = {**layout.claim(revision, "shapes.area", BINDING_CONTRACT_VERSION_V2), "schema_version": SOURCE_CLAIM_V2,
             "kind": "SOURCE_STUDY", "symbols": [], "sources": ["src/shapes/area.py", "src/tools.py"]}
    path = write_learn_input(tmp_path, study)
    path.write_text(json.dumps({**json.loads(path.read_text()), "schema_version": SOURCE_INGESTION_V2}))

    code, result = learn(state, path)

    assert code == 0 and result["status"] == "RETAINED", result
    code, found = recall(state, tmp_path, study, statement="circle area of shapes")
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert {(item["module"], item["qualname"], item["contract_version"]) for item in record["bindings"]} == {
        ("shapes.area", "shapes.area", BINDING_CONTRACT_VERSION_V2), ("shapes.area", "circle", BINDING_CONTRACT_VERSION_V2)}
    assert [(item["path"], item["reason"]) for item in record["uninterpreted"]] == [("src/tools.py", "MODULE_AMBIGUOUS")]
