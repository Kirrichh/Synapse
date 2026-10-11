"""Account for actual files retained by an engineering workload."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


def category(relative):
    parts = relative.parts
    if parts[:2] == ("memory", "project-memory"):
        if "events" in parts:
            if relative.name == "commit-marker.json":
                return "memory_event_markers"
            return "memory_event_manifests" if relative.name == "event-manifest.json" else "memory_event_members"
        if any("block" in part for part in parts[2:]):
            return "memory_shared_blocks"
        return "memory_owner_other"
    if parts[:2] == ("memory", "memory-gateway"):
        return "gateway_evidence" if "evidence" in parts else "gateway_journals_and_locks"
    if parts and parts[0] == "memory":
        return "project_other"
    if parts and parts[0] == "runs":
        if "checkpoint" in str(relative).lower():
            return "runtime_checkpoints"
        return "runtime_histories_and_artifacts"
    return "acceptance_sources_and_outputs"


def physical_bytes(dataset):
    """Count file lengths, including publication overhead, without estimating deduplication."""
    dataset = Path(dataset)
    totals, counts, allocated_files, allocated_dirs = Counter(), Counter(), Counter(), Counter()
    files, directories, allocations_available = [], [], True
    for path in [dataset, *sorted(dataset.rglob("*"))]:
        if ".git" in path.relative_to(dataset).parts:
            continue
        relative, stat = path.relative_to(dataset), path.stat()
        size = stat.st_size
        kind = category(relative)
        allocated = stat.st_blocks * 512 if hasattr(stat, "st_blocks") else None
        allocations_available = allocations_available and allocated is not None
        if path.is_dir():
            allocated_dirs[kind] += allocated or 0
            directories.append({"path": str(relative), "allocated_bytes": allocated, "category": kind})
            continue
        if not path.is_file():
            continue
        totals[kind] += size
        counts[kind] += 1
        allocated_files[kind] += allocated or 0
        files.append({"path": str(relative), "bytes": size, "category": kind, "allocated_bytes": allocated})
    return {"bytes_by_category": dict(totals), "files_by_category": dict(counts),
            "total_bytes": sum(totals.values()), "files": files,
            "allocated_file_bytes_by_category": dict(allocated_files) if allocations_available else None,
            "allocated_directory_bytes_by_category": dict(allocated_dirs) if allocations_available else None,
            "total_allocated_file_bytes": sum(allocated_files.values()) if allocations_available else None,
            "total_allocated_directory_bytes": sum(allocated_dirs.values()) if allocations_available else None,
            "directories": directories, "directories_by_category": dict(Counter(
                item["category"] for item in directories)),
            "embedded_runtime_sections": runtime_sections(dataset),
            "scope": "File lengths plus filesystem-reported allocation (st_blocks * 512) per pathname. "
                     "Files and directories are separate; includes markers, blocks, histories and checkpoints. "
                     "Excludes task-project .git. Allocation is filesystem-specific, not device I/O or OS cache."}


def runtime_sections(dataset):
    """Report inline history/checkpoint payloads separately from their containing files."""
    rows = []
    for path in sorted((dataset / "runs").rglob("*.json")):
        document = json.loads(path.read_text())
        if type(document) is not dict or "replay_state" not in document:
            continue
        state, row = document["replay_state"], {"path": str(path.relative_to(dataset))}
        for key in ("execution_history", "checkpoints"):
            value = state[key]
            row[key + "_count"] = len(value)
            row[key + "_json_bytes"] = len(json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        rows.append(row)
    return {"artifacts": rows,
            "scope": "Standalone compact JSON lengths of inline sections, already contained in runtime "
                     "artifact file lengths. Do not add these figures to physical totals."}
