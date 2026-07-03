"""Unit tests for vice_driver.binmon opcode wrappers and the call() core.

Uses a FakeSocket that scripts the bytes the asid-vice server would send,
so every protocol-level path is exercised without a real container.
"""

from __future__ import annotations

import socket
import struct
from typing import Optional

import pytest

from vice_driver import binmon
from vice_driver.binmon import (
    API_VERSION,
    CHECK_EXEC,
    ERR_OK,
    MEMSPACE_MAIN,
    OPCODE,
    RELEASE_NONE,
    RELEASE_OBSERVED,
    RELEASE_TIMEOUT,
    STX,
    TAP_MODE_FIXED,
    TAP_MODE_OBSERVED,
    BinMon,
    BinmonError,
    BinmonResponse,
    CpuHistoryRecord,
)

# VICE register IDs (monitor.h) — not exported from binmon but used here.
REG_PC = 3
REG_FLAGS = 5


class FakeSocket:
    """Minimal sockets-API stand-in.

    ``recv`` drains from ``self.recv_queue``; if the queue is empty, raises
    ``socket.timeout`` (matches the real socket's behaviour when the
    server hasn't sent anything in the current settimeout window).

    ``sendall`` records bytes into ``self.sent`` for assertion.
    """

    def __init__(self) -> None:
        self.sent = bytearray()
        self.recv_queue = bytearray()
        self._timeout: Optional[float] = None
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, n: int) -> bytes:
        if not self.recv_queue:
            raise socket.timeout("FakeSocket: no scripted bytes")
        chunk = bytes(self.recv_queue[:n])
        del self.recv_queue[:n]
        return chunk

    def settimeout(self, t: Optional[float]) -> None:
        self._timeout = t

    def gettimeout(self) -> Optional[float]:
        return self._timeout

    def close(self) -> None:
        self.closed = True


def _resp_bytes(req_id: int, opcode: int, body: bytes = b"", err: int = ERR_OK) -> bytes:
    return struct.pack("<BBIBBI", STX, API_VERSION, len(body), opcode, err, req_id) + body


def _parse_header(buf: bytes) -> tuple[int, int, int, int, int]:
    """Decode an outbound request header (11 bytes)."""
    return struct.unpack("<BBIIB", buf[:11])  # stx, ver, body_len, req_id, opcode


def _make_bm() -> tuple[BinMon, FakeSocket]:
    """Construct a BinMon wired to a FakeSocket. auto_resume=False so we
    don't have to script EXIT responses for every wrapper call."""
    bm = BinMon(auto_resume=False)
    s = FakeSocket()
    bm._sock = s
    return bm, s


# ---- _next_req --------------------------------------------------------------


def test_next_req_starts_at_1_and_wraps_past_zero() -> None:
    bm, _ = _make_bm()
    assert bm._next_req() == 1
    assert bm._next_req() == 2
    bm._req_id = 0xFFFFFFFF  # next increment wraps to 0, then forced to 1
    assert bm._next_req() == 1


# ---- low-level framing ------------------------------------------------------


def test_send_then_read_response_round_trips_header() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.PING))
    resp = bm.call(OPCODE.PING)
    assert isinstance(resp, BinmonResponse)
    assert resp.opcode == OPCODE.PING
    assert resp.err == ERR_OK
    # Outbound header is 11 bytes.
    stx, ver, body_len, req_id, opcode = _parse_header(bytes(s.sent))
    assert stx == STX
    assert ver == API_VERSION
    assert body_len == 0
    assert req_id == 1
    assert opcode == OPCODE.PING


def test_recv_exact_raises_on_closed_socket() -> None:
    bm, s = _make_bm()

    # Empty recv (no bytes scripted, no timeout window scripted) → socket.timeout
    # — _recv_exact only raises on EOF (b"" from recv), but FakeSocket raises
    # timeout when empty. So inject an EOF-style return.
    class EofSocket(FakeSocket):
        def recv(self, n: int) -> bytes:
            return b""

    bm._sock = EofSocket()
    with pytest.raises(BinmonError, match="socket closed"):
        bm._recv_exact(4)


def test_read_response_rejects_bad_framing() -> None:
    bm, s = _make_bm()
    # Construct a header with wrong STX byte.
    bad = bytes([0xAA, API_VERSION, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0])
    s.recv_queue.extend(bad)
    with pytest.raises(BinmonError, match="bad framing"):
        bm._read_response()


def test_call_raises_on_error_response() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.PING, err=0x80))
    with pytest.raises(BinmonError, match="returned err"):
        bm.call(OPCODE.PING)


def test_call_routes_event_to_on_event_callback() -> None:
    events: list[BinmonResponse] = []
    bm = BinMon(auto_resume=False, on_event=lambda r: events.append(r))
    s = FakeSocket()
    bm._sock = s
    # Server sends an event (req_id=0xFFFFFFFF) before our reply (req_id=1).
    s.recv_queue.extend(_resp_bytes(req_id=0xFFFFFFFF, opcode=OPCODE.PING))
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.PING))
    bm.call(OPCODE.PING)
    assert len(events) == 1
    assert events[0].req_id == 0xFFFFFFFF


def test_call_event_callback_exceptions_are_swallowed() -> None:
    def boom(_r: BinmonResponse) -> None:
        raise RuntimeError("event handler exploded")

    bm = BinMon(auto_resume=False, on_event=boom)
    s = FakeSocket()
    bm._sock = s
    s.recv_queue.extend(_resp_bytes(req_id=0xFFFFFFFF, opcode=OPCODE.PING))
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.PING))
    # Should NOT raise — the wrapper logs and continues.
    bm.call(OPCODE.PING)


# ---- halted() context manager ----------------------------------------------


def test_halted_temporarily_disables_auto_resume() -> None:
    bm, _ = _make_bm()
    bm.auto_resume = True
    with bm.halted():
        assert bm.auto_resume is False
    assert bm.auto_resume is True


# ---- mem_get / mem_set ------------------------------------------------------


def test_mem_get_packs_request_and_parses_response() -> None:
    bm, s = _make_bm()
    payload = b"\x11\x22\x33"
    body = struct.pack("<H", len(payload)) + payload
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.MEM_GET, body=body))
    out = bm.mem_get(0x1F00, 0x1F02)
    assert out == payload
    # Outbound body is "<BHHBH" = side_effects + start + end + memspace + bank.
    req_body = bytes(s.sent[11:])
    side_effects, start, end, memspace, bank = struct.unpack("<BHHBH", req_body)
    assert (side_effects, start, end, memspace, bank) == (0, 0x1F00, 0x1F02, 0, 0)


def test_mem_get_rejects_short_response() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.MEM_GET, body=b"\x00"))
    with pytest.raises(BinmonError, match="short response"):
        bm.mem_get(0x0, 0x0)


def test_mem_set_skips_empty_data() -> None:
    bm, s = _make_bm()
    bm.mem_set(0x1000, b"")
    # No request should have been sent.
    assert s.sent == bytearray()


def test_mem_set_packs_data_payload() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.MEM_SET))
    bm.mem_set(0x1000, b"\xaa\xbb\xcc")
    req_body = bytes(s.sent[11:])
    # Header (5 fields) + data
    header_len = struct.calcsize("<BHHBH")
    assert req_body[header_len:] == b"\xaa\xbb\xcc"


# ---- registers_get / registers_set -----------------------------------------


def test_registers_get_parses_register_block() -> None:
    bm, s = _make_bm()
    # Response: 6 registers, each (sz=3, id, value:u16).
    n = 6
    body = struct.pack("<H", n)
    for rid in range(n):
        body += struct.pack("<BBH", 3, rid, rid * 0x10)
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.REGISTERS_GET, body=body))
    regs = bm.registers_get()
    assert regs == {i: i * 0x10 for i in range(n)}


def test_registers_get_rejects_short_response() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.REGISTERS_GET, body=b"\x00"))
    with pytest.raises(BinmonError, match="short"):
        bm.registers_get()


def test_registers_set_packs_each_register() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.REGISTERS_SET))
    bm.registers_set({REG_PC: 0xABCD, REG_FLAGS: 0x20})
    req_body = bytes(s.sent[11:])
    memspace, n = struct.unpack_from("<BH", req_body)
    assert memspace == 0
    assert n == 2
    # Two register entries of 4 bytes each follow.
    entries = req_body[3:]
    assert len(entries) == 8


# ---- exit / reset / ping ----------------------------------------------------


def test_exit_sends_only_exit_opcode() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.EXIT))
    bm.exit()
    _, _, _, _, opcode = _parse_header(bytes(s.sent))
    assert opcode == OPCODE.EXIT


def test_reset_passes_mode_byte() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.RESET))
    bm.reset(mode=1)
    assert bytes(s.sent[11:]) == bytes([1])


def test_ping_round_trips() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.PING))
    bm.ping()


# ---- advance_instructions / run_until_pc -----------------------------------


def test_advance_instructions_packs_count_and_step_over() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.ADVANCE_INSTRUCTIONS))
    bm.advance_instructions(count=5, step_over_subroutines=True)
    req_body = bytes(s.sent[11:])
    step_over, count = struct.unpack("<BH", req_body)
    assert step_over == 1
    assert count == 5


def test_run_until_pc_fast_path_when_already_at_target() -> None:
    bm, s = _make_bm()
    # registers_get response: PC = target.
    body = struct.pack("<H", 1) + struct.pack("<BBH", 3, REG_PC, 0x1234)
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.REGISTERS_GET, body=body))
    bm.run_until_pc(0x1234)
    # Only one request should have been sent (REGISTERS_GET); no checkpoint.
    assert len(s.sent) == 11 + 1  # header + memspace byte


# ---- checkpoints -----------------------------------------------------------


def _checkpoint_info_body(checknum: int, hit: bool = False) -> bytes:
    return struct.pack(
        "<I B HH BBBB II BB",
        checknum,
        int(hit),
        0x1000,
        0x1000,
        0,  # stop_when_hit
        1,  # enabled
        CHECK_EXEC,
        0,  # temporary
        0,  # hit_count
        0,  # ignore_count
        0,  # has_condition
        MEMSPACE_MAIN,
    )


def test_checkpoint_set_parses_info_response() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(
        _resp_bytes(req_id=1, opcode=OPCODE.CHECKPOINT_SET, body=_checkpoint_info_body(42))
    )
    cp = bm.checkpoint_set(0x1000)
    assert cp.checknum == 42
    assert cp.start == 0x1000
    assert cp.op == CHECK_EXEC
    assert cp.enabled is True


def test_checkpoint_set_defaults_end_to_start() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(
        _resp_bytes(req_id=1, opcode=OPCODE.CHECKPOINT_SET, body=_checkpoint_info_body(1))
    )
    bm.checkpoint_set(0x2000)
    req_body = bytes(s.sent[11:])
    start, end = struct.unpack_from("<HH", req_body)
    assert start == end == 0x2000


def test_checkpoint_get_delete_toggle() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(
        _resp_bytes(req_id=1, opcode=OPCODE.CHECKPOINT_GET, body=_checkpoint_info_body(7))
    )
    s.recv_queue.extend(_resp_bytes(req_id=2, opcode=OPCODE.CHECKPOINT_TOGGLE))
    s.recv_queue.extend(_resp_bytes(req_id=3, opcode=OPCODE.CHECKPOINT_DELETE))
    assert bm.checkpoint_get(7).checknum == 7
    bm.checkpoint_toggle(7, enabled=False)
    bm.checkpoint_delete(7)


def test_checkpoint_set_parse_rejects_short_body() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.CHECKPOINT_SET, body=b"\x00" * 10))
    with pytest.raises(BinmonError, match="checkpoint info short"):
        bm.checkpoint_set(0x0)


def test_checkpoint_list_collects_infos_until_terminator() -> None:
    bm, s = _make_bm()
    # Two CHECKPOINT_INFO responses sharing req_id=1, then the LIST terminator.
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=0x11, body=_checkpoint_info_body(1)))
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=0x11, body=_checkpoint_info_body(2)))
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=0x14))
    result = bm.checkpoint_list()
    assert [c.checknum for c in result] == [1, 2]


# ---- keymatrix -------------------------------------------------------------


def test_keymatrix_set_packs_each_key() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.KEYMATRIX_SET))
    bm.keymatrix_set([(1, 4, 1), (2, 7, 0)])
    req_body = bytes(s.sent[11:])
    # u8 n_keys followed by n_keys × (s8 row, s8 col, u8 value).
    assert req_body[0] == 2
    keys = list(struct.iter_unpack("<bbB", req_body[1:]))
    assert keys == [(1, 4, 1), (2, 7, 0)]


def test_keymatrix_release_all_sends_all_64_keys() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.KEYMATRIX_SET))
    bm.keymatrix_release_all()
    req_body = bytes(s.sent[11:])
    # The release_all implementation enumerates every (row, col) in the
    # 8×8 matrix with value=0 — count byte is 64.
    assert req_body[0] == 64
    # Every value byte must be zero (i.e. release).
    keys = list(struct.iter_unpack("<bbB", req_body[1:]))
    assert all(v == 0 for _r, _c, v in keys)


def test_keymatrix_tap_packs_mode_and_frames() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.KEYMATRIX_TAP))
    bm.keymatrix_tap([(1, 4)], mode=TAP_MODE_FIXED, frames=12)
    req_body = bytes(s.sent[11:])
    # u8 mode + u16 frames + u8 n_keys + n_keys × (s8 row, s8 col).
    mode, frames, n = struct.unpack_from("<BHB", req_body)
    assert mode == TAP_MODE_FIXED
    assert frames == 12
    assert n == 1
    assert tuple(req_body[4:6]) == (1, 4)


def test_keymatrix_get_parses_response() -> None:
    bm, s = _make_bm()
    # Response body layout: keyarr[8], custom, 3 padding, cia1_total u32,
    # cia1_sampled u32, release_reason u8, n_keys u8, frames_to u16. = 24
    keyarr = bytes(range(8))
    body = (
        keyarr
        + bytes([0xFF])  # custom-key state
        + b"\x00\x00\x00"  # padding to align cia1 counters
        + struct.pack("<II", 12345, 7)
        + struct.pack("<BBH", RELEASE_OBSERVED, 1, 0)
    )
    assert len(body) == 24
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.KEYMATRIX_GET, body=body))
    out = bm.keymatrix_get()
    assert out.keyarr == keyarr
    assert out.cia1_reads_total == 12345
    assert out.cia1_reads_sampling == 7
    assert out.release_reason == RELEASE_OBSERVED
    assert out.n_keys == 1


def test_keymatrix_get_rejects_short_response() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.KEYMATRIX_GET, body=b"\x00" * 10))
    with pytest.raises(BinmonError, match="short"):
        bm.keymatrix_get()


def test_release_reason_constants_distinct() -> None:
    # Sanity: the three release-reason values are distinct.
    assert len({RELEASE_NONE, RELEASE_OBSERVED, RELEASE_TIMEOUT}) == 3
    # And the two tap modes also differ.
    assert TAP_MODE_FIXED != TAP_MODE_OBSERVED


# ---- screen_get ------------------------------------------------------------


def test_screen_get_returns_body_bytes() -> None:
    bm, s = _make_bm()
    body = bytes(range(64))
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.SCREEN_GET, body=body))
    out = bm.screen_get()
    assert out == body


# ---- display_get / palette_get ---------------------------------------------


def test_display_get_returns_body_and_sends_request() -> None:
    bm, s = _make_bm()
    body = bytes(range(64))
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.DISPLAY_GET, body=body))
    out = bm.display_get()
    assert out == body
    _, _, body_len, _, opcode = _parse_header(bytes(s.sent))
    assert opcode == OPCODE.DISPLAY_GET
    assert bytes(s.sent)[11:] == bytes([1, 0])  # use_vic=1, format=indexed


def test_palette_get_returns_body() -> None:
    bm, s = _make_bm()
    body = bytes([2, 0]) + bytes([3, 1, 2, 3]) + bytes([3, 4, 5, 6])
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.PALETTE_GET, body=body))
    out = bm.palette_get(use_vic=False)
    assert out == body
    assert bytes(s.sent)[11:] == bytes([0])  # use_vic=0


# ---- video_record / video_stop ----------------------------------------------


def test_video_record_packs_start_flag_and_path() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.VIDEO_RECORD))
    bm.video_record("/renders/out.avi")
    body = bytes(s.sent[11:])
    assert body[0] == 1  # action=start
    assert body[1] == len(b"/renders/out.avi")
    assert body[2:] == b"/renders/out.avi"
    _, _, _, _, opcode = _parse_header(bytes(s.sent[:11]))
    assert opcode == OPCODE.VIDEO_RECORD


def test_video_record_rejects_empty_path() -> None:
    bm, _ = _make_bm()
    with pytest.raises(BinmonError, match="1..255 bytes"):
        bm.video_record("")


def test_video_record_rejects_long_path() -> None:
    bm, _ = _make_bm()
    with pytest.raises(BinmonError, match="1..255 bytes"):
        bm.video_record("/" + "x" * 300)


def test_video_stop_sends_stop_flag_only() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.VIDEO_RECORD))
    bm.video_stop()
    body = bytes(s.sent[11:])
    assert body == bytes([0])
    _, _, _, _, opcode = _parse_header(bytes(s.sent[:11]))
    assert opcode == OPCODE.VIDEO_RECORD


# ---- close + context manager -----------------------------------------------


def test_close_zeroes_sock_attribute() -> None:
    bm, s = _make_bm()
    bm.close()
    assert bm._sock is None
    assert s.closed is True


def test_close_is_noop_when_already_closed() -> None:
    bm = BinMon()
    bm.close()  # never connected, nothing to close.
    assert bm._sock is None


# ---- connect retries -------------------------------------------------------


def test_connect_raises_when_all_attempts_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_a, **_kw):
        raise OSError("connection refused")

    monkeypatch.setattr(binmon.socket, "socket", fail_socket)
    bm = BinMon()
    with pytest.raises(BinmonError, match="could not connect"):
        bm.connect(timeout=0.01, attempts=2, retry_delay=0.0)


# ---- run_until_pc slow path -------------------------------------------------


def test_run_until_pc_installs_checkpoint_and_waits_for_hit() -> None:
    bm, s = _make_bm()
    target = 0xABCD
    # registers_get reply: PC != target (so fast-path skipped).
    body = struct.pack("<H", 1) + struct.pack("<BBH", 3, REG_PC, 0x0000)
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.REGISTERS_GET, body=body))
    # checkpoint_set reply: 23-byte CHECKPOINT_INFO body.
    cp_body = struct.pack(
        "<I B HH BBBB II BB", 99, 0, target, target, 1, 1, CHECK_EXEC, 1, 0, 0, 0, MEMSPACE_MAIN
    )
    s.recv_queue.extend(_resp_bytes(req_id=2, opcode=OPCODE.CHECKPOINT_SET, body=cp_body))
    # EXIT ack.
    s.recv_queue.extend(_resp_bytes(req_id=3, opcode=OPCODE.EXIT))
    # CHECKPOINT_INFO event (req_id=0xFFFFFFFF) with hit=True (byte 4 of body).
    info_body = struct.pack(
        "<I B HH BBBB II BB", 99, 1, target, target, 1, 1, CHECK_EXEC, 1, 1, 0, 0, MEMSPACE_MAIN
    )
    s.recv_queue.extend(_resp_bytes(req_id=0xFFFFFFFF, opcode=0x11, body=info_body))
    # STOPPED event.
    s.recv_queue.extend(_resp_bytes(req_id=0xFFFFFFFF, opcode=OPCODE.STOPPED))
    # CHECKPOINT_DELETE ack.
    s.recv_queue.extend(_resp_bytes(req_id=4, opcode=OPCODE.CHECKPOINT_DELETE))
    bm.run_until_pc(target, timeout=1.0)


def test_run_until_pc_fast_path_only_reads_registers() -> None:
    bm, s = _make_bm()
    target = 0x1234
    # registers_get reply has PC=target, so fast-path returns immediately.
    body = struct.pack("<H", 1) + struct.pack("<BBH", 3, REG_PC, target)
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.REGISTERS_GET, body=body))
    bm.run_until_pc(target)
    # Only one opcode was sent (REGISTERS_GET).
    _, _, _, _, opcode = _parse_header(bytes(s.sent[:11]))
    assert opcode == OPCODE.REGISTERS_GET
    # No subsequent header was sent.
    assert len(s.sent) == 11 + 1  # header + memspace byte


# ---- autostart / resources / drive attach ----------------------------------


def test_autostart_encodes_path_and_run_flag() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.AUTOSTART))
    bm.autostart("/work/foo.d64", run_after=True, file_index=0)
    req_body = bytes(s.sent[11:])
    run_after, file_index, path_len = struct.unpack_from("<BHB", req_body)
    assert run_after == 1
    assert file_index == 0
    assert path_len == len(b"/work/foo.d64")
    assert req_body[4:] == b"/work/foo.d64"


def test_autostart_accepts_bytes_path() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.AUTOSTART))
    bm.autostart(b"raw-petscii-bytes", run_after=False)
    req_body = bytes(s.sent[11:])
    assert req_body[0] == 0  # run_after=False


def test_resource_set_string_packs_type_name_and_value() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.RESOURCE_SET))
    bm.resource_set_string("WarpMode", "1")
    body = bytes(s.sent[11:])
    assert body[0] == 0x00  # type=string
    name_len = body[1]
    assert body[2 : 2 + name_len] == b"WarpMode"
    val_len = body[2 + name_len]
    assert val_len == 1
    assert body[3 + name_len :] == b"1"


def test_resource_set_int_packs_u32_le() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.RESOURCE_SET))
    bm.resource_set_int("DriveTrueEmulation", 0x12345678)
    body = bytes(s.sent[11:])
    assert body[0] == 0x01  # type=int
    name_len = body[1]
    assert body[2 + name_len] == 4  # value length
    val = struct.unpack_from("<I", body, 3 + name_len)[0]
    assert val == 0x12345678


def test_attach_drive_packs_unit_drive_and_path() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.DRIVE_ATTACH))
    bm.attach_drive("/host/x.d64", unit=8, drive=0)
    body = bytes(s.sent[11:])
    assert body[0] == 8
    assert body[1] == 0
    assert body[2] == len(b"/host/x.d64")
    assert body[3:] == b"/host/x.d64"


def test_attach_drive_rejects_long_path() -> None:
    bm, _ = _make_bm()
    with pytest.raises(BinmonError, match="path too long"):
        bm.attach_drive("/" + "x" * 300)


def test_detach_drive_sends_zero_length_path() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.DRIVE_ATTACH))
    bm.detach_drive(unit=9, drive=1)
    body = bytes(s.sent[11:])
    assert body == bytes([9, 1, 0])


def test_flush_drive_is_an_attach_alias() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.DRIVE_ATTACH))
    bm.flush_drive("/host/x.d64")
    # Same outgoing opcode as attach_drive.
    _, _, _, _, opcode = _parse_header(bytes(s.sent[:11]))
    assert opcode == OPCODE.DRIVE_ATTACH


# ---- cpuhistory_get parsing ------------------------------------------------


def _cpuhistory_record_payload(pc: int, cycle: int) -> bytes:
    """Pack a single cpuhistory record payload (without the leading
    item_size byte). One register entry: PC (reg id 3, value u16).

    Per the binmon parser, each register entry is ``u8 size + u8 id +
    u16 value`` where ``size`` is the byte count of (id + value) — i.e.
    3 for a u16-valued register.
    """
    # register_block_count (u16) + 1 register × (sz=3 + id=3 + value u16)
    reg_block = struct.pack("<H", 1) + struct.pack("<BBH", 3, 3, pc & 0xFFFF)
    # cycle u64 + instruction_length(=4) + op + p1 + p2 + 0xff placeholder
    trailer = struct.pack("<QB", cycle, 4) + bytes([0, 0, 0, 0xFF])
    return reg_block + trailer


def test_cpuhistory_get_parses_records() -> None:
    bm, s = _make_bm()
    rec1 = _cpuhistory_record_payload(pc=0x1000, cycle=100)
    rec2 = _cpuhistory_record_payload(pc=0x2000, cycle=200)
    body = struct.pack("<I", 2) + bytes([len(rec1)]) + rec1 + bytes([len(rec2)]) + rec2
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.CPUHISTORY_GET, body=body))
    recs = bm.cpuhistory_get(count=2)
    assert len(recs) == 2
    assert recs[0].pc == 0x1000
    assert recs[0].cycle == 100
    assert recs[1].pc == 0x2000


def test_cpuhistory_get_rejects_out_of_range_count() -> None:
    bm, _ = _make_bm()
    with pytest.raises(BinmonError, match="out of range"):
        bm.cpuhistory_get(count=0)
    with pytest.raises(BinmonError, match="out of range"):
        bm.cpuhistory_get(count=0x10000)


def test_cpuhistory_get_rejects_short_response() -> None:
    bm, s = _make_bm()
    s.recv_queue.extend(_resp_bytes(req_id=1, opcode=OPCODE.CPUHISTORY_GET, body=b"\x00"))
    with pytest.raises(BinmonError, match="cpuhistory short"):
        bm.cpuhistory_get()


# ---- cpuhistory record properties ------------------------------------------


def test_cpuhistory_record_register_accessors() -> None:
    rec = CpuHistoryRecord(
        registers={0: 0xAA, 1: 0xBB, 2: 0xCC, 3: 0x1234, 4: 0xDD, 5: 0xEE},
        cycle=42,
        op=0,
        p1=0,
        p2=0,
    )
    assert rec.a == 0xAA
    assert rec.x == 0xBB
    assert rec.y == 0xCC
    assert rec.pc == 0x1234
    assert rec.sp == 0xDD
    assert rec.flags == 0xEE
