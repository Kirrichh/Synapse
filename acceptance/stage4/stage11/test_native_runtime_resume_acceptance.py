"""A real wheel binary replacement invalidates an already frozen CLI run."""

from pathlib import Path
import subprocess
import sys

from tests.test_stage4_gold_native import copied_native, native_environment


def test_frozen_cli_run_refuses_changed_inner_native_binary(tmp_path):
    native_root, binary, wrapper = copied_native(tmp_path)
    result = subprocess.run([sys.executable, "-B", "-c", """
from pathlib import Path
import sys
from acceptance.stage4.stage11._project_inputs import project_input_case
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs

case = project_input_case(Path(sys.argv[1]))
code, pending = case.start()
assert code == 3, pending
reopen_frozen_inputs(case.run_root)
binary = Path(sys.argv[2])
replacement = binary.with_name("replacement.bin")
replacement.write_bytes(binary.read_bytes() + b"\\0native identity acceptance\\0")
replacement.replace(binary)
try:
    reopen_frozen_inputs(case.run_root)
except ValueError as exc:
    assert "runtime sources differ" in str(exc), exc
else:
    raise AssertionError("frozen runtime accepted a changed native binary")
code, refused = case.cli("project", "resume", "--run-dir", case.run_root)
assert code != 0, refused
assert case.worker.calls == 0
print("native-drift-refused")
""", str(tmp_path / "case"), str(binary)],
        cwd=Path(__file__).resolve().parents[3], env=native_environment(native_root),
        capture_output=True, text=True, timeout=240)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "native-drift-refused"
