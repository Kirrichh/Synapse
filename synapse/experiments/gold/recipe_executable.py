"""Operator-admitted native executable identity for a source verification recipe."""

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import sys


@dataclass(frozen=True)
class RecipeExecutable:
    name: str
    path: str
    sha256: str

    def __post_init__(self):
        if type(self.name) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", self.name) is None:
            raise ValueError("invalid recipe tool name")
        if type(self.path) is not str or not Path(self.path).is_absolute() or ".." in Path(self.path).parts:
            raise ValueError("native recipe tool path must be absolute and canonical")
        if type(self.sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("invalid recipe tool digest")

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"name", "path", "sha256"}:
            raise ValueError("invalid recipe tool declaration")
        return cls(**value)

    def to_dict(self):
        return asdict(self)

    def measure(self):
        path = Path(self.path)
        if not os.access(path, os.X_OK):
            raise ValueError("native recipe executable is unavailable")
        mode = path.stat()
        if not stat.S_ISREG(mode.st_mode) or mode.st_size > 512 * 1024 * 1024:
            raise ValueError("native recipe executable is not a bounded regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return {**self.to_dict(), "sha256": digest.hexdigest()}


def recipe_executable(recipe):
    if recipe is None or "tool" not in recipe:
        return None
    tool = RecipeExecutable.from_dict(recipe["tool"])
    if recipe["command"][0] not in (tool.name, tool.path):
        raise ValueError("native recipe command differs from its declared tool")
    return tool


def admit_recipe_executable(recipe, granted):
    tool = recipe_executable(recipe)
    if tool is None:
        if recipe is not None and recipe["command"][0] != sys.executable:
            command = Path(recipe["command"][0])
            python = Path(sys.executable)
            if (not command.is_absolute() or command.name != python.name or not command.parent.is_dir()
                    or not command.parent.samefile(python.parent)):
                raise ValueError("source recipe requires the measured Python interpreter or an admitted native tool")
    elif tool not in granted or tool.measure() != tool.to_dict():
        raise ValueError("native recipe executable differs from the operator grant or measured bytes")
    return tool
