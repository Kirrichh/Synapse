import re
import pathlib
import pytest
from synapse.version import LANGUAGE_VERSION, RUNTIME_VERSION, SPEC_VERSION


def test_readme_version():
    readme_path = pathlib.Path("README.md")
    if not readme_path.exists():
        pytest.skip("README.md not found")
    readme = readme_path.read_text(encoding="utf-8")
    match = re.search(r"Текущая версия:\s*\**v?([\d.]+(?:-[A-Za-z0-9]+)*)\**", readme)
    assert match, "README.md не содержит строки 'Текущая версия: vX.Y.Z'"
    assert match.group(1) == LANGUAGE_VERSION, f"README version mismatch: {match.group(1)} != {LANGUAGE_VERSION}"


def test_init_version():
    import synapse
    assert synapse.__version__ == RUNTIME_VERSION, f"__init__ version mismatch: {synapse.__version__} != {RUNTIME_VERSION}"


def test_spec_version():
    spec_path = pathlib.Path("docs/SPEC.md")
    if not spec_path.exists():
        pytest.skip("docs/SPEC.md not found")
    spec = spec_path.read_text(encoding="utf-8")
    match = re.search(r"Spec Version:\s*v?([\d.]+(?:-[A-Za-z0-9]+)*)", spec)
    assert match, "docs/SPEC.md не содержит заголовка 'Spec Version: vX.Y.Z'"
    assert match.group(1) == SPEC_VERSION, f"SPEC version mismatch: {match.group(1)} != {SPEC_VERSION}"


def test_version_authority_is_v22_alpha3e():
    assert LANGUAGE_VERSION == "2.2.0-alpha3e"
    assert RUNTIME_VERSION == "0.22.0-alpha3e"
    assert SPEC_VERSION == "2.2.0-alpha3e"
    from synapse.version import __version__
    assert __version__ == "0.22.0-alpha3e"
