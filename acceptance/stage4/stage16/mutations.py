"""Run declared falsifications in disposable Git worktrees and retain proof.

The matrix names an exact weakening and an existing acceptance killer. A red
test alone is insufficient: the unmodified killer must pass, the expected
assertion must fail, and source restoration must be verified. No production
module imports this runner or receives policy from it.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from .protocol import canonical, code_identity, digest, environment_identity, source


def _run_killer(root, killer, output, name, timeout):
    junit, log = output / (name + ".xml"), output / (name + ".log")
    command = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
        "--junitxml", str(junit), killer]
    started = time.monotonic_ns()
    with log.open("wb") as stream:
        process = subprocess.run(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT,
            timeout=timeout, env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    cases = list(ET.parse(junit).getroot().iter("testcase")) if junit.exists() else []
    failures = [{"case": case.attrib, "message": failure.get("message", ""), "text": failure.text or ""}
        for case in cases for failure in case.findall("failure")]
    return {"argv": command, "returncode": process.returncode,
        "duration_ns": str(time.monotonic_ns() - started), "cases": len(cases),
        "errors": sum(len(case.findall("error")) for case in cases),
        "skipped": sum(len(case.findall("skipped")) for case in cases), "failures": failures,
        "log_ref": source(log), "junit_ref": source(junit) if junit.exists() else None}


def run_mutation(*, repository, case, output):
    repository, output = Path(repository).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.run(["git", "-C", str(repository), "diff", "--quiet", "HEAD", "--"]).returncode:
        raise ValueError("mutation execution requires a clean committed revision")
    report = {"schema_version": "synapse.acceptance.stage16.mutation/v1", "case": case,
        "case_id": digest(case), "revision": revision, "environment": environment_identity(), "status": "NOT_RUN"}
    with tempfile.TemporaryDirectory(prefix="synapse-falsification-") as directory:
        checkout = Path(directory) / "repo"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "--detach", str(checkout), revision],
            check=True, capture_output=True)
        try:
            # Dependency installation can leave build metadata in the caller.
            # Mutations execute the clean committed checkout, never that debris.
            if subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain"]):
                raise ValueError("isolated mutation checkout is not clean")
            initial = code_identity(checkout)
            if initial != code_identity(repository):
                raise ValueError("caller runtime or verifier differs from the committed snapshot")
            report["code"] = initial
            baseline = _run_killer(checkout, case["killer"], output, "baseline", case.get("timeout_seconds", 1200))
            report["baseline"] = baseline
            if baseline["returncode"] != 0 or not baseline["cases"] or baseline["errors"] or baseline["skipped"]:
                report["status"] = "BASELINE_FAILED"
            else:
                path = checkout / case["path"]
                if not path.resolve().is_relative_to(checkout) or path.is_symlink():
                    raise ValueError("mutant path escaped the isolated checkout")
                original = path.read_bytes() if path.exists() else None
                if case["before"] is None:
                    if original is not None:
                        raise ValueError("add-file mutant target already exists")
                    mutated = case["after"].encode()
                else:
                    before = case["before"].encode()
                    if original is None or original.count(before) != 1:
                        raise ValueError("mutant no longer matches one exact source location")
                    mutated = original.replace(before, case["after"].encode(), 1)
                report["patch_identity"] = digest({"path": case["path"],
                    "before": None if original is None else hashlib.sha256(original).hexdigest(),
                    "after": hashlib.sha256(mutated).hexdigest()})
                try:
                    path.write_bytes(mutated)
                    negative = _run_killer(checkout, case["killer"], output, "mutated", case.get("timeout_seconds", 1200))
                    report["mutated"] = negative
                    expected = [failure for failure in negative["failures"]
                        if case["expected_failure"] in failure["message"] + failure["text"]]
                    report["status"] = "KILLED" if negative["returncode"] == 1 and expected and not negative["errors"] and not negative["skipped"] else (
                        "SURVIVED" if negative["returncode"] == 0 else "UNEXPECTED_FAILURE")
                finally:
                    if original is None:
                        path.unlink()
                    else:
                        path.write_bytes(original)
                    restored = code_identity(checkout)
                    dirty = subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain"])
                    report["restoration"] = {"code": restored, "clean": not dirty, "matches_initial": restored == initial}
                    if dirty or restored != initial:
                        report["status"] = "RESTORATION_FAILED"
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            report.update(status="INFRASTRUCTURE_FAILURE", error_class=type(exc).__name__, detail=str(exc)[:500])
        finally:
            subprocess.run(["git", "-C", str(repository), "worktree", "remove", "--force", str(checkout)],
                check=True, capture_output=True)
    report["report_id"] = digest(report)
    (output / "report.json").write_bytes(canonical(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    cases = json.loads(Path(__file__).with_name("mutations.json").read_bytes())["cases"]
    selected = [case for case in cases if case["id"] == args.case]
    if len(selected) != 1:
        parser.error("choose one registered mutation identity")
    report = run_mutation(repository=root, case=selected[0], output=args.output)
    print(canonical(report).decode())
    return 0 if report["status"] == "KILLED" else 1


if __name__ == "__main__":
    sys.exit(main())
