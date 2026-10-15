"""Bind task requirements to committed project elements for the run.

This adapter joins the governing-task contract to the existing Python binding
resolver. It owns the bounded selection rule and its reproducible explanation,
not syntax resolution, memory admission, plan approval or execution authority.
"""
from pathlib import Path
import re

from .bindings import PythonSymbolKind, binding_to_ref, discover_python_bindings, resolve_creation_binding
from .contracts import RepositoryRevision
from .stage10.context_codec import decode_canonical, encode_canonical
from .stage10.intent import EffectDisposition, EffectKind
from .stage10.task_contract import GoverningTaskContract, TASK_CONTRACT_SCHEMA_V3


TASK_TARGET_RESOLUTION_V1 = "synapse.stage4.gold.task-target-resolution/v1"
TARGET_SELECTION_PROFILE_V1 = "committed-python-effect-paths-and-mentioned-symbols/v1"
MAX_TARGET_PATHS = 32
MAX_PROJECT_ELEMENTS = 1024


def _resolve(task, repository_root):
    if type(task) is not GoverningTaskContract or task.schema_version != TASK_CONTRACT_SCHEMA_V3:
        raise ValueError("automatic target resolution requires governing task v3")
    if type(repository_root) is not type(Path()) or not repository_root.is_absolute():
        raise ValueError("target resolution requires an absolute repository location")
    expected = tuple(item for item in task.effects if item.disposition is EffectDisposition.EXPECTED)
    if not expected or any(item.kind not in (EffectKind.PATH_MODIFIED, EffectKind.PATH_CREATED)
                           or item.subject_path is None for item in expected):
        raise ValueError("target selection profile supports existing Python modification and declared creation targets")
    creation_paths = tuple(sorted({item.subject_path for item in expected
                                   if item.kind is EffectKind.PATH_CREATED}))
    paths = tuple(sorted({item.subject_path for item in expected}))
    if creation_paths and any(item.subject_path in creation_paths
                             for item in expected if item.kind is EffectKind.PATH_MODIFIED):
        raise ValueError("a path cannot be both created and modified by one task")
    if len(paths) > MAX_TARGET_PATHS:
        raise ValueError("task target paths exceed the selection budget")
    revision = RepositoryRevision.git_commit(task.repository_revision_sha256)
    elements, targets = [], []
    for path in paths:
        if not task.allowed_scope.covers(path):
            raise ValueError("target selection cannot expand task scope")
        if path in creation_paths:
            # A declared creation target binds to its provable absence in the
            # frozen revision, mirroring how a modification target binds to its
            # committed content. The resolver refuses a path that already
            # exists, so a "creation" of present code cannot pass selection.
            binding = resolve_creation_binding(repository_root, repository_revision=revision, path=path)
            elements.append({"binding": binding.to_dict(), "selection_reasons": ["EXPECTED_CREATION_PATH"]})
            targets.append(binding)
            if len(elements) > MAX_PROJECT_ELEMENTS:
                raise ValueError("project elements exceed the selection budget")
            continue
        bindings = discover_python_bindings(repository_root, repository_revision=revision, path=path)
        for binding in bindings:
            reasons = []
            if binding.symbol_kind is PythonSymbolKind.MODULE:
                reasons.append("EXPECTED_EFFECT_PATH")
            elif any(re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w.])", task.task_statement)
                     for name in (binding.qualname, binding.module + "." + binding.qualname)):
                reasons.append("TASK_MENTIONS_SYMBOL")
            elements.append({"binding": binding.to_dict(), "selection_reasons": reasons})
            if len(elements) > MAX_PROJECT_ELEMENTS:
                raise ValueError("project elements exceed the selection budget")
            if reasons:
                targets.append(binding)
    # Keep one canonical target order for retrieval, intent, approval and resume.
    # Creation bindings carry no symbol identity (their subject is an absent
    # path), so the shared key projects absent fields to their empty values
    # instead of assuming every target is a content binding.
    def _target_key(item):
        symbol_kind = getattr(item, "symbol_kind", None)
        return (item.path, getattr(item, "qualname", ""), "" if symbol_kind is None else symbol_kind.value)

    def _element_key(item):
        binding = item["binding"]
        return (binding["path"], binding.get("qualname", ""), binding.get("symbol_kind", ""))

    targets.sort(key=_target_key)
    elements.sort(key=_element_key)
    return {
        "schema_version": TASK_TARGET_RESOLUTION_V1, "profile": TARGET_SELECTION_PROFILE_V1,
        "task_contract_ref": task.reference.to_dict(), "repository_revision": revision.to_dict(),
        "effect_paths": list(paths), "elements": elements,
        "target_bindings": [binding_to_ref(item).to_dict() for item in targets],
    }, tuple(targets)


def resolve_task_targets(*, task, repository_root) -> bytes:
    """Freeze structural evidence without changing the governing task's bytes."""
    payload, _ = _resolve(task, repository_root)
    return encode_canonical(payload)


def read_task_targets(raw: bytes, *, task, repository_root):
    """Reproduce selection from the frozen Git revision before consuming it.

    A caller cannot supply just plausible binding hashes or delete a rejected
    element from the inventory. Missing commits, duplicate symbols, unsupported
    modes and changed selection records fail before plan acceptance.
    """
    if type(raw) is not bytes:
        raise ValueError("task target resolution must contain exact canonical bytes")
    recorded = decode_canonical(raw)
    actual, targets = _resolve(task, repository_root)
    if recorded != actual or raw != encode_canonical(actual):
        raise ValueError("task target resolution differs from committed project evidence")
    return targets
