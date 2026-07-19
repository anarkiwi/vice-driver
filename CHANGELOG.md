# Changelog

All notable changes to `vice-driver` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses semantic-versioning-ish tags but the v0.x line is
still pre-stable.

## [0.4.1] — checkpoint-scoped run_until_pc wait

### Fixed

- `run_until_pc` accepted a hit event from ANY checkpoint, so a stale event from a
  previous call satisfied the current wait and returned with the CPU still running;
  the next command then halted it at a host-timing-dependent point. The wait now
  matches its own checknum. Note asid-vice's checkpoint stop is itself asynchronous
  (the CPU halts at a varying PC after a hit), so exact stop position still cannot be
  guaranteed client-side.

## [0.4.0] — published X11 image

### Changed

- `ViceContainer.image` now defaults to the published
  `anarkiwi/asid-vice:latest` (was the locally built `asid-vice:latest`).
  That image is the full GTK3 (X11) build whose ENTRYPOINT is an `Xvfb`
  wrapper (`exec "$@"`), so `start()` now prepends the emulator binary to
  the flags; binmon driving still needs no host X server. New
  `ViceContainer.emulator` field (default `x64sc`) selects the binary —
  set it to `x128`, `xvic`, etc. An explicit `entrypoint=` override still
  suppresses the prepend for shell-entrypoint images.

## [0.3.2] — video recording

### Added

- `BinMon.video_record(path)` / `BinMon.video_stop()` — opcode 0x79
  wrappers driving VICE's native ZMBV movie recorder: lossless video
  inside an AVI container, no external ffmpeg dependency. Recording
  forces warp mode off (VICE skips encoding while warping);
  `video_stop()` finalizes the file and restores the prior warp state.

## [0.3.0] — display framebuffer grab

### Added

- `vice_driver.display` — DISPLAY_GET / PALETTE_GET parsing and true-colour
  framebuffer extraction. `parse_display_response` / `parse_palette_response`
  decode the responses; `DisplaySnapshot.to_rgb()` / `.save_png()` render the
  frame (optionally cropping the border); `write_png()` is a standalone
  stdlib-only PNG encoder. Captures VICE's own rendered display — border,
  sprites, raster/FLD effects, any video mode — unlike the text-only
  SCREEN_GET path, and keeps the package dependency-free (uses `zlib`).
- `BinMon.display_get()` / `BinMon.palette_get()` — opcode wrappers for
  0x84 / 0x91.

## [0.2.0] — entrypoint override

### Added

- `ViceContainer.entrypoint` field. When set, passed through to
  `docker run --entrypoint`. Enables driving images whose default
  `ENTRYPOINT` is not `x64sc` (e.g. `anarkiwi/headlessvice`, whose
  default entrypoint is `/bin/bash`).

## [0.1.0] — initial release

### Added

- `vice_driver.binmon` — asid-vice binary-monitor wire client. Single
  TCP socket per connection, request/response matching by id,
  asynchronous event surface (STOPPED / RESUMED / JAM). Wraps every
  documented opcode: mem_get / mem_set, registers_get / registers_set,
  exit / advance, checkpoint set / list / delete / toggle,
  cpuhistory_get, palette_get, keymatrix_tap / set / get, screen_get.
- `vice_driver.keys` — C64 key-matrix table mirroring asid-vice's
  ``mon_keymatrix.c``. Case-insensitive lookup with alias support
  (SHIFT→LSHIFT, BACKSPACE→DEL, etc.). ``canonical_name(name)`` for
  alias normalisation. ``text_to_chords(text)`` converts a printable
  ASCII string to a sequence of key-name chords.
- `vice_driver.screen` — ``SCREEN_GET`` response parser, screencode →
  PETSCII → ASCII conversion, ``ScreenSnapshot`` with ``text()``,
  ``find_text()``, ``contains()`` helpers.
- `vice_driver.vice_docker` — ``ViceContainer`` context manager and
  ``DiskMount`` dataclass. Spawns a one-shot asid-vice container with
  configurable binmon port, sound device + dump path, SID extras
  configuration, warp / silent / truedrive flags, and host disk mounts.
- `vice_driver.coverage` — per-action 6502 code-coverage harness.
  Byte-granular (one CHECK_EXEC per byte) or page-granular (one per
  256-byte page) installation. ``Coverage.measure(action, name)``
  records hit deltas, cpuhistory PCs, and cycle counts; ``ActionCoverage``
  records can be ``aggregate``d across runs.
- `vice_driver.expect` — ``Expect`` dataclass (addr + predicate +
  timeout + poll interval) and ``verify(bm, expect) -> (ok, last_byte)``
  helper for post-action state assertions.

### CI

GitHub Actions workflow runs on every push and PR:

- ``ruff check`` (lint)
- ``ruff format --check`` (format)
- ``black --check`` (format)
- ``pytest --cov=vice_driver --cov-fail-under=85`` (unit tests + 85%
  branch coverage gate)
- Python matrix: 3.10 / 3.11 / 3.12
