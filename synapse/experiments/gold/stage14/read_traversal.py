"""Lifetime of one synchronous reconstruction of retained publication history.

One publication read can recursively share committed predecessors.
Its physical reader retains bounded immutable bytes for that root read only.
This scope must not cross an execution, publication, or authority decision.
Independent reads, copied completed contexts and other threads start afresh.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import get_ident


_PUBLICATION_READ = ContextVar("gold_publication_read", default=None)


@contextmanager
def publication_read_scope():
    traversal = _PUBLICATION_READ.get()
    token = None
    if traversal is None or traversal["closed"] or traversal["owner"] != get_ident():
        traversal = {"active": set(), "verified": {}, "byte_length": 0,
                     "closed": False, "owner": get_ident()}
        token = _PUBLICATION_READ.set(traversal)
    try:
        yield traversal
    finally:
        if token is not None:
            # Copies of this context cannot reuse observations after it closes.
            traversal["closed"] = True
            traversal["verified"].clear()
            _PUBLICATION_READ.reset(token)
