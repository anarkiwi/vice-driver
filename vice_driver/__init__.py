"""vice-driver — Python automation framework for driving asid-vice (a binmon-
extended C64 emulator).

Public surface:

  * :mod:`vice_driver.binmon` — wire-level binary-monitor client + the
    asid-vice keymatrix / screen-scrape / cpuhistory extensions.
  * :mod:`vice_driver.keys` — symbolic C64 key-matrix names and
    ASCII → chord conversion.
  * :mod:`vice_driver.screen` — SCREEN_GET response parsing + screencode →
    ASCII rendering.
  * :mod:`vice_driver.display` — DISPLAY_GET / PALETTE_GET parsing +
    true-colour framebuffer extraction and a stdlib-only PNG writer.
  * :mod:`vice_driver.vice_docker` — one-shot container management for
    the asid-vice Docker image.
  * :mod:`vice_driver.coverage` — per-action code-coverage harness using
    CHECK_EXEC checkpoints + cpuhistory drains.
  * :mod:`vice_driver.expect` — :class:`Expect` predicate and
    :func:`verify` polling helper for post-action state assertions.

See ``README.md`` for installation, container setup, and a worked
"connect → screen-grab → tap a chord" example.
"""

from .binmon import OPCODE, BinMon, BinmonError
from .coverage import ActionCoverage, Coverage
from .display import (
    DisplaySnapshot,
    parse_display_response,
    parse_palette_response,
    write_png,
)
from .expect import Expect, ExpectPredicate, verify
from .keys import KEY, canonical_name, chord_to_keys, lookup, text_to_chords
from .screen import ScreenSnapshot, parse_screen_response, screencode_to_ascii
from .vice_docker import DiskMount, ViceContainer, ViceContainerError

__all__ = [
    "ActionCoverage",
    "BinMon",
    "BinmonError",
    "Coverage",
    "DiskMount",
    "DisplaySnapshot",
    "Expect",
    "ExpectPredicate",
    "KEY",
    "OPCODE",
    "ScreenSnapshot",
    "ViceContainer",
    "ViceContainerError",
    "canonical_name",
    "chord_to_keys",
    "lookup",
    "parse_display_response",
    "parse_palette_response",
    "parse_screen_response",
    "screencode_to_ascii",
    "text_to_chords",
    "verify",
    "write_png",
]

__version__ = "0.4.0"
