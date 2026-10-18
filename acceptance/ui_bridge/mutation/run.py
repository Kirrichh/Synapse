"""Run seeded behavioral mutants in isolated copies, never in the working tree."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
from time import monotonic

ROOT = Path(__file__).resolve().parents[3]
IGNORED = shutil.ignore_patterns("__pycache__", ".pytest_cache", "node_modules", "target", "dist", "gen", "resources")


def copy_subject(destination):
    shutil.copytree(ROOT / "synapse", destination / "synapse", ignore=IGNORED)
    shutil.copytree(ROOT / "scripts/ui", destination / "scripts/ui", ignore=IGNORED)
    shutil.copytree(ROOT / "acceptance/ui_bridge", destination / "acceptance/ui_bridge", ignore=IGNORED)
    (destination / "synapse/ui/node_modules").symlink_to(ROOT / "synapse/ui/node_modules", target_is_directory=True)
    resource = destination / "synapse/ui/src-tauri/resources"
    resource.mkdir()
    (resource / "core").symlink_to(ROOT / "synapse/ui/src-tauri/resources/core", target_is_directory=True)
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, destination / name)


@contextmanager
def serve(directory):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def execute(seed, root, python, cargo, timeout):
    environment = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    kind = seed["kind"]
    def run(command, cwd=root):
        return subprocess.run(command, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=timeout)
    if kind == "python":
        result = run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *seed["cases"]])
        marker = " failed"
    elif kind == "rust":
        result = run([cargo, "test", "--offline", "--manifest-path", "acceptance/ui_bridge/native/Cargo.toml", "--test", seed["case"]])
        marker = "test result: FAILED"
    elif kind in {"typescript", "generator"}:
        if kind == "generator":
            generated = run(["node", "tooling/generate-contracts.mjs"], root / "synapse/ui")
            if generated.returncode:
                return "INVALID", generated.stdout
        cases = sorted(str(path.relative_to(root)) for path in (root / "acceptance/ui_bridge/typescript").glob("*.test.ts"))
        result = run(["node", "--import", "./synapse/ui/node_modules/tsx/dist/loader.mjs", "--test", *cases])
        marker = "AssertionError"
    elif kind == "browser":
        build = run(["npm", "run", "build"], root / "synapse/ui")
        if build.returncode:
            return "INVALID", build.stdout
        with serve(root / "synapse/ui/dist") as url:
            result = run([sys.executable, "acceptance/ui_bridge/browser_acceptance.py", "--url", url])
        marker = "AssertionError"
        if "playwright._impl._errors.TimeoutError" in result.stdout:
            marker = "playwright._impl._errors.TimeoutError"
    else:
        raise ValueError(kind)
    if result.returncode == 0:
        return "SURVIVED", result.stdout
    if marker in result.stdout and "ERROR collecting" not in result.stdout:
        return "KILLED", result.stdout
    return "INVALID", result.stdout


def selected_cases(seed):
    if seed["kind"] == "python":
        return seed["cases"]
    if seed["kind"] == "rust":
        return [f"acceptance/ui_bridge/native/{seed['case']}.rs"]
    if seed["kind"] == "browser":
        return ["acceptance/ui_bridge/browser_acceptance.py"]
    return sorted(str(path.relative_to(ROOT)) for path in
                  (ROOT / "acceptance/ui_bridge/typescript").glob("*.test.ts"))


def main(args):
    seeds = [seed for source in args.seeds for seed in json.loads(source.read_text())]
    if args.only:
        seeds = [seed for seed in seeds if seed["id"] in args.only]
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    baseline_signatures = set()
    for seed in seeds:
        signature = json.dumps({key: seed[key] for key in ("kind", "case", "cases") if key in seed}, sort_keys=True)
        with tempfile.TemporaryDirectory(prefix="synapse-mutant-", dir=args.workspace) as temporary:
            subject = Path(temporary)
            copy_subject(subject)
            if signature not in baseline_signatures:
                status, _ = execute(seed, subject, args.python, args.cargo, args.timeout)
                if status != "SURVIVED":
                    raise RuntimeError(f"Baseline failed for {seed['id']}: {status}")
                baseline_signatures.add(signature)
            path = subject / seed["target"]
            original = path.read_text()
            if original.count(seed["before"]) != 1:
                raise ValueError(f"Mutation no longer has one application site: {seed['id']}")
            path.write_text(original.replace(seed["before"], seed["after"], 1))
            started = monotonic()
            try:
                status, _ = execute(seed, subject, args.python, args.cargo, args.timeout)
            except subprocess.TimeoutExpired:
                status = "TIMEOUT"
            records.append({"id": seed["id"], "behavior": seed["behavior"], "target": seed["target"],
                "source_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "result": status, "seconds": round(monotonic() - started, 3),
                "selected_acceptance": selected_cases(seed)})
            (args.output / "results.json").write_text(json.dumps({"baseline": "PASS", "mutants": records}, indent=2) + "\n")
            print(seed["id"], status, flush=True)
    return 0 if all(record["result"] == "KILLED" for record in records) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seeds", nargs="+", type=Path)
    parser.add_argument("--python", required=True)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--only", action="append")
    raise SystemExit(main(parser.parse_args()))
