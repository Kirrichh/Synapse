"""Operation Blackout — field test of Synapse through its canonical CLI.

Usage (from the repository root):
    python field_test/run_field_test.py [--seeds N] [--incidents K]

Sections:
  A. Correctness: N random worlds x K incidents, every conclusion vs an oracle.
  B. Sabotage: the same scenario with one injected fault; governance must stop it.
  C. Durable approval workflow across processes (PENDING -> ... -> COMPLETED),
     duplicate / conflicting signals and a tampered artifact.
  D. Golden record + mock replay of the full scenario and of minimal programs.
  E. Probes for defects found while writing the agents (REPRODUCED = still there).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from gen import make_incident, oracle, to_synapse  # noqa: E402

SCENARIO = (HERE / "blackout.syn").read_text(encoding="utf-8")
WORK = Path(tempfile.mkdtemp(prefix="blackout-"))
RESULTS: list[tuple[str, str, bool, str]] = []  # section, name, ok, detail


def check(section: str, name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((section, name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def synapse(*args: str, stdin: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "synapse", *args],
        cwd=REPO, capture_output=True, text=True, input=stdin, timeout=timeout,
    )


def write(name: str, text: str) -> Path:
    path = WORK / name
    path.write_text(text, encoding="utf-8")
    return path


def world(seed: int, k: int) -> list[dict]:
    rng = random.Random(seed)
    return [make_incident(rng, i + 1) for i in range(k)]


def program(incidents: list[dict], scenario: str = SCENARIO) -> str:
    return "let incidents = " + to_synapse(incidents) + "\n" + scenario


def parse_results(stdout: str) -> dict[tuple[str, str], str]:
    out = {}
    for line in stdout.splitlines():
        if line.startswith("RESULT|"):
            _, inc, key, value = line.split("|", 3)
            out[(inc, key)] = value
    return out


# ─────────────────────────────────────────────────────────────── A ──
def section_a(seeds: int, k: int) -> None:
    print(f"\nA. Correctness: {seeds} worlds x {k} incidents")
    total_checks = wrong = 0
    durations = []
    for seed in range(1, seeds + 1):
        incs = world(seed, k)
        path = write(f"world{seed}.syn", program(incs))
        t0 = time.perf_counter()
        proc = synapse("run", str(path))
        durations.append(time.perf_counter() - t0)
        got = parse_results(proc.stdout)
        errors = []
        if proc.returncode != 0:
            errors.append(f"exit {proc.returncode}: {proc.stderr.strip()[-200:]}")
        seen_roots: list[str] = []
        for inc in incs:
            exp = oracle(inc)
            exp["prior_incidents_same_root"] = str(seen_roots.count(exp["root"]))
            seen_roots.append(exp["root"])
            for key, value in exp.items():
                if value is None:
                    continue
                total_checks += 1
                actual = got.get((inc["id"], key))
                if actual != value:
                    wrong += 1
                    errors.append(f"{inc['id']}.{key}: expected {value!r}, got {actual!r}")
        audit = {
            "policy_evaluated": 3 * k,         # intent + two Ops sends per incident
            "policy_violation": 0,
            "message_received": sum(len(i["alerts"]) for i in incs) + 2 * k,
            "integrate_rollback": k,          # the bad draft
            "integrate_committed": k,         # the verified postmortem
            "habit_activated": k,
            "fractures_integrated": k,
            "roots": ",".join(oracle(i)["root"] for i in incs),
        }
        for key, value in audit.items():
            total_checks += 1
            actual = got.get(("ALL", key))
            if actual != str(value):
                wrong += 1
                errors.append(f"ALL.{key}: expected {value}, got {actual}")
        check("A", f"world seed={seed} ({sum(len(i['graph']) for i in incs)} services, "
              f"{sum(len(i['alerts']) for i in incs)} alerts)", not errors, "; ".join(errors[:4]))
    print(f"  conclusions checked: {total_checks}, wrong: {wrong}; "
          f"run time per world: min {min(durations):.2f}s, max {max(durations):.2f}s")


# ─────────────────────────────────────────────────────────────── B ──
def sabotage(name: str, incs: list[dict], expect_error: str, mutate=None, scenario_patch=None,
             must_not_contain: tuple[str, ...] = ()) -> None:
    scenario = SCENARIO
    if scenario_patch:
        old, new = scenario_patch
        assert old in scenario, f"patch anchor missing for {name}"
        scenario = scenario.replace(old, new, 1)
    if mutate:
        mutate(incs)
    proc = synapse("run", str(write(f"sabotage_{name}.syn", program(incs, scenario))))
    stopped = proc.returncode != 0 and expect_error in (proc.stderr + proc.stdout)
    leaked = [m for m in must_not_contain if m in proc.stdout]
    check("B", name, stopped and not leaked,
          f"exit={proc.returncode} stderr={proc.stderr.strip()[-160:]!r} leaked={leaked}")


def section_b() -> None:
    print("\nB. Sabotage: one injected fault per run; governance must stop it")

    def narrow_approval(incs):
        incs[1]["approved_radius"] = 1  # incident 2 needs more than one service

    sabotage("blast radius above approval (INC-2)", world(7, 3), "blast radius above approval",
             mutate=narrow_approval, must_not_contain=("RESULT|INC-2|ops_executed", "RESULT|INC-3|"))
    sabotage("destructive command to Ops", world(8, 2), "destructive command forbidden",
             scenario_patch=('{"command": "restart " + root', '{"command": "DROP TABLE " + root'),
             must_not_contain=("RESULT|INC-1|ops_executed",))
    sabotage("irreversible rollback intent", world(9, 2), "rollback must be reversible",
             scenario_patch=("            reversible true\n        }\n        declare intent rollback",
                             "            reversible false\n        }\n        declare intent rollback"),
             must_not_contain=("RESULT|INC-1|ops_executed",))
    sabotage("ungrounded claim fails verify", world(10, 2), "root cause must be grounded",
             scenario_patch=("    confidence high\n}", "    confidence low\n}"),
             must_not_contain=("RESULT|INC-1|ops_executed",))
    sabotage("guard tries to send (purity)", world(11, 1), "Policy guard cannot send actor messages",
             scenario_patch=('if args[0].command.contains("rm -rf")',
                             'send Triage.alert("leak")\n        if args[0].command.contains("rm -rf")'),
             must_not_contain=("RESULT|INC-1|ops_executed",))
    sabotage("dream tries to act on the world (send)", world(12, 1), "dream cannot send actor messages",
             scenario_patch=("        // ── 7. memory",
                             '        let leak = dream { scenario "x" send Ops.execute({"command": "rollback"}) return 1 }\n        // ── 7. memory'),
             must_not_contain=("RESULT|INC-1|prior_incidents_same_root",))


# ─────────────────────────────────────────────────────────────── C ──
DURABLE = """
agent Executor { model "mock" }
let executor = spawn Executor()
let summary = llm "Summarize the incident and propose a rollback"
print("summary: " + summary)
let approval = suspend await_human_approval("rollback of culprit deploy")
if approval == "approve" {
    executor => execute("rollback culprit")
    let verify = suspend await_external_check("post-rollback health")
    print("health " + str(verify))
    if verify == "green" { print("RESOLVED") } else { print("ESCALATED") }
} else {
    print("REJECTED by on-call")
}
"""


def durable_run(run_id: str, signals: list, sids: list | None = None) -> tuple[list[dict], Path]:
    state = WORK / f"state-{run_id}"
    state.mkdir()
    src = write(f"{run_id}.syn", DURABLE)
    outs = []
    proc = synapse("run", str(src), "--durable", "--state-dir", str(state), "--run-id", run_id)
    outs.append(json.loads(proc.stdout.splitlines()[0]))
    artifact = state / f"{run_id}.json"
    for index, value in enumerate(signals):
        sig = write(f"{run_id}-sig{index}.json", json.dumps(value))
        sid = json.loads(artifact.read_text())["active_suspension"]["suspension_id"]
        if sids is not None:
            sids.append(sid)
        proc = synapse("resume", "--state-file", str(artifact), "--suspension-id", sid, "--signal-file", str(sig))
        outs.append(json.loads(proc.stdout.splitlines()[0]))
    return outs, artifact


def section_c() -> None:
    print("\nC. Durable approval workflow: every step is a separate OS process")
    sids: list[str] = []
    outs, artifact = durable_run("happy", ["db pool exhausted after d1-c", "approve", "green"], sids)
    statuses = [o["status"] for o in outs]
    printed = sum((o.get("output_delta") or [] for o in outs), [])
    check("C", "approve + green -> RESOLVED in 4 processes",
          statuses == ["PENDING", "PENDING", "PENDING", "COMPLETED"] and printed[-1] == "RESOLVED",
          f"{statuses} {printed}")
    outs, _ = durable_run("reject", ["summary", "deny"])
    printed = sum((o.get("output_delta") or [] for o in outs), [])
    check("C", "deny -> REJECTED, no execution", outs[-1]["status"] == "COMPLETED" and printed[-1] == "REJECTED by on-call",
          f"{printed}")
    outs, _ = durable_run("red", ["summary", "approve", "red"])
    printed = sum((o.get("output_delta") or [] for o in outs), [])
    check("C", "approve + red -> ESCALATED", printed[-1] == "ESCALATED", f"{printed}")

    data = json.loads(artifact.read_text())
    first = sids[1]  # the approval suspension
    same = write("dup.json", json.dumps("approve"))
    other = write("conflict.json", json.dumps("deny"))
    dup = synapse("resume", "--state-file", str(artifact), "--suspension-id", first, "--signal-file", str(same))
    stored = data["idempotency"]["resolved_suspensions"][first]["operation_result"]
    answer = json.loads(dup.stdout.splitlines()[0]) if dup.stdout else {}
    check("C", "duplicate signal is answered from the stored result, artifact untouched",
          {k: v for k, v in answer.items() if k != "resume_argv"} == stored and dup.returncode == stored["exit_code"]
          and json.loads(artifact.read_text())["revision"] == data["revision"], dup.stdout[:160])
    conflict = synapse("resume", "--state-file", str(artifact), "--suspension-id", first, "--signal-file", str(other))
    check("C", "conflicting signal is refused (exit 24)", conflict.returncode == 24, conflict.stdout[:160])

    outs, artifact = durable_run("tamper", [])
    doc = json.loads(artifact.read_text())
    doc["replay_state"]["source_code"] = doc["replay_state"]["source_code"].replace('"approve"', '"deny"')
    artifact.write_text(json.dumps(doc))
    sig = write("t.json", json.dumps("x"))
    proc = synapse("resume", "--state-file", str(artifact), "--suspension-id",
                   doc["active_suspension"]["suspension_id"], "--signal-file", str(sig))
    check("C", "tampered source in artifact is refused (exit 21)", proc.returncode == 21, proc.stdout[:160])


# ─────────────────────────────────────────────────────────────── D ──
def record_and_replay(name: str, source: str) -> tuple[bool, str]:
    src = write(f"{name}.syn", source)
    out = WORK / f"rec-{name}"
    rec = synapse("run", str(src), "--record", "--output", str(out))
    if rec.returncode != 0:
        return False, f"record failed: {rec.stderr.strip()[-160:]}"
    rep = synapse("replay", "--mock", str(out))
    return rep.returncode == 0, (rep.stdout + rep.stderr).strip()[-200:]


def section_d() -> None:
    print("\nD. Golden record + mock replay")
    ok, detail = record_and_replay("full", program(world(1, 3)))
    check("D", "full Blackout scenario replays without drift", ok, detail)
    no_memory = SCENARIO
    ok, detail = record_and_replay("tamper-src", program(world(2, 1), no_memory))
    out = WORK / "rec-tamper-src"
    if (out / "history.json").exists():
        hist = json.loads((out / "history.json").read_text())
        hist[len(hist) // 2]["type"] = "forged_event"
        (out / "history.json").write_text(json.dumps(hist))
        rep = synapse("replay", "--mock", str(out))
        check("D", "forged event in recorded history is detected", rep.returncode != 0,
              (rep.stdout + rep.stderr)[-160:])
    pd = 'memory palace "P" { rooms { episodic } backend sqlite bind palace }\n'
    for name, src in {
        "minimal debate": 'let d = debate { branch a { return llm "x" } branch b { return llm "y" } } judge "j" rounds 2',
        "minimal fracture": 'agent L { model "mock" }\nlet self = L\nfracture self into { A { return 1 } } consensus weighted',
        "minimal integrate rollback": 'let a = 1\nintegrate "x" { a = 2 assert false, "no" } on fail rollback',
        "minimal imprint": pd + 'imprint into palace.episodic { content "x" confidence 0.9 source "s" bind id }',
        "minimal intention cascade": 'intention cascade "R" { mission "m" objective "o" task "t" action "a" bind plan }',
    }.items():
        ok, detail = record_and_replay(name.replace(" ", "_"), src)
        check("D", f"{name} replays without drift", ok, detail)


# ─────────────────────────────────────────────────────────────── E ──
def probe(name: str, source: str, bug_if) -> None:
    proc = synapse("run", "-c", source)
    present = bug_if(proc)
    RESULTS.append(("E", name, not present, "REPRODUCED" if present else "not reproduced"))
    print(f"  [{'BUG ' if present else 'OK  '}] {name}")


def section_e() -> None:
    print("\nE. Defect probes (BUG = still reproducible)")
    probe("VM resume inside `context` clobbers the host context stack",
          'context "c" { compile vm { source "let x = 1 let y = 2" bind code } '
          'run vm { source code gas 100 checkpoint "m" at_ip 1 bind r } '
          'run vm { resume_from "m" gas 100 bind f } } print("ok")',
          lambda p: "Context mismatch" in p.stderr)
    probe("affective threshold never fires for events inside a function",
          'affective state "S" { dimensions { valence [-1.0, 1.0] arousal [0.0, 1.0] dominance [0.0, 1.0] } '
          'baseline { valence 0.2 arousal 0.3 dominance 0.6 } bind mood } '
          'affective threshold "T" { when arousal > 0.7 for 1 events priority critical action { reflect { last 1 events } } } '
          'fn go() { affective event "e" { valence -0.7 arousal 0.9 dominance 0.1 bind t } let a = 1 let b = 2 '
          'let n = reflect { last 50 events filter type == "affective_threshold_triggered" } return len(n) } print(go())',
          lambda p: p.stdout.strip().endswith("0"))
    probe("threshold action `reflect on self` (spec example) fails",
          'affective state "S" { dimensions { valence [-1.0, 1.0] arousal [0.0, 1.0] dominance [0.0, 1.0] } '
          'baseline { valence 0.2 arousal 0.3 dominance 0.6 } bind mood } '
          'affective threshold "T" { when arousal > 0.7 for 1 events priority critical action { reflect on self { last 5 events } } } '
          'affective event "e" { valence -0.7 arousal 0.9 dominance 0.1 bind t } let a = 1 '
          'let f = reflect { last 50 events filter type == "affective_threshold_action_failed" } print(len(f))',
          lambda p: p.stdout.strip() not in {"", "0"})
    probe("`imprint` inside `dream` escapes the sandbox into long-term memory",
          'agent L { model "mock" } memory palace "P" { rooms { episodic } backend sqlite bind palace } let self = L '
          'let r = dream { scenario "x" imprint into palace.episodic { content "smuggled" confidence 0.9 source "d" bind z } return 1 } '
          'recall from palace.episodic { query "smuggled" threshold 0.1 limit 5 bind got } print(len(got))',
          lambda p: p.stdout.strip() == "1")
    probe("fracture `consensus majority` does not pick the majority position",
          'agent L { model "mock" } let self = L fracture self into { A { return "x" } B { return "x" } C { return "y" } } '
          'consensus majority integrate { print(consensus) }',
          lambda p: "'C': 'y'" in p.stdout)
    probe("`distributed consensus` cannot collect votes from .syn (always deferred)",
          'agent P { model "mock" fn consensus_vote(p) { return {"vote": "yes"} } } '
          'distributed consensus with [P] on "x" { quorum 1 timeout 5 policy "MajorityVote" bind v } print(v.committed)',
          lambda p: p.stdout.strip() == "False")
    probe("string literals have no escapes (\\n printed literally)",
          'print("a\\nb")', lambda p: "\\n" in p.stdout)
    probe("map literal with a variable key uses the variable name, error is a bare KeyError",
          'let k = "real" let m = {k: 1} print(m["real"])', lambda p: p.stderr.strip() == "Error: 'real'")
    probe("policy violation cannot be handled in the interpreter (no try/catch)",
          'agent W { model "mock" } policy P { target "W.go" guard (a) { reject "no" } } '
          'try { send W.go(1) } catch (GUARD_VIOLATION) { print("handled") }',
          lambda p: "handled" not in p.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--incidents", type=int, default=3)
    parser.add_argument("--keep", action="store_true", help="keep the work directory")
    args = parser.parse_args()
    t0 = time.perf_counter()
    print(f"work dir: {WORK}")
    section_a(args.seeds, args.incidents)
    section_b()
    section_c()
    section_d()
    section_e()
    by = {}
    for section, _, ok, _ in RESULTS:
        p, f = by.get(section, (0, 0))
        by[section] = (p + ok, f + (not ok))
    print("\nSUMMARY")
    for section in sorted(by):
        p, f = by[section]
        label = "bugs reproduced" if section == "E" else "failed"
        print(f"  {section}: {p} ok, {f} {label}")
    print(f"  wall time {time.perf_counter() - t0:.1f}s")
    (HERE / "last_results.json").write_text(json.dumps(
        [{"section": s, "name": n, "ok": o, "detail": d} for s, n, o, d in RESULTS], indent=2, ensure_ascii=False))
    if not args.keep:
        shutil.rmtree(WORK, ignore_errors=True)
    return 0 if all(ok for s, _, ok, _ in RESULTS if s in {"A", "B", "C"}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
