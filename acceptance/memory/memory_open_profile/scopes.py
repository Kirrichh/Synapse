"""Capture independent profiles of observed memory call intervals."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import cProfile
import json
import os
from pathlib import Path
import sys
import time


class ScopedProfiles:
    """Observe selected call intervals without replacing their implementations."""

    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.rows = []
        self.active = None
        self.targets = {}
        self.ordinals = Counter()
        sys.addaudithook(self._audit)

    def _audit(self, event, args):
        if self.active is None or event != "open":
            return
        path, _, flags = args
        if isinstance(path, (str, bytes)) and isinstance(flags, int) and flags & os.O_ACCMODE == os.O_RDONLY:
            self.active["read_opens"][os.fsdecode(path)] += 1

    def _begin(self, name, code=None):
        if self.active is not None:
            raise RuntimeError("independent profiles cannot overlap")
        self.active = {"name": name, "code": code, "read_opens": Counter(), "profiler": cProfile.Profile(),
                       "wall_started": time.perf_counter(), "cpu_started": time.process_time()}
        self.active["profiler"].enable()

    def _end(self):
        active = self.active
        active["profiler"].disable()
        wall = time.perf_counter() - active["wall_started"]
        cpu = time.process_time() - active["cpu_started"]
        self.active = None
        path = self.output / (active["name"] + ".pstats")
        active["profiler"].dump_stats(path)
        self.rows.append({"name": active["name"], "profile": str(path), "profiled_wall_s": wall,
                          "profiled_cpu_s": cpu, "read_open_counts": dict(active["read_opens"])})
        print(json.dumps({"profile_completed": active["name"], "wall_s": wall}), flush=True)

    def call(self, name, function, *args, **kwargs):
        self._begin(name)
        try:
            return function(*args, **kwargs)
        finally:
            self._end()

    def _started(self, code, offset):
        if self.active is None:
            name = self.targets[code]
            self.ordinals[name] += 1
            self._begin(f"{name}-{self.ordinals[name]}", code)

    def _returned(self, code, offset, value):
        if self.active is not None and self.active["code"] is code:
            self._end()

    @contextmanager
    def observe(self, functions):
        if not hasattr(sys, "monitoring"):
            raise RuntimeError("scoped observation requires Python 3.12 sys.monitoring")
        monitoring = sys.monitoring
        tool = next(identity for identity in (5, 4, 3, 1, 0) if monitoring.get_tool(identity) is None)
        monitoring.use_tool_id(tool, "memory-opening-acceptance-profile")
        self.targets = {function.__code__: name for name, function in functions.items()}
        events = monitoring.events
        monitoring.register_callback(tool, events.PY_START, self._started)
        monitoring.register_callback(tool, events.PY_RETURN, self._returned)
        monitoring.register_callback(tool, events.PY_UNWIND, self._returned)
        mask = events.PY_START | events.PY_RETURN
        try:
            monitoring.set_events(tool, events.PY_UNWIND)
            for code in self.targets:
                monitoring.set_local_events(tool, code, mask)
            yield
        finally:
            monitoring.set_events(tool, 0)
            for code in self.targets:
                monitoring.set_local_events(tool, code, 0)
            for event in (events.PY_START, events.PY_RETURN, events.PY_UNWIND):
                monitoring.register_callback(tool, event, None)
            monitoring.free_tool_id(tool)
            if self.active is not None:
                self._end()
            self.targets = {}
