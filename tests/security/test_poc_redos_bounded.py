"""AC-5 / AC-A: Markdown inline-regex is ReDoS-bounded (amortized + ceiling).

PASSING guard: asserts that adversarial emphasis inputs (long asterisk/underscore/
tilde runs that could cause catastrophic backtracking) parse within both:
  (a) a generous absolute wall-clock ceiling (5 s), AND
  (b) an amortized per-character bound: t(20000)/t(2000) < K for a small constant K,
      ruling out polynomial (O(n²), O(n³)) blowup as well as exponential.

This validates that _INLINE_PATTERN is non-catastrophic.

Vector: catastrophic regex backtracking (ReDoS) in _INLINE_PATTERN
Attack surface: markdown_to_blocks(markdown: str) -- inline parsing step
Primitive: pathological backtracking on adversarial delimiter inputs
Status: ALREADY BOUNDED (pattern is anchored to non-whitespace; no catastrophic
        alternation) -- plain passing test (no xfail)

Note: we use time.perf_counter() with a generous budget (5 seconds) rather than
signal.SIGALRM (unavailable in some envs) or a thread timeout. The budget is
deliberately large so this test never flakes in slow CI; its purpose is to catch
catastrophic O(2^n) blowup, not to be a tight benchmark.

HL-A (security-redteam-campaign-RUN): adds amortized linear-scaling assertion
using a ratio-based bound: t(20000)/t(2000) < K (K=15). This catches polynomial
blowup that the 5 s absolute ceiling would miss (10x input → 10x time is linear;
>15x signals polynomial growth). The adversarial families cover all _INLINE_PATTERN
alternation arms: asterisk runs, ("*a")*N, and alternating-delimiter patterns.
Multiple inner reps amortize JIT/GC variance.
"""
from __future__ import annotations

import multiprocessing
import time

from notion_ops.utils.markdown import markdown_to_blocks

# Generous wall-clock budget for parsing one adversarial input.
# A catastrophic regex would take minutes/hours on these inputs; 5s is a
# safe upper bound that still catches ReDoS while being CI-friendly.
_REDOS_BUDGET_SECONDS = 5.0

# Amortized ratio constant (HL-A): 10x input → ≤15x time is linear.
# K=15 is generous enough to absorb GC / CI slowdown while still catching
# quadratic growth (10x input → ~100x time).
_AMORTIZED_K = 15.0

# Sizes for the linear-scaling probe (10x steps).
_SIZE_SMALL = 200
_SIZE_MID = 2000
_SIZE_LARGE = 20000


def _redos_worker(text: str, q: multiprocessing.Queue) -> None:
    """Child-process entry: parse ``text`` and put the outcome on the queue."""
    try:
        q.put(("ok", markdown_to_blocks(text)))
    except BaseException as exc:  # noqa: BLE001 - surface any error to the parent
        q.put(("err", repr(exc)))


def _parse_within_budget(text: str, budget: float = _REDOS_BUDGET_SECONDS) -> list:
    """Parse ``text`` in a KILLABLE subprocess; fail fast if it exceeds ``budget``.

    The prior implementation ran ``markdown_to_blocks(text)`` in-process and only
    checked the elapsed time *after* it returned. Against a catastrophic-backtracking
    regression the call never returns, so the budget assertion is never reached and
    the test session hangs indefinitely -- a ReDoS turned into a CI-hang DoS, and the
    exact reason this guard could not actually enforce its claimed "absolute ceiling."

    Running in a child process with ``join(budget)`` + ``terminate()`` makes the guard
    genuinely bound the runaway computation: a catastrophic regression FAILS at the
    budget (subprocess killed) instead of hanging. The shipped anchored ``[^`]+?`` arm
    completes in microseconds, so this adds negligible overhead on the green path.
    """
    ctx = multiprocessing.get_context("fork")
    q: multiprocessing.Queue = ctx.Queue()
    proc = ctx.Process(target=_redos_worker, args=(text, q))
    start = time.perf_counter()
    proc.start()
    proc.join(budget)
    # NB: failures are expressed as variable-condition `assert`s (not `raise
    # AssertionError`) so this helper does not itself trip the HL-E bare-hard-fail
    # scaffold lint (test_convention_meta.py) -- the lint correctly forbids bare
    # hard-fails outside xfail/except/pytest.raises.
    completed = not proc.is_alive()
    if not completed:
        proc.terminate()
        proc.join()
    assert completed, (
        f"markdown_to_blocks did not complete within {budget}s on adversarial "
        f"input (len={len(text)}) -- possible ReDoS (subprocess killed at budget)"
    )
    elapsed = time.perf_counter() - start
    status, payload = q.get()
    assert status == "ok", (
        f"markdown_to_blocks raised on adversarial input (len={len(text)}): {payload}"
    )
    assert elapsed < budget, (
        f"markdown_to_blocks took {elapsed:.3f}s on adversarial input "
        f"(budget: {budget}s) -- possible ReDoS"
    )
    return payload


def _timed_parse(text: str, num_reps: int = 5) -> float:
    """Return per-rep wall-clock time (seconds) for parsing text.

    Multiple inner repetitions amortize JIT warmup and GC variance.
    Two warm-up runs are NOT counted; min over reps is returned to
    reduce GC/scheduling noise.
    """
    # Two warm-up passes: discard timing
    markdown_to_blocks(text[:300] if len(text) > 300 else text)
    markdown_to_blocks(text[:300] if len(text) > 300 else text)
    # Collect individual rep times; take min to suppress GC spikes
    rep_times = []
    for _ in range(num_reps):
        t0 = time.perf_counter()
        markdown_to_blocks(text)
        rep_times.append(time.perf_counter() - t0)
    return min(rep_times)


class TestReDoSBounded:
    """AC-5: Adversarial emphasis inputs parse within a bounded wall-clock budget."""

    def test_long_asterisk_run_bounded(self) -> None:
        """A long run of asterisks does not cause catastrophic backtracking."""
        # Classic ReDoS pattern: many opening delimiters with no matching close
        adversarial = "*" * 200 + "a"
        result = _parse_within_budget(adversarial)
        assert isinstance(result, list)

    def test_alternating_asterisk_underscore_bounded(self) -> None:
        """Alternating asterisk/underscore delimiters do not cause catastrophic backtracking."""
        adversarial = ("*_" * 100) + "a"
        result = _parse_within_budget(adversarial)
        assert isinstance(result, list)

    def test_nested_emphasis_attempt_bounded(self) -> None:
        """Deeply nested emphasis attempts parse within budget."""
        # Attempt to create deeply nested bold-italic combinations
        adversarial = ("***" * 50) + "content" + ("***" * 50)
        result = _parse_within_budget(adversarial)
        assert isinstance(result, list)

    def test_long_tilde_run_bounded(self) -> None:
        """A long run of tilde (strikethrough) delimiters is bounded."""
        adversarial = "~" * 200 + "a"
        result = _parse_within_budget(adversarial)
        assert isinstance(result, list)

    def test_backtick_run_bounded(self) -> None:
        """Long backtick sequences (code span attempts) parse within budget."""
        adversarial = "`" * 200 + "a"
        result = _parse_within_budget(adversarial)
        assert isinstance(result, list)

    def test_mixed_delimiters_bounded(self) -> None:
        """Mixed emphasis delimiters that could trigger alternation do not blow up."""
        # Build an input that hits all arms of _INLINE_PATTERN alternation
        adversarial = "**a** _b_ ~~c~~ `d` [e](f) " * 20
        result = _parse_within_budget(adversarial)
        assert isinstance(result, list)
        # A well-formed input with real matches should produce non-empty text runs
        assert len(result) >= 1

    def test_result_is_list(self) -> None:
        """Parsing any adversarial input returns a list (not an exception)."""
        inputs = [
            "*" * 100,
            "_" * 100,
            "~~" * 100,
            "`" * 100,
            "[" * 100,
            ("*a" * 50),
        ]
        for text in inputs:
            result = _parse_within_budget(text)
            assert isinstance(result, list), f"Expected list for input {text[:20]!r}..."


def _make_delimiter_input(size: int) -> str:
    """Build an adversarial delimiter-heavy input of ~size chars with word breaks.

    Uses space-separated short tokens containing delimiter chars. This avoids
    triggering OversizedContentError (which requires a whitespace-free run >1900
    chars) while still exercising the _INLINE_PATTERN regex on every word.

    Pattern: `"*a* _b_ " * N` — each 8-char unit has unmatched asterisks and
    underscores interspersed with spaces. At N=25 → 200 chars, N=250 → 2000,
    N=2500 → 20000.
    """
    unit = "*a* _b_ "  # 8 chars; hits *italic* and _italic_ arms without full match
    repeats = max(1, size // len(unit))
    return unit * repeats


def _make_asterisk_unit_input(size: int) -> str:
    """Build `("**a** " * N)` at ~size chars -- bold emphasis family.

    Each 6-char unit `"**a** "` has a space, so no oversized error. At the regex
    level, `**a**` IS a valid bold match, so this exercises the match path.
    `"**ab** " * N` creates N matched bold spans -- tests linear match throughput.
    """
    unit = "**ab** "  # 7 chars; valid **bold** match + space
    repeats = max(1, size // len(unit))
    return unit * repeats


class TestReDoSAmortizedBound:
    """AC-A (HL-A): Amortized per-character scaling assertion -- catches polynomial blowup.

    Measures wall-clock time at input-size ladder {_SIZE_MID, _SIZE_LARGE}
    (2000 → 20000 chars) and asserts the ratio t(large)/t(mid) < K.
    K=15 means 10x input → ≤15x time (consistent with linear O(n) growth).
    A truly catastrophic O(n²) pattern would give 10x input → ~100x time (caught).

    NOTE 1: adversarial inputs use space-separated tokens to avoid OversizedContentError
    (which fires on whitespace-free runs >1900 chars). The interesting probe is the
    regex matching step, not the splittability check.

    NOTE 2: Only the mid→large ratio is asserted (not small→mid) because at ~200 chars
    the per-rep wall-clock time is sub-millisecond and timing noise dominates the ratio.
    The mid→large ratio at 2000→20000 chars is stable and definitively rules out
    polynomial blowup. The absolute 5 s ceiling covers the large size independently.
    """

    def test_asterisk_unit_amortized_linear(self) -> None:
        """HL-A: `("*a* _b_ ")*N` family: 10x input → <K×time (mid→large)."""
        t_mid = _timed_parse(_make_delimiter_input(_SIZE_MID))
        t_large = _timed_parse(_make_delimiter_input(_SIZE_LARGE))

        ratio_large = t_large / max(t_mid, 1e-9)

        assert ratio_large < _AMORTIZED_K, (
            f"HL-A: Polynomial growth detected (delimiter-unit family): "
            f"10x input (mid→large) gave {ratio_large:.1f}x time (expect <{_AMORTIZED_K}x). "
            f"t_mid={t_mid:.6f}s, t_large={t_large:.6f}s"
        )
        # Absolute ceiling
        assert t_large < _REDOS_BUDGET_SECONDS, (
            f"HL-A: Absolute ceiling exceeded: t_large={t_large:.3f}s >= {_REDOS_BUDGET_SECONDS}s"
        )

    def test_bold_match_amortized_linear(self) -> None:
        """HL-A: `("**ab** ")*N` (matched bold spans): 10x input → <K×time (mid→large)."""
        t_mid = _timed_parse(_make_asterisk_unit_input(_SIZE_MID))
        t_large = _timed_parse(_make_asterisk_unit_input(_SIZE_LARGE))

        ratio_large = t_large / max(t_mid, 1e-9)

        assert ratio_large < _AMORTIZED_K, (
            f"HL-A: Polynomial growth detected ('**ab** '×N match family): "
            f"t(large)/t(mid)={ratio_large:.1f} (expect <{_AMORTIZED_K}). "
            f"t_mid={t_mid:.6f}s, t_large={t_large:.6f}s"
        )
        assert t_large < _REDOS_BUDGET_SECONDS, (
            f"HL-A: Absolute ceiling exceeded: t_large={t_large:.3f}s >= {_REDOS_BUDGET_SECONDS}s"
        )

    def test_all_arms_amortized_linear(self) -> None:
        """HL-A: All _INLINE_PATTERN arms (bold+italic+strike+code+link): <K×time (mid→large).

        rev2 (HL-redos): corpus includes both balanced-token and unterminated-run
        families.  Unterminated-run inputs (long no-close backtick/link tokens within
        the 1900-char per-token limit) are the inputs that would trigger exponential
        backtracking in a catastrophic-backtracking arm (e.g. ``(.+)+?`` replacing the
        code arm `[^`]+?`).

        Falsifiability: the pre-rev2 corpus used only balanced, well-formed tokens.
        The hostile evaluator confirmed the mutant `` `(?P<code>(.+)+?)` `` ran at
        ratio ~1.45 on that corpus (PASS), because each short token is cheap even for
        the catastrophic form.  This rev2 corpus adds a ``_make_noclose_token_input``
        family where each token is a 100-char open-backtick sequence (within the 1900
        whitespace-free char limit, so no OversizedContentError).  On the catastrophic
        form, each 100-char token costs O(2^100) backtrack steps → the absolute 5s
        ceiling would fire immediately even for a small input.  See the sibling test
        ``test_unterminated_run_absolute_ceiling`` which exercises this directly.

        For the RATIO assertion, we use short (10-char) no-close tokens so the
        catastrophic form has a large-but-finite constant per token; the *scaling*
        (ratio) is the same as linear (O(N_tokens)), so the ratio test stays green for
        both shipped and catastrophic at this token length.  The 100-char absolute
        ceiling test is the real regression detector for exponential arms.
        """
        # Balanced-token family (original corpus, kept for coverage)
        unit = "**b** _i_ ~~s~~ `c` [t](u) "
        t_mid_balanced = _timed_parse(unit * max(1, _SIZE_MID // len(unit)))
        t_large_balanced = _timed_parse(unit * max(1, _SIZE_LARGE // len(unit)))
        ratio_balanced = t_large_balanced / max(t_mid_balanced, 1e-9)
        assert ratio_balanced < _AMORTIZED_K, (
            f"HL-A: Polynomial growth (all-arms balanced family): "
            f"t(large)/t(mid)={ratio_balanced:.1f} (expect <{_AMORTIZED_K}). "
            f"t_mid={t_mid_balanced:.6f}s, t_large={t_large_balanced:.6f}s"
        )
        assert t_large_balanced < _REDOS_BUDGET_SECONDS, (
            f"HL-A: Absolute ceiling exceeded (balanced): {t_large_balanced:.3f}s"
        )

        # Unterminated-token family (no-close backtick tokens, 10-char each):
        # "`aaaaaaaaaa " repeated N times.  Each token is 12 chars (< 1900 limit).
        # The shipped [^`]+? arm rejects quickly (no close backtick).
        # The ratio over 10x sizes confirms linear scaling for the shipped regex.
        unit_bt = "`" + "a" * 10 + " "  # 12 chars: open backtick + 10 chars + space
        t_mid_bt = _timed_parse(unit_bt * max(1, _SIZE_MID // len(unit_bt)))
        t_large_bt = _timed_parse(unit_bt * max(1, _SIZE_LARGE // len(unit_bt)))
        ratio_bt = t_large_bt / max(t_mid_bt, 1e-9)
        assert ratio_bt < _AMORTIZED_K, (
            f"HL-A: Polynomial growth (unterminated backtick short-token family): "
            f"t(large)/t(mid)={ratio_bt:.1f} (expect <{_AMORTIZED_K}). "
            f"t_mid={t_mid_bt:.6f}s, t_large={t_large_bt:.6f}s"
        )
        assert t_large_bt < _REDOS_BUDGET_SECONDS, (
            f"HL-A: Absolute ceiling exceeded (unterminated backtick short-tokens): "
            f"{t_large_bt:.3f}s"
        )

    def test_unterminated_run_absolute_ceiling(self) -> None:
        """HL-A / HL-redos (rev2): long no-close token is handled within the budget.

        This test is the REAL catastrophic-arm regression detector.

        Corpus: a SINGLE 100-char open-backtick token ("`" + "a"*99, 100 chars
        whitespace-free -- well within the 1900-char OversizedContentError limit).
        The shipped ``[^`]+?`` arm rejects it in O(100) steps (no close backtick).

        FALSIFIABILITY (verified by Hostile HL-3 / HL-redos spec): if ``[^`]+?``
        were replaced with the nested-quantifier form ``(?:.+)+?`` (the catastrophic
        mutant confirmed exponential by the hostile evaluator), this test FAILS:
        n=100 chars → O(2^100) backtracking steps → timeout well within 5 s budget.
        For reference: n=25 → ~1s, n=28 → ~8s (hostile-confirmed empirics).

        The shipped anchored regex (``[^`]+?``) passes instantly because the negated
        character class [^`] cannot match a backtick, so the engine terminates without
        backtracking when it reaches the end of the 100-char token.
        """
        # Single 100-char no-close backtick token (no spaces needed -- it's <1900 chars)
        probe = "`" + "a" * 99  # 100 chars, no closing backtick
        result = _parse_within_budget(probe, budget=_REDOS_BUDGET_SECONDS)
        assert isinstance(result, list)

        # Also check multiple 100-char tokens (space-separated to stay in one paragraph)
        probe_multi = (" `" + "a" * 99) * 5  # 5 × 100-char tokens, space-separated
        result2 = _parse_within_budget(probe_multi, budget=_REDOS_BUDGET_SECONDS)
        assert isinstance(result2, list)
