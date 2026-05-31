"""Binding tests for the nops-security-A scanners-first configuration.

These assert the security lint is actually wired into the project config, so a
revert (dropping `S` from ruff, or un-ignoring it for tests) fails the gate
rather than silently disabling the scanner.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _load_pyproject() -> dict:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_ruff_enables_bandit_security_rules() -> None:
    cfg = _load_pyproject()
    lint = cfg["tool"]["ruff"]["lint"]
    assert "S" in lint["extend-select"], (
        "ruff S (flake8-bandit) must stay enabled for the library surface "
        "(nops-security-A)."
    )


def test_tests_are_ignored_for_bandit() -> None:
    cfg = _load_pyproject()
    per_file = cfg["tool"]["ruff"]["lint"]["per-file-ignores"]
    assert "S" in per_file["tests/**"], (
        "tests/** must ignore S — pytest legitimately uses assert (S101)."
    )
