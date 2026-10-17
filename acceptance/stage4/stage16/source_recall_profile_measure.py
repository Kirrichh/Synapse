"""Recall profile v1 against v2 on labelled queries over real repositories (pallets/flask, Synapse).

Each operation is a verified REPOSITORY_FACT_CHECK of one real symbol, learned through the canonical source
ingestion of a connected project (flask under the import root ``src``, binding contract v2; Synapse without
roots, contract v1). Each query names its one relevant operation and has one of three styles: ``named`` names
the relevant symbol, ``described`` names no symbol, ``named-other`` names another studied symbol while the
defect is in the relevant one (the case a symbol-first profile can get wrong).

The ranking is evaluated tie-aware: inside a group of equal keys every order is equally likely, so neither
profile is credited for the alphabet of operation ids (McSherry & Najork, ECIR 2008). Reported: Success@1,
Success@3 and the reciprocal rank, averaged per style, with a two-sided paired randomization test on the
reciprocal ranks (Smucker, Allan & Carterette, CIKM 2007). Fact checks run no command, so the ``output`` tier
of v2 is zero here; the acceptance file ``test_source_recall_profile_acceptance.py`` covers it.

A learned state directory is reused when it exists. Usage::

    python -B -m acceptance.stage4.stage16.source_recall_profile_measure \
        --flask-root <pallets/flask checkout> --synapse-root <Synapse checkout> \
        --work <scratch directory> --output <report.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sys
from pathlib import Path

from acceptance.stage4.stage16._source_inputs import write_learn_input
from synapse.change.workspace import resolve_revision
from synapse.experiments.gold import source_experience
from synapse.experiments.gold.bindings import BINDING_CONTRACT_VERSION_V1, BINDING_CONTRACT_VERSION_V2
from synapse.experiments.gold.contracts import ActorIdentity, AuthorityIdentity
from synapse.experiments.gold.knowledge_environment import (
    GoldProjectDeclaration, GoldProjectEntitlements, GoldProjectIdentities, connect_gold_project, open_gold_project,
)
from synapse.experiments.gold.source_experience import (
    SOURCE_RECALL_PROFILE_V2, SOURCE_RECALL_QUERY_V1, SOURCE_RECALL_QUERY_V2, recall_source_experience,
    source_publications,
)
from synapse.experiments.gold.source_ingestion import execute_source_ingestion
from synapse.experiments.gold.source_operation_journal import read_source_operations
from synapse.experiments.gold.source_verification import SOURCE_CLAIM_V1

#: (python import roots, read scope) of each measured repository.
LAYOUTS = {"flask": (("src",), ("src",)), "synapse": ((), ("synapse",))}

# (repository, operation id, path, module, qualname, kind)
OPERATIONS = [
    ("flask", "helpers-url-for", "src/flask/helpers.py", "flask.helpers", "url_for", "FUNCTION"),
    ("flask", "helpers-send-file", "src/flask/helpers.py", "flask.helpers", "send_file", "FUNCTION"),
    ("flask", "helpers-get-flashed", "src/flask/helpers.py", "flask.helpers", "get_flashed_messages", "FUNCTION"),
    ("flask", "helpers-flash", "src/flask/helpers.py", "flask.helpers", "flash", "FUNCTION"),
    ("flask", "ctx-after-request", "src/flask/ctx.py", "flask.ctx", "after_this_request", "FUNCTION"),
    ("flask", "ctx-copy-context", "src/flask/ctx.py", "flask.ctx", "copy_current_request_context", "FUNCTION"),
    ("flask", "ctx-has-request", "src/flask/ctx.py", "flask.ctx", "has_request_context", "FUNCTION"),
    ("flask", "cli-find-best-app", "src/flask/cli.py", "flask.cli", "find_best_app", "FUNCTION"),
    ("flask", "cli-prepare-import", "src/flask/cli.py", "flask.cli", "prepare_import", "FUNCTION"),
    ("flask", "cli-load-dotenv", "src/flask/cli.py", "flask.cli", "load_dotenv", "FUNCTION"),
    ("flask", "sessions-signing", "src/flask/sessions.py", "flask.sessions",
     "SecureCookieSessionInterface.get_signing_serializer", "METHOD"),
    ("flask", "sessions-cookie-domain", "src/flask/sessions.py", "flask.sessions",
     "SessionInterface.get_cookie_domain", "METHOD"),
    ("flask", "sessions-should-set", "src/flask/sessions.py", "flask.sessions",
     "SessionInterface.should_set_cookie", "METHOD"),
    ("flask", "json-dumps", "src/flask/json/provider.py", "flask.json.provider", "DefaultJSONProvider.dumps", "METHOD"),
    ("flask", "json-default", "src/flask/json/provider.py", "flask.json.provider", "_default", "FUNCTION"),
    ("flask", "json-response", "src/flask/json/provider.py", "flask.json.provider", "JSONProvider.response", "METHOD"),
    ("flask", "app-make-response", "src/flask/app.py", "flask.app", "Flask.make_response", "METHOD"),
    ("flask", "app-handle-exception", "src/flask/app.py", "flask.app", "Flask.handle_exception", "METHOD"),
    ("flask", "app-url-adapter", "src/flask/app.py", "flask.app", "Flask.create_url_adapter", "METHOD"),
    ("synapse", "replay-record-source", "synapse/golden_replay.py", "synapse.golden_replay", "record_source", "FUNCTION"),
    ("synapse", "replay-mock", "synapse/golden_replay.py", "synapse.golden_replay", "replay_mock_artifact", "FUNCTION"),
    ("synapse", "replay-llm-cache", "synapse/golden_replay.py", "synapse.golden_replay", "extract_llm_cache", "FUNCTION"),
    ("synapse", "experience-recall", "synapse/experiments/gold/source_experience.py",
     "synapse.experiments.gold.source_experience", "recall_source_experience", "FUNCTION"),
    ("synapse", "experience-export", "synapse/experiments/gold/source_experience.py",
     "synapse.experiments.gold.source_experience", "export_source_knowledge", "FUNCTION"),
    ("synapse", "experience-publications", "synapse/experiments/gold/source_experience.py",
     "synapse.experiments.gold.source_experience", "source_publications", "FUNCTION"),
    ("synapse", "pool-assess", "synapse/memory_consolidation/court/pool.py",
     "synapse.memory_consolidation.court.pool", "assess", "FUNCTION"),
    ("synapse", "pool-independence", "synapse/memory_consolidation/court/pool.py",
     "synapse.memory_consolidation.court.pool", "independence_of", "FUNCTION"),
    ("synapse", "pool-merge", "synapse/memory_consolidation/court/pool.py",
     "synapse.memory_consolidation.court.pool", "merge_pool", "FUNCTION"),
    ("synapse", "targets-resolve", "synapse/experiments/gold/task_targets.py",
     "synapse.experiments.gold.task_targets", "resolve_task_targets", "FUNCTION"),
    ("synapse", "targets-read", "synapse/experiments/gold/task_targets.py",
     "synapse.experiments.gold.task_targets", "read_task_targets", "FUNCTION"),
    ("synapse", "provenance-closure", "synapse/memory_consolidation/learning/provenance.py",
     "synapse.memory_consolidation.learning.provenance", "closure", "FUNCTION"),
    ("synapse", "provenance-relation", "synapse/memory_consolidation/learning/provenance.py",
     "synapse.memory_consolidation.learning.provenance", "relation", "FUNCTION"),
    ("synapse", "provenance-witnesses", "synapse/memory_consolidation/learning/provenance.py",
     "synapse.memory_consolidation.learning.provenance", "independent_witnesses", "FUNCTION"),
    ("synapse", "profile-validate", "synapse/durable_profile.py", "synapse.durable_profile",
     "validate_cognitive_program", "FUNCTION"),
    ("synapse", "profile-prepare", "synapse/durable_profile.py", "synapse.durable_profile",
     "prepare_cognitive_interpreter", "FUNCTION"),
    ("synapse", "profile-call", "synapse/durable_profile.py", "synapse.durable_profile", "_call", "FUNCTION"),
]

# (relevant operation id, style, scope, statement)
QUERIES = [
    ("helpers-url-for", "named", "src/flask", "url_for builds an external URL with the wrong scheme behind a proxy"),
    ("helpers-send-file", "described", "src/flask/helpers.py",
     "a downloaded attachment gets the wrong content type and no cache headers"),
    ("helpers-get-flashed", "named", "src/flask/helpers.py",
     "get_flashed_messages returns categories in the wrong order when a filter is given"),
    ("helpers-flash", "named-other", "src/flask/helpers.py",
     "a message stored for the next request loses its category before get_flashed_messages reads it"),
    ("ctx-after-request", "described", "src/flask/ctx.py", "a callback registered during a view never runs on the response"),
    ("ctx-copy-context", "named", "src/flask",
     "copy_current_request_context fails inside a greenlet started from a view"),
    ("ctx-has-request", "described", "src/flask/ctx.py",
     "checking for an active request inside a background thread raises instead of returning false"),
    ("cli-find-best-app", "described", "src/flask/cli.py",
     "the command line cannot discover the application when the module only defines a factory taking arguments"),
    ("cli-prepare-import", "named", "src/flask/cli.py",
     "prepare_import adds the wrong directory to sys.path for a package with an init file"),
    ("cli-load-dotenv", "named", "src/flask", "load_dotenv overrides variables already set in the environment"),
    ("sessions-signing", "described", "src/flask/sessions.py",
     "sessions cannot be signed without a secret key and the serializer should then be missing"),
    ("sessions-cookie-domain", "named", "src/flask",
     "get_cookie_domain ignores a session cookie domain set to localhost"),
    ("sessions-should-set", "described", "src/flask/sessions.py",
     "the session cookie is refreshed on every request even though it was not modified"),
    ("json-dumps", "named", "src/flask/json/provider.py", "dumps sorts keys even when sorting is disabled in the provider"),
    ("json-default", "described", "src/flask/json/provider.py",
     "dataclasses and decimals cannot be serialized to JSON in a response"),
    ("json-response", "named-other", "src/flask/json/provider.py",
     "jsonify returns a response with the wrong mimetype when dumps is customized"),
    ("app-make-response", "named", "src/flask",
     "make_response rejects a tuple of body, status and headers in that order"),
    ("app-handle-exception", "described", "src/flask/app.py",
     "an unhandled error in a view is not logged before the internal server error page is returned"),
    ("app-url-adapter", "named-other", "src/flask/app.py",
     "url_for outside a request builds URLs without the configured server name"),
    ("replay-record-source", "named", "synapse/golden_replay.py",
     "record_source writes the manifest without the run identity, so replay of VM programs drifts"),
    ("replay-mock", "described", "synapse",
     "mock replay draws random values again instead of serving the recorded ones"),
    ("replay-llm-cache", "named-other", "synapse/golden_replay.py",
     "replay_mock_artifact misses the cache because only the last answer of a repeated prompt is kept"),
    ("experience-recall", "described", "synapse/experiments/gold",
     "recall ranks operations over one large file alphabetically and cuts off the relevant one"),
    ("experience-export", "named", "synapse/experiments/gold/source_experience.py",
     "export_source_knowledge includes retired publications in the exported candidates"),
    ("experience-publications", "named-other", "synapse/experiments/gold/source_experience.py",
     "recall_source_experience reads publications whose request schema was retired"),
    ("pool-assess", "named", "synapse/memory_consolidation",
     "assess counts copies of one observation as distinct episodes for a birth"),
    ("pool-independence", "described", "synapse/memory_consolidation/court/pool.py",
     "witnesses of different claims are paired when deciding whether sources are independent"),
    ("pool-merge", "named-other", "synapse/memory_consolidation/court/pool.py",
     "assess never sees this window's episodes because the pool keeps an expired candidate"),
    ("targets-resolve", "named", "synapse/experiments/gold",
     "resolve_task_targets misses a symbol the task names with its module path"),
    ("targets-read", "described", "synapse/experiments/gold/task_targets.py",
     "a target resolution whose elements were edited and rehashed is accepted when reopened"),
    ("provenance-closure", "described", "synapse/memory_consolidation/learning/provenance.py",
     "the ancestry of a source loops forever on a cycle in the declared graph"),
    ("provenance-relation", "named", "synapse/memory_consolidation",
     "relation treats an unknown source as dependent instead of not established"),
    ("provenance-witnesses", "named-other", "synapse/memory_consolidation/learning/provenance.py",
     "the closure of two sources is right but the search for a pairwise independent group picks a dependent pair"),
    ("profile-validate", "named", "synapse/durable_profile.py",
     "validate_cognitive_program accepts a palace with a file backend"),
    ("profile-prepare", "described", "synapse/durable_profile.py",
     "the interpreter of a durable cognitive run is not prepared with the run identity before execution"),
    ("profile-call", "described", "synapse",
     "a call to an unknown function inside dream is accepted by the durable profile"),
]
STYLES = ("named", "described", "named-other")
ROUNDS, SEED = 20000, 20261016


def _connect(name: str, root: Path, state: Path) -> None:
    roots, scopes = LAYOUTS[name]
    identities = GoldProjectIdentities(ActorIdentity("eval-attester"), ActorIdentity("eval-builder"),
        AuthorityIdentity("eval-classifier"), AuthorityIdentity("eval-reviewer"), AuthorityIdentity("eval-supersession"),
        AuthorityIdentity("eval-revocation"), ActorIdentity("eval-lifecycle"), AuthorityIdentity("eval-operator"))
    connect_gold_project(GoldProjectDeclaration(root, state, "recall-eval/v1", "recall-eval-env", identities,
        GoldProjectEntitlements(scopes, ("read",), ("source-verifier",)), python_roots=roots))


def _learn(name: str, state: Path, inputs: Path, revision: str) -> None:
    contract = BINDING_CONTRACT_VERSION_V2 if LAYOUTS[name][0] else BINDING_CONTRACT_VERSION_V1
    inputs.mkdir(parents=True, exist_ok=True)
    for repository, operation_id, path, module, qualname, kind in OPERATIONS:
        if repository != name:
            continue
        claim = {"schema_version": SOURCE_CLAIM_V1, "operation_id": operation_id, "revision": revision,
                 "kind": "REPOSITORY_FACT_CHECK", "sources": [path], "replay_gas_budget": 10_000,
                 "symbols": [{"path": path, "module": module, "qualname": qualname, "symbol_kind": kind,
                              "contract_version": contract}], "recipe": None}
        code, result = execute_source_ingestion(state_root=state, input_path=write_learn_input(inputs, claim))
        if code != 0 or result["status"] != "PUBLISHED":
            raise SystemExit(f"{operation_id} was not learned: {result}")


class _Project:
    """One learned project's committed operations, read once for every query."""

    def __init__(self, state: Path, revision: str) -> None:
        self.state, self.revision = state, revision
        self.grant = open_gold_project(state).declaration.entitlements.to_dict()
        self.operations = read_source_operations(state)
        self.publications = {request["domain"]["operation_id"]: result
                             for result, request, _ in source_publications(state)}

    def ranked(self, scope: str, statement: str, profile: str | None) -> list[tuple[tuple, str]]:
        query = {"schema_version": SOURCE_RECALL_QUERY_V1, "statement": statement, "revision": self.revision,
                 "scope": [scope], "limit": 64}
        if profile is not None:
            query.update(schema_version=SOURCE_RECALL_QUERY_V2, profile=profile)
        value = recall_source_experience(state_root=self.state, query=query, entitlements=self.grant,
                                         operations=self.operations, publications=self.publications)
        rows = []
        for item in value["selected"]:
            match = item["match"]
            terms = match["terms"]
            key = ((len(match["paths"]), len(terms)) if profile is None
                   else (len(match["paths"]), *(len(terms[name]) for name in ("identity", "output", "source"))))
            rows.append((key, item["experience"]["operation_id"]))
        return rows


def tie_aware(rows: list[tuple[tuple, str]], relevant: str, limits=(1, 3)) -> dict:
    """The relevant operation's expected metrics when its tie group is ordered uniformly at random."""
    keys = [key for key, _ in rows]
    target = next(key for key, operation in rows if operation == relevant)
    before = sum(1 for key in keys if key > target)
    group = keys.count(target)
    reciprocal = sum(1 / (before + index) for index in range(1, group + 1)) / group
    success = {f"success@{k}": max(0.0, min(1.0, (k - before) / group)) for k in limits}
    return {"rank_from": before + 1, "rank_to": before + group, "tie_group": group, "eligible": len(rows),
            "reciprocal_rank": reciprocal, **success}


def paired_randomization(differences: list[float]) -> float:
    """Two-sided p of the summed paired difference under random sign flips (add-one estimate)."""
    observed = abs(sum(differences))
    generator = random.Random(SEED)
    extreme = sum(1 for _ in range(ROUNDS)
                  if abs(sum(value if generator.random() < 0.5 else -value for value in differences)) >= observed - 1e-12)
    return (extreme + 1) / (ROUNDS + 1)


def summary(rows: list[dict]) -> dict:
    result = {}
    for subset in ("all", *STYLES):
        chosen = [row for row in rows if subset == "all" or row["style"] == subset]
        differences = [row["v2"]["reciprocal_rank"] - row["v1"]["reciprocal_rank"] for row in chosen]
        result[subset] = {
            "queries": len(chosen),
            **{f"{profile}_{metric}": round(sum(row[profile][metric] for row in chosen) / len(chosen), 4)
               for profile in ("v1", "v2") for metric in ("success@1", "success@3", "reciprocal_rank")},
            "v2_wins": sum(1 for value in differences if value > 1e-12),
            "ties": sum(1 for value in differences if abs(value) <= 1e-12),
            "v2_losses": sum(1 for value in differences if value < -1e-12),
            "paired_randomization_p": paired_randomization(differences),
            "success@1_losses": sum(1 for row in chosen if row["v2"]["success@1"] < row["v1"]["success@1"] - 1e-12),
            "success@3_losses": sum(1 for row in chosen if row["v2"]["success@3"] < row["v1"]["success@3"] - 1e-12),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--flask-root", type=Path, required=True)
    parser.add_argument("--synapse-root", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="learned project states (reused when present)")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    roots = {"flask": options.flask_root.resolve(), "synapse": options.synapse_root.resolve()}
    projects = {}
    for name, root in roots.items():
        revision = resolve_revision(root, "HEAD")
        state = options.work.resolve() / f"{name}-state"
        if not (state / "project.json").exists():
            _connect(name, root, state)
            _learn(name, state, options.work.resolve() / f"{name}-inputs", revision)
        projects[name] = _Project(state, revision)
    repository_of = {operation_id: repository for repository, operation_id, *_ in OPERATIONS}
    rows = []
    for relevant, style, scope, statement in QUERIES:
        project = projects[repository_of[relevant]]
        rows.append({"relevant": relevant, "repository": repository_of[relevant], "style": style, "scope": scope,
                     "statement": statement,
                     "v1": tie_aware(project.ranked(scope, statement, None), relevant),
                     "v2": tie_aware(project.ranked(scope, statement, SOURCE_RECALL_PROFILE_V2), relevant)})
    profile_source = Path(source_experience.__file__).resolve()
    report = {"schema_version": "synapse.stage4.gold.source-recall-profile-evaluation/v1",
              "profiles": {"v1": source_experience.SOURCE_RECALL_PROFILE_V1, "v2": SOURCE_RECALL_PROFILE_V2},
              "repositories": {name: {"revision": project.revision, "python_roots": list(LAYOUTS[name][0]),
                                      "operations": sum(1 for item in OPERATIONS if item[0] == name)}
                               for name, project in projects.items()},
              "source_experience_sha256": hashlib.sha256(profile_source.read_bytes()).hexdigest(),
              "python": sys.version.split()[0], "platform": platform.platform(),
              "method": {"ties": "tie-aware: every order inside a group of equal keys is equally likely",
                         "test": f"two-sided paired randomization on reciprocal ranks, {ROUNDS} sign flips, "
                                 f"seed {SEED}, add-one estimate",
                         "labels": "one relevant operation per query"},
              "limitations": [
                  f"{len(QUERIES)} queries over {len(OPERATIONS)} operations in two repositories: a small set",
                  "the queries and their labels were written by the profile's author after the profile; the "
                  "named-other queries were written to probe its known weakness",
                  "every operation is a fact check without a command, so the output tier is zero throughout",
                  "the eligible sets are small (2 to 19 operations), so Success@3 is near its ceiling for both "
                  "profiles"],
              "summary": summary(rows), "queries": rows}
    options.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
