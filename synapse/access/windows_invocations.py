"""Windows resource custody for the invocation-owner registration profile."""
from contextlib import contextmanager
from pathlib import Path

from .records import AccessRefused
from .windows_objects import WindowsObjects


class WindowsInvocationResources:
    def __init__(self):
        self.objects = WindowsObjects()

    @contextmanager
    def hold(self, resource):
        with self.objects.hold(resource):
            root = Path(resource.path)
            # Pin the actual database through the SQLite operation; path checks
            # alone leave a replacement race between authorization and opening.
            with self.objects.open(root / "executions.sqlite3") as (_, info):
                if info.attributes & 0x10:
                    raise AccessRefused("RESOURCE_CHANGED")
                # SQLite auxiliary files are owned by SQLite. Refuse existing
                # redirections without blocking SQLite's normal close/unlink.
                for suffix in ("-wal", "-shm", "-journal"):
                    path = root / ("executions.sqlite3" + suffix)
                    if path.exists() or path.is_symlink():
                        with self.objects.open(path) as (_, auxiliary):
                            if auxiliary.attributes & 0x10:
                                raise AccessRefused("RESOURCE_CHANGED")
                yield
