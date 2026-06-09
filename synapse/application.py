"""Application services for canonical Synapse program execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from . import run as run_source_runtime
from .golden_replay import record_source
from .interpreter import Interpreter


@dataclass(frozen=True)
class ReplayArtifactSummary:
    recorded: str
    program_hash: str
    final_history_hash: str
    history_length: int

    def to_json(self) -> dict[str, object]:
        return {
            "recorded": self.recorded,
            "program_hash": self.program_hash,
            "final_history_hash": self.final_history_hash,
            "history_length": self.history_length,
        }


@dataclass(frozen=True)
class FileExecutionRequest:
    path: Path
    record: bool = False
    output_dir: Path | None = None
    layer: str = "strict"


@dataclass(frozen=True)
class SourceExecutionRequest:
    source: str


@dataclass(frozen=True)
class ReplRequest:
    banner: bool = True
    prompt: str = "synapse> "


@dataclass(frozen=True)
class RuntimeExecutionResult:
    status: str
    exit_code: int
    output: str
    diagnostics: tuple[str, ...] = ()
    artifact: ReplayArtifactSummary | None = None


@dataclass(frozen=True)
class ReplResult:
    status: str
    exit_code: int
    diagnostics: tuple[str, ...] = ()


def execute_loaded_source(request: SourceExecutionRequest, *, interpreter: Interpreter | None = None) -> RuntimeExecutionResult:
    """Execute already-loaded Synapse source through the public runtime primitive."""

    try:
        output = run_source_runtime(request.source, interpreter)
    except Exception as exc:  # keep application error normalization at this layer
        return RuntimeExecutionResult(
            status="ERROR",
            exit_code=1,
            output="",
            diagnostics=(str(exc),),
        )
    return RuntimeExecutionResult(status="OK", exit_code=0, output=output)


def execute_source(request: SourceExecutionRequest) -> RuntimeExecutionResult:
    return execute_loaded_source(request)


def execute_file(request: FileExecutionRequest) -> RuntimeExecutionResult:
    try:
        source = request.path.read_text(encoding="utf-8")
    except Exception as exc:
        return RuntimeExecutionResult(
            status="ERROR",
            exit_code=1,
            output="",
            diagnostics=(f"File not found: {request.path}" if isinstance(exc, FileNotFoundError) else str(exc),),
        )

    if request.record:
        if request.output_dir is None:
            return RuntimeExecutionResult(
                status="ERROR",
                exit_code=1,
                output="",
                diagnostics=("synapse run --record requires --output <artifact_dir>",),
            )
        try:
            manifest = record_source(source, request.output_dir, source_path=str(request.path), layer=request.layer)
        except Exception as exc:
            return RuntimeExecutionResult(status="ERROR", exit_code=1, output="", diagnostics=(str(exc),))
        return RuntimeExecutionResult(
            status="RECORDED",
            exit_code=0,
            output="",
            artifact=ReplayArtifactSummary(
                recorded=str(request.output_dir),
                program_hash=manifest["metadata"]["program_hash"],
                final_history_hash=manifest["final"]["final_history_hash"],
                history_length=manifest["final"]["history_length"],
            ),
        )

    return execute_loaded_source(SourceExecutionRequest(source))


def run_repl(request: ReplRequest, *, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> ReplResult:
    if request.banner:
        stdout.write("╔══════════════════════════════════════╗\n")
        stdout.write("║  Synapse v0.7.0 - Язык для ИИ        ║\n")
        stdout.write("║  Type 'exit' to quit                 ║\n")
        stdout.write("╚══════════════════════════════════════╝\n")
    interpreter = Interpreter()
    while True:
        try:
            stdout.write(request.prompt)
            stdout.flush()
            line = stdin.readline()
            if line == "":
                break
            line = line.rstrip("\n")
            if line.strip() in ["exit", "quit"]:
                break
            if not line.strip():
                continue
            result = execute_loaded_source(SourceExecutionRequest(line), interpreter=interpreter)
            if result.output:
                stdout.write(f"{result.output}\n")
            if result.exit_code != 0:
                stdout.write(f"Error: {'; '.join(result.diagnostics)}\n")
        except KeyboardInterrupt:
            stdout.write("\n")
            break
        except Exception as exc:
            stderr.write(f"Error: {exc}\n")
    return ReplResult(status="OK", exit_code=0)


def metrics_text() -> str:
    return Interpreter().metrics_text()
