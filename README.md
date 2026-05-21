# vice-driver

[![CI](https://github.com/anarkiwi/vice-driver/actions/workflows/ci.yml/badge.svg)](https://github.com/anarkiwi/vice-driver/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

`vice-driver` is a Python automation framework for driving
[asid-vice](https://github.com/anarkiwi/asid-vice), an extension of the
VICE C64 emulator that exposes a binary monitor with key-matrix tap and
screen-scrape opcodes.

It is application-agnostic: use it to drive any C64 program inside a
binmon-extended VICE container. (The companion
[`defmon-driver`](https://github.com/anarkiwi/defmon-driver) package
builds defMON-specific automation on top of this library.)

The driver itself is pure-Python with **no runtime dependencies** beyond
the standard library. It speaks the asid-vice binary monitor protocol
over a single TCP socket per connection.

## Capabilities

- **`vice_driver.binmon`** — wire-level binary-monitor client. Handles
  framing, request/response matching by id, asynchronous events (STOPPED
  / RESUMED / JAM), checkpoints (CHECK_EXEC / CHECK_LOAD / CHECK_STORE),
  cpuhistory, mem_get / mem_set, register get/set, keymatrix tap +
  set + get, SCREEN_GET.
- **`vice_driver.keys`** — symbolic C64 key-matrix names (case-insensitive
  with aliases) + ASCII → chord conversion for typing text into programs
  that read the matrix directly.
- **`vice_driver.screen`** — SCREEN_GET response parser + screencode →
  PETSCII → ASCII rendering with `find_text()`.
- **`vice_driver.vice_docker`** — `ViceContainer` context manager that
  spins up a one-shot asid-vice Docker container with the right binmon
  binding, sound dump, SID configuration, and disk mounts.
- **`vice_driver.coverage`** — per-action 6502 code-coverage harness
  built on CHECK_EXEC checkpoints + cpuhistory drains. Byte- or
  page-granularity.
- **`vice_driver.expect`** — `Expect` dataclass + `verify()` polling
  helper for "did this byte change to X within T seconds" assertions.

## Requirements

- Python ≥ 3.10
- Docker, with a built `asid-vice:latest` image — see
  [`anarkiwi/asid-vice`](https://github.com/anarkiwi/asid-vice) for the
  Dockerfile and build instructions.
- A C64 `.d64` (or PRG / TAP) image to autostart.

## Installation

```sh
pip install vice-driver
```

For development:

```sh
git clone https://github.com/anarkiwi/vice-driver
cd vice-driver
pip install -e ".[dev]"
pytest
```

## Quick start

```python
import logging

from vice_driver import BinMon, DiskMount, ViceContainer

logging.basicConfig(level=logging.INFO)

container = ViceContainer(
    autostart="/work/program.d64",
    mounts=[DiskMount("/host/path/to/program.d64", "/work/program.d64", read_only=True)],
)

with container:
    bm = BinMon("127.0.0.1", 6502)
    bm.connect(timeout=10.0, attempts=80, retry_delay=0.25)
    # Drain the initial halt and resume the CPU.
    bm.exit()

    # ... drive the program: bm.keymatrix_tap, bm.mem_get/set, bm.screen_get ...

    bm.close()
```

## Testing

```sh
pytest                      # runs unit tests + lint + format gates
pytest --cov-report=html    # open htmlcov/index.html for the line-level report
```

CI enforces:

- `ruff check` (lint)
- `ruff format --check` (format)
- `black --check` (format, redundant with ruff format but explicit)
- `pytest` (unit tests)
- Coverage ≥ 85% over `vice_driver/`

Run individual gates locally:

```sh
ruff check vice_driver tests
ruff format --check vice_driver tests
black --check vice_driver tests
pytest --cov=vice_driver --cov-fail-under=85
```

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
