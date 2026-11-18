"""Differential fuzz of the PR 112 text, behavior and verification-record paths.

Run once per source tree with the same seed and compare the section digests:

    python -I -B acceptance/verification/pr112_differential_fuzz.py --tree <root> --seed 1

``--tree`` is an extracted source tree (for example ``git archive <commit>``);
only that tree is imported. Each observation is the result ``repr`` or the full
exception (class, failure code, detail, message, args), so equal digests mean
equal results and equal refusals for every generated input.

The corpus does not plant objects in sealed values beyond the forged unit
fields and record fields below, does not instrument projections, and does not
mutate a record between two reads of one operation. Those cases are pinned by
named tests instead: planted digests and the guard-refusal reread in
``acceptance/stage4/stage12/test_outcome_contract.py``, the canonical bundle
refusal order in ``tests/test_stage4_gold_behavior.py``.
"""
import argparse
import hashlib
import json
import random
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--tree', required=True)
    parser.add_argument('--seed', type=int, required=True)
    args = parser.parse_args()
    sys.path.insert(0, args.tree)
    print(json.dumps({'seed': args.seed, 'sections': run(args.seed)}, indent=2, sort_keys=True))


def observe(function, value):
    try:
        return ['ok', repr(function(value))]
    except Exception as exc:
        return ['err', type(exc).__name__, str(getattr(exc, 'failure_code', '')),
                str(getattr(exc, 'detail', '')), str(exc), repr(exc.args)]


class Section:
    def __init__(self):
        self.digest, self.observations = hashlib.sha256(), 0

    def put(self, observation):
        self.digest.update(json.dumps(observation).encode())
        self.observations += 1

    def result(self):
        return {'observations': self.observations, 'sha256': self.digest.hexdigest()}


def run(seed):
    from synapse import canonical_values as cv
    from synapse.experiments.gold import behavior as b
    from synapse.experiments.gold import canonicalization as c
    from synapse.experiments.gold.stage10.context_codec import encode_canonical
    from synapse.experiments.gold.stage12 import verification_contract as vc
    from tests.test_stage4_gold_behavior import make_valid_unit

    profile, codec = c.STAGE4_CANONICAL_PROFILE_V1, c.STABLE_CANONICAL_CODEC_ID
    sections = {name: Section() for name in (
        'stage4-payload', 'stage4-decode', 'stable-bytes', 'behavior-text', 'program-ir-decode',
        'text-validators', 'inline-boundary', 'behavior-units', 'verification-records')}

    class Sub(str):
        pass

    def encode(value):
        return c.canonicalize_stage4_payload(value, profile_id=profile, codec_id=codec)

    rng = random.Random(seed)
    alphabet = ['a', 'Z', 'é', 'é', 'Å', '\x00', '\x1f', '"', '\\', '😀', '﻿', '\ud800', '\udfff', 'ﬁ']

    def text(low=0, high=6):
        return ''.join(rng.choice(alphabet) for _ in range(rng.randint(low, high)))

    def value(depth=0, sub=False):
        roll = rng.random()
        if depth > 4 or roll < 0.4:
            choices = [None, True, False, 1, -3, 2**53, 1.5, -0.0, float('nan'), text(), text()]
            if sub:
                choices.append(Sub(text()))
            return rng.choice(choices)
        if roll < 0.7:
            return [value(depth + 1, sub) for _ in range(rng.randint(0, 4))]
        return {(Sub(text()) if sub and rng.random() < .2 else text()): value(depth + 1, sub)
                for _ in range(rng.randint(0, 4))}

    for _ in range(40000):
        payload = value()
        encoded = observe(encode, payload)
        sections['stage4-payload'].put(encoded)
        sections['stable-bytes'].put(observe(lambda item: cv.canonical_bytes(item).data, value(sub=True)))
        sections['behavior-text'].put(observe(lambda item: b._text(item, 'f'), text()))
        if encoded[0] == 'ok':
            sections['stage4-decode'].put(observe(lambda raw: c.decode_stage4_canonical_bytes(
                eval(raw), profile_id=profile, codec_id=codec), encoded[1]))

    ir = {'schema_version': c.CANONICAL_PROGRAM_IR_V1, 'program': {'node': 'program', 'statements': []}}
    good = c.canonical_program_ir_bytes(ir)
    for _ in range(3000):
        blob = bytearray(good)
        for _ in range(rng.randint(0, 3)):
            if blob:
                blob[rng.randrange(len(blob))] = rng.randrange(256)
        sections['program-ir-decode'].put(observe(c.decode_canonical_program_ir, bytes(blob)))
    sections['program-ir-decode'].put(observe(
        c.decode_canonical_program_ir, b'x' * (c.MAX_INLINE_CANONICAL_PROGRAM_BYTES + 1)))

    alphabet = ['a', 'Z', 'é', 'é', 'Å', '\x00', '\x7f', '\x80', '"', '\\', '😀', '﻿', '\ud800',
                '\udfff', 'ﬁ', '-', '_', '1', '/', '.', 'v']
    patterns = [None, b._VERSIONED_RE, b._CLAIM_RE]
    validators = sections['text-validators']
    for _ in range(60000):
        sample = text(0, 8)
        validators.put(observe(lambda item: b._text(item, 'f', rng.choice(patterns)), sample))
        validators.put(observe(lambda item: b._inline_string(item, '$.v'), sample))
        validators.put(observe(lambda item: c._text(item, 'f'), sample))
        validators.put(observe(lambda item: c._text(item, 'f', nonempty=False), sample))
        validators.put(observe(cv.canonicalize, sample))
        validators.put(observe(cv.canonicalize, Sub(sample)))
        validators.put(observe(lambda item: cv.canonicalize({item: item}), sample))

    for size in range(4090, 4100):
        for char in ['x', 'é', '😀', 'ࠀ']:
            for tail in ['', '\ud800', 'a']:
                for sample in ((char * size)[:size] + tail, char * (size // len(char.encode())) + tail):
                    sections['inline-boundary'].put(observe(lambda item: b._inline_string(item, '$.v'), sample))

    units = sections['behavior-units']
    for _ in range(200):
        unit = make_valid_unit()
        units.put(observe(lambda item: item.to_dict(), unit))
        units.put(observe(lambda item: item.core.to_dict(), unit))
        units.put(observe(b.validate_behavior_unit, unit))
        units.put(observe(lambda item: b.behavior_unit_from_dict(item.to_dict()).to_dict(), unit))
        for field in ('context_sha256', 'content_key', 'canonical_core', 'core', '_trusted_seal'):
            forged = make_valid_unit()
            object.__setattr__(forged, field, rng.choice(['0' * 64, None, 1, make_valid_unit().core]))
            units.put(observe(lambda item: item.to_dict(), forged))
            units.put(observe(b.validate_behavior_unit, forged))

    records = sections['verification-records']
    for _ in range(500):
        payload = {'schema_version': rng.choice([vc.VERIFICATION_SCHEMA_V5, 'other']), 'run_id': text(1, 8)}
        try:
            raw = encode_canonical(payload)
        except Exception as exc:
            records.put(['encode', type(exc).__name__])
            continue
        record = object.__new__(vc.VerificationRecord)
        object.__setattr__(record, '_bytes', rng.choice([raw, raw + b' ', bytearray(raw)]))
        digest = hashlib.sha256(raw).hexdigest()
        object.__setattr__(record, '_digest', rng.choice([digest, '0' * 64, None, digest]))
        object.__setattr__(record, '_seal', rng.choice([vc._SEAL, vc._SEAL, None]))
        records.put(observe(lambda item: item.payload(), record))
        records.put(observe(lambda item: item.reference.to_dict(), record))
        records.put(observe(lambda item: item.to_dict(), record))
        records.put(observe(lambda item: vc.require_verification_record(item) is item, record))
    return {name: section.result() for name, section in sections.items()}


if __name__ == '__main__':
    main()
