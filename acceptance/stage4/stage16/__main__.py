"""Operator CLI for the external acceptance platform; never a product entrypoint."""

import argparse
import json
from pathlib import Path
import sys

from .assessment import assess
from .harness import Experiment
from .protocol import Protocol, canonical, preregister, source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[3])
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="freeze all allocations and their actual initial sources")
    freeze.add_argument("--design", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run", help="dispatch or resume the registered inventory")
    run.add_argument("--experiment", type=Path, required=True)
    run.add_argument("--protocol", type=Path)
    run.add_argument("--approve-pending", action="store_true", help="perform the ordinary Gold operator approval")
    report = commands.add_parser("report", help="physically reopen every arm and emit a diagnostic assessment")
    report.add_argument("--experiment", type=Path, required=True)
    report.add_argument("--format", choices=("json", "table"), default="json")
    prepare = commands.add_parser("prepare-astropy", help="prepare pinned external Astropy pilot inputs")
    prepare.add_argument("--root", type=Path, required=True)
    calibrate = commands.add_parser("calibrate-astropy", help="check real negative and reference-fix oracle outcomes")
    calibrate.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"prepare-astropy", "calibrate-astropy"}:
            from .astropy_pilot import prepare_astropy, calibrate_astropy
            value = (prepare_astropy if args.command == "prepare-astropy" else calibrate_astropy)(args.root)
            print(canonical(value).decode())
            return 2 if value["status"] == "CALIBRATION_FAILED" else 0
        if args.command == "freeze":
            design = json.loads(args.design.read_bytes())
            specification = design.pop("specification")
            pairs = [{**pair, "inputs": {arm: source(path) for arm, path in pair["inputs"].items()}}
                for pair in design.pop("pairs")]
            protocol = preregister(**design, pairs=pairs, repository=args.repository,
                specification={"version": specification["version"], "sha256": source(specification["path"])["sha256"]})
            # Exclusive creation prevents accidental replacement of a frozen design.
            with args.output.open("xb") as stream:
                stream.write(protocol.raw)
            value = {"protocol_id": protocol.identity, "protocol_ref": source(args.output), "schedule": protocol.schedule()}
        else:
            protocol = None if args.command != "run" or args.protocol is None else Protocol(args.protocol.read_bytes())
            experiment = Experiment(args.experiment, repository=args.repository, protocol=protocol)
            if args.command == "run":
                allocations = experiment.run_all(approve_pending=args.approve_pending)
                value = {"protocol_id": experiment.protocol.identity,
                    "allocations": [{k: slot[k] for k in ("slot_id", "arm", "state", "receipt")} for slot in allocations]}
                print(canonical(value).decode())
                return 3 if any(slot["state"] == "APPROVAL_REQUIRED" for slot in allocations) else (
                    0 if all(slot["state"] == "FINISHED" for slot in allocations) else 2)
            value = assess(experiment)
            if args.format == "table":
                print("| Task | Replica | Arm | Execution | Outcome | Attempts | Provider tokens | Seconds | Accounting |")
                print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
                for row in value["runs"]:
                    cells = [row["task_id"], row["replicate_id"], row["arm"], row["execution_state"],
                        None if row["outcome"] is None else row["outcome"]["status"], row["attempt_count"],
                        row["provider_tokens"], row["elapsed_seconds"], row["measurements_status"]]
                    print("| " + " | ".join("UNKNOWN" if cell is None else str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
                print("\nSeconds exclude operator idle time. Money and CPU/I/O coverage are in the JSON source reports.")
                print("Differences describe these runs; they do not attribute savings to reuse.")
                print("Assessment: " + value["assessment_id"])
                return 0
        print(canonical(value).decode())
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(canonical({"status": "REFUSED", "error_class": type(exc).__name__, "detail": str(exc)[:500]}).decode())
        return 2


if __name__ == "__main__":
    sys.exit(main())
