"""Acceptance: the compiled VM reaches the host as the interpreter does.

A model call in the middle or at the end of a VM program is answered and the
program continues; the prompt the provider receives is the program's prompt;
``time``/``random``/``uuid`` are recorded and replayed; a free-tier provider is
reached only for content the operator declared. The provider is a synthetic
Gemini transport: the product's gateway, privacy policy and parser are real.
"""
import pytest

from synapse import Interpreter, compile_to_ast
from synapse.builtins import LLMBackend
from synapse.llm.gateway import LLMGateway, config_from_env
from synapse.llm.gemini import GeminiProvider

FREE_TIER = {"SYNAPSE_LLM_PROVIDER": "gemini", "SYNAPSE_LLM_TIER": "free",
             "SYNAPSE_LLM_MODEL": "gemini-3.1-flash-lite", "GEMINI_API_KEY": "acceptance-key"}
DECLARED_PUBLIC = {"SYNAPSE_LLM_DATA_CLASSIFICATION": "synthetic", "SYNAPSE_LLM_REPOSITORY_VISIBILITY": "public",
                   "SYNAPSE_LLM_CONTAINS_SECRETS": "false", "SYNAPSE_LLM_CONTAINS_PERSONAL_DATA": "false"}
RUN_VM = "compile vm { source src bind code }\nrun vm { source code }\n"


def gemini(environ, prompts):
    """A product LLM backend whose Gemini transport records each prompt it is sent and answers ``four``."""
    def transport(url, payload, timeout, headers):
        prompts.append(payload["contents"][0]["parts"][0]["text"])
        return {"candidates": [{"content": {"parts": [{"text": "four"}]}}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1, "totalTokenCount": 4}}
    provider = GeminiProvider(api_key=environ["GEMINI_API_KEY"], model=environ["SYNAPSE_LLM_MODEL"], transport=transport)
    return LLMBackend(environ=environ, gateway=LLMGateway(config_from_env(environ=environ), provider_instance=provider))


def in_vm(program_source, backend=None, snapshot=None):
    interp = Interpreter()
    if snapshot is not None:
        interp.load_snapshot(snapshot)
    if backend is not None:
        interp.llm_backend = backend
    interp.global_env.define("src", program_source)
    interp.interpret(compile_to_ast(RUN_VM))
    return interp


@pytest.mark.parametrize("source, reached", [
    ('let answer = llm "What is two plus two?"\nlet reached = 123', 123),
    ('let reached = 123\nlet answer = llm "What is two plus two?"', 123),
])
def test_a_model_call_is_answered_and_the_vm_program_continues(source, reached):
    prompts = []
    result = in_vm(source, gemini({**FREE_TIER, **DECLARED_PUBLIC}, prompts)).global_env.get("vm_result")
    assert result["halted"] is True
    assert result["locals"]["reached"] == reached
    assert result["locals"]["answer"]["text"] == "four"
    assert result["stack"] == []
    assert prompts == ["What is two plus two?"]


def test_the_provider_receives_the_vm_prompt_text_and_template_as_the_interpreter_renders_them():
    prompts = []
    backend = gemini({**FREE_TIER, **DECLARED_PUBLIC}, prompts)
    in_vm('let q = "capital of France"\nlet a = llm "What is two plus two?"\n'
          'let b = llm prompt "Tell me the {q}, {missing} and {{braces}}"', backend)
    tree_prompts = []
    tree = Interpreter()
    tree.llm_backend = gemini({**FREE_TIER, **DECLARED_PUBLIC}, tree_prompts)
    tree.interpret(compile_to_ast('let q = "capital of France"\nlet a = llm "What is two plus two?"\n'
                                  'let b = llm prompt "Tell me the {q}, {missing} and {{braces}}"'))
    assert prompts == ["What is two plus two?", "Tell me the capital of France, {missing} and {braces}"]
    assert tree_prompts == prompts


def test_a_free_tier_model_call_from_a_program_needs_the_operator_declaration():
    undeclared, private = [], []
    with pytest.raises(Exception, match="privacy_free_tier_context_required"):
        tree = Interpreter()
        tree.llm_backend = gemini(FREE_TIER, undeclared)
        tree.interpret(compile_to_ast('let answer = llm "What is two plus two?"'))
    with pytest.raises(Exception, match="privacy_free_tier_private_or_unknown_content"):
        tree = Interpreter()
        tree.llm_backend = gemini({**FREE_TIER, **DECLARED_PUBLIC, "SYNAPSE_LLM_DATA_CLASSIFICATION": "private"}, private)
        tree.interpret(compile_to_ast('let answer = llm "What is two plus two?"'))
    assert undeclared == [] and private == []
    declared = []
    tree = Interpreter()
    tree.llm_backend = gemini({**FREE_TIER, **DECLARED_PUBLIC}, declared)
    tree.interpret(compile_to_ast('let answer = llm "What is two plus two?"'))
    assert tree.global_env.get("answer") == "four"
    assert declared == ["What is two plus two?"]


def test_vm_time_random_and_uuid_are_recorded_and_replayed():
    source = "let t = time()\nlet r = random()\nlet u = uuid()"
    live = in_vm(source)
    recorded = [event["name"] for event in live.execution_history if event.get("type") == "side_effect"]
    assert recorded == ["time", "random", "uuid"]
    values = live.global_env.get("vm_result")["locals"]
    assert isinstance(values["t"], float) and isinstance(values["r"], float) and isinstance(values["u"], str)
    replayed = in_vm(source, snapshot=live.snapshot()).global_env.get("vm_result")["locals"]
    assert replayed == values


def test_a_model_call_inside_a_context_keeps_the_context_open_until_it_closes_normally():
    prompts = []
    interp = in_vm('context "work" {\n    let a = llm "What is two plus two?"\n    print("inside")\n}\nlet reached = 1',
                   gemini({**FREE_TIER, **DECLARED_PUBLIC}, prompts))
    trail = [(event["type"], event.get("unwind_reason")) for event in interp.execution_history
             if event["type"] in {"context_entered", "context_exited", "LLM_RESPONSE_CACHED"}]
    assert trail == [("context_entered", None), ("LLM_RESPONSE_CACHED", None), ("context_exited", None)]
    assert interp.global_env.get("vm_result")["output"] == ["inside"]
    assert prompts == ["What is two plus two?"]
