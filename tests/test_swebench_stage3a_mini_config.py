"""Stage 3A mini invocation wrapper tests."""

from __future__ import annotations

from synapse.experiments.swebench.mini_config import MiniInvocationConfig
from synapse.worker.mini_adapter import MiniAdapterConfig
from synapse.worker import smoke


def test_mini_invocation_config_converts_to_adapter_config():
    config = MiniInvocationConfig(
        executable="mini",
        environment_class="local",
        api_base="http://127.0.0.1:11434",
        extra_config=("model.temperature=0",),
        cost_limit=7.5,
        step_limit=12,
        timeout_seconds=34,
    )

    adapter = config.to_adapter_config()

    assert isinstance(adapter, MiniAdapterConfig)
    assert adapter.timeout_seconds == 34
    assert adapter.max_steps == 12
    assert adapter.cost_limit == 7.5
    assert adapter.model == "ollama_chat/qwen3-coder:30b"


def test_wrapper_command_prefix_contains_only_wrapper_owned_args():
    command = MiniInvocationConfig(
        api_base="http://wsl-gateway:11434",
        extra_config=("model.context_window=8192",),
    ).command_prefix()

    assert command[:3] == ("mini", "--agent-class", "default")
    assert "-c" in command
    assert "model.model_kwargs.api_base=http://wsl-gateway:11434" in command
    assert "model.context_window=8192" in command
    forbidden = {
        "-m",
        "-l",
        "-t",
        "-o",
        "--exit-immediately",
        "mini.yaml",
    }
    assert forbidden.isdisjoint(command)
    assert not any("agent.step_limit" in part for part in command)


def test_wrapper_omits_optional_agent_and_environment_class_when_none():
    command = MiniInvocationConfig(agent_class=None, environment_class=None).command_prefix()

    assert "--agent-class" not in command
    assert "--environment-class" not in command
    assert command == ("mini",)


def test_wrapper_adds_environment_class_only_when_set():
    command = MiniInvocationConfig(environment_class="docker").command_prefix()

    assert command[:5] == ("mini", "--agent-class", "default", "--environment-class", "docker")


def test_smoke_ollama_config_does_not_require_gemini_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("SYNAPSE_OLLAMA_API_BASE", "http://127.0.0.1:11434")

    assert smoke._provider_config_present() is True


def test_smoke_reports_missing_provider_config_without_gemini(monkeypatch):
    for name in ("GEMINI_API_KEY", "SYNAPSE_MINI_WORKER_MODEL", "SYNAPSE_OLLAMA_API_BASE", "MSWEA_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)

    assert smoke._provider_config_present() is False
