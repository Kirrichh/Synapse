import copy

import pytest

from synapse import (
    DreamSandboxEnvironment,
    DreamSandboxIsolationError,
    Environment,
    Interpreter,
    RuntimeMode,
    run,
)


def test_dream_assignment_isolation():
    source = '''
let x = 1
let result = dream {
  x = 2
  return x
}
print(x)
print(result)
'''
    interp = Interpreter()
    run(source, interp)

    assert interp.get_output() == "1\n2"


def test_dream_list_mutation_isolation():
    parent = Environment()
    xs = [1, 2]
    parent.define("xs", xs)
    sandbox = DreamSandboxEnvironment(parent)

    sandbox.get("xs").append(3)

    assert xs == [1, 2]
    assert parent.get("xs") == [1, 2]
    assert sandbox.get("xs") == [1, 2, 3]


def test_dream_dict_mutation_isolation():
    parent = Environment()
    data = {"a": 1}
    parent.define("data", data)
    sandbox = DreamSandboxEnvironment(parent)

    sandbox.get("data")["b"] = 2

    assert data == {"a": 1}
    assert parent.get("data") == {"a": 1}
    assert sandbox.get("data") == {"a": 1, "b": 2}


def test_dream_set_mutation_isolation():
    parent = Environment()
    values = {"a", "b"}
    parent.define("values", values)
    sandbox = DreamSandboxEnvironment(parent)

    sandbox.get("values").add("c")

    assert values == {"a", "b"}
    assert parent.get("values") == {"a", "b"}
    assert sandbox.get("values") == {"a", "b", "c"}


def test_dream_repeated_read_returns_same_clone():
    parent = Environment()
    parent.define("xs", [1, 2])
    sandbox = DreamSandboxEnvironment(parent)

    first = sandbox.get("xs")
    second = sandbox.get("xs")
    first.append(3)
    second.append(4)

    assert first is second
    assert sandbox.get("xs") == [1, 2, 3, 4]
    assert parent.get("xs") == [1, 2]


def test_dream_alias_preservation_for_parent_mutables():
    parent = Environment()
    shared = [1, 2]
    parent.define("xs", shared)
    parent.define("ys", shared)
    sandbox = DreamSandboxEnvironment(parent)

    xs = sandbox.get("xs")
    ys = sandbox.get("ys")
    xs.append(3)
    ys.append(4)

    assert xs is ys
    assert xs == [1, 2, 3, 4]
    assert parent.get("xs") == [1, 2]
    assert parent.get("ys") == [1, 2]
    assert parent.get("xs") is parent.get("ys")


def test_dream_tuple_nested_mutable_isolation():
    parent = Environment()
    nested = ([1, 2], {"a": 1})
    parent.define("nested", nested)
    sandbox = DreamSandboxEnvironment(parent)

    local_nested = sandbox.get("nested")
    local_nested[0].append(3)
    local_nested[1]["b"] = 2

    assert nested == ([1, 2], {"a": 1})
    assert local_nested == ([1, 2, 3], {"a": 1, "b": 2})


class UnsupportedObject:
    pass


def test_dream_unsupported_object_rejected():
    parent = Environment()
    parent.define("obj", UnsupportedObject())
    sandbox = DreamSandboxEnvironment(parent)

    with pytest.raises(DreamSandboxIsolationError, match="Unsupported|Cannot access"):
        sandbox.get("obj")


def test_dream_replay_parent_scope_unchanged_after_assignment():
    source = '''
let x = 1
let result = dream {
  x = 2
  return x
}
print(x)
print(result)
'''
    live = Interpreter()
    run(source, live)

    replay = Interpreter()
    replay.execution_history = copy.deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    run(source, replay)

    assert live.get_output() == "1\n2"
    assert replay.get_output() == "1\n2"
    assert replay.replay_cursor == len(replay.execution_history)
