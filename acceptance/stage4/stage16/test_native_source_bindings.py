"""Committed and retained native declarations select the same task target."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from acceptance.stage4.stage16._source_inputs import prepare
from synapse.experiments.gold.bindings import (
    BindingKind,
    BindingViolation,
    binding_from_dict,
    discover_code_bindings,
    discover_retained_code,
    inspect_retained_code_binding,
)
from synapse.experiments.gold.contracts import RepositoryRevision
from synapse.experiments.gold.native_syntax import CONTRACT

pytest.importorskip("tree_sitter_cpp")
pytest.importorskip("tree_sitter_go")
pytest.importorskip("tree_sitter_javascript")

cases = {
    item["language"]: (item["path"], item["source"])
    for item in json.loads(Path(__file__).with_name("language_declarations.json").read_text())
    if item["language"] in {"cpp", "go", "javascript"}
}


@pytest.mark.parametrize("language", cases)
def test_symbol_is_bound_to_committed_and_retained_bytes(tmp_path, language):
    target, content = cases[language]
    repo, _, claim_path = prepare(tmp_path, extra_sources={target: content})
    revision = RepositoryRevision.git_commit(json.loads(claim_path.read_text())["claim"]["revision"])
    changed = content.replace("a - b", "a * b")
    (repo / target).write_text(changed)
    symbols = discover_code_bindings(repo, repository_revision=revision, path=target)
    fn = next(b for b in symbols if b.qualname == ("Add" if language == "go" else "add"))
    assert (
        fn.language == language
        and fn.binding_kind is BindingKind.NATIVE_CODE
        and fn.contract_version == CONTRACT
    )
    assert fn.module == target
    transport = fn.to_dict()
    assert binding_from_dict(transport, repo_root=repo, consumer_revision=revision).to_dict() == transport
    assert inspect_retained_code_binding(transport, content.encode()).to_dict() == transport
    with pytest.raises(BindingViolation):
        inspect_retained_code_binding(transport, changed.encode())
    with pytest.raises(BindingViolation):
        binding_from_dict(
            {**transport, "source_span_hash": "0" * 64}, repo_root=repo, consumer_revision=revision
        )


@pytest.mark.parametrize(
    "target,content",
    [
        ("sum.cpp", "int add(int x) { return x; }\ndouble add(double x) { return x; }"),
        ("sum.js", "function sum( {"),
        ("sum.go", "package add\nfunc Sum(x int"),
        ("sum.unknown", "fn sum() {}"),
    ],
)
def test_ambiguous_invalid_and_unsupported_source_has_no_binding(target, content):
    revision = RepositoryRevision.git_commit("1" * 40)
    with pytest.raises(BindingViolation):
        discover_retained_code(content.encode(), repository_revision=revision, path=target)


@pytest.mark.parametrize(
    "target,text,names",
    [
        (
            "sum.cpp",
            "namespace math { struct Box { int get() { return 3; } }; }",
            {"math.Box": "CLASS", "math.Box.get": "METHOD"},
        ),
        (
            "sum.mjs",
            "export class Box { get() { return 3; } }\nexport const sum = (a,b) => a+b;",
            {"Box": "CLASS", "Box.get": "METHOD", "sum": "FUNCTION"},
        ),
        (
            "sum.go",
            "package sum\ntype Box struct {}\nfunc (b *Box) Get() int { return 3 }\n",
            {"Box": "TYPE", "Box.Get": "METHOD"},
        ),
    ],
)
def test_qualified_declarations_keep_their_owner(target, text, names):
    records = discover_retained_code(
        text.encode(), repository_revision=RepositoryRevision.git_commit("1" * 40), path=target
    )
    assert {r.qualname: r.symbol_kind.value for r in records if r.qualname != target} == names


@pytest.mark.parametrize("language", cases)
def test_governing_task_selects_the_committed_native_function(tmp_path, language):
    from acceptance.stage4.stage10._builders import plan_world
    from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
    from synapse.experiments.gold.stage10.task_contract import TASK_CONTRACT_SCHEMA_V3
    from synapse.experiments.gold.task_targets import resolve_task_targets, read_task_targets

    name = "Add" if language == "go" else "add"
    target, content = cases[language]
    repo, _, input_path = prepare(tmp_path, extra_sources={target: content})
    revision = json.loads(input_path.read_text())["claim"]["revision"]
    original = plan_world()[3].task_contract
    task = replace(
        original,
        schema_version=TASK_CONTRACT_SCHEMA_V3,
        behavior_refs=(),
        target_bindings=(),
        task_statement=f"Fix {name} in {target}.",
        repository_revision_sha256=revision,
        allowed_scope=create_repository_scope((target,)),
        effects=(replace(original.effects[0], subject_path=target),),
    )
    resolution = resolve_task_targets(task=task, repository_root=repo)
    selected = read_task_targets(resolution, task=task, repository_root=repo)
    assert {binding.qualname for binding in selected} == {name, target}
