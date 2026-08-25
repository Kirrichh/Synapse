"""Production composition root for the Library-owned ProgramArtifact CAS."""

from __future__ import annotations

from pathlib import Path

from .library import BehaviorLibrary, PublisherIdentity
from .library_program_artifacts import create_library_program_artifact_lifecycle
from .persistence import StoreMutationFencePort


def create_program_artifact_behavior_library(
    root: Path,
    *,
    publisher_identity: PublisherIdentity,
    mutation_fence: StoreMutationFencePort,
    write_history: object,
) -> BehaviorLibrary:
    """Compose one Library owner with its exact immutable program lifecycle."""

    return BehaviorLibrary(
        root,
        publisher_identity=publisher_identity,
        mutation_fence=mutation_fence,
        write_history=write_history,
        program_artifact_lifecycle=create_library_program_artifact_lifecycle(),
    )


__all__ = ["create_program_artifact_behavior_library"]
