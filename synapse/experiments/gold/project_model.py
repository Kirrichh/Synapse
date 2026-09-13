"""Project elements and task-scoped memory, derived from retained observations.

System and subsystem nodes describe the known project structure; elements are
committed Python identities or retained documents. Model coverage is explicit.
Goals are declarations, current state is revision-bound evidence, and a delta
remains unverified until a separate execution result establishes it.
"""

import ast
import base64
import hashlib
from pathlib import PurePosixPath

from .source_verification import canonical
from .stage10.intent import EffectDisposition

PROJECT_MODEL_V1 = "synapse.stage4.gold.project-element-model/v1"
ACTIVE_MEMORY_FRAME_V1 = "synapse.stage4.gold.active-memory-frame/v1"
MEMORY_KINDS = ("Identity", "Structural", "Goal", "Context", "Episodic", "Decision", "Defect", "Evidence")
MAX_MODEL_ELEMENTS = 1024


def element_identity(project_identity, path, name, kind):
    return hashlib.sha256(canonical([project_identity, path, name, kind])).hexdigest()


def build_project_model(*, project_identity, resolution, source_snapshot):
    bindings = {(item["binding"]["path"], item["binding"]["qualname"], item["binding"]["symbol_kind"]):
                item["binding"] for item in resolution["elements"]}
    sources = {}
    for selected in source_snapshot["recall"]["selected"]:
        experience = selected["experience"]
        for binding in experience["bindings"]:
            bindings.setdefault((binding["path"], binding["qualname"], binding["symbol_kind"]), binding)
        retained = {item["ref"]["sha256"]: item["content_base64url"] for item in experience["retained"]}
        for source in experience["sources"]:
            encoded = retained[source["ref"]["sha256"]]
            retained_source = (source["ref"], base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if source["path"] in sources and sources[source["path"]] != retained_source:
                raise ValueError("current source evidence disagrees for one project path")
            sources[source["path"]] = retained_source
    nodes, edges = {}, set()
    system = element_identity(project_identity, "", "", "System")
    nodes[system] = {"element_id": system, "kind": "System", "path": "", "name": "project",
                     "parent_id": None, "binding": None, "source_ref": None}
    def subsystem(path):
        if not path or path == ".":
            return system
        identifier = element_identity(project_identity, path, "", "Subsystem")
        if identifier not in nodes:
            parent = subsystem(str(PurePosixPath(path).parent))
            nodes[identifier] = {"element_id": identifier, "kind": "Subsystem", "path": path,
                                 "name": PurePosixPath(path).name, "parent_id": parent, "binding": None,
                                 "source_ref": None}
            edges.add((parent, identifier, "CONTAINS"))
        return identifier
    modules, symbols = {}, {}
    for (path, name, kind), binding in sorted(bindings.items()):
        identifier = element_identity(project_identity, path, name, kind)
        symbols[(path, name)] = identifier
        if kind == "MODULE":
            modules[path] = identifier
        nodes[identifier] = {"element_id": identifier, "kind": "Element", "path": path,
            "name": name, "symbol_kind": kind, "parent_id": None, "binding": binding,
            "source_ref": sources.get(path, (None, None))[0]}
    for path, (reference, raw) in sorted(sources.items()):
        if path not in modules and not any(key[0] == path for key in bindings):
            identifier = element_identity(project_identity, path, path, "DOCUMENT")
            nodes[identifier] = {"element_id": identifier, "kind": "Element", "path": path,
                "name": path, "symbol_kind": "DOCUMENT", "parent_id": None, "binding": None,
                "source_ref": reference}
    if len(nodes) > MAX_MODEL_ELEMENTS:
        raise ValueError("known project model exceeds its element budget")
    for identifier, node in tuple(nodes.items()):
        if node["kind"] != "Element":
            continue
        path = node["path"]
        parent = subsystem(str(PurePosixPath(path).parent))
        if node["symbol_kind"] not in {"MODULE", "DOCUMENT"}:
            owner_name, separator, _ = node["name"].rpartition(".")
            module_parent = modules.get(path, parent)
            parent = symbols.get((path, owner_name), module_parent) if separator else module_parent
        node["parent_id"] = parent
        edges.add((parent, identifier, "CONTAINS"))
    module_names = {nodes[identifier]["binding"]["module"]: identifier for path, identifier in modules.items()}
    unresolved = []
    for path, identifier in sorted(modules.items()):
        if path not in sources:
            continue
        try:
            parsed = ast.parse(sources[path][1], filename=path)
        except (ValueError, SyntaxError, UnicodeError):
            continue  # Binding verification owns syntax; incomplete material has no dependency claim.
        for node in ast.walk(parsed):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    parts = nodes[identifier]["binding"]["module"].split(".")
                    package = parts if path.endswith("/__init__.py") else parts[:-1]
                    prefix = package[:len(package) - node.level + 1]
                    name = ".".join(prefix + ([name] if name else []))
                names = [name] if name else []
            for name in names:
                target = module_names.get(name)
                if target is not None and target != identifier:
                    edges.add((identifier, target, "IMPORTS"))
                elif target is None:
                    unresolved.append({"element_id": identifier, "module": name, "status": "UNRESOLVED"})
    if len(nodes) > MAX_MODEL_ELEMENTS:
        raise ValueError("known project model exceeds its element budget")
    active = set(resolution["effect_paths"])
    return {"schema_version": PROJECT_MODEL_V1, "project_identity": project_identity,
        "repository_revision": resolution["repository_revision"], "coverage": "STUDIED_AND_TASK_TARGET_ELEMENTS",
        "nodes": sorted(nodes.values(), key=lambda item: item["element_id"]),
        "edges": [{"source": source, "target": target, "kind": kind} for source, target, kind in sorted(edges)],
        "unresolved_dependencies": sorted(unresolved, key=canonical), "active_paths": sorted(active)}


def build_active_memory_frame(*, model, task, source_snapshot, prior_episodes=()):
    selected = source_snapshot["recall"]["selected"]
    elements = [item for item in model["nodes"] if item["kind"] == "Element"
                and item["path"] in model["active_paths"]]
    memories = []
    for element in elements:
        path = element["path"]
        episodes = [item["experience"] for item in selected if path in item["match"]["paths"]]
        history = [item for item in prior_episodes if path in item["paths"]]
        expected = [item.to_dict() for item in task.effects
                    if item.subject_path == path and item.disposition is EffectDisposition.EXPECTED]
        owner_id = "element-owner-" + element["element_id"]
        memory = {
            "Identity": {"owner_id": owner_id, "element_id": element["element_id"], "name": element["name"],
                         "status": "RESOLVED" if element["binding"] is not None else "RETAINED_MATERIAL"},
            "Structural": {"parent_id": element["parent_id"], "binding": element["binding"],
                "dependencies": [edge for edge in model["edges"] if edge["source"] == element["element_id"]]},
            "Goal": {"task_contract_ref": task.reference.to_dict(), "expected_effects": expected,
                     "acceptance": [item.to_dict() for item in task.acceptance], "status": "DECLARED"},
            "Context": {"repository_revision": model["repository_revision"], "allowed_scope": task.allowed_scope.to_dict()},
            "Episodic": {"source_outcomes": [{"operation_id": item["claim"]["operation_id"],
                "status": item["status"], "verification": item["verification"],
                "execution": item["execution"]} for item in episodes], "task_outcomes": history},
            "Decision": {"recall_profile": source_snapshot["recall"]["profile"],
                "selections": [{"operation_id": item["experience"]["claim"]["operation_id"], "match": item["match"]}
                               for item in selected if path in item["match"]["paths"]]},
            "Defect": {"nonzero_source_exits": [{"operation_id": item["claim"]["operation_id"],
                "execution": item["execution"]} for item in episodes if item["execution"] == "EXITED_NONZERO"],
                "task_outcomes": [item for item in history if item["status"] != "FULL"],
                "claim": "OBSERVATIONS_ONLY"},
            "Evidence": {"source_refs": [source["ref"] for item in episodes for source in item["sources"]],
                "source_snapshot_schema": source_snapshot["schema_version"],
                "task_contract_ref": task.reference.to_dict()},
        }
        memories.append({"element_id": element["element_id"], "owner_id": owner_id, "path": path,
            "target_state": memory["Goal"], "current_state": memory["Context"] | {"binding": element["binding"]},
            "development_delta": {"expected_effects": expected, "status": "AWAITING_FRESH_VERIFICATION"},
            "memory": memory})
    return {"schema_version": ACTIVE_MEMORY_FRAME_V1, "project_identity": model["project_identity"],
        "task_contract_ref": task.reference.to_dict(), "repository_revision": model["repository_revision"],
        "memory_kinds": list(MEMORY_KINDS), "elements": memories}
