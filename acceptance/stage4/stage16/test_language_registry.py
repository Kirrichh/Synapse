"""Structural proofs and refusal conditions for installed Gold language profiles."""

import base64
import hashlib
from importlib.metadata import Distribution
import json
from pathlib import Path

import pytest

from synapse.experiments.gold.bindings import (
    BindingViolation,
    discover_retained_code,
    inspect_retained_code_binding,
)
from synapse.experiments.gold.contracts import RepositoryRevision
from synapse.experiments.gold.languages import registry as profiles

REVISION = RepositoryRevision.git_commit("d" * 40)
EXAMPLES = json.loads(Path(__file__).with_name("language_declarations.json").read_text())


def discover(path, raw):
    return discover_retained_code(raw, repository_revision=REVISION, path=path)


@pytest.mark.parametrize("example", EXAMPLES, ids=[e["language"] for e in EXAMPLES])
def test_declaration_binds_its_implementation(example):
    if example["language"] != "python":
        pytest.importorskip("tree_sitter")
    if example["language"] == "r" and profiles.for_path(example["path"]) is None:
        pytest.skip("the separately distributed R profile is not installed")
    if example["language"] not in {"python", *profiles.ORIGINAL}:
        pytest.importorskip("tree_sitter_language_pack")
    if example["language"] in profiles.ORIGINAL:
        pytest.importorskip("tree_sitter_" + example["language"])
    path, text, name = example["path"], example["source"], example["symbol"]
    result = discover(path, text.encode())
    binding = next(b for b in result if b.qualname == name)
    assert binding.symbol_kind.value == example["kind"]
    assert inspect_retained_code_binding(binding.to_dict(), text.encode()).to_dict() == binding.to_dict()
    edited = text.replace(example["old"], example["fixed"]).encode()
    new = next(b for b in discover(path, edited) if b.qualname == name)
    assert new.source_span_hash != binding.source_span_hash
    with pytest.raises(BindingViolation):
        inspect_retained_code_binding(binding.to_dict(), edited)


def test_header_requires_a_unique_owner():
    assert profiles.for_path("src/io.h") is None


def test_absent_language_refuses_a_named_binding():
    with pytest.raises(BindingViolation):
        discover("calc.unknown", b"anything here")


def test_duplicate_declarations_are_rejected():
    pytest.importorskip("tree_sitter_language_pack")
    with pytest.raises(BindingViolation, match="ambiguous"):
        discover("calc.rs", b"fn f() {} fn f() {}")


def test_syntax_errors_do_not_produce_a_module():
    pytest.importorskip("tree_sitter_language_pack")
    with pytest.raises(BindingViolation):
        discover("calc.rs", b"pub fn broken(")


def test_tsx_uses_its_own_grammar():
    pytest.importorskip("tree_sitter_language_pack")
    assert "Page" in [b.qualname for b in discover("x.tsx", b"export function Page() { return <div/>; }")]


def test_labels_cover_their_instructions_but_stop_at_the_next_label():
    pytest.importorskip("tree_sitter_language_pack")
    src = b".text\nfirst:\n ret\nsecond:\n ret\n"

    def names(text):
        return {b.qualname: b.source_span_hash for b in discover("x.s", text)}

    before, after = names(src), names(src.replace(b"first:\n ret", b"first:\n nop\n ret"))
    assert before["first"] != after["first"] and before["second"] == after["second"]


def test_r_profile_content_affects_identity():
    data = json.loads(
        (Path(__file__).resolve().parents[3] / "language-packs/r/synapse_gold_r/profile.json").read_text()
    )
    first = profiles._profile(data, "demo==1", external=True)
    other = profiles._profile({**data, "query": data["query"] + "\n"}, "demo==1", external=True)
    assert first.resolver != other.resolver


def test_external_package_cannot_select_an_implementation_driver():
    data = json.loads(
        (Path(__file__).resolve().parents[3] / "language-packs/r/synapse_gold_r/profile.json").read_text()
    )
    with pytest.raises(profiles.LanguageError):
        profiles._profile({**data, "driver": "python"}, "demo==1", external=True)


def test_installed_profile_binds_the_recorded_resource_without_importing_its_package(tmp_path, monkeypatch):
    package = tmp_path / "example_profile"
    package.mkdir()
    (package / "__init__.py").write_text('raise AssertionError("profile package was executed")')
    resource = package / "profile.json"
    source = Path(__file__).resolve().parents[3] / "language-packs/r/synapse_gold_r/profile.json"
    raw = source.read_bytes()
    resource.write_bytes(raw)
    dist_info = tmp_path / "example_profile-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: example-profile\nVersion: 1.0\n")
    (dist_info / "entry_points.txt").write_text(f"[{profiles.GROUP}]\nr = example_profile:profile\n")
    digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
    (dist_info / "RECORD").write_text(f"example_profile/profile.json,sha256={digest},{len(raw)}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    (point,) = Distribution.at(dist_info).entry_points
    assert profiles._installed_profile(point).distribution == "example-profile==1.0"
    resource.write_bytes(raw.replace(b'"label": "R"', b'"label": "X"'))
    assert resource.read_bytes() != raw and resource.stat().st_size == len(raw)
    with pytest.raises(profiles.LanguageError, match="installation record"):
        profiles._installed_profile(point)


def test_operator_reports_an_unavailable_grammar(capsys, monkeypatch):
    from synapse.cli import main

    def unavailable(profile_id, grammar):
        raise profiles.LanguageError("grammar deliberately unavailable")

    monkeypatch.setattr(profiles, "parser_spec", unavailable)
    assert main(["project", "languages", "--check"]) == 1
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)["languages"]}
    assert rows["python"]["status"] == "READY"
    assert rows["go"]["status"] == "UNAVAILABLE"
    assert "deliberately unavailable" in rows["go"]["reason"]
