"""Unit tests for vice_driver.expect — the Expect dataclass and the
verify() polling helper. No emulator required."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from vice_driver.expect import Expect, verify


@dataclass
class FakeBinMon:
    """Scripts a sequence of bytes returned from mem_get(addr, addr)
    at a single fixed address. Each successive call advances through
    ``sequence`` and clamps to the last value once exhausted, so the
    "first-match-then-stays-matched" case is easy to express."""

    sequence: list[int] = field(default_factory=list)
    addr: int = 0x1234
    reads: int = 0

    def mem_get(self, start: int, end: int) -> bytes:
        assert start == end == self.addr, f"unexpected read at {start:#x}..{end:#x}"
        idx = min(self.reads, len(self.sequence) - 1)
        self.reads += 1
        return bytes([self.sequence[idx]])


def test_verify_exact_match_first_read() -> None:
    bm = FakeBinMon(sequence=[0x42])
    ok, observed = verify(bm, Expect(addr=0x1234, want=0x42, timeout=0.1))  # type: ignore[arg-type]
    assert ok is True
    assert observed == 0x42
    assert bm.reads == 1


def test_verify_exact_match_after_polls() -> None:
    bm = FakeBinMon(sequence=[0, 0, 0x42])
    ok, observed = verify(
        bm,  # type: ignore[arg-type]
        Expect(addr=0x1234, want=0x42, timeout=1.0, poll_interval=0.01),
    )
    assert ok is True
    assert observed == 0x42
    assert bm.reads >= 3


def test_verify_callable_predicate() -> None:
    bm = FakeBinMon(sequence=[0xAA, 0xBB])
    seen: list[int] = []

    def pred(v: int) -> bool:
        seen.append(v)
        return v == 0xBB

    ok, observed = verify(
        bm,  # type: ignore[arg-type]
        Expect(addr=0x1234, want=pred, timeout=1.0, poll_interval=0.01),
    )
    assert ok is True
    assert observed == 0xBB
    # The predicate is invoked on every observation.
    assert seen == [0xAA, 0xBB]


def test_verify_timeout_returns_false_with_last_value() -> None:
    bm = FakeBinMon(sequence=[0x11])  # never matches
    start = time.monotonic()
    ok, observed = verify(
        bm,  # type: ignore[arg-type]
        Expect(addr=0x1234, want=0x42, timeout=0.08, poll_interval=0.02),
    )
    elapsed = time.monotonic() - start
    assert ok is False
    assert observed == 0x11
    # We waited at least the timeout but not pathologically longer.
    assert 0.07 <= elapsed < 0.5


def test_verify_advanced_off_prior_pattern() -> None:
    # "advanced off the previous value" is a common cycle-until pattern.
    bm = FakeBinMon(sequence=[0xD4, 0xD4, 0xD5])
    ok, observed = verify(
        bm,  # type: ignore[arg-type]
        Expect(
            addr=0x1234,
            want=lambda v, _p=0xD4: v != _p,
            timeout=1.0,
            poll_interval=0.01,
        ),
    )
    assert ok is True
    assert observed == 0xD5


def test_expect_is_frozen_dataclass() -> None:
    e = Expect(addr=0x1000, want=0x00)
    # The frozen dataclass guarantees hashability + immutability so
    # Expect instances can be stored in sets / dict keys.
    assert hash(e) == hash(Expect(addr=0x1000, want=0x00))
    try:
        e.timeout = 5.0  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("Expect should be frozen")
