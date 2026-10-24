"""Fresh-interpreter admission against a physical isolated runtime copy.

Each case copies the Python runtime outside the checkout and hashes installed
dependencies. Keep this heavier runtime-identity scenario independently schedulable.
No plugin is loaded and no agent process is dispatched.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = r'''
from pathlib import Path
import sys
from synapse.agents.codec import digest
from synapse.agents.configuration import (
    AGENT_CONFIGURATION_V1, capture_definition, profile_from_dict,
    validate_configuration, verify_configuration_runtime,
)
from synapse.agents.contracts import AgentProfile, AgentTransportKind, LocalInformationPolicy

evidence = Path("synthetic-admission.txt")
evidence.write_text("Synthetic acceptance evidence for the isolated drift scenario.")
profile = AgentProfile("reference", "reference", "1", "reference", "1", AgentTransportKind.STDIO,
    ("bytes.measure",), ("application/octet-stream",), ("reference.byte-measurement/v1",),
    LocalInformationPolicy.NOT_SUPPORTED, ("READ_ONLY",), "reference")
definition = capture_definition(factory="stdio", profile=profile, native={}, distributions=(),
    evidence_paths=(evidence,), capabilities=profile.capabilities)
configuration = {"schema_version": AGENT_CONFIGURATION_V1, "profiles": [definition], "preferred_profiles": []}
verify_configuration_runtime(configuration)
case = sys.argv[1]
if case == "code":
    path = Path("synapse/commands/jcs.py")
    source = path.read_text()
    assert "MAX_DEPTH = 32" in source
    path.write_text(source.replace("MAX_DEPTH = 32", "MAX_DEPTH = 31"))
elif case == "schema":
    path = Path("synapse/commands/binding.schema.json")
    source = path.read_text()
    assert '"maxLength":192' in source
    path.write_text(source.replace('"maxLength":192', '"maxLength":191'))
else:
    definition["distributions"] = [item for item in definition["distributions"]
                                    if item["name"].lower() != "rfc8785"]
    identity = {"files": definition["runtime_files"], "distributions": definition["distributions"],
                "environment": definition["runtime_environment"]}
    definition["profile"]["runtime_identity"] = digest(identity)
    definition["admission"]["profile_sha256"] = digest(profile_from_dict(definition["profile"]))
    validate_configuration(configuration)
try:
    verify_configuration_runtime(configuration)
except ValueError:
    print("CHANGED_COMMAND_RUNTIME_REFUSED")
else:
    raise AssertionError("changed command runtime escaped admission verification")
'''


@pytest.mark.parametrize("case", ["code", "schema", "dependency"])
def test_command_runtime_drift_refuses_previously_admitted_configuration(tmp_path, case):
    shutil.copytree(ROOT / "synapse", tmp_path / "synapse",
                    ignore=shutil.ignore_patterns("ui", "__pycache__", "*.pyc"))
    result = subprocess.run([sys.executable, "-B", "-c", SCRIPT, case], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"},
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "CHANGED_COMMAND_RUNTIME_REFUSED"
