"""Publish a whole Golden Replay recording and recover its previous generation."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Callable, TypeVar
import shutil

from filelock import FileLock


def _coordination(output: Path) -> Path:
    return output.parent / (".synapse-record-" + output.name)


def _recover(output: Path, control: Path) -> None:
    previous = control / "previous"
    if previous.exists():
        if output.exists():
            shutil.rmtree(previous)
        else:
            os.replace(previous, output)
    staged = control / "recording"
    if staged.exists():
        shutil.rmtree(staged)


_T = TypeVar("_T")


def read_recording(output: Path | str, load: Callable[[Path], _T]) -> _T:
    """Read one generation. Legacy bundles can be read even on read-only media."""
    output = Path(output)
    control = _coordination(output.absolute())
    if not control.exists():
        try:
            result = load(output)
        except Exception:
            if not control.exists():
                raise
        else:
            if not control.exists():
                return result
        # The first writer started during a legacy read. Read again under its lock.
    if control.is_symlink():
        raise ValueError("record transaction directory must not be symbolic")
    with FileLock(str(control / "publish.lock")):
        _recover(output, control)
        return load(output)


@contextmanager
def publish_recording(output):
    """Stage complete bytes first. Readers and writers share the commit boundary."""
    output = Path(output).absolute()
    if not output.name or output.is_symlink():
        raise ValueError("record destination must be a real directory")
    control = _coordination(output)
    control.mkdir(mode=0o700, parents=True, exist_ok=True)
    if control.is_symlink():
        raise ValueError("record transaction directory must not be symbolic")
    with FileLock(str(control / "publish.lock")):
        _recover(output, control)
        staged = control / "recording"
        staged.mkdir()
        try:
            yield staged
            if output.exists():
                if output.is_symlink() or not output.is_dir():
                    raise ValueError("record destination must be a real directory")
                existing = list(output.iterdir())
                if existing:
                    if (not (output / "manifest.json").is_file()
                            or any(item.is_symlink() or not (staged / item.name).is_file()
                                   or not item.is_file() for item in existing)):
                        raise ValueError("refusing to replace a directory containing unrelated files")
                    with (output / "manifest.json").open(encoding="utf-8") as stream:
                        manifest = json.load(stream)
                    metadata = manifest.get("metadata") if isinstance(manifest, dict) else None
                    if not isinstance(metadata, dict) or metadata.get("generated_by") not in (
                        "synapse.golden_replay", "synapse.golden_replay.record_integrate_artifact",
                    ):
                        raise ValueError("refusing to replace a directory without a Synapse recording")
                os.replace(output, control / "previous")
            os.replace(staged, output)
        finally:
            _recover(output, control)
