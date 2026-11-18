"""Heavy: build a real wheel and verify the Python import boundary in isolation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def test_python_distribution_excludes_desktop_namespace_and_keeps_machine_contract(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(ROOT / "synapse", source / "synapse", ignore=shutil.ignore_patterns("ui", "__pycache__"))
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, source / name)
    desktop = source / "synapse/ui/renderer"
    desktop.mkdir(parents=True)
    # Acceptance-only contamination candidate; never written in product source.
    (desktop / "injected.py").write_text("VALUE = 'desktop source must not be a Python import'\n")
    built = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_path / "wheel")],
        cwd=source, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    wheel, = (tmp_path / "wheel").glob("*.whl")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)
    program = """
import importlib.util, json, sys
sys.path.insert(0, sys.argv[1])
from importlib.resources import files
import synapse
print(json.dumps({
    'desktop_importable': importlib.util.find_spec('synapse.ui') is not None,
    'profile': json.loads(files('synapse.machine').joinpath('release_profile.json').read_text())['profile_id'],
    'schema_version': json.loads(files('synapse.machine').joinpath('schemas/envelope.schema.json').read_text())['$defs']['schemaVersion']['const'],
    'loaded_from_wheel': synapse.__file__.startswith(sys.argv[1]),
}))
"""
    probe = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(installed)],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, capture_output=True, text=True, timeout=10)
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout) == {"desktop_importable": False, "profile": "synapse.control.v1",
                                     "schema_version": "1.0", "loaded_from_wheel": True}
