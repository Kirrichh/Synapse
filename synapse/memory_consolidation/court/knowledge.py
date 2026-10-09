"""The court's record of semantic knowledge (refinement §15): the one writer of its timeline.

A session only declares what it read (``knowledge_declared``); the court folds
every declaration of a window into memory, in the order the world was read (the
gateway's sequence of each statement's source observation), and gives each
version its transaction time — the window it became known in:

* the very same statement (same source version) is a copy and adds nothing;
  the same source read again stating the same thing is a copy too: repetition
  never raises confidence;
* the same source stating something else about the same slot and start is a
  **correction**: the earlier version's transaction time closes
  (``known_until``, ``corrected_by``) and it stays in history, answerable as
  of any earlier window;
* another source stating something else about the same slot and start is a
  **conflict**: both versions stay current and admission lets neither win by
  similarity, confidence or insertion time;
* any other statement is a new version; its valid time places it on the
  timeline (a late report about the past never overrides a later start);
* a version memory held before — closed by a correction, or withdrawn with
  forgotten results — observed again is held again from this window: it
  corrects what holds its slot now (a correction back to an earlier answer,
  20 → 25 → 20), and the periods it was held before stay in its history
  (``earlier_known``), so every earlier window still answers as it did
  (review AUD-3).

The world's order holds across windows (recheck of 556624d). For each source,
slot and start memory keeps every statement it folded at its place on the
gateway's sequence (``stated``: the place its answer was observed, the
statement, the answer, what it states), copies included: a restatement adds no
confidence, but it is the source saying the same again later. What the source
stated last holds the slot, whatever window a session finished in: a statement
its source made before it stated something else — read by a session that
finished later — is kept with its provenance as history, closed in the very
window it became known by the version of what the source stated later, and is
never current; a forget withdraws what was read, never the fact that the source
said something else later, so a forgotten statement still makes an earlier one
history. A source that really returned to an earlier answer states it again
later and corrects what memory holds. A held version no folded statement places
(decided before the order was kept, and restored by no record) is ordered by
arrival. A report
carries only what its window adds to each order (``additions``), and the
projection joins it by the court's own rule (``placed_in``).

The corrections are read from that order (``corrections_of``): a statement whose
content differs from the one before it corrects every answer of the run of equal
statements it ends, at its own place. A window's corrections are the ones its
statements add to the order — a late statement between two others adds the
corrections the same statements make in one window. A correction revises what
depended on the corrected answer, as a truth maintenance system withdraws
beliefs with their premises (Doyle 1979), without deleting anything: learned
habits whose basis episodes admitted the corrected statement go to probation
(TC); the hypotheses read from that answer, or checked against the corrected
source, are revised by the hypothesis stage in the gateway's order together with
the window's checks (``hypotheses.py``), so a status decided before the
correction returns to ``provisional`` and one checked after it stands, whichever
window either was consolidated in (review M4). A late statement that shows the
source changed earlier than memory knew moves the correction to its own place
and leaves the later statement a restatement: the statuses resting on that
source are decided again from their checks, so one checked between the two
places is its check's again (``restored``) and one checked before both is
provisional at the earlier place. The runs that admitted each version are
recorded as its uses.

A window is consolidated when it declares or admits a statement too
(``changes_knowledge``): a statement read after a consolidation is folded like
any other (review M3).
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from ..knowledge.statements import slot, source_identity
from ..records import KINDS, canonical, digest, verify
from .dependencies import basis_runs
from .habit_state import EFFECTIVE

EVENTS = ("knowledge_declared", "memory_admission")


def records_use(event: Mapping[str, Any]) -> bool:
    """Whether a history event records a use of a statement of memory: an admission that admitted one."""
    return (event.get("type") == "memory_admission" and event.get("decision") == "admitted"
            and str(event.get("fact") or "").startswith(KINDS["statement"][1]))


def changes_knowledge(event: Mapping[str, Any]) -> bool:
    """Whether a history event changes memory's knowledge or the record of its uses."""
    return event.get("type") == "knowledge_declared" or records_use(event)


def knowledge_events(found: Mapping[str, list], run_id: str, observed: Mapping[int, int]) -> dict[str, list]:
    """A session's statement declarations, each with the gateway sequence of the observation it was read from
    (``observed``, by history position; a declaration with none is the session's integrity problem and is not
    folded), and admissions of statements, in history order."""
    declared = [{"run_id": run_id, "position": position, "statement": event["statement"],
                 "vector": event.get("vector"), "embedded_by": event.get("embedded_by"),
                 "observed": observed[position]}
                for position, event in found.get("knowledge_declared", []) if position in observed]
    uses = [{"run_id": run_id, "statement": event["fact"]} for _, event in found.get("memory_admission", [])
            if records_use(event)]
    return {"declared": declared, "uses": uses}


def _content(record) -> bytes:
    return canonical(_stated(record))


def _stated(record) -> dict[str, Any]:
    """What a statement states, whatever answer it was read from."""
    return {"value": record["value"], "polarity": record["polarity"], "valid": record["valid"]}


def _revise_habits(state, habits, runs, statement_id, forced) -> list[str]:
    """Live habits whose basis admitted the corrected version go to probation."""
    found = []
    for habit_id in sorted(habits):
        metadata = habits[habit_id]
        if metadata["state"] not in EFFECTIVE or metadata.get("superseded_by") is not None:
            continue
        if basis_runs(state, habit_id) & runs:
            forced.setdefault(habit_id, ("TC", f"a fact its basis admitted was corrected ({statement_id})"))
            found.append(habit_id)
    return found


def _reindex(versions, section, entry, item) -> None:
    """A copy adds no confidence, but its vector restores the search index (review DEEP-5, DEEP-6): a version
    with no vector, or one of another embedder, takes the vector this reading brings. The statement, its
    periods and its uses stay as they are."""
    if item["vector"] is None or item["embedded_by"] is None or (
            entry.get("vector") is not None and entry.get("embedded_by") == item["embedded_by"]):
        return
    identity = entry["record"]["id"]
    versions[identity] = {**copy.deepcopy(versions.get(identity, entry)), "vector": copy.deepcopy(item["vector"]),
                          "embedded_by": item["embedded_by"]}
    section["reindexed"].append({"run_id": item["run_id"], "statement": identity})


EMPTY = {"versions": {}, "uses": {}, "stated": {}}


def known_of(state: Mapping[str, Any]) -> Mapping[str, Any]:
    """The knowledge a state holds (a state from before semantic knowledge holds none; one folded before the
    world's order of statements was kept holds no order)."""
    known = state.get("knowledge") or EMPTY
    return known if "stated" in known else {**known, "stated": {}}


# -- the world's order of statements ------------------------------------------------------------------------
def _group(record) -> str:
    """Which order a statement belongs to: its source's statements about one slot and start."""
    return digest({"slot": slot(record), "source": source_identity(record), "from": record["valid"]["from"]})


def _statement(record, place: int | None) -> list:
    """A statement in its order: where its answer was observed (``None``: unplaced), the statement, the answer
    it was read from and what it states."""
    return [place, record["id"], record["source"]["ref"], digest(_stated(record))]


def _ordered(statement) -> tuple:
    return statement[0] is not None, statement[0] or 0, statement[1]


def placed_in(group: Mapping[str, Any], statements) -> list[list]:
    """The statements of the order ``group`` with ``statements`` joined: a statement placed takes the place of the
    same statement ordered by arrival, and one statement at one place is held once — a statement declared again
    from the same answer is the same statement at the same place; the same answer observed again is another
    place. The court and the projection of its reports join an order by this one rule."""
    placed = {statement[1] for statement in statements if statement[0] is not None}
    joined = {tuple(statement) for statement in group["statements"]
              if statement[0] is not None or statement[1] not in placed}
    joined |= {tuple(statement) for statement in statements}
    return sorted((list(statement) for statement in joined), key=_ordered)


def additions(held: Mapping[str, Any], stated: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """What a decision's orders (``stated``) add to the orders memory holds (``held``): for each order it changed,
    its source and the statements memory does not hold yet. A report carries only these, so an order read on every
    window is never written whole again; the projection joins them (``placed_in``)."""
    found = {}
    for key, group in sorted(stated.items()):
        kept = {tuple(statement) for statement in (held.get(key) or {"statements": []})["statements"]}
        new = [copy.deepcopy(statement) for statement in group["statements"] if tuple(statement) not in kept]
        if new:
            found[key] = {"source": copy.deepcopy(group["source"]), "statements": new}
    return found


def _order(knowledge, record, versions) -> dict[str, Any]:
    """The order ``record`` belongs to as memory keeps it, with the versions memory holds of its source, slot and
    start that no statement places put first — ordered by arrival."""
    group = copy.deepcopy(knowledge["stated"].get(_group(record))) or {
        "source": {"tool": record["source"]["tool"], "args": copy.deepcopy(record["source"]["args"])},
        "statements": []}
    placed = {statement[1] for statement in group["statements"]}
    key = (slot(record), source_identity(record), record["valid"]["from"])
    for identity, version in sorted(versions.items()):
        if (identity not in placed and version["known_until"] is None and version.get("withdrawn") is None
                and (version["slot"], version["source_identity"], version["record"]["valid"]["from"]) == key):
            group["statements"].append(_statement(version["record"], None))
    group["statements"].sort(key=_ordered)
    return group


def _last_stated(group, record) -> int | None:
    """Where the order last has its source state what ``record`` states (``None``: at no place it knows)."""
    content = digest(_stated(record))
    places = [statement[0] for statement in group["statements"] if statement[3] == content and statement[0] is not None]
    return max(places) if places else None


def _made(group) -> dict[tuple, dict[str, Any]]:
    """The corrections one order makes: a statement whose content differs from the one before it corrects every
    answer of the run of equal statements it ends, at its own place."""
    found: dict[tuple, dict[str, Any]] = {}
    run: list = []
    for place, identity, ref, content in sorted(group["statements"], key=_ordered):
        if run and run[-1][3] != content and place is not None:
            for _, corrected, corrected_ref, _ in run:
                found[(place, corrected_ref)] = {
                    "old": {"record": {"id": corrected, "source": {**group["source"], "ref": corrected_ref}}},
                    "by": identity, "at": place}
            run = []
        run.append((place, identity, ref, content))
    return found


def corrections_of(knowledge: Mapping[str, Any], sources=None) -> list[dict[str, Any]]:
    """Every correction the world's order memory keeps makes, each at its place with its own revision entry (of
    the sources named by their identity in ``sources``, or of all)."""
    found = []
    for key, group in sorted(knowledge["stated"].items()):
        if sources is None or source_identity({"source": group["source"]}) in sources:
            found.extend({**correction, "revision": {"statement": correction["old"]["record"]["id"], "hypotheses": []}}
                         for _, correction in sorted(_made(group).items()))
    return found


def unplaced_corrections(knowledge: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The corrections the timeline records that the world's order cannot decide — a version closed by a statement
    no folded statement places (closed before the order was kept, by a session no record restores): each is
    placed after everything, so it reaches every status that rests on it. A closure by a statement the order
    places is the order's to decide: a statement folded late under an earlier policy closed what its source
    stated after it, which the order shows was no correction."""
    placed = {statement[1] for group in knowledge["stated"].values() for statement in group["statements"]
              if statement[0] is not None}
    found = []
    for statement_id, version in sorted(knowledge["versions"].items()):
        for period in [*version.get("earlier_known", []), {"corrected_by": version.get("corrected_by")}]:
            if period.get("corrected_by") is not None and period["corrected_by"] not in placed:
                found.append({"old": version, "by": period["corrected_by"], "at": None,
                              "revision": {"statement": statement_id, "hypotheses": []}})
    return found


def knowledge_stage(context, habits, forced, configuration) -> dict[str, Any]:
    """Versions, uses and the order of statements of one window, and the corrections its statements add to that
    order; the report's ``knowledge`` section. Each correction carries its place on the gateway's sequence and
    the revision entry of the statement it corrects, whose hypotheses the hypothesis stage names."""
    state, window = {**context.state, "knowledge": known_of(context.state)}, context.window
    section = {"declared": [], "copies": [], "corrections": [], "conflicts": [], "revisions": [], "reindexed": []}
    context.report["knowledge"] = section
    versions: dict[str, dict] = {}
    uses: dict[str, list] = {}
    stated: dict[str, dict] = {}
    before: dict[str, dict] = {}
    knowledge = context.draft.get("knowledge") or {"declared": [], "uses": []}
    for item in knowledge["uses"]:
        uses.setdefault(item["statement"], sorted(set(state["knowledge"]["uses"].get(item["statement"], []))))
        uses[item["statement"]] = sorted(set(uses[item["statement"]]) | {item["run_id"]})
    # In the order the world was read — the gateway's sequence of each statement's source — never by the names
    # of the runs that read it: one consolidation of several sessions folds 20 then 25 as 25 (review DEEP-3).
    for item in sorted(knowledge["declared"], key=lambda value: (value["observed"], value["run_id"],
                                                                 value["position"])):
        record = verify(item["statement"], "statement")
        known = {**state["knowledge"]["versions"], **versions}
        key = _group(record)
        if key not in stated:
            stated[key] = _order(state["knowledge"], record, known)
            before[key] = copy.deepcopy(stated[key])
        _fold(item, record, known, stated[key], versions, section, configuration, window)
        # Placed now: a version ordered by arrival so far takes its place.
        stated[key]["statements"] = placed_in(stated[key], [_statement(record, item["observed"])])
    corrections, revisions = [], {}
    for key in sorted(stated):
        made_before = _made(before[key])
        for found, correction in sorted(_made(stated[key]).items()):
            if found in made_before:
                continue
            statement_id = correction["old"]["record"]["id"]
            if statement_id not in revisions:
                runs = set(state["knowledge"]["uses"].get(statement_id, [])) | set(uses.get(statement_id, []))
                revisions[statement_id] = {"statement": statement_id, "hypotheses": [],
                                           "habits": _revise_habits(state, habits, runs, statement_id, forced)}
                section["revisions"].append(revisions[statement_id])
            corrections.append({**correction, "revision": revisions[statement_id]})
    return {"versions": versions, "uses": uses, "stated": stated, "corrections": corrections}


def _fold(item, record, known, group, versions, section, configuration, window) -> None:
    """One declaration's place in the timeline: a copy, a statement made before what memory holds (history), a
    new version or one held again, correcting the version of the same source it follows."""
    earlier = known.get(record["id"])
    if earlier is not None and earlier.get("withdrawn") is None and earlier["known_until"] is None:
        section["copies"].append({"run_id": item["run_id"], "statement": record["id"], "of": record["id"]})
        _reindex(versions, section, earlier, item)
        return
    own_slot, source = slot(record), source_identity(record)
    current = [entry for entry in known.values() if entry["slot"] == own_slot and entry["known_until"] is None
               and entry.get("withdrawn") is None and entry["record"]["valid"]["from"] == record["valid"]["from"]]
    same = [entry for entry in current if entry["source_identity"] == source]
    repeated = next((entry for entry in same if _content(entry["record"]) == _content(record)), None)
    if repeated is not None:
        section["copies"].append({"run_id": item["run_id"], "statement": record["id"],
                                  "of": repeated["record"]["id"]})
        _reindex(versions, section, repeated, item)
        return
    # What the source stated after this reading, by any version memory knows of this order: a forget withdraws
    # what was read, never the fact that the source said something else later.
    later = [(stated, entry) for entry in known.values() if entry["slot"] == own_slot
             and entry["source_identity"] == source and entry["record"]["valid"]["from"] == record["valid"]["from"]
             and _content(entry["record"]) != _content(record)
             for stated in [_last_stated(group, entry["record"])] if stated is not None and stated > item["observed"]]
    if later and earlier is not None:
        # A version memory held before, read again before its source last stated what memory holds now.
        section["copies"].append({"run_id": item["run_id"], "statement": record["id"], "of": record["id"],
                                  "late": True})
        return
    contract = configuration.tools.tools.get(record["source"]["tool"])
    entry = {"record": copy.deepcopy(record), "vector": copy.deepcopy(item["vector"]),
             "embedded_by": item["embedded_by"] if item["vector"] is not None else None, "slot": own_slot,
             "source_identity": source, "source": None if contract is None else contract.source,
             "run_id": item["run_id"], "known_from": window, "known_until": None, "corrected_by": None}
    if later:
        # Stated before what memory holds (a session that finished later): history, never current.
        _, held = max(later, key=lambda value: (value[0], value[1]["record"]["id"]))
        versions[record["id"]] = {**entry, "known_until": window, "corrected_by": held["record"]["id"]}
        section["declared"].append({"run_id": item["run_id"], "statement": record["id"], "slot": own_slot,
                                    "late": True})
        section["corrections"].append({"statement": record["id"], "corrected_by": held["record"]["id"],
                                       "slot": own_slot, "late": True})
        return
    if earlier is not None:  # Held again: the periods it was held before stay in its history.
        entry["earlier_known"] = [*copy.deepcopy(earlier.get("earlier_known", [])), {
            "from": earlier["known_from"], "until": window if earlier["known_until"] is None
            else earlier["known_until"], "corrected_by": earlier.get("corrected_by"),
            "withdrawn": copy.deepcopy(earlier.get("withdrawn"))}]
    versions[record["id"]] = entry
    section["declared"].append({"run_id": item["run_id"], "statement": record["id"], "slot": own_slot,
                                **({"held_again": True} if earlier is not None else {})})
    for old in same:
        versions[old["record"]["id"]] = {**copy.deepcopy(old), "known_until": window, "corrected_by": record["id"]}
        section["corrections"].append({"statement": old["record"]["id"], "corrected_by": record["id"],
                                       "slot": own_slot})
    others = [entry for entry in current if entry["source_identity"] != source
              and _content(entry["record"]) != _content(record)]
    if others:
        section["conflicts"].append({"statement": record["id"], "slot": own_slot,
                                     "with": sorted(entry["record"]["id"] for entry in others)})


# -- reassessment ---------------------------------------------------------------------------------------------
def recorded_order(state: Mapping[str, Any], sessions) -> dict[str, dict[str, Any]]:
    """The world's order of statements the record shows (a reassessment; ``sessions`` reads each session's whole
    history once): every declaration the court consolidated, at the place of its observation, from sessions whose
    record still verifies."""
    found: dict[str, dict[str, Any]] = {}
    for run_id, cursor in sorted(state["cursors"].items()):
        facts = sessions.facts(run_id)
        if facts is None or facts.problems:
            continue  # A record that does not verify places nothing.
        for position, event in facts.found.get("knowledge_declared", []):
            if position >= cursor["to"] or position not in facts.observed:
                continue
            record = verify(event["statement"], "statement")
            group = found.setdefault(_group(record), {
                "source": {"tool": record["source"]["tool"], "args": copy.deepcopy(record["source"]["args"])},
                "statements": []})
            statement = _statement(record, facts.observed[position])
            if statement not in group["statements"]:  # Declared again from the same answer: the same place.
                group["statements"].append(statement)
    for group in found.values():
        group["statements"].sort(key=_ordered)
    return found


def _holds(version) -> bool:
    return version["known_until"] is None and version.get("withdrawn") is None


def _held_again(version, window) -> dict[str, Any]:
    """A version held again from ``window``: the period it was held in last stays in its history."""
    return {**copy.deepcopy(version), "earlier_known": [*copy.deepcopy(version.get("earlier_known", [])), {
        "from": version["known_from"], "until": version["known_until"], "corrected_by": version.get("corrected_by"),
        "withdrawn": copy.deepcopy(version.get("withdrawn"))}],
        "known_from": window, "known_until": None, "corrected_by": None}


def retimed(knowledge: Mapping[str, Any], recorded: Mapping[str, Mapping[str, Any]],
            window: int) -> tuple[dict[str, dict], dict[str, dict], list[dict[str, Any]]]:
    """The order of statements and the timeline under court policy v6 (a reassessment, pure). Every statement the
    record places joins the order memory keeps — a place kept is never lost, an unplaced statement the record
    places is placed. Then each source, slot and start is held by a version of what the source stated last: a
    version memory holds that its source stated before another — a statement folded late under an earlier
    policy — is corrected from ``window`` by a version of the later one, held again from ``window`` unless a
    forget withdrew it (then nothing holds the slot); the periods memory held each in stay as they were, so every
    earlier window answers as it did. Returns the changed orders, the changed versions and one row per
    correction."""
    stated: dict[str, dict] = {}
    for key in sorted(set(knowledge["stated"]) | set(recorded)):
        kept = knowledge["stated"].get(key)
        group = copy.deepcopy(kept or recorded[key])
        group["statements"] = placed_in(group, (recorded.get(key) or {"statements": []})["statements"])
        if group != kept:
            stated[key] = group
    order = {**knowledge["stated"], **stated}
    versions = knowledge["versions"]
    groups: dict[str, list] = {}
    for identity, version in sorted(versions.items()):
        groups.setdefault(_group(version["record"]), []).append(version)
    updated: dict[str, dict] = {}
    rows = []
    for key in sorted(groups):
        held = [version for version in groups[key]
                if version["known_until"] is None and version.get("withdrawn") is None]
        group = order.get(key)
        if not held or group is None:
            continue
        stated_last = {}
        for version in groups[key]:
            last = _last_stated(group, version["record"])
            if last is not None:
                stated_last[version["record"]["id"]] = last
        if not stated_last or any(version["record"]["id"] not in stated_last for version in held):
            continue  # What is held cannot be placed: nothing to order it against.
        winner = max(stated_last, key=lambda identity: (stated_last[identity], _holds(versions[identity]),
                                                        versions[identity]["known_from"], identity))
        if _holds(versions[winner]):
            continue
        for version in held:
            updated[version["record"]["id"]] = {**copy.deepcopy(version), "known_until": window,
                                                "corrected_by": winner}
            rows.append({"statement": version["record"]["id"], "corrected_by": winner, "slot": version["slot"]})
        if versions[winner].get("withdrawn") is None:  # A forgotten version corrects; it is never held again.
            updated[winner] = _held_again(versions[winner], window)
    return stated, updated, rows


def reassessed_knowledge(context, habits, forced, recorded) -> dict[str, dict[str, Any]]:
    """The knowledge a reassessment decides (``retimed``) and its report: each version it corrects is reported
    with its revision — live habits whose basis admitted it go to probation, as for any correction; the
    hypotheses are decided again from the record by the reassessment itself."""
    knowledge = known_of(context.state)
    stated, versions, rows = retimed(knowledge, recorded, context.window)
    section = context.report["knowledge"]
    for row in rows:
        runs = set(knowledge["uses"].get(row["statement"], []))
        section["corrections"].append(dict(row))
        section["revisions"].append({"statement": row["statement"], "hypotheses": [],
                                     "habits": _revise_habits(context.state, habits, runs, row["statement"], forced)})
    return {"versions": versions, "stated": stated}
