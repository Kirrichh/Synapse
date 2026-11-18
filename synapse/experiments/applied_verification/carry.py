"""Raw carry semantics for the Stage 3A baseline retry loop."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RawCarryEntry:
    attempt_id: int
    worker_summary: str | None
    oracle_stdout: str
    oracle_stderr: str
    diagnostics: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


@dataclass(frozen=True)
class RawTranscriptCarry:
    entries: tuple[RawCarryEntry, ...] = ()

    def append(self, entry: RawCarryEntry) -> "RawTranscriptCarry":
        return RawTranscriptCarry(entries=(*self.entries, entry))

    def render_prompt_suffix(self) -> str:
        if not self.entries:
            return ""
        parts = [
            "\n\n=== RAW BASELINE RETRY CONTEXT ===",
            "The following is raw growing baseline context from previous failed attempts.",
            "It is not distilled Gold evidence and is not a verification claim.",
        ]
        for entry in self.entries:
            parts.extend(
                [
                    f"\n--- previous attempt {entry.attempt_id} raw context ---",
                    f"worker_summary:\n{entry.worker_summary or ''}",
                    f"oracle_stdout:\n{entry.oracle_stdout}",
                    f"oracle_stderr:\n{entry.oracle_stderr}",
                    "diagnostics:",
                    *entry.diagnostics,
                ]
            )
        return "\n".join(parts)
