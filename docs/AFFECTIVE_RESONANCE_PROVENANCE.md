# Affective Resonance Profile Provenance

Status: **Production capability**

Implemented by PR #10 and present on `main` from merge commit `49c771f4edff140a96e505f4a96a31ccf61a87ef`.

## Observable contract

A successful affective resonance evaluation returns a bridge containing:

```json
{
  "profile_source": "explicit"
}
```

`profile_source` identifies where the resonance profile came from.

| Value | Mode | Meaning |
|---|---|---|
| `explicit` | LIVE | The profile was read from the current environment. |
| `history` | LIVE | The latest matching durable `resonance_profile_computed` event was used. |
| `neutral_fallback` | LIVE | No explicit or matching historical profile existed, so the existing neutral fallback profile was used. |
| `legacy_unknown` | REPLAY only | A legacy `affective_resonance_applied` event has no persisted `profile_source`; the original source cannot be reconstructed safely. |

LIVE never creates or persists `legacy_unknown`.

## Durable ownership

For new `affective_resonance_applied` events, the canonical durable owner is:

```python
event["profile_source"]
```

The returned bridge projects that value for the runtime consumer:

```python
final_bridge["profile_source"]
```

The persisted bridge intentionally does not duplicate ownership:

```python
"profile_source" not in event["bridge"]
```

The pre-existing event field remains unchanged:

```python
event["source"] == "resonate_with_user"
```

`source` identifies the operation that produced the event; it is not the profile provenance field.

## Resolution order in LIVE

The runtime resolves the profile once, before delta computation:

```text
current environment profile
→ latest matching durable history profile
→ neutral fallback
```

The resolved profile is then passed into the existing affective delta computation. Mirror, regulate, dampen, rounding, clamp boundaries, event order and atomic PAD mutation are unchanged by provenance reporting.

## Replay behavior

For a new event with a valid persisted source, replay:

1. consumes the saved `affective_resonance_applied` event;
2. validates `event["profile_source"]`;
3. applies the recorded deltas;
4. returns the saved provenance in the derived bridge;
5. does not call the profile resolver.

For a legacy event where the key is absent, replay returns `legacy_unknown` only in the derived bridge. It does not add the field to the historical event, persisted bridge or execution history and does not recompute old hashes.

If the key is present but invalid, replay fails closed with a replay history mismatch. Invalid values are not replaced by fallback provenance.

## Duplicate application

If an event has already been applied, the runtime does not reapply PAD deltas, add another affective tag or call the resolver. It returns a copied bridge with the validated or derived `profile_source`, leaving the event and its persisted bridge unchanged.

## Integrity boundary

`profile_source` is part of the top-level event payload. The existing full-payload event hash chain therefore covers the provenance value for new events. The hash algorithm, canonical serialization, history seed, checkpoint format, snapshot format and CVM ABI were not changed for this capability.

## Compatibility

- New runtime + old history: supported through the derived `legacy_unknown` projection.
- Old runtime + new event: the pre-P1 event apply path accepts the additive top-level field and continues to apply recorded deltas.
- Legacy events remain immutable.
- Existing history hashes are not recomputed.
