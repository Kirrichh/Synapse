from __future__ import annotations

import copy
from dataclasses import fields, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from synapse.experiments.gold import bindings
from synapse.experiments.gold.behavior import (
    BehaviorCore,
    BehaviorFailureCode,
    BehaviorViolation,
    behavior_manifest_from_dict,
    create_behavior_blob,
    create_behavior_manifest,
    create_behavior_unit,
)
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import RepositoryRevision


_CASES = Path(__file__).parent / "fixtures" / "gold" / "binding_cases"
_BEHAVIOR_VECTORS = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"
_EXPECTED = _CASES / "expected_vectors_v1.json"


def _git(repo: Path, *args: str, env: dict[str, str] | None = None, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_bytes,
        capture_output=True,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode("utf-8", "replace"))
    return completed.stdout


def _commit(repo: Path, message: str, second: int) -> str:
    _git(repo, "add", "-A")
    return _commit_index(repo, message, second)


def _commit_index(repo: Path, message: str, second: int) -> str:
    env = os.environ.copy()
    date = f"2000-01-01T00:00:{second:02d}+0000"
    env.update(
        {
            "GIT_AUTHOR_NAME": "Synapse Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_NAME": "Synapse Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_DATE": date,
        }
    )
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message, env=env)
    return _git(repo, "rev-parse", "HEAD").decode("ascii").strip()


def _base_repo(tmp_path: Path) -> tuple[Path, RepositoryRevision]:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    python_path = repo / "pkg" / "symbols.py"
    python_path.parent.mkdir()
    python_path.write_bytes((_CASES / "python_symbols.py").read_bytes())
    document = bytes.fromhex((_CASES / "governing_document.canonical.hex").read_text(encoding="ascii").strip())
    document_path = repo / "governance" / "stage4.binding.json"
    document_path.parent.mkdir()
    document_path.write_bytes(document)
    commit = _commit(repo, "binding fixture", 0)
    return repo, RepositoryRevision.git_commit(commit)


def _python(
    repo: Path,
    revision: RepositoryRevision,
    *,
    qualname: str = "top_level",
    kind: bindings.PythonSymbolKind = bindings.PythonSymbolKind.FUNCTION,
    path: str = "pkg/symbols.py",
    module: str = "pkg.symbols",
    contract_version: str = bindings.BINDING_CONTRACT_VERSION_V1,
    resolver_version: str = bindings.PYTHON_BINDING_RESOLVER_V1,
) -> bindings.PythonBinding:
    return bindings.resolve_python_binding(
        repo,
        repository_revision=revision,
        path=path,
        module=module,
        qualname=qualname,
        symbol_kind=kind,
        contract_version=contract_version,
        resolver_version=resolver_version,
    )


def _document(
    repo: Path,
    revision: RepositoryRevision,
    *,
    section_id: str = "binding-core",
    document_id: str = "stage4-spec",
    document_revision: str = "v2.2",
    path: str = "governance/stage4.binding.json",
) -> bindings.DocumentBinding:
    return bindings.resolve_document_binding(
        repo,
        repository_revision=revision,
        path=path,
        document_id=document_id,
        document_revision=document_revision,
        section_id=section_id,
        contract_version=bindings.BINDING_CONTRACT_VERSION_V1,
        resolver_version=bindings.DOCUMENT_BINDING_RESOLVER_V1,
    )


def _requirement(
    repo: Path,
    revision: RepositoryRevision,
    requirement_ids: object = ("REQ-001", "REQ-002"),
) -> bindings.RequirementBinding:
    return bindings.resolve_requirement_binding(
        repo,
        repository_revision=revision,
        path="governance/stage4.binding.json",
        document_id="stage4-spec",
        document_revision="v2.2",
        section_id="binding-core",
        requirement_ids=requirement_ids,
        contract_version=bindings.BINDING_CONTRACT_VERSION_V1,
        resolver_version=bindings.DOCUMENT_BINDING_RESOLVER_V1,
    )


def _write_and_commit(repo: Path, path: str, content: bytes, *, second: int = 1) -> RepositoryRevision:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return RepositoryRevision.git_commit(_commit(repo, "fixture change", second))


@pytest.mark.parametrize(
    ("qualname", "kind"),
    [
        ("pkg.symbols", bindings.PythonSymbolKind.MODULE),
        ("top_level", bindings.PythonSymbolKind.FUNCTION),
        ("async_top_level", bindings.PythonSymbolKind.FUNCTION),
        ("Outer", bindings.PythonSymbolKind.CLASS),
        ("Outer.Inner", bindings.PythonSymbolKind.CLASS),
        ("Outer.Inner.method", bindings.PythonSymbolKind.METHOD),
        ("Outer.Inner.async_method", bindings.PythonSymbolKind.METHOD),
    ],
)
def test_s4_p3_acc_python_01_exact_static_symbol_resolution(
    tmp_path: Path,
    qualname: str,
    kind: bindings.PythonSymbolKind,
) -> None:
    repo, revision = _base_repo(tmp_path)
    binding = _python(repo, revision, qualname=qualname, kind=kind)
    assert binding.repository_revision == revision
    assert binding.module == "pkg.symbols"
    assert binding.qualname == qualname
    assert binding.symbol_kind is kind
    assert bindings.consume_python_binding(repo, binding, repository_revision=revision).to_dict() == binding.to_dict()


def test_s4_p3_acc_identity_01_literal_payload_id_and_ref_vector(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    vector = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert revision.git_sha == vector["repository_revision"]
    python_binding = _python(repo, revision)
    document_binding = _document(repo, revision)
    requirement_binding = _requirement(repo, revision)
    for name, binding in (
        ("python", python_binding),
        ("document", document_binding),
        ("requirement", requirement_binding),
    ):
        expected = vector[name]
        canonical = bindings.binding_canonical_payload_bytes(binding)
        ref = bindings.binding_to_ref(binding)
        assert binding.to_dict() == expected["transport"]
        assert canonical.hex() == expected["canonical_payload_hex"]
        assert binding.binding_id.value == expected["binding_id"]
        assert ref.to_dict() == expected["ref"]


@pytest.mark.parametrize("boundary", ["python", "document", "requirement", "transport", "consumer", "to_dict", "ref"])
def test_s4_p3_acc_revision_01_not_applicable_is_rejected_before_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    repo, revision = _base_repo(tmp_path)
    python_binding = _python(repo, revision)
    not_applicable = RepositoryRevision.not_applicable()
    if boundary in {"python", "document", "requirement"}:
        monkeypatch.setattr(bindings, "resolve_revision", lambda *_args, **_kwargs: pytest.fail("Git was called"))
    with pytest.raises(bindings.BindingViolation) as exc:
        if boundary == "python":
            _python(repo, not_applicable)
        elif boundary == "document":
            _document(repo, not_applicable)
        elif boundary == "requirement":
            _requirement(repo, not_applicable)
        elif boundary == "transport":
            transport = python_binding.to_dict()
            transport["repository_revision"] = not_applicable.to_dict()
            bindings.binding_from_dict(transport, repo_root=repo, consumer_revision=not_applicable)
        else:
            forged = object.__new__(bindings.PythonBinding)
            for field in fields(bindings.PythonBinding):
                object.__setattr__(forged, field.name, getattr(python_binding, field.name))
            object.__setattr__(forged, "repository_revision", not_applicable)
            if boundary == "consumer":
                bindings.consume_python_binding(repo, forged, repository_revision=not_applicable)
            elif boundary == "to_dict":
                forged.to_dict()
            else:
                bindings.binding_to_ref(forged)
    assert exc.value.failure_code is bindings.BindingFailureCode.REVISION_MISMATCH


@pytest.mark.parametrize(
    "path",
    ["../pkg/symbols.py", "./pkg/symbols.py", "pkg\\symbols.py", "pkg//symbols.py", "/pkg/symbols.py", "C:/pkg/symbols.py"],
)
def test_s4_p3_acc_path_01_traversal_absolute_and_aliases_fail_closed(tmp_path: Path, path: str) -> None:
    repo, revision = _base_repo(tmp_path)
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, revision, path=path)
    assert exc.value.failure_code is bindings.BindingFailureCode.INVALID_PATH


def test_s4_p3_acc_python_02_commit_move_module_qualname_and_kind_drift(tmp_path: Path) -> None:
    repo, first = _base_repo(tmp_path)
    binding = _python(repo, first)
    second = _write_and_commit(repo, "pkg/symbols.py", (_CASES / "python_symbols.py").read_bytes() + b"\n# drift\n")
    with pytest.raises(bindings.BindingViolation) as exc:
        bindings.consume_python_binding(repo, binding, repository_revision=second)
    assert exc.value.failure_code is bindings.BindingFailureCode.REVISION_MISMATCH

    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, first, module="symbols")
    assert exc.value.failure_code is bindings.BindingFailureCode.MODULE_MISMATCH
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, first, qualname="top_leve1")
    assert exc.value.failure_code is bindings.BindingFailureCode.SYMBOL_MISSING
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, first, qualname="Outer", kind=bindings.PythonSymbolKind.FUNCTION)
    assert exc.value.failure_code is bindings.BindingFailureCode.SYMBOL_KIND_MISMATCH

    _git(repo, "mv", "pkg/symbols.py", "pkg/moved.py")
    moved = RepositoryRevision.git_commit(_commit(repo, "move fixture", 2))
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, moved)
    assert exc.value.failure_code is bindings.BindingFailureCode.REPOSITORY_SNAPSHOT_UNAVAILABLE


def test_s4_p3_acc_python_03_duplicate_qualname_and_version_mismatches(tmp_path: Path) -> None:
    repo, _revision = _base_repo(tmp_path)
    duplicate = b"def repeated():\n    return 1\n\ndef repeated():\n    return 2\n"
    revision = _write_and_commit(repo, "pkg/duplicate.py", duplicate)
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, revision, path="pkg/duplicate.py", module="pkg.duplicate", qualname="repeated")
    assert exc.value.failure_code is bindings.BindingFailureCode.SYMBOL_AMBIGUOUS

    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, revision, contract_version="synapse.stage4.gold.binding-contract/v2")
    assert exc.value.failure_code is bindings.BindingFailureCode.CONTRACT_VERSION_MISMATCH
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, revision, resolver_version="synapse.stage4.gold.python-binding-resolver/v2")
    assert exc.value.failure_code is bindings.BindingFailureCode.UNKNOWN_RESOLVER_VERSION


def test_s4_p3_acc_python_04_decorator_span_and_transport_substitution(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    binding = _python(repo, revision)
    source = (_CASES / "python_symbols.py").read_bytes()
    start = source.index(b"@fixture_decorator")
    end = source.index(b"\n\n\nasync def async_top_level")
    assert binding.source_span_hash == hashlib.sha256(source[start:end]).hexdigest()
    transport = binding.to_dict()
    transport["source_span_hash"] = "0" * 64
    with pytest.raises(bindings.BindingViolation) as exc:
        bindings.binding_from_dict(transport, repo_root=repo, consumer_revision=revision)
    assert exc.value.failure_code is bindings.BindingFailureCode.SOURCE_SPAN_HASH_MISMATCH
    transport = binding.to_dict()
    transport["binding_id"]["value"] = bindings.BINDING_ID_TEXT_PREFIX_V1 + "0" * 64
    with pytest.raises(bindings.BindingViolation) as exc:
        bindings.binding_from_dict(transport, repo_root=repo, consumer_revision=revision)
    assert exc.value.failure_code is bindings.BindingFailureCode.BINDING_ID_MISMATCH


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"\xef\xbb\xbfdef value():\n    return 1\n", bindings.BindingFailureCode.INVALID_PYTHON_ENCODING),
        (b"# coding: latin-1\ndef value():\n    return '\xff'\n", bindings.BindingFailureCode.INVALID_PYTHON_ENCODING),
        (b"def value(:\n    pass\n", bindings.BindingFailureCode.INVALID_PYTHON_SOURCE),
        (b"def value():\n    nonlocal missing\n", bindings.BindingFailureCode.INVALID_PYTHON_SCOPE),
    ],
)
def test_s4_p3_acc_python_05_encoding_and_scope_fail_closed(
    tmp_path: Path,
    content: bytes,
    code: bindings.BindingFailureCode,
) -> None:
    repo, _revision = _base_repo(tmp_path)
    revision = _write_and_commit(repo, "pkg/invalid.py", content)
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, revision, path="pkg/invalid.py", module="pkg.invalid", qualname="value")
    assert exc.value.failure_code is code


def test_s4_p3_acc_python_06_resource_limits_are_versioned_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = _base_repo(tmp_path)
    assert bindings.MAX_PYTHON_SOURCE_BYTES_V1 == 1_048_576
    assert bindings.MAX_PYTHON_AST_NODES_V1 == 100_000
    assert bindings.MAX_PYTHON_SCOPE_DEPTH_V1 == 128
    monkeypatch.setattr(bindings, "MAX_PYTHON_SOURCE_BYTES_V1", 8)
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, revision)
    assert exc.value.failure_code is bindings.BindingFailureCode.RESOURCE_LIMIT_EXCEEDED
    monkeypatch.setattr(bindings, "MAX_PYTHON_SOURCE_BYTES_V1", 1_048_576)
    monkeypatch.setattr(bindings, "MAX_PYTHON_AST_NODES_V1", 5)
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, revision)
    assert exc.value.failure_code is bindings.BindingFailureCode.RESOURCE_LIMIT_EXCEEDED


def test_s4_p3_acc_document_01_exact_resolution_round_trip_and_fresh_consumption(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    binding = _document(repo, revision)
    assert binding.document_id == "stage4-spec"
    assert binding.document_revision == "v2.2"
    assert binding.section_id == "binding-core"
    assert binding.source_hash == hashlib.sha256(
        bytes.fromhex((_CASES / "governing_document.canonical.hex").read_text(encoding="ascii").strip())
    ).hexdigest()
    restored = bindings.DocumentBinding.from_dict(
        binding.to_dict(),
        repo_root=repo,
        consumer_revision=revision,
    )
    assert restored.to_dict() == binding.to_dict()
    assert bindings.consume_document_binding(repo, binding, repository_revision=revision).to_dict() == binding.to_dict()


def _document_payload() -> dict[str, object]:
    raw = bytes.fromhex((_CASES / "governing_document.canonical.hex").read_text(encoding="ascii").strip())
    return json.loads(raw.decode("utf-8"))


def _canonical_document(payload: dict[str, object]) -> bytes:
    return bindings.canonicalize_stage4_payload(
        payload,
        profile_id=bindings.STAGE4_CANONICAL_PROFILE_V1,
        codec_id=bindings.STABLE_CANONICAL_CODEC_ID,
    )


def test_s4_p3_acc_document_02_exact_id_revision_section_and_no_similarity(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    for kwargs, code in (
        ({"document_id": "stage4-speck"}, bindings.BindingFailureCode.DOCUMENT_ID_MISMATCH),
        ({"document_revision": "v2.3"}, bindings.BindingFailureCode.DOCUMENT_REVISION_MISMATCH),
        ({"section_id": "binding-cor"}, bindings.BindingFailureCode.SECTION_MISSING),
    ):
        with pytest.raises(bindings.BindingViolation) as exc:
            _document(repo, revision, **kwargs)
        assert exc.value.failure_code is code

    payload = _document_payload()
    payload["sections"].append(copy.deepcopy(payload["sections"][0]))
    duplicate_revision = _write_and_commit(repo, "governance/duplicate.json", _canonical_document(payload))
    with pytest.raises(bindings.BindingViolation) as exc:
        _document(repo, duplicate_revision, path="governance/duplicate.json")
    assert exc.value.failure_code is bindings.BindingFailureCode.SECTION_AMBIGUOUS


def test_s4_p3_acc_document_03_canonical_bytes_hash_and_resource_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, revision = _base_repo(tmp_path)
    raw = bytes.fromhex((_CASES / "governing_document.canonical.hex").read_text(encoding="ascii").strip())
    noncanonical = _write_and_commit(repo, "governance/noncanonical.json", raw + b"\n")
    with pytest.raises(bindings.BindingViolation) as exc:
        _document(repo, noncanonical, path="governance/noncanonical.json")
    assert exc.value.failure_code is bindings.BindingFailureCode.SOURCE_HASH_MISMATCH

    binding = _document(repo, revision)
    transport = binding.to_dict()
    transport["source_hash"] = "0" * 64
    with pytest.raises(bindings.BindingViolation) as exc:
        bindings.binding_from_dict(transport, repo_root=repo, consumer_revision=revision)
    assert exc.value.failure_code is bindings.BindingFailureCode.SOURCE_HASH_MISMATCH

    monkeypatch.setattr(bindings, "MAX_DOCUMENT_SOURCE_BYTES_V1", 8)
    with pytest.raises(bindings.BindingViolation) as exc:
        _document(repo, revision)
    assert exc.value.failure_code is bindings.BindingFailureCode.RESOURCE_LIMIT_EXCEEDED


def test_s4_p3_acc_requirement_01_order_is_identity_bearing_and_only_active_ids_resolve(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    first = _requirement(repo, revision, ("REQ-001", "REQ-002"))
    reversed_ids = _requirement(repo, revision, ("REQ-002", "REQ-001"))
    assert first.requirement_ids == ("REQ-001", "REQ-002")
    assert first.binding_id.value != reversed_ids.binding_id.value
    assert bindings.consume_requirement_binding(repo, first, repository_revision=revision).to_dict() == first.to_dict()
    assert bindings.RequirementBinding.from_dict(
        first.to_dict(), repo_root=repo, consumer_revision=revision
    ).to_dict() == first.to_dict()

    for requirement_ids, code in (
        ((), bindings.BindingFailureCode.TYPE_MISMATCH),
        (("REQ-001", "REQ-001"), bindings.BindingFailureCode.TYPE_MISMATCH),
        (("REQ-MISSING",), bindings.BindingFailureCode.REQUIREMENT_MISSING),
        (("REQ-OLD",), bindings.BindingFailureCode.REQUIREMENT_SUPERSEDED),
    ):
        with pytest.raises(bindings.BindingViolation) as exc:
            _requirement(repo, revision, requirement_ids)
        assert exc.value.failure_code is code


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_s4_p3_acc_snapshot_01_symlink_and_gitlink_are_rejected_before_read(tmp_path: Path, mode: str) -> None:
    repo, revision = _base_repo(tmp_path)
    assert revision.git_sha is not None
    if mode == "120000":
        object_id = _git(repo, "hash-object", "-w", "--stdin", input_bytes=b"pkg/symbols.py").decode("ascii").strip()
        path = "unsafe.py"
    else:
        object_id = revision.git_sha
        path = "submodule"
    _git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{object_id},{path}")
    unsafe_revision = RepositoryRevision.git_commit(_commit_index(repo, "unsafe mode", 3))
    with pytest.raises(bindings.BindingViolation) as exc:
        _python(repo, unsafe_revision, path=path, module="unsafe" if mode == "120000" else "submodule", qualname="unsafe")
    assert exc.value.failure_code is bindings.BindingFailureCode.UNSUPPORTED_GIT_MODE


def test_s4_p3_acc_snapshot_02_missing_commit_fails_but_exact_clone_reproduces(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    binding = _python(repo, revision)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _git(unrelated, "init", "-q")
    with pytest.raises(bindings.BindingViolation) as exc:
        bindings.consume_python_binding(unrelated, binding, repository_revision=revision)
    assert exc.value.failure_code is bindings.BindingFailureCode.REPOSITORY_SNAPSHOT_UNAVAILABLE

    clone = tmp_path / "clone"
    completed = subprocess.run(["git", "clone", "-q", str(repo), str(clone)], capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    reproduced = bindings.consume_python_binding(clone, binding, repository_revision=revision)
    assert reproduced.to_dict() == binding.to_dict()


def test_s4_p3_acc_trust_01_constructor_replace_and_low_level_forgery_fail_closed(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    binding = _python(repo, revision)
    with pytest.raises(TypeError):
        bindings.PythonBinding()
    with pytest.raises(TypeError):
        replace(binding, path="pkg/other.py")
    forged = object.__new__(bindings.PythonBinding)
    for field in fields(bindings.PythonBinding):
        if field.name != "_trusted_seal":
            object.__setattr__(forged, field.name, getattr(binding, field.name))
    with pytest.raises(bindings.BindingViolation) as exc:
        bindings.consume_python_binding(repo, forged, repository_revision=revision)
    assert exc.value.failure_code is bindings.BindingFailureCode.TRUSTED_OBJECT_FORGED


def test_s4_p3_acc_ref_01_binding_ref_has_distinct_payload_and_identity_hashes(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    binding = _python(repo, revision)
    canonical = bindings.binding_canonical_payload_bytes(binding)
    ref = bindings.binding_to_ref(binding)
    assert ref.kind is RefKind.BINDING
    assert ref.ref_id == binding.binding_id.value
    assert ref.schema_id == bindings.BINDING_SCHEMA_V1
    assert ref.sha256 == hashlib.sha256(canonical).hexdigest()
    assert ref.byte_length == len(canonical)
    identity_digest = hashlib.sha256(bindings.BINDING_IDENTITY_PREFIX_V1 + canonical).hexdigest()
    assert binding.binding_id.value == bindings.BINDING_ID_TEXT_PREFIX_V1 + identity_digest
    assert ref.sha256 != identity_digest


def _valid_unit():
    vectors = json.loads(_BEHAVIOR_VECTORS.read_text(encoding="utf-8"))
    core = BehaviorCore.from_dict(copy.deepcopy(vectors["vectors"][0]["core"]))
    return create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )


def test_s4_p3_acc_manifest_01_project_bindings_change_manifest_not_unit_identity(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    python_ref = bindings.binding_to_ref(_python(repo, revision))
    document_ref = bindings.binding_to_ref(_document(repo, revision))
    unit = _valid_unit()
    unit_wire = unit.to_dict()
    core_bytes = bytes(unit.canonical_core.canonical_bytes)
    blob = create_behavior_blob(unit)
    old_manifest = create_behavior_manifest(unit, blob, compiler_binding=None)
    python_manifest = create_behavior_manifest(
        unit, blob, compiler_binding=None, binding_refs=(python_ref,)
    )
    document_manifest = create_behavior_manifest(
        unit, blob, compiler_binding=None, binding_refs=(document_ref,)
    )
    assert python_manifest.manifest_id != document_manifest.manifest_id
    assert unit.to_dict() == unit_wire
    assert unit.canonical_core.canonical_bytes == core_bytes
    assert python_manifest.content_key.value == document_manifest.content_key.value == unit.content_key.value
    assert python_manifest.binding_refs == (python_ref,)
    assert document_manifest.binding_refs == (document_ref,)
    restored = behavior_manifest_from_dict(
        python_manifest.to_dict(unit=unit, blob=blob),
        unit=unit,
        blob=blob,
        compiler_binding=None,
        binding_refs=(python_ref,),
    )
    assert restored.to_dict(unit=unit, blob=blob) == python_manifest.to_dict(unit=unit, blob=blob)

    vectors = json.loads(_BEHAVIOR_VECTORS.read_text(encoding="utf-8"))
    assert old_manifest.to_dict(unit=unit, blob=blob) == vectors["vectors"][0]["manifest_without_binding"]


def test_s4_p3_acc_manifest_02_effective_refs_are_typed_and_include_core_refs(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    binding_ref = bindings.binding_to_ref(_python(repo, revision))
    unit = _valid_unit()
    blob = create_behavior_blob(unit)
    wrong_kind = HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=binding_ref.ref_id,
        schema_id=binding_ref.schema_id,
        sha256=binding_ref.sha256,
        byte_length=binding_ref.byte_length,
        media_type=binding_ref.media_type,
    )
    with pytest.raises(BehaviorViolation) as exc:
        create_behavior_manifest(unit, blob, compiler_binding=None, binding_refs=(wrong_kind,))
    assert exc.value.failure_code is BehaviorFailureCode.REF_KIND_MISMATCH

    core = BehaviorCore.from_dict({**unit.core.to_dict(), "binding_refs": [binding_ref.to_dict()]})
    unit_with_core_ref = create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )
    blob_with_core_ref = create_behavior_blob(unit_with_core_ref)
    with pytest.raises(BehaviorViolation) as exc:
        create_behavior_manifest(
            unit_with_core_ref,
            blob_with_core_ref,
            compiler_binding=None,
            binding_refs=(),
        )
    assert exc.value.failure_code is BehaviorFailureCode.MANIFEST_MISMATCH


def test_s4_p3_acc_transport_01_unknown_fields_kind_hash_and_revision_substitution_fail(tmp_path: Path) -> None:
    repo, revision = _base_repo(tmp_path)
    binding = _requirement(repo, revision)
    for mutate, code in (
        (lambda value: value.update(extra=True), bindings.BindingFailureCode.TYPE_MISMATCH),
        (lambda value: value.update(binding_kind="UNKNOWN"), bindings.BindingFailureCode.UNKNOWN_BINDING_KIND),
        (lambda value: value.update(source_hash="0" * 64), bindings.BindingFailureCode.SOURCE_HASH_MISMATCH),
        (
            lambda value: value.update(repository_revision=RepositoryRevision.not_applicable().to_dict()),
            bindings.BindingFailureCode.REVISION_MISMATCH,
        ),
    ):
        transport = binding.to_dict()
        mutate(transport)
        with pytest.raises(bindings.BindingViolation) as exc:
            bindings.binding_from_dict(transport, repo_root=repo, consumer_revision=revision)
        assert exc.value.failure_code is code
