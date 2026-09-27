"""Bounded method-selection evidence; data and decisions, without authority.

Coverage observations describe source applicability to task bindings. Greedy
partitioning prefers the largest remaining path coverage, then canonical
subject identity. Every candidate, including an inapplicable one, is retained.
Uncovered paths stay explicit public proposals. This is a structural planning
profile, not a claim to infer arbitrary procedures from prose.
"""

from ..canonicalization import HashBoundRef, RefKind
from ..contracts import record_id_reference_from_dict
from .context_codec import decode_canonical, encode_canonical
from .repository_scope import normalize_repository_path

PLANNING_BASIS_V1 = "synapse.stage4.gold.procedural-planning-basis/v1"
METHOD_SELECTION_V1 = "source-coverage-greedy-partition/v1"
MAX_METHODS = 128
MAX_PATHS = 32


def _paths(value):
    if (type(value) is not list or len(value) > MAX_PATHS
            or any(type(item) is not str or normalize_repository_path(item) != item for item in value)
            or value != sorted(set(value))):
        raise ValueError("method paths must be bounded, canonical and unique")
    return value


def _alternatives(value, target_paths):
    if type(value) is not list or len(value) > MAX_METHODS:
        raise ValueError("method search exceeds its declared candidate budget")
    identities = []
    for item in value:
        if type(item) is not dict or set(item) != {
                "subject_ref", "observation_id", "terminal_snapshot_ref", "coverage", "covered_paths"}:
            raise ValueError("method observation has an unknown contract")
        subject = HashBoundRef.from_dict(item["subject_ref"])
        if subject.kind is not RefKind.ARTIFACT:
            raise ValueError("method must name an admitted library subject")
        record_id_reference_from_dict(item["observation_id"])
        HashBoundRef.from_dict(item["terminal_snapshot_ref"])
        coverage = item["coverage"]
        if (type(coverage) is not list or len(coverage) != 3
                or any(type(word) is not int or word < 0 for word in coverage)
                or coverage[0] not in {0, 1} or coverage[0] != int(coverage[1] > 0)):
            raise ValueError("method requires typed applicability and coverage observations")
        if not set(_paths(item["covered_paths"])) <= set(target_paths):
            raise ValueError("method coverage exceeds the task")
        if bool(item["covered_paths"]) != bool(coverage[0]):
            raise ValueError("method applicability differs from its covered paths")
        identities.append(encode_canonical(item["subject_ref"]))
    if identities != sorted(set(identities)):
        raise ValueError("method alternatives must use unique canonical subject order")


def _choose(alternatives, target_paths):
    remaining = set(target_paths)
    choices = []
    while remaining:
        eligible = [(len(remaining & set(item["covered_paths"])), index, item)
                    for index, item in enumerate(alternatives) if item["coverage"][0] == 1]
        eligible = [item for item in eligible if item[0] > 0]
        if not eligible:
            break
        _, index, method = min(eligible, key=lambda item: (-item[0], item[1]))
        paths = sorted(remaining & set(method["covered_paths"]))
        choices.append({"alternative_index": index, "paths": paths})
        remaining.difference_update(paths)
    return choices, sorted(remaining)


def create_planning_basis(*, target_paths, alternatives):
    paths = _paths(list(target_paths))
    rows = sorted(alternatives, key=lambda item: encode_canonical(item["subject_ref"]))
    _alternatives(rows, paths)
    choices, uncovered = _choose(rows, paths)
    return encode_canonical({
        "schema_version": PLANNING_BASIS_V1, "selection_policy": METHOD_SELECTION_V1,
        "search_bounds": {"maximum_methods": MAX_METHODS, "maximum_paths": MAX_PATHS},
        "target_paths": paths, "alternatives": rows, "choices": choices,
        "uncovered_paths": uncovered,
    })


def read_planning_basis(raw):
    if type(raw) is not bytes or len(raw) > 2 * 1024 * 1024:
        raise ValueError("planning basis requires bounded canonical bytes")
    value = decode_canonical(raw)
    if (type(value) is not dict or set(value) != {"schema_version", "selection_policy", "search_bounds",
            "target_paths", "alternatives", "choices", "uncovered_paths"}
            or value["schema_version"] != PLANNING_BASIS_V1 or value["selection_policy"] != METHOD_SELECTION_V1
            or value["search_bounds"] != {"maximum_methods": MAX_METHODS, "maximum_paths": MAX_PATHS}):
        raise ValueError("planning basis has an unknown contract")
    _paths(value["target_paths"])
    _alternatives(value["alternatives"], value["target_paths"])
    choices, uncovered = _choose(value["alternatives"], value["target_paths"])
    if encode_canonical([value["choices"], value["uncovered_paths"]]) != encode_canonical([choices, uncovered]):
        raise ValueError("saved method choice differs from its observed alternatives")
    if encode_canonical(value) != raw:
        raise ValueError("planning basis must preserve canonical bytes")
    return value


def method_groups(raw):
    value = read_planning_basis(raw)
    groups = [(tuple(item["paths"]),
               HashBoundRef.from_dict(value["alternatives"][item["alternative_index"]]["subject_ref"]))
              for item in value["choices"]]
    if value["uncovered_paths"]:
        groups.append((tuple(value["uncovered_paths"]), None))
    return tuple(groups)
