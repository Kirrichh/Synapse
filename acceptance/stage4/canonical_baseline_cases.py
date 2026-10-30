"""Inputs and typed fingerprints for the frozen public Stage4 baseline."""
import hashlib
import json
import math


UNSUPPORTED_SAMPLE_TYPE = type('x' * 300, (), {})


def samples():
    cycle_list = []
    cycle_list.append(cycle_list)
    cycle_dict = {}
    cycle_dict['self'] = cycle_dict
    deep = None
    for _ in range(130):
        deep = [deep]
    within = None
    for _ in range(128):
        within = [within]
    over = [within]
    return [None, {'a': ['e\u0301', -0.0, 2**53 - 1]}, {'é': 1, 'e\u0301': 2},
            {'é': 1, 'e\u0301': 2, 'bad': '\ud800'}, {'e\u0301': {'bad': '\ud800'}},
            {'é': 1, 'e\u0301': 2, 'bad': 2**60},
            {'e\u0301' * 130: {'bad': '\ud800'}}, cycle_list, cycle_dict, within, over, deep,
            math.nan, math.inf, -math.inf, 2**53, -(2**53), -(2**53 - 1), 1e300, -0.0,
            UNSUPPORTED_SAMPLE_TYPE()]



DECODE_SAMPLES = [b'{"a":1,"a":2}', b'\xff', b'{ "a": 1 }', b'NaN', b'Infinity', b'1e999',
                  b'"\\ud800"', b'{"e\\u0301":1,"\\u00e9":2}', b'-0.0', b'null']


def fingerprint(value):
    """Hash a deterministic typed graph description, including aliases and cycles."""
    seen = {}

    def describe(item):
        kind = type(item)
        if item is None:
            return ['none']
        if kind is bool:
            return ['bool', item]
        if kind is int:
            return ['int', str(item)]
        if kind is float:
            return ['float', item.hex()]
        if kind is str:
            return ['str', item]
        if kind is bytes:
            return ['bytes', item.hex()]
        identity = id(item)
        if identity in seen:
            return ['ref', seen[identity]]
        number = len(seen)
        seen[identity] = number
        if kind is list:
            return ['list', number, [describe(child) for child in item]]
        if kind is dict:
            return ['dict', number, [[describe(key), describe(child)] for key, child in item.items()]]
        # The baseline deliberately includes one unsupported object type.
        if kind is UNSUPPORTED_SAMPLE_TYPE and vars(item) == {}:
            return ['object', number, kind.__module__, kind.__qualname__, describe(vars(item))]
        raise TypeError('unsupported baseline fingerprint type: ' + kind.__qualname__)

    description = json.dumps(describe(value), ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(description.encode('ascii')).hexdigest()
