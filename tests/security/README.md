# tests/security/ -- Red-Team PoC Regression Suite

This directory contains the **pre-publish red-team PoC regression tests** for
`notion_ops`. It is the scaffolding artifact of `ao-meta-I`; the full campaign
run (producing `redteam-report.md`) is a separate downstream activity.

## The xfail-strict-until-hardened Convention

Every PoC in this directory follows this lifecycle:

```
                            [library does NOT yet defend]
                                         |
                                         v
        @pytest.mark.xfail(strict=True, reason="<vector> not yet hardened")
        def test_poc_<name>():
            # adversarial input that triggers the vulnerability
            ...  # currently fails / raises the wrong thing
                                         |
                              [harden the library]
                                         |
                                         v
        # DROP the xfail annotation -- the test now PASSES as a guard
        def test_poc_<name>():
            # same adversarial input
            ...  # now passes because the library is hardened
```

**Key rules:**

1. **No bare `pytest.fail()` or hard-failing tests** in the committed scaffold.
   A hard-failing test reds the regression gate and violates the convention.
2. **`xfail(strict=True)` not `xfail()`** -- `strict=True` causes the test to
   FAIL if it unexpectedly passes (XPASS). This self-polices: if a library change
   accidentally hardens a PoC, the suite forces the author to drop the xfail and
   promote it to a guard.
3. **Demonstrative PoCs PASS** -- the tests in this scaffold target behavior the
   library ALREADY defends against, so they run as plain PASSING guards.

## Fixture: FakeClient

`conftest.py` provides a `fake_client` fixture that:
- Records every `blocks.children.append` call in `fake_client.calls`.
- Returns deterministic block IDs (no randomness).
- **Raises `AssertionError` if any real HTTP/network call is attempted** -- ensuring
  the SSRF PoC assertion is enforced at the infrastructure level.

## Hypothesis CI Profile

`conftest.py` also registers the `ci` hypothesis profile:
```python
settings.register_profile("ci", max_examples=50, derandomize=True, deadline=None)
```
This profile is loaded at conftest import time. Properties:
- `max_examples=50` -- bounded wall-clock; not exhaustive.
- `derandomize=True` -- deterministic across runs (same inputs every time).
- `deadline=None` -- no per-test deadline flake; overall wall-clock is bounded by max_examples.

Run twice and get identical results. No flake, no native fuzzer.

## Test Inventory

| File | AC | What it guards |
|------|----|--------------------|
| `test_convention_meta.py` | AC-3 | README + conftest + __init__ exist; README states convention |
| `test_poc_oversized_escalation.py` | AC-4 | Oversized unsplittable text raises OversizedContentError (ISS-013) |
| `test_poc_redos_bounded.py` | AC-5 | Adversarial emphasis inputs parse within bounded wall-clock |
| `test_poc_deep_nesting.py` | AC-6 | Deep block nesting terminates without RecursionError under FakeClient |
| `test_poc_ssrf_no_fetch.py` | AC-7 | SSRF-shaped URLs reach no network under FakeClient |
| `test_fuzz_markdown.py` | AC-8 | Bounded hypothesis fuzz: markdown_to_blocks safety property |
| `test_fuzz_publish.py` | AC-9 | Bounded hypothesis fuzz: publish_block_tree terminates, no network |
| `test_fuzz_profile_registered.py` | AC-10 | Hypothesis CI profile is registered with bounded budget |

## Running the Suite

```bash
# Scoped run (this suite only):
PYTHONPATH=. python -m pytest tests/security/ -v

# Full regression (includes this suite):
bash dev-cycles/regression.sh
```

## Relationship to the Campaign Run

This directory is the **HARNESS** scaffolding. The **campaign RUN** is a separate
downstream `nops` activity that:
1. Executes the full 6-stage offensive campaign (see `.claude/skills/security-redteam/SKILL.md`).
2. Produces `redteam-report.md` (exhaustive account of all vectors, PoCs, findings, hardenings).
3. Adds PoC tests for every finding (xfail until hardened, then passing guards).
4. Is gated on the v0.1.0 PyPI publish.
