# Changelog

All notable changes to `vice-driver` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses semantic-versioning-ish tags but the v0.x line is
still pre-stable.

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
