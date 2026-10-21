"""Replay a golden artifact like `synapse replay --mock` and print the differing events."""
import json, sys
from pathlib import Path
import synapse.golden_replay as g
root = Path(sys.argv[1])
captured = {}
orig = g._diff_events
def spy(expected, actual):
    captured["e"], captured["a"] = expected, actual
    return orig(expected, actual)
g._diff_events = spy
try:
    g.replay_mock_artifact(root)
    print("no drift")
except Exception as exc:
    print(type(exc).__name__, exc)
e, a = captured.get("e", []), captured.get("a", [])
print("lengths", len(e), len(a))
norm = lambda ev: {k: v for k, v in ev.items() if k not in {"trace_id", "hash", "prev_hash", "timestamp", "event_hash"}}
shown = 0
for i in range(max(len(e), len(a))):
    x = norm(e[i]) if i < len(e) else None
    y = norm(a[i]) if i < len(a) else None
    if x != y:
        keys = sorted(set((x or {}).keys()) | set((y or {}).keys()))
        diff = {k: ((x or {}).get(k), (y or {}).get(k)) for k in keys if (x or {}).get(k) != (y or {}).get(k)}
        print(i, (x or {}).get("type"), "|", json.dumps(diff, default=str)[:400])
        shown += 1
        if shown > 15: break
