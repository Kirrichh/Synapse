"""Artifact storage helpers for Stage 3A telemetry."""

from __future__ import annotations

from pathlib import Path
import hashlib

from .contract import ArtifactRef


class ArtifactStore:
    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.artifacts_dir = self.run_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def write_text(self, name: str, kind: str, text: str) -> ArtifactRef | None:
        if text == "":
            return None
        safe_name = name.replace("\\", "_").replace("/", "_")
        path = self.artifacts_dir / safe_name
        data = text.encode("utf-8")
        path.write_bytes(data)
        return ArtifactRef(
            kind=kind,
            path=str(path.relative_to(self.run_dir)).replace("\\", "/"),
            sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data),
        )
