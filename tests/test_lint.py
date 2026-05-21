"""Lint + format checks.

Every tracked ``.py`` file under ``vice_driver/`` and ``tests/`` must
pass ``ruff check``, ``ruff format --check``, and ``black --check``.
The project's ``pyproject.toml`` configures ruff (line-length 100,
ruleset ``E,F,I,W,B``); ``black`` runs with its defaults. These tests
enforce that configuration on every CI run.

We invoke each tool as a subprocess rather than importing its Python
API because (a) the APIs are not stable and (b) the binaries are what
developers run locally — keeping the test on the same code path avoids
surprises where API and CLI disagree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LINT_DIRS = ("vice_driver", "tests")


def _python_files() -> list[Path]:
    """All project .py paths under LINT_DIRS, excluding generated dirs."""
    skip_dirs = {".git", "__pycache__", "build", "dist", ".venv", "venv"}
    files: list[Path] = []
    for top in LINT_DIRS:
        top_path = REPO_ROOT / top
        if not top_path.is_dir():
            continue
        for path in top_path.rglob("*.py"):
            if any(part in skip_dirs for part in path.relative_to(REPO_ROOT).parts):
                continue
            files.append(path)
    return sorted(files)


def _subprocess_env() -> dict[str, str]:
    """Strip pytest-cov subprocess-injection env vars before spawning the
    linters. Otherwise the spawned subprocess inherits ``COV_CORE_*`` and
    pytest-cov's sitecustomize hook fires inside it, importing pygments
    which may not be installed and which would make the subprocess exit
    non-zero with an error that masquerades as a lint failure."""
    return {k: v for k, v in os.environ.items() if not k.startswith("COV_CORE_")}


class TestRuffLint(unittest.TestCase):
    """Run ``ruff check`` and ``ruff format --check`` against the project."""

    def setUp(self) -> None:
        ruff = shutil.which("ruff")
        if ruff is None:
            self.skipTest("ruff not installed")
        self.ruff: str = ruff
        self.files = _python_files()
        self.assertGreater(len(self.files), 0, "no .py files discovered")

    def test_ruff_check_clean(self) -> None:
        result = subprocess.run(
            [self.ruff, "check", *(str(p) for p in self.files)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=_subprocess_env(),
        )
        if result.returncode != 0:
            offenders = result.stdout.strip() or result.stderr.strip()
            self.fail("ruff check failed; run `ruff check --fix` to fix:\n" + offenders)

    def test_ruff_format_clean(self) -> None:
        result = subprocess.run(
            [self.ruff, "format", "--check", *(str(p) for p in self.files)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=_subprocess_env(),
        )
        if result.returncode != 0:
            offenders = result.stdout.strip() or result.stderr.strip()
            self.fail("ruff format --check failed; run `ruff format` to fix:\n" + offenders)


class TestBlackFormat(unittest.TestCase):
    """Run ``black --check`` against the project."""

    def setUp(self) -> None:
        black = shutil.which("black")
        if black is None:
            self.skipTest("black not installed")
        self.black: str = black
        self.files = _python_files()
        self.assertGreater(len(self.files), 0, "no .py files discovered")

    def test_black_format_clean(self) -> None:
        result = subprocess.run(
            [self.black, "--check", *(str(p) for p in self.files)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=_subprocess_env(),
        )
        if result.returncode != 0:
            offenders = result.stdout.strip() or result.stderr.strip()
            self.fail("black --check failed; run `black .` to fix:\n" + offenders)


if __name__ == "__main__":
    unittest.main()
