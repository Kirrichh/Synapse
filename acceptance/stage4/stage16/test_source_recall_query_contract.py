"""A recall query names exactly one known ranking profile, or the canonical CLI refuses it.

Query v1 keeps profile v1 and names no profile; query v2 names a profile the product knows, as text. Any
other query is refused before any operation is read. The project's state directory may be named relative to
the working directory, as for ``project learn``.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from acceptance.stage4.stage16._source_inputs import learn, prepare
from synapse.experiments.gold.source_experience import (
    SOURCE_RECALL_PROFILE_V2, SOURCE_RECALL_QUERY_V1, SOURCE_RECALL_QUERY_V2,
)


@pytest.mark.parametrize("damage", ["unknown-profile", "non-text-profile", "v2-without-profile", "v1-with-profile"])
def test_a_query_names_exactly_one_known_profile(tmp_path, damage):
    _, state, path = prepare(tmp_path)
    query = {"schema_version": SOURCE_RECALL_QUERY_V2, "statement": "calculate double value",
             "revision": json.loads(path.read_text())["claim"]["revision"], "scope": ["calc.py"], "limit": 3,
             "profile": SOURCE_RECALL_PROFILE_V2}
    if damage == "unknown-profile":
        query["profile"] = "exact-revision-path-and-term-overlap/v3"
    elif damage == "non-text-profile":
        query["profile"] = [SOURCE_RECALL_PROFILE_V2]
    elif damage == "v2-without-profile":
        del query["profile"]
    else:
        query["schema_version"] = SOURCE_RECALL_QUERY_V1
    (tmp_path / "query.json").write_text(json.dumps(query))

    result = subprocess.run([sys.executable, "-B", "-m", "synapse", "project", "recall", "--state-dir", str(state),
                             "--input", str(tmp_path / "query.json")], cwd=Path(__file__).resolve().parents[3],
                            text=True, capture_output=True, timeout=180)

    assert result.returncode == 2
    assert json.loads(result.stdout.splitlines()[-1]) == {"status": "REFUSED",
                                                          "reason": "source recall requires an exact task query"}


def test_a_state_directory_named_relative_to_the_working_directory_is_the_same_project(tmp_path):
    _, state, path = prepare(tmp_path)
    code, _ = learn(state, path)
    assert code == 0
    query = {"schema_version": SOURCE_RECALL_QUERY_V1, "statement": "calculate double value",
             "revision": json.loads(path.read_text())["claim"]["revision"], "scope": ["calc.py"], "limit": 3}
    (tmp_path / "query.json").write_text(json.dumps(query))
    repository = Path(__file__).resolve().parents[3]
    answers = []
    for cwd, state_dir, query_path in ((repository, str(state), str(tmp_path / "query.json")),
                                       (tmp_path, state.name, "query.json")):
        result = subprocess.run([sys.executable, "-B", "-m", "synapse", "project", "recall", "--state-dir", state_dir,
                                 "--input", query_path], cwd=cwd, env={**os.environ, "PYTHONPATH": str(repository)},
                                text=True, capture_output=True, timeout=180)
        assert result.returncode == 0, result.stdout
        answers.append(json.loads(result.stdout.splitlines()[-1]))
    assert answers[0] == answers[1] and [item["experience"]["operation_id"] for item in answers[0]["selected"]] == [
        "learn-calculation"]
