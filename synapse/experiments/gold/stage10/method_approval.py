"""The operator contract for bounded source-driven edit partitioning.

A grant authorizes an explicit partition rule over the exact task effects.
It preserves every capability, condition, actor and ordered verification
command. Individual partitions remain subject to plan authority and physical
replay verification; this projection grants neither knowledge admission nor
permission for additional effects.
"""

from .context_codec import encode_canonical
from .planning_basis import METHOD_SELECTION_V1, read_planning_basis, method_groups
from .planning import OPERATION_PLAN_SCHEMA_V2, OperationKind


def method_approval_contract(*, intent, plan):
    basis = read_planning_basis(encode_canonical(plan["planning_basis"]))
    groups = method_groups(encode_canonical(basis))
    operations = plan["operations"]
    if not groups or len(operations) < len(groups):
        raise ValueError("method approval requires the complete edit partition")
    for index, operation in enumerate(operations):
        expected = [] if index == 0 else [operations[index - 1]["operation_id"]]
        if operation["depends_on"] != expected:
            raise ValueError("method profile requires ordered effects and verification")
    edits, checks = operations[:len(groups)], operations[len(groups):]
    for index, (edit, (paths, subject)) in enumerate(zip(edits, groups)):
        if (edit["kind"] != OperationKind.EDIT_CONTROLLED_CHANGE.value
                or edit["subject_paths"] != list(paths) or edit["argv"]
                or edit["capability"] != edits[0]["capability"]
                or edit["verification"] != edits[0]["verification"]
                or index > 0 and edit["acceptance_criterion_ids"]):
            raise ValueError("method approval cannot change the edit contract")
        if subject is not None and subject.to_dict() not in intent["behavior_refs"]:
            raise ValueError("method approval names an unselected source")
        if subject is not None and subject.to_dict() not in edit["input_refs"]:
            raise ValueError("edit lost its selected procedural input")
        allowed_inputs = intent["target_bindings"] + ([] if subject is None else [subject.to_dict()])
        if any(ref not in allowed_inputs for ref in edit["input_refs"]):
            raise ValueError("edit input exceeds its target and selected method")
    if any(item["kind"] != OperationKind.RUN_VERIFICATION_COMMAND.value for item in checks):
        raise ValueError("method approval contains an undeclared operation kind")
    merged = dict(edits[0])
    merged.update(operation_id="operation-main", subject_paths=basis["target_paths"],
        input_refs=sorted(intent["target_bindings"], key=lambda ref: (ref["kind"], ref["ref_id"], ref["sha256"])),
        effect_constraint_ids=sorted(item for edit in edits for item in edit["effect_constraint_ids"]))
    result_operations = [merged]
    for index, command in enumerate(checks, 1):
        result_operations.append({**command, "operation_id": f"operation-check-{index}",
                                  "depends_on": [result_operations[-1]["operation_id"]]})
    result = dict(plan)
    result.pop("planning_basis")
    result.update(schema_version=OPERATION_PLAN_SCHEMA_V2, operation_selection=METHOD_SELECTION_V1,
        planning_bounds=basis["search_bounds"], operations=result_operations,
        execution_order=[item["operation_id"] for item in result_operations])
    return result
