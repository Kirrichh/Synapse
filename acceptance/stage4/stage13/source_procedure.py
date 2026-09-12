"""Acceptance-only literal data sample for exercising the existing CVM.

The VM returns these parameters without reading a file or running a remembered
command. A consumer must separately establish task applicability, execution
authority and fresh results. This sample cannot grant any of them and is not a
production source-publication profile, planner or procedural replay engine.
"""

from copy import deepcopy

from synapse.experiments.gold.behavior import InlineProgram
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.source_verification import SOURCE_KNOWLEDGE_V1, canonical, source_ref


SOURCE_PROCEDURE_V1 = "synapse.stage4.gold.source-procedure/v1"


def source_procedure(knowledge):
    """Project the independently verified content into explicit operation data."""
    if knowledge["schema_version"] != SOURCE_KNOWLEDGE_V1:
        raise ValueError("source procedure requires its verified knowledge contract")
    operations = [{"kind": "INSPECT_READ", "path": item["path"],
                   "expected_source_ref": item["ref"]} for item in knowledge["sources"]]
    if knowledge["kind"] == "VERIFICATION_RECIPE":
        operations.append({"kind": "RUN_VERIFICATION_COMMAND", **knowledge["recipe"]})
    elif knowledge["kind"] != "REPOSITORY_FACT_CHECK" or knowledge["recipe"] is not None:
        raise ValueError("source procedure has an unsupported extraction profile")
    return {"schema_version": SOURCE_PROCEDURE_V1,
            "knowledge_ref": source_ref(canonical(knowledge), SOURCE_KNOWLEDGE_V1).to_dict(),
            "knowledge": deepcopy(knowledge),
            "repository_revision": knowledge["revision"],
            "operations": operations,
            "execution_semantics": "REQUIRES_CURRENT_TASK_AUTHORITY_AND_FRESH_EXECUTION"}


def procedure_return_value(procedure):
    """Position-typed operation parameters within the canonical IR v1 scalars.

    The root carries version, knowledge identity, revision and operations.
    Operation tags 1/2 select read/verification layouts. Text is its UTF-8 byte
    length followed by six-byte words; hash field widths are fixed by schema.
    This avoids repeatedly encoding schema names and scalar/list type tags in
    every literal subtree while preserving all command arguments and conditions.
    """
    if procedure["schema_version"] != SOURCE_PROCEDURE_V1:
        raise ValueError("unknown source procedure profile")
    def text_words(value):
        raw = value.encode("utf-8")
        return [len(raw), *(int.from_bytes(raw[index:index + 6], "big") for index in range(0, len(raw), 6))]

    def hash_words(value, width):
        if type(value) is not str or len(value) != width or any(c not in '0123456789abcdef' for c in value):
            raise ValueError("source procedure requires an exact hexadecimal identity")
        return [int(value[index:index + 13], 16) for index in range(0, len(value), 13)]

    operations = []
    for item in procedure["operations"]:
        if item["kind"] == "INSPECT_READ":
            ref = HashBoundRef.from_dict(item["expected_source_ref"])
            operations.append([1, text_words(item["path"]), hash_words(ref.sha256, 64), ref.byte_length])
        elif item["kind"] == "RUN_VERIFICATION_COMMAND":
            expectation = item["expectation"]
            operations.append([2, [text_words(arg) for arg in item["command"]], expectation["expected_exit_codes"],
                expectation["expected_nonzero_exit"], [text_words(text) for text in expectation["combined_output_contains"]],
                [text_words(text) for text in expectation["combined_output_not_contains"]], expectation["timeout_seconds"]])
        else:
            raise ValueError("source procedure includes an unsupported operation")
    return [1, hash_words(procedure["knowledge_ref"]["sha256"], 64),
            hash_words(procedure["repository_revision"], 40), operations]


def source_procedure_program(procedure):
    """Compile only a bounded literal operation declaration, with no effects."""
    def node(value):
        if type(value) is list:
            return {"node": "list", "elements": [node(item) for item in value]}
        kinds = {int: "INT", bool: "BOOL"}
        if type(value) not in kinds:
            raise ValueError("source procedure contains a nonliteral value")
        return {"node": "literal", "value_kind": kinds[type(value)], "value": value}
    return InlineProgram.from_dict({"form": "INLINE_IR_V1", "ir": {
        "schema_version": "synapse.stage4.gold.canonical-program-ir/v1",
        "program": {"node": "program", "statements": [
            {"node": "return", "value": node(procedure_return_value(procedure))}]}}})
