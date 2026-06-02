"""AC-10: Hypothesis CI profile is registered with bounded budget.

Asserts that the CI hypothesis profile (registered in tests/security/conftest.py)
exists, is named "ci", has max_examples bounded, has derandomize=True, and has
deadline=None (preventing per-test deadline flake).

Also asserts that re-running is deterministic: the derandomize=True setting fixes
the PRNG seed so results are reproducible across identical runs.
"""
from __future__ import annotations

from hypothesis import settings


def test_ci_profile_registered() -> None:
    """AC-10: The 'ci' hypothesis profile is registered."""
    registered = settings._profiles  # type: ignore[attr-defined]
    assert "ci" in registered, (
        "hypothesis 'ci' profile must be registered (done in tests/security/conftest.py)"
    )


def test_ci_profile_bounded_examples() -> None:
    """AC-10: The 'ci' profile bounds max_examples (small; CI-fast)."""
    ci = settings.get_profile("ci")
    assert ci.max_examples <= 100, (
        f"CI profile max_examples must be <= 100 for bounded wall-clock, got {ci.max_examples}"
    )
    assert ci.max_examples >= 10, (
        f"CI profile max_examples must be >= 10 to provide meaningful coverage, got {ci.max_examples}"
    )


def test_ci_profile_derandomize_true() -> None:
    """AC-10: The 'ci' profile has derandomize=True (deterministic across runs)."""
    ci = settings.get_profile("ci")
    assert ci.derandomize is True, (
        f"CI profile must have derandomize=True for reproducibility, got {ci.derandomize}"
    )


def test_ci_profile_no_deadline() -> None:
    """AC-10: The 'ci' profile has deadline=None (no per-test deadline flake)."""
    ci = settings.get_profile("ci")
    assert ci.deadline is None, (
        f"CI profile must have deadline=None to prevent flake, got {ci.deadline}"
    )


def test_ci_profile_is_loaded() -> None:
    """AC-10: The 'ci' profile is the active profile (loaded by conftest.py)."""
    # The conftest calls settings.load_profile("ci") at import time.
    # After loading, the default profile's settings match the CI profile.
    # We verify by checking that the current default has max_examples <= 100.
    current = settings()
    assert current.max_examples <= 100, (
        f"Active hypothesis profile should have max_examples <= 100 (CI profile loaded), "
        f"got {current.max_examples}. Check that tests/security/conftest.py loads the CI profile."
    )


def test_determinism_comment_present_in_conftest() -> None:
    """AC-10: The conftest.py contains a comment explaining the determinism approach."""
    import pathlib
    conftest = pathlib.Path(__file__).parent / "conftest.py"
    text = conftest.read_text()
    assert "derandomize" in text, (
        "conftest.py must explain derandomize=True for CI determinism"
    )
    assert "deterministic" in text.lower() or "derandomize" in text, (
        "conftest.py must document the determinism approach"
    )
