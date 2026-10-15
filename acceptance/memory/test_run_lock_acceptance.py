"""A cognitive run's lock is never left without the owner that proves it stale (review LOCK).

Real CLI processes take the lock of one run. A driver stops a process at a
point inside the lock protocol — killed (SIGKILL) or held until released —
by tracing the product's own functions; nothing in the product is replaced:

* killed while naming its owner, the process leaves no lock, and the run starts
  again and books once;
* held at that same point, the process keeps the run to itself: a second
  process for the same run is refused before any effect, and the first one
  finishes and books once;
* killed once its lock exists, it leaves a lock naming a dead owner, which the
  next start clears — and the same holds for a recovery killed while it holds
  the lock of the run it recovers;
* a lock that names no owner (a run of the P2a profile, or a version before the
  claim protocol) is never guessed stale: the run stays refused.

Every booking is counted by the environment's own record.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from acceptance.memory import _bookings as desk
from acceptance.memory._world import REPOSITORY

DRIVER = r'''
import json, os, signal, sys, time
from synapse.cli import main

spec = json.load(open(sys.argv[1]))
event_name, function = spec["point"]

def trace(frame, event, arg):
    if event == event_name and frame.f_code.co_name == function and frame.f_code.co_filename.endswith("durable_cognitive.py"):
        with open(spec["marker"], "w") as marker:
            marker.write(spec["action"])
        if spec["action"] == "kill":
            os.kill(os.getpid(), signal.SIGKILL)
        while not os.path.exists(spec["release"]):
            time.sleep(0.05)
    return trace

sys.settrace(trace)
raise SystemExit(main(spec["argv"]))
'''
NAMING = ("call", "write_lock_owner")
NAMED = ("return", "create_named_lock")


def _driven(tmp_path, arguments, point, action) -> tuple[subprocess.Popen, dict]:
    spec = {"argv": list(map(str, arguments)), "point": list(point), "action": action,
            "marker": str(tmp_path / f"{action}.marker"), "release": str(tmp_path / f"{action}.release")}
    path = tmp_path / f"{action}.driver.json"
    path.write_text(json.dumps(spec))
    process = subprocess.Popen([sys.executable, "-B", "-c", DRIVER, str(path)], cwd=REPOSITORY,
                               env={**os.environ, "PYTHONPATH": str(REPOSITORY)},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return process, spec


def _reached(spec, process, seconds=120) -> None:
    deadline = time.monotonic() + seconds
    while not Path(spec["marker"]).exists():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "the process did not reach the lock protocol"
        time.sleep(0.05)


def _session(tmp_path, run_id):
    world = desk.world(tmp_path)
    return world, world._run_arguments(desk.BOOK, run_id, desk.inputs(run_id, "book", "once", "F-OK"))


def test_a_process_killed_while_naming_its_owner_leaves_no_lock_and_the_run_starts_again(tmp_path):
    world, arguments = _session(tmp_path, "early")
    process, spec = _driven(tmp_path, arguments, NAMING, "kill")
    process.communicate(timeout=120)
    assert process.returncode == -signal.SIGKILL and open(spec["marker"]).read() == "kill"
    assert not (world.runs / "early.json.lock").exists() and not (world.runs / "early.json").exists()
    code, payload, stderr = world._cli(*arguments)
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    assert len(desk.bookings(world, "F-OK")) == 1
    assert list(world.runs.glob(".early.json.lock.*.claim")) == []  # The killed taker's preparation is gone.


def test_a_live_process_at_the_same_point_keeps_the_run_to_itself(tmp_path):
    world, arguments = _session(tmp_path, "contested")
    process, spec = _driven(tmp_path, arguments, NAMING, "pause")
    _reached(spec, process)
    code, payload, _ = world._cli(*arguments)
    assert code == 26 and payload["error"]["code"] == "ARTIFACT_EXISTS_OR_LOCKED", payload
    assert desk.bookings(world) == []
    open(spec["release"], "w").close()
    stdout, stderr = process.communicate(timeout=600)
    result = json.loads([line for line in stdout.splitlines() if line.startswith("{")][-1])
    assert process.returncode == 0 and result["status"] == "COMPLETED", (result, stderr)
    assert len(desk.bookings(world, "F-OK")) == 1
    assert not (world.runs / "contested.json.lock").exists()


def test_a_lock_naming_a_dead_owner_is_cleared_by_the_next_start(tmp_path):
    world, arguments = _session(tmp_path, "named")
    process, spec = _driven(tmp_path, arguments, NAMED, "kill")
    process.communicate(timeout=120)
    lock = world.runs / "named.json.lock"
    assert process.returncode == -signal.SIGKILL and (lock / "owner.json").exists()
    assert not (world.runs / "named.json").exists()
    code, payload, stderr = world._cli(*arguments)
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    assert len(desk.bookings(world, "F-OK")) == 1


def test_a_recovery_killed_while_holding_the_lock_leaves_it_to_the_next_recovery(tmp_path):
    world = desk.world(tmp_path)
    started = world.start(desk.BOOK, "slow", desk.inputs("slow", "book", "once", "F-SLOW"))
    deadline = time.monotonic() + 180
    while len(desk.bookings(world, "F-SLOW")) < 1:
        assert started.poll() is None and time.monotonic() < deadline, "the session did not book"
        time.sleep(0.2)
    os.killpg(started.pid, signal.SIGKILL)  # After the booking, before its answer.
    started.wait(60)
    recovery, spec = _driven(tmp_path, ["resume", "--state-file", world.runs / "slow.json"], NAMED, "kill")
    recovery.communicate(timeout=120)
    assert recovery.returncode == -signal.SIGKILL and (world.runs / "slow.json.lock" / "owner.json").exists()
    code, payload, stderr = world.resume("slow")
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    assert len(desk.bookings(world, "F-SLOW")) == 1


def test_a_lock_naming_no_owner_is_never_guessed_stale(tmp_path):
    world, arguments = _session(tmp_path, "ownerless")
    (world.runs / "ownerless.json.lock").mkdir(parents=True)
    for _ in range(2):
        code, payload, _ = world._cli(*arguments)
        assert code == 26 and payload["error"]["code"] == "ARTIFACT_EXISTS_OR_LOCKED", payload
    assert desk.bookings(world) == [] and (world.runs / "ownerless.json.lock").is_dir()
