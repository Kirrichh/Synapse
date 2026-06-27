"""mini invocation wrapper for Stage 3A baseline runs."""

from __future__ import annotations

from dataclasses import dataclass

from synapse.worker.mini_adapter import MiniAdapterConfig


@dataclass(frozen=True)
class MiniInvocationConfig:
    executable: str = "mini"
    agent_class: str | None = "default"
    environment_class: str | None = None
    model: str = "ollama_chat/qwen3-coder:30b"
    api_base: str | None = None
    cost_limit: float = 5.0
    step_limit: int = 50
    timeout_seconds: int = 600
    config_file: str = "mini.yaml"
    extra_config: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra_config", tuple(self.extra_config))

    def command_prefix(self) -> tuple[str, ...]:
        command: list[str] = [self.executable]
        if self.agent_class is not None:
            command.extend(("--agent-class", self.agent_class))
        if self.environment_class is not None:
            command.extend(("--environment-class", self.environment_class))
        if self.api_base:
            command.extend(("-c", f"model.model_kwargs.api_base={self.api_base}"))
        for entry in self.extra_config:
            command.extend(("-c", entry))
        return tuple(command)

    def to_adapter_config(self) -> MiniAdapterConfig:
        if self.config_file != "mini.yaml":
            raise ValueError(
                "stage3a: unsupported_mini_config_file - Stage 2 adapter currently owns fixed mini.yaml config"
            )
        return MiniAdapterConfig(
            command=self.command_prefix(),
            timeout_seconds=self.timeout_seconds,
            max_steps=self.step_limit,
            cost_limit=self.cost_limit,
            model=self.model,
        )
