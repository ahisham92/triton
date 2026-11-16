"""Stop pressed while an element is being designed.

One element (a long front beam) can take minutes, so asking between elements is not enough. The
section calculations every design runs through (cracked sections, N–M resultants, crack widths) call
``checkpoint()``; while a design is ``watching`` it asks, at most every quarter of a second, whether
Stop was pressed and raises ``Stopped`` if so. Outside a design it costs nothing.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar


class Stopped(Exception):
    """The user pressed Stop; raised at the next sheet, element or checkpoint."""


class _Watch:
    def __init__(self, asked: Callable[[], bool], every: float) -> None:
        self.asked, self.every, self.next = asked, every, 0.0


_WATCH: ContextVar[_Watch | None] = ContextVar("triton_stop_watch", default=None)


def checkpoint() -> None:
    """Raise ``Stopped`` if Stop was pressed on the design running here."""
    w = _WATCH.get()
    if w is None:
        return
    now = time.monotonic()
    if now < w.next:
        return
    w.next = now + w.every
    if w.asked():
        raise Stopped


@contextlib.contextmanager
def watching(asked: Callable[[], bool], every: float = 0.25) -> Iterator[None]:
    """Checkpoints inside the block ask ``asked()`` whether Stop was pressed."""
    token = _WATCH.set(_Watch(asked, every))
    try:
        yield
    finally:
        _WATCH.reset(token)
