"""Describe observed memory costs without adding overlapping cumulative times."""
from __future__ import annotations

from collections import Counter
import argparse
import json
from pathlib import Path
import pstats


def category(key):
    path, _, name = key
    if path.endswith("/memory_open_profile/scopes.py"):
        return "profile_observer"
    if path.endswith("/copy.py"):
        return "object_copy"
    if "/json/decoder.py" in path or ("/json/__init__.py" in path and name in {"load", "loads"}):
        return "json_deserialization"
    if "/json/encoder.py" in path or ("/json/__init__.py" in path and name in {"dump", "dumps"}):
        return "json_serialization"
    if "hashlib" in path or "hashlib" in name or "HASH" in name:
        return "hashing"
    if path == "~" and any(word in name for word in ("posix.read", "posix.open", "posix.stat", "posix.lstat",
                                                     "posix.fstat", "posix.close", "'read' of", "'readinto' of")):
        return "filesystem_read_and_metadata"
    if path.endswith("/canonical_values.py") or path.endswith("/canonicalization.py"):
        return "canonical_value_processing_self"
    if path.endswith("/context_codec.py") or path.endswith("/memory_consolidation/records.py"):
        return "record_codec_and_validation_self"
    if path.endswith("/gold/persistence.py"):
        return "persistence_coordination_self"
    return "other_self"


def function_row(key, value):
    primitive, calls, own, cumulative, callers = value
    return {"file": key[0], "line": key[1], "function": key[2], "primitive_calls": primitive,
            "calls": calls, "self_s": own, "inclusive_cumulative_s": cumulative,
            "callers": [{"file": item[0], "line": item[1], "function": item[2], "profile": list(counts)}
                        for item, counts in callers.items()]}


def profile_summary(path):
    stats = pstats.Stats(str(path))
    categories = Counter()
    for key, value in stats.stats.items():
        categories[category(key)] += value[2]
    assert abs(sum(categories.values()) - stats.total_tt) < 0.000001
    selected = {"_scan_inventory", "_read", "inventory", "read_committed_snapshot_transaction",
                "read_regular_bytes", "decode_canonical", "deepcopy", "court_chain", "_report", "verify",
                "state", "apply_report", "_snapshot", "latest_boundary", "boundaries", "consolidate",
                "assemble", "boundary_record", "open_session", "_exam_session", "_recorded_pin"}
    ranked = sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)
    return {"total_profile_self_s": stats.total_tt, "total_calls": stats.total_calls,
            "disjoint_self_time_s": dict(categories),
            "selected_inclusive": [function_row(key, value) for key, value in ranked if key[2] in selected],
            "file_read_calls": [function_row(key, value) for key, value in ranked
                                if key[0] == "~" and any(token in key[2] for token in
                                                        ("posix.read", "'read' of", "'readinto' of"))],
            "top_inclusive": [function_row(key, value) for key, value in ranked[:30]],
            "accounting": "Self categories partition observed function self time exactly. Inclusive rows "
                          "overlap and must not be summed. cProfile instrumentation changes elapsed time."}


def opened_file(path, cache):
    if path not in cache:
        file = Path(path)
        row = {"path": path, "size_bytes_after_run": file.stat().st_size} if file.is_file() else {"path": path}
        if file.name == "event.json" and file.parent.parent.name == "events":
            event = json.loads(file.read_text())
            row["memory_event_kind"] = event.get("kind")
        cache[path] = row
    return cache[path]


def inputs_summary(opens, cache):
    files = [opened_file(path, cache) for path in sorted(opens)]
    kinds = Counter(row["memory_event_kind"] for row in files if "memory_event_kind" in row)
    return {"read_open_calls": sum(opens.values()), "unique_opened_paths": len(opens),
            "unique_file_size_bytes_after_run": sum(row.get("size_bytes_after_run", 0) for row in files),
            "unique_event_kinds": dict(kinds), "snapshot_boundaries": kinds["SNAPSHOT_BOUNDARY"],
            "materialized_memory_states": kinds["MEMORY_STATE"], "files": files,
            "accounting": "Audit events count read-only file opens, not bytes read or disk I/O. Sizes describe "
                          "unique files after the run; repeated opens do not multiply unique bytes."}


def summarize(rows):
    cache = {}
    return [{**row, "functions": profile_summary(row["profile"]),
             "inputs": inputs_summary(row["read_open_counts"], cache)} for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    options = parser.parse_args()
    captured = json.loads(options.capture.read_text())
    result = {**captured, "profiles": summarize(captured["profiles"])}
    options.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({"summary": str(options.output), "intervals": len(result["profiles"])}))


if __name__ == "__main__":
    main()
