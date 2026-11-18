"""Only the granted and measured executable can supply a recipe observation."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import pytest

from acceptance.stage4.stage16._source_inputs import learn, prepare
from synapse.experiments.gold.compatibility import _source_gap, CompatibilityReason
from synapse.experiments.gold.contracts import RepositoryRevision
from synapse.experiments.gold.knowledge_environment import connect_gold_project, open_gold_project
from synapse.experiments.gold.recipe_executable import RecipeExecutable, admit_recipe_executable
from synapse.experiments.gold.source_verification import observe_source_runtime


def new_recipe(tmp_path, *, grant):
    repo, state, input_path = prepare(tmp_path, execute=True)
    if os.name == "nt":
        original = shutil.which("findstr.exe")
        executable = tmp_path / "native-check.exe"
        command_args = ["return", "calc.py"]
    else:
        # Multicall implementations of cat select their operation by invocation name.
        original = shutil.which("cat")
        executable = tmp_path / "cat"
        command_args = ["calc.py"]
    shutil.copy2(original, executable)
    tool = RecipeExecutable(
        executable.stem, str(executable), hashlib.sha256(executable.read_bytes()).hexdigest()
    )
    if grant:
        declaration = open_gold_project(state).declaration
        state = tmp_path / "granted"
        connect_gold_project(
            replace(
                declaration,
                state_root=state,
                entitlements=replace(declaration.entitlements, recipe_tools=(tool,)),
            )
        )
    spec = json.loads(input_path.read_text())
    spec["claim"].update(
        kind="VERIFICATION_RECIPE",
        operation_id="recipe-one",
        recipe={
            "tool": tool.to_dict(),
            "command": [tool.name, *command_args],
            "expectation": {
                "expected_exit_codes": [0],
                "expected_nonzero_exit": False,
                "combined_output_contains": ["return value * 2"],
                "combined_output_not_contains": [],
                "timeout_seconds": 10,
            },
        },
    )
    input_path.write_text(json.dumps(spec))
    return state, executable, tool, spec, input_path


def test_ungranted_native_recipe_is_refused(tmp_path):
    state, _, _, _, path = new_recipe(tmp_path, grant=False)
    rc, value = learn(state, path)
    assert rc == 2 and value["status"] == "REFUSED" and "operator grant" in value["reason"]


def test_changed_tool_cannot_reuse_an_identical_claim_or_get_a_second_receipt(tmp_path, monkeypatch):
    state, path, tool, spec, input_path = new_recipe(tmp_path, grant=True)
    if os.name != "nt":
        # A PATH lookup must fail even when the host also provides a working cat.
        search_dir = tmp_path / "search-path"
        search_dir.mkdir()
        ungranted = search_dir / tool.name
        ungranted.write_text("#!/bin/sh\nexit 23\n", encoding="ascii")
        ungranted.chmod(0o755)
        monkeypatch.setenv("PATH", str(search_dir), prepend=os.pathsep)
    rc, value = learn(state, input_path)
    assert rc == 0 and value["status"] == "PUBLISHED", value
    spec["claim"]["operation_id"] = "known"
    input_path.write_text(json.dumps(spec))
    rc, value = learn(state, input_path)
    assert rc == 0 and value["status"] == "ALREADY_KNOWN", value
    prior = observe_source_runtime(tool)
    with path.open("ab") as stream:
        stream.write(b"changed program bytes")
    spec["claim"]["operation_id"] = "changed-binary"
    input_path.write_text(json.dumps(spec))
    rc, failure = learn(state, input_path)
    assert rc == 2 and failure["status"] == "REFUSED" and "measured bytes" in failure["reason"]
    assert learn(state, input_path) == (rc, failure)
    revision = RepositoryRevision.git_commit(spec["claim"]["revision"])
    # Check that a new consumer of the successful historical observation also rejects current bytes.
    context = SimpleNamespace(repository_revision=revision, allowed_scope=("calc.py",))
    evidence = SimpleNamespace(bindings=[SimpleNamespace(path="calc.py")])
    facts = {"claim": spec["claim"], "observed_runtime": prior, "sources": []}
    assert _source_gap(None, context, evidence, facts) is CompatibilityReason.ENVIRONMENT_MISMATCH


def test_wrong_command_cannot_use_another_tool_grant(tmp_path):
    state, _, _, spec, path = new_recipe(tmp_path, grant=True)
    spec["claim"]["recipe"]["command"] = ["different-tool"]
    path.write_text(json.dumps(spec))
    rc, value = learn(state, path)
    assert rc == 2 and value["status"] == "REFUSED" and "differs from its declared tool" in value["reason"]


def test_legacy_python_recipe_recognizes_its_interpreter_only_in_the_same_environment(tmp_path):
    python = Path(sys.executable)
    equivalent = python.parent / ".." / python.parent.name / python.name
    assert admit_recipe_executable({"command": [str(equivalent)]}, ()) is None
    with pytest.raises(ValueError, match="measured Python interpreter"):
        admit_recipe_executable({"command": [str(tmp_path / python.name)]}, ())
