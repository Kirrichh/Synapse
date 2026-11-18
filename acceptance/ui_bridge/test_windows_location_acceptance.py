"""Machine access location survives a minimal child and rejects caller routing."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from synapse.access.records import AccessRefused

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installation location")


def test_native_minimal_and_forged_environments_resolve_the_same_machine_location():
    from synapse.access.windows_location import installation_root

    expected = str(installation_root())
    for environment in ({"SystemRoot": os.environ["SystemRoot"]},
            {"SystemRoot": os.environ["SystemRoot"], "SystemDrive": "Z:",
             "ProgramData": r"Z:\synthetic-untrusted", "ALLUSERSPROFILE": r"Z:\synthetic-untrusted"}):
        child = subprocess.run([sys.executable, "-B", "-c",
            "from synapse.access.windows_location import installation_root; print(installation_root())"],
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert child.returncode == 0, "location failed in the native child environment"
        assert child.stdout.strip() == expected, "caller environment redirected machine authority"


def test_configured_absolute_location_is_preserved_and_system_drive_is_expanded(monkeypatch):
    import winreg
    from synapse.access.windows_location import installation_root

    monkeypatch.setattr(winreg, "QueryValueEx", lambda *_: (r"D:\synthetic-shared-data", winreg.REG_SZ))
    assert installation_root() == Path(r"D:\synthetic-shared-data\SynapseAccessV1")
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *_: (r"%sYsTeMdRiVe%\synthetic-data", winreg.REG_EXPAND_SZ))
    expanded = installation_root()
    assert expanded.is_absolute() and "%" not in str(expanded)
    assert expanded.parts[-2:] == ("synthetic-data", "SynapseAccessV1")


@pytest.mark.parametrize("configured", [r"%UNRECOGNIZED%\data", r"relative\data",
    r"\\synthetic-server\share", r"C:\data\..\other", "C:\\data\0other"])
def test_unknown_or_nonlocal_configurations_never_fall_back(monkeypatch, configured):
    import winreg
    from synapse.access.windows_location import installation_root

    monkeypatch.setattr(winreg, "QueryValueEx", lambda *_: (configured, winreg.REG_EXPAND_SZ))
    with pytest.raises(AccessRefused, match="^ACCESS_UNAVAILABLE$"):
        installation_root()


def test_unavailable_machine_configuration_has_a_named_refusal(monkeypatch):
    import winreg
    from synapse.access.windows_location import installation_root

    def unavailable(*_):
        raise PermissionError()
    monkeypatch.setattr(winreg, "QueryValueEx", unavailable)
    with pytest.raises(AccessRefused, match="^ACCESS_UNAVAILABLE$"):
        installation_root()
