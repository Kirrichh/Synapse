"""Court of the element-owner journal: one writer, idempotent explainable decisions.

The court judges completed owner outcomes that no earlier decision judged. It
first establishes each outcome's basis from the physical run: the retained
learning and typed episode outcome must equal a fresh derivation. An outcome
whose basis cannot be established stays pending; it is neither lost nor
credited. Judgement itself is pure. An established outcome credits every
verified publication origin once to its exact patch subject, an uncertain or
unverifiable one is deferred, and a declared, versioned policy derives the
automation state of each subject. Application is one immutable JUDGED event
under the owner session. Its journal key is derived from the previous decision,
so concurrent task streams cannot fork the court state and the same inputs
return the same decision. While any outcome is pending the court is in
emergency mode: it records credits and restrictions but grants no new
automatic authority. Readers re-derive every decision from its recorded inputs.
"""
from copy import deepcopy
import hashlib

from .persistence import PersistenceViolation
from .project_episode_outcome import (EPISODE_OUTCOME_V1, ESTABLISHED_OUTCOMES, FULFILLED, UNCERTAIN,
                                      UNVERIFIABLE, observe_completed_run, reopen_completed_run)
from .project_learning import EPISODE_LEARNING_V1, consolidate_episode
from .source_verification import canonical

COURT_DECISION_V1 = "synapse.stage4.gold.court-decision/v1"
# Declared before any run and retained in every decision. Another threshold or
# rule is a new policy version; it never reinterprets an earlier decision.
COURT_POLICY_V1 = {"schema_version": "synapse.stage4.gold.court-policy/v1", "subject": "EXACT_PATCH",
                   "admission_minimum_support": 1, "conflict": "SLOW_ONLY", "uncertain_outcome": "DEFERRED",
                   "pending_outcome": "NO_NEW_AUTOMATIC_AUTHORITY"}
SUBJECT_STATES = ("OBSERVED", "ADMITTED", "SLOW_ONLY", "REFUTED")
PENDING_REASONS = ("BASIS_UNAVAILABLE", "BASIS_INVALID", "RETAINED_DIFFERS")
EMPTY_COURT_STATE = {"judged": [], "subjects": {}}
_DECISION_FIELDS = {"schema_version", "project_identity", "predecessor", "policy", "mode", "inputs",
                    "verdicts", "credits", "excluded", "transitions", "state_sha256"}
_INPUT_FIELDS = {"outcome", "learning", "observation", "pending"}


def _identifier(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _decision_key(project_identity, predecessor):
    return _identifier(["synapse.stage4.gold.court", project_identity, predecessor])


def judge(previous, entries):
    """Judge exactly these inputs from a previous court state, without effects.

    Each entry holds its recorded ``input`` and, unless it is pending, the
    retained ``learning`` and ``observation`` payloads. Returns the decision
    body and the resulting state.
    """
    judged = {canonical(item) for item in previous["judged"]}
    subjects = deepcopy(previous["subjects"])
    mode = "EMERGENCY" if any(item["input"]["pending"] is not None for item in entries) else "FULL"
    inputs, verdicts, credits, excluded, order = [], [], [], [], []
    for item in sorted(entries, key=lambda value: canonical(value["input"]["outcome"])):
        entry = item["input"]
        outcome = entry["outcome"]
        if (type(entry) is not dict or set(entry) != _INPUT_FIELDS or canonical(outcome) in judged
                or any(canonical(outcome) == canonical(value["outcome"]) for value in inputs)):
            raise ValueError("an owner outcome is judged exactly once")
        inputs.append(entry)
        if entry["pending"] is not None:
            if entry["pending"] not in PENDING_REASONS or entry["learning"] is not None or entry["observation"] is not None:
                raise ValueError("a pending outcome has only its declared reason")
            verdicts.append({"outcome": outcome, "run_id": None, "verdict": "PENDING",
                             "requirement_outcome": None, "reasons": [entry["pending"]]})
            continue
        observation, learning = item["observation"], item["learning"]
        if (observation["schema_version"] != EPISODE_OUTCOME_V1 or learning["schema_version"] != EPISODE_LEARNING_V1
                or observation["outcome_ref"] != outcome or learning["outcome_ref"] != outcome):
            raise ValueError("court input differs from its owner outcome")
        requirement = observation["requirement"]["outcome"]
        judged.add(canonical(outcome))
        order.append(outcome)
        counted = requirement in ESTABLISHED_OUTCOMES
        verdicts.append({"outcome": outcome, "run_id": observation["run_id"],
                         "verdict": "COUNTED" if counted else "DEFERRED", "requirement_outcome": requirement,
                         "reasons": [requirement] if counted else _deferred_reasons(observation)})
        if not counted:
            continue  # An unknown effect or invalid proof credits neither support nor refutation.
        for assertion in learning["assertions"]:
            credit = _credit(assertion, observation, outcome)
            subject = subjects.setdefault(credit["subject_id"], {"subject": credit["subject"],
                                                                 "support": {}, "refutations": {}, "state": None})
            origin = _identifier(credit["origin"])
            side, other = ("support", "refutations") if credit["status"] == "CONFIRMED" else ("refutations", "support")
            if origin in subject[other]:
                raise ValueError("one origin cannot both confirm and refute a subject")
            if origin in subject[side]:
                # A copied publication or repeated run is the same witness.
                excluded.append({"subject_id": credit["subject_id"], "origin": credit["origin"],
                                 "outcome": outcome, "reason": "REPEATED_ORIGIN"})
                continue
            subject[side][origin] = credit["origin"]
            credits.append(credit)
    transitions = []
    for subject_id in sorted(subjects):
        subject = subjects[subject_id]
        state = _subject_state(subject, mode)
        if state != subject["state"]:
            transition = {"subject_id": subject_id, "patch_sha256": subject["subject"]["patch_sha256"],
                          "from": subject["state"], "to": state, "support": len(subject["support"]),
                          "refutations": len(subject["refutations"])}
            if state == "OBSERVED" and _admissible(subject):
                transition["withheld"] = "PENDING_OUTCOMES"
            transitions.append(transition)
            subject["state"] = state
    state = {"judged": previous["judged"] + order, "subjects": subjects}
    return {"mode": mode, "inputs": inputs, "verdicts": verdicts, "credits": credits, "excluded": excluded,
            "transitions": transitions, "state_sha256": _identifier(state)}, state


def _admissible(subject):
    return len(subject["support"]) >= COURT_POLICY_V1["admission_minimum_support"] and not subject["refutations"]


def _subject_state(subject, mode):
    """Policy v1: contradiction and refutation restrict; only a full court admits."""
    if subject["support"] and subject["refutations"]:
        return "SLOW_ONLY"  # Lifted only by a later policy's verified verdict, never by more support.
    if subject["refutations"]:
        return "REFUTED"
    if not _admissible(subject):
        return "OBSERVED"
    return "ADMITTED" if subject["state"] == "ADMITTED" or mode == "FULL" else "OBSERVED"


def _deferred_reasons(observation):
    reasons = {reason for item in observation["attempts"] if item["outcome"] in {UNCERTAIN, UNVERIFIABLE}
               for reason in item["reasons"]}
    reasons |= {reason for item in observation["uncertainties"] if item["attempt_id"] is None
                for reason in item["reasons"]}
    return sorted(reasons) or [observation["requirement"]["run_status"]]


def _credit(assertion, observation, outcome):
    claim, requirement = assertion["claim"], observation["requirement"]
    if (assertion["status"] not in {"CONFIRMED", "REFUTED"}
            or claim["task_contract_ref"] != requirement["task_contract_ref"]
            or claim["repository_revision"] != requirement["repository_revision"]):
        raise ValueError("a learned assertion belongs to another requirement")
    if assertion["status"] == "CONFIRMED" and requirement["outcome"] != FULFILLED:
        raise ValueError("a confirmation needs a fulfilled requirement")
    subject = {"task_contract_ref": claim["task_contract_ref"], "repository_revision": claim["repository_revision"],
               "patch_sha256": claim["patch_sha256"]}
    return {"subject_id": _identifier(subject), "subject": subject, "assertion_id": assertion["assertion_id"],
            "status": assertion["status"], "origin": assertion["origin"], "outcome": outcome}


def _establish(store, guard, retained, receipt, project_identity):
    """Retain the derived learning and episode outcome, or keep the outcome pending."""
    event = store.read(receipt)
    pending = {"outcome": receipt, "learning": None, "observation": None}
    try:
        frozen, state, records = reopen_completed_run(event["payload"], project_identity)
        derived = {"CONSOLIDATED": consolidate_episode(frozen=frozen, state=state, records=records, outcome_ref=receipt),
                   "OBSERVED": observe_completed_run(frozen=frozen, state=state, outcome_ref=receipt)}
        judge(EMPTY_COURT_STATE, [{"input": {**pending, "pending": None},
                                   "learning": derived["CONSOLIDATED"], "observation": derived["OBSERVED"]}])
    except (PersistenceViolation, OSError):
        return {"input": {**pending, "pending": "BASIS_UNAVAILABLE"}}
    except (ValueError, KeyError, TypeError):
        return {"input": {**pending, "pending": "BASIS_INVALID"}}
    if any((saved := retained.get((event["job_key"], kind))) is not None and saved[0]["payload"] != payload
           for kind, payload in derived.items()):
        return {"input": {**pending, "pending": "RETAINED_DIFFERS"}}
    receipts = {kind: store.put(kind=kind, job_key=event["job_key"], payload=payload, guard=guard)
                for kind, payload in derived.items()}
    return {"input": {"outcome": receipt, "learning": receipts["CONSOLIDATED"],
                      "observation": receipts["OBSERVED"], "pending": None},
            "learning": derived["CONSOLIDATED"], "observation": derived["OBSERVED"]}


def _load(store, entry):
    """Reopen one recorded input from the journal for re-derivation."""
    if type(entry) is not dict or set(entry) != _INPUT_FIELDS:
        raise ValueError("court input has an unknown contract")
    event = store.read(entry["outcome"])
    if event["kind"] != "OUTCOME_RECORDED":
        raise ValueError("court input is not a completed owner outcome")
    loaded = {"input": entry, "result_ref": event["payload"]["result_ref"]}
    if entry["pending"] is not None:
        return loaded
    learning, observation = store.read(entry["learning"]), store.read(entry["observation"])
    if ((learning["kind"], observation["kind"]) != ("CONSOLIDATED", "OBSERVED")
            or {learning["job_key"], observation["job_key"]} != {event["job_key"]}):
        raise ValueError("court input differs from its owner outcome")
    return {**loaded, "learning": learning["payload"], "observation": observation["payload"]}


def _replay(store, decisions, project_identity):
    """Re-derive each decision in order; return the judged history and state."""
    state, history, predecessor = EMPTY_COURT_STATE, [], None
    for payload, receipt in decisions:
        if (type(payload) is not dict or set(payload) != _DECISION_FIELDS
                or payload["schema_version"] != COURT_DECISION_V1 or payload["policy"] != COURT_POLICY_V1
                or payload["project_identity"] != project_identity or payload["predecessor"] != predecessor):
            raise ValueError("court decision has an unknown contract, policy or predecessor")
        entries = [_load(store, item) for item in payload["inputs"]]
        body, state = judge(state, entries)
        if any(payload[key] != value for key, value in body.items()):
            raise ValueError("court decision differs from its recorded inputs")
        for item, verdict in zip(entries, body["verdicts"]):
            if verdict["verdict"] != "PENDING":
                history.append({"outcome": verdict["outcome"], "verdict": verdict["verdict"],
                                "reasons": verdict["reasons"], "decision": receipt,
                                "result_ref": item["result_ref"], "learning": item["learning"],
                                "observation": item["observation"]})
        predecessor = receipt
    return history, state


def _job_identity(retained, job_key):
    request = retained.get((job_key, "REQUESTED"))
    if request is None:
        raise ValueError("owner outcome has no maintenance request")
    return request[0]["payload"]["project_identity"]


def consolidate_court(store, guard, *, project_identity):
    """Judge every unjudged completed outcome of this project once, under the owner session.

    Returns a summary of the decision in force. Completed outcomes of another
    project identity belong to that identity's court.
    """
    retained = {(event["job_key"], event["kind"]): (event, receipt) for event, receipt in store.inventory(guard=guard)}
    decisions, predecessor = [], None
    while (found := retained.get((_decision_key(project_identity, predecessor), "JUDGED"))) is not None:
        decisions.append((found[0]["payload"], found[1]))
        predecessor = found[1]
    if len(decisions) != sum(1 for (_, kind), (event, _) in retained.items()
                             if kind == "JUDGED" and event["payload"].get("project_identity") == project_identity):
        raise ValueError("court decisions do not form one chain")
    _, state = _replay(store, decisions, project_identity)
    judged = {canonical(item) for item in state["judged"]}
    tail = sorted((receipt for (job_key, kind), (_, receipt) in retained.items()
                   if kind == "OUTCOME_RECORDED" and canonical(receipt) not in judged
                   and _job_identity(retained, job_key) == project_identity), key=canonical)
    head = None if not decisions else decisions[-1]
    if tail:
        entries = [_establish(store, guard, retained, receipt, project_identity) for receipt in tail]
        unchanged = (head is not None and head[0]["mode"] == "EMERGENCY"
                     and [item["input"] for item in entries] == [item for item in head[0]["inputs"]
                                                                  if item["pending"] is not None])
        if not unchanged:  # The same unresolved basis is not a new decision.
            body, _ = judge(state, entries)
            payload = {"schema_version": COURT_DECISION_V1, "project_identity": project_identity,
                       "predecessor": predecessor, "policy": COURT_POLICY_V1, **body}
            receipt = store.put(kind="JUDGED", job_key=_decision_key(project_identity, predecessor),
                                payload=payload, guard=guard)
            head = payload, receipt
    return _summary(head)


def _summary(head):
    if head is None:
        return {"decision": None, "mode": None, "pending": 0}
    payload, receipt = head
    return {"decision": receipt, "mode": payload["mode"],
            "pending": sum(1 for item in payload["verdicts"] if item["verdict"] == "PENDING")}


def read_court(store, *, project_identity, decision):
    """Validated court history and state up to one pinned decision."""
    decisions, receipt = [], decision
    while receipt is not None:
        event = store.read(receipt)
        payload = event["payload"]
        if (event["kind"] != "JUDGED" or type(payload) is not dict
                or event["job_key"] != _decision_key(project_identity, payload.get("predecessor"))):
            raise ValueError("court decision is outside its project chain")
        decisions.append((payload, receipt))
        receipt = payload["predecessor"]
    decisions.reverse()
    history, state = _replay(store, decisions, project_identity)
    last = decisions[-1][0] if decisions else None
    return {"decision": decision, "mode": None if last is None else last["mode"], "judged": history,
            "pending": [] if last is None else [{"outcome": item["outcome"], "reasons": item["reasons"]}
                                                for item in last["verdicts"] if item["verdict"] == "PENDING"],
            "subjects": state["subjects"]}


def outcome_judgement(court, outcome):
    """Stable judgement of one recorded outcome in a validated court history."""
    for item in court["judged"]:
        if item["outcome"] == outcome:
            return {"verdict": item["verdict"], "reasons": item["reasons"], "decision": item["decision"]}
    for item in court["pending"]:
        if item["outcome"] == outcome:
            return {"verdict": "PENDING", "reasons": item["reasons"], "decision": None}
    raise ValueError("the court has not seen this owner outcome")


def task_subjects(court, task):
    """Court state of the exact patch subjects bound to one task and revision."""
    reference = task.reference.to_dict()
    return sorted(({"patch_sha256": value["subject"]["patch_sha256"], "state": value["state"],
                    "support": len(value["support"]), "refutations": len(value["refutations"])}
                   for value in court["subjects"].values()
                   if value["subject"]["task_contract_ref"] == reference
                   and value["subject"]["repository_revision"] == task.repository_revision_sha256),
                  key=lambda item: item["patch_sha256"])
