"""Unit tests for the pure-python helpers in vice_driver.coverage.

Covers ``Coverage.diff_hits`` (which is a static-ish hit-delta computer),
``aggregate``, ``union_pcs``, and the constructor's input validation.
All BinMon interactions are mocked out — no socket / no docker."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest

from vice_driver.binmon import Checkpoint, CpuHistoryRecord
from vice_driver.coverage import ActionCoverage, Coverage, aggregate, union_pcs


def _cov() -> Coverage:
    """Build a Coverage without invoking install(). bm is unused for the
    pure helpers below; pass None and rely on the helpers not touching it."""
    return Coverage(bm=None, granularity="page")  # type: ignore[arg-type]


# ---- diff_hits --------------------------------------------------------


def test_diff_hits_returns_positive_deltas_only() -> None:
    cov = _cov()
    before = {0x10: 5, 0x11: 10, 0x12: 0}
    after = {0x10: 8, 0x11: 10, 0x12: 7}
    diff = cov.diff_hits(before, after)
    assert diff == {0x10: 3, 0x12: 7}


def test_diff_hits_drops_zero_deltas() -> None:
    cov = _cov()
    diff = cov.diff_hits({0x42: 100}, {0x42: 100})
    assert diff == {}


def test_diff_hits_drops_negative_deltas() -> None:
    # Negative deltas should not appear (hit_counts are monotone) but if
    # the table churns the helper defends.
    cov = _cov()
    diff = cov.diff_hits({0x42: 5}, {0x42: 3})
    assert diff == {}


def test_diff_hits_handles_missing_after_key() -> None:
    cov = _cov()
    # Key missing from after = treated as 0; negative delta = dropped.
    diff = cov.diff_hits({0x42: 1}, {})
    assert diff == {}


# ---- constructor validation ------------------------------------------


def test_constructor_rejects_unknown_granularity() -> None:
    with pytest.raises(ValueError, match="granularity"):
        Coverage(bm=None, granularity="wrong")  # type: ignore[arg-type]


def test_constructor_rejects_misaligned_start_in_page_mode() -> None:
    with pytest.raises(ValueError, match="page-aligned"):
        Coverage(bm=None, start=0x1001, end=0x10FF, granularity="page")  # type: ignore[arg-type]


def test_constructor_rejects_misaligned_end_in_page_mode() -> None:
    # end=0x10FE is not page-aligned (end+1 must have low byte 0).
    with pytest.raises(ValueError, match="page-aligned"):
        Coverage(bm=None, start=0x1000, end=0x10FE, granularity="page")  # type: ignore[arg-type]


def test_constructor_accepts_page_aligned_byte_mode() -> None:
    # Byte mode doesn't require page alignment.
    cov = Coverage(bm=None, start=0x1234, end=0x12FF, granularity="byte")  # type: ignore[arg-type]
    assert cov.granularity == "byte"


def test_constructor_rejects_end_le_start() -> None:
    with pytest.raises(ValueError, match="exceed"):
        Coverage(bm=None, start=0x2000, end=0x2000, granularity="byte")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceed"):
        Coverage(bm=None, start=0x2000, end=0x1FFF, granularity="byte")  # type: ignore[arg-type]


def test_constructor_rejects_oob_history_count() -> None:
    with pytest.raises(ValueError, match="history_count"):
        Coverage(bm=None, history_count=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="history_count"):
        Coverage(bm=None, history_count=0x10000)  # type: ignore[arg-type]


# ---- page_count / page_ids in byte mode without install ----------------


def test_page_count_in_byte_mode_spans_address_range() -> None:
    cov = Coverage(bm=None, start=0x1000, end=0x10FF, granularity="byte")  # type: ignore[arg-type]
    # One page in the range $10xx.
    assert cov.page_count == 1


def test_page_count_in_byte_mode_multi_page() -> None:
    cov = Coverage(bm=None, start=0x1000, end=0x12FF, granularity="byte")  # type: ignore[arg-type]
    # Pages $10, $11, $12.
    assert cov.page_count == 3


# ---- aggregate / union_pcs --------------------------------------------


def _ac(name: str, page_hits: dict[int, int], pcs: set[int]) -> ActionCoverage:
    return ActionCoverage(
        name=name,
        page_hits=page_hits,
        total_hits=sum(page_hits.values()),
        cpuhistory_pcs=frozenset(pcs),
        cycles_elapsed=0,
        history_records=0,
        executed_pcs=frozenset(pcs),
    )


def test_aggregate_sums_page_hits_across_actions() -> None:
    out = aggregate(
        [
            _ac("a", {0x10: 1, 0x20: 4}, set()),
            _ac("b", {0x10: 2, 0x30: 5}, set()),
        ]
    )
    assert out == {0x10: 3, 0x20: 4, 0x30: 5}


def test_aggregate_empty() -> None:
    assert aggregate([]) == {}


def test_union_pcs_prefers_executed_pcs() -> None:
    a = _ac("a", {}, {0x1234, 0x5678})
    b = _ac("b", {}, {0x1234, 0x9ABC})
    assert union_pcs([a, b]) == frozenset({0x1234, 0x5678, 0x9ABC})


def test_union_pcs_falls_back_to_cpuhistory_when_executed_empty() -> None:
    # executed_pcs empty (page mode); cpuhistory_pcs must still be used.
    ac = ActionCoverage(
        name="x",
        page_hits={},
        total_hits=0,
        cpuhistory_pcs=frozenset({0xAA, 0xBB}),
        cycles_elapsed=0,
        history_records=0,
        executed_pcs=frozenset(),
    )
    assert union_pcs([ac]) == frozenset({0xAA, 0xBB})


def test_action_coverage_pages_touched_view() -> None:
    ac = _ac("x", {0x10: 1, 0x42: 9}, set())
    assert ac.pages_touched == frozenset({0x10, 0x42})


# ---- install / snapshot / measure with a fake BinMon -----------------------


@dataclass
class _FakeCheckpoint:
    """A scriptable checkpoint state. ``Coverage`` reads only checknum
    and hit_count from the Checkpoint dataclass we return."""

    checknum: int
    start: int
    end: int
    hit_count: int = 0


@dataclass
class FakeBinMon:
    """Minimal BinMon-shaped stub for testing Coverage end-to-end.

    Tracks checkpoints in an in-memory dict so install/list/delete round-
    trips return sane data. ``hit_count`` for every checkpoint can be
    bumped via ``set_hits``.
    """

    checkpoints: dict[int, _FakeCheckpoint] = field(default_factory=dict)
    next_checknum: int = 1
    cpuhistory_records: list[CpuHistoryRecord] = field(default_factory=list)
    foreign_checkpoint: bool = False  # if True, a stray checkpoint appears in list

    @contextmanager
    def halted(self):
        yield

    def checkpoint_set(
        self,
        start: int,
        end: int | None = None,
        op: int = 0,  # noqa: ARG002
        stop_when_hit: bool = False,  # noqa: ARG002
        enabled: bool = True,  # noqa: ARG002
        temporary: bool = False,  # noqa: ARG002
        memspace: int = 0,  # noqa: ARG002
        silent: bool = False,  # noqa: ARG002
    ) -> Checkpoint:
        cn = self.next_checknum
        self.next_checknum += 1
        self.checkpoints[cn] = _FakeCheckpoint(
            checknum=cn, start=start, end=end if end is not None else start, hit_count=0
        )
        return Checkpoint(
            checknum=cn,
            hit=False,
            start=start,
            end=end if end is not None else start,
            stop_when_hit=stop_when_hit,
            enabled=enabled,
            op=op,
            temporary=temporary,
            hit_count=0,
            ignore_count=0,
            has_condition=False,
            memspace=memspace,
        )

    def checkpoint_delete(self, checknum: int) -> None:
        self.checkpoints.pop(checknum, None)

    def checkpoint_list(self) -> list[Checkpoint]:
        result = [
            Checkpoint(
                checknum=cp.checknum,
                hit=False,
                start=cp.start,
                end=cp.end,
                stop_when_hit=False,
                enabled=True,
                op=0,
                temporary=False,
                hit_count=cp.hit_count,
                ignore_count=0,
                has_condition=False,
                memspace=0,
            )
            for cp in self.checkpoints.values()
        ]
        if self.foreign_checkpoint:
            result.append(
                Checkpoint(
                    checknum=99999,
                    hit=False,
                    start=0x0000,
                    end=0x0000,
                    stop_when_hit=False,
                    enabled=True,
                    op=0,
                    temporary=False,
                    hit_count=0,
                    ignore_count=0,
                    has_condition=False,
                    memspace=0,
                )
            )
        return result

    def cpuhistory_get(
        self,
        count: int = 256,
        memspace: int = 0,  # noqa: ARG002
    ) -> list[CpuHistoryRecord]:
        return list(self.cpuhistory_records[-count:])

    def set_hits(self, mapping: dict[int, int]) -> None:
        """``{checknum: hit_count}`` — overrides the counters before
        the next ``snapshot_hits()`` call."""
        for cn, hc in mapping.items():
            if cn in self.checkpoints:
                self.checkpoints[cn].hit_count = hc


# ---- install / remove --------------------------------------------------


def test_install_page_creates_one_checkpoint_per_page() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x12FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    # 3 pages: $10, $11, $12.
    assert cov.checkpoint_count == 3
    assert cov.page_count == 3
    assert cov.page_ids == (0x10, 0x11, 0x12)


def test_install_byte_creates_one_checkpoint_per_address() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x101F, granularity="byte")  # type: ignore[arg-type]
    cov.install()
    assert cov.checkpoint_count == 0x20


def test_install_twice_raises() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x10FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    with pytest.raises(RuntimeError, match="already installed"):
        cov.install()


def test_remove_deletes_every_installed_checkpoint() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x12FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    cov.remove()
    assert cov.checkpoint_count == 0
    assert bm.checkpoints == {}


def test_remove_drop_only_skips_vice_roundtrip() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x10FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    bm_cp_count_before = len(bm.checkpoints)
    cov.remove(drop_only=True)
    # Python-side map clears...
    assert cov.checkpoint_count == 0
    # ...but VICE-side checkpoints stay.
    assert len(bm.checkpoints) == bm_cp_count_before


def test_remove_is_idempotent() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x10FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    cov.remove()
    cov.remove()  # second call: no-op
    assert cov.checkpoint_count == 0


def test_remove_swallows_checkpoint_delete_errors() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x10FF, granularity="page")  # type: ignore[arg-type]
    cov.install()

    # checkpoint_delete will now raise; remove() must not propagate.
    def boom(_checknum):
        raise RuntimeError("vice gone")

    bm.checkpoint_delete = boom  # type: ignore[method-assign]
    cov.remove()
    assert cov.checkpoint_count == 0


def test_context_manager_installs_and_removes() -> None:
    bm = FakeBinMon()
    with Coverage(bm, start=0x1000, end=0x10FF, granularity="page") as cov:  # type: ignore[arg-type]
        assert cov.checkpoint_count == 1
    assert cov.checkpoint_count == 0


# ---- snapshot_hits / measure ------------------------------------------------


def test_snapshot_hits_reads_each_checkpoint() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x12FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    # Force a couple of pages to have hits.
    bm.set_hits({1: 42, 2: 7, 3: 0})
    snap = cov.snapshot_hits()
    assert snap == {0x10: 42, 0x11: 7, 0x12: 0}


def test_snapshot_hits_ignores_foreign_checkpoints() -> None:
    bm = FakeBinMon(foreign_checkpoint=True)
    cov = Coverage(bm, start=0x1000, end=0x10FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    snap = cov.snapshot_hits()
    # Only the one we installed appears.
    assert set(snap) == {0x10}


def test_snapshot_hits_raises_on_missing_installed_checkpoint() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x12FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    # Simulate VICE losing one of our checkpoints.
    bm.checkpoints.pop(2)
    with pytest.raises(RuntimeError, match="missing pages"):
        cov.snapshot_hits()


def test_measure_returns_action_coverage_byte_mode() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x10FF, granularity="byte")  # type: ignore[arg-type]
    cov.install()
    # All checknums correspond to bytes $1000..$10FF in install order.
    # Bump a few specific addresses.
    target_addrs = [0x1010, 0x1020, 0x1030]
    target_cns = [cn for cn, cp in bm.checkpoints.items() if cp.start in target_addrs]
    assert len(target_cns) == 3

    def action() -> None:
        bm.set_hits({cn: 1 for cn in target_cns})

    ac = cov.measure(action, "x", settle=0)
    assert ac.executed_pcs == frozenset(target_addrs)
    # All hits land on page 0x10.
    assert ac.page_hits == {0x10: 3}
    assert ac.total_hits == 3


def test_measure_raises_without_install() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x10FF, granularity="page")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="not installed"):
        cov.measure(lambda: None, "x", settle=0)


def test_measure_idle_uses_settle_as_duration() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x10FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    ac = cov.measure_idle(duration=0.01)
    assert ac.name == "<idle>"


def test_measure_page_mode_leaves_executed_pcs_empty() -> None:
    bm = FakeBinMon()
    cov = Coverage(bm, start=0x1000, end=0x12FF, granularity="page")  # type: ignore[arg-type]
    cov.install()

    # Bump the page-$10 checkpoint's hit_count during the action, so
    # diff_hits sees a positive delta.
    def action() -> None:
        bm.set_hits({1: 5})

    ac = cov.measure(action, "p", settle=0)
    assert ac.executed_pcs == frozenset()
    assert ac.page_hits == {0x10: 5}


def test_measure_uses_cpuhistory_when_available() -> None:
    bm = FakeBinMon()
    bm.cpuhistory_records = [
        CpuHistoryRecord(registers={3: 0xABCD}, cycle=100, op=0, p1=0, p2=0),
        CpuHistoryRecord(registers={3: 0x1234}, cycle=200, op=0, p1=0, p2=0),
    ]
    cov = Coverage(bm, start=0x1000, end=0x12FF, granularity="page")  # type: ignore[arg-type]
    cov.install()
    ac = cov.measure(lambda: None, "p", settle=0)
    # The two history PCs from immediate+after drains; PCs are register id 3.
    assert {0xABCD, 0x1234}.issubset(ac.cpuhistory_pcs)
    assert ac.cycles_elapsed >= 0
