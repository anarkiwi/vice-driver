"""Post-action state assertions over a :class:`BinMon` connection.

When driving a 6502 program through asid-vice, the typical pattern is
"perform an action, then assert that some memory byte changed". The
:class:`Expect` dataclass packages the polling predicate and timeout,
and :func:`verify` runs the poll and returns ``(ok, last_byte_seen)``
so the caller can surface the observed value in a clear error message
without an extra mem_get.

These primitives are transport-agnostic: they work whether the action
was a ``keymatrix_tap`` chord, a direct memory write, or a CPU register
set + run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Union

from .binmon import BinMon

# Either an exact u8 to match, or a callable taking the observed byte
# and returning True iff the desired state has been reached.
ExpectPredicate = Union[int, Callable[[int], bool]]


@dataclass(frozen=True)
class Expect:
    """Post-action state assertion.

    Polls the byte at ``addr`` until ``want`` is satisfied or ``timeout``
    expires. ``want`` is either an int (exact match) or a one-arg
    callable taking the observed byte (e.g. ``lambda v: v != prior`` for
    "advanced off the previous value").
    """

    addr: int
    want: ExpectPredicate
    timeout: float = 0.5
    poll_interval: float = 0.05


def _matches(want: ExpectPredicate, observed: int) -> bool:
    if callable(want):
        return bool(want(observed))
    return observed == want


def verify(bm: BinMon, expect: Expect) -> tuple[bool, int]:
    """Poll ``expect.addr`` until ``expect.want`` matches or
    ``expect.timeout`` elapses.

    Returns ``(ok, last_byte_seen)`` — the second element is the byte
    value observed on the final read, so callers can surface it in
    error messages without an extra ``mem_get``.
    """
    deadline = time.monotonic() + expect.timeout
    observed = bm.mem_get(expect.addr, expect.addr)[0]
    if _matches(expect.want, observed):
        return True, observed
    while time.monotonic() < deadline:
        time.sleep(expect.poll_interval)
        observed = bm.mem_get(expect.addr, expect.addr)[0]
        if _matches(expect.want, observed):
            return True, observed
    return False, observed
