"""Tests for retry decorators in notion_ops.utils.retry."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import Headers, HTTPStatusError, Request, Response
from notion_client import APIResponseError
from notion_client.errors import APIErrorCode

from notion_ops.exceptions import RateLimitError
from notion_ops.utils.retry import (
    MAX_ATTEMPTS,
    MAX_DELAY,
    MAX_RETRY_AFTER,
    _get_retry_delay,
    _should_retry,
    retry_on_transient,
    retry_on_transient_api,
    retry_on_transient_async,
)


def _make_http_status_error(status_code, headers=None):
    """Helper to create a realistic HTTPStatusError."""
    request = Request(method="POST", url="https://api.notion.com/v1/pages")
    response = Response(
        status_code=status_code,
        request=request,
        headers=headers or {},
    )
    return HTTPStatusError(
        message=f"{status_code} Error",
        request=request,
        response=response,
    )


def _rate_limited(retry_after: str | None = None) -> APIResponseError:
    """A raw SDK 429, the shape that reaches the retry wrapper after rev2.

    rev2 moved the mapping OUTSIDE the wrapper (contract-5), so what the wrapper
    now sees on a rate limit is this, not a ``RateLimitError``.
    """
    return APIResponseError(
        code=APIErrorCode.RateLimited,
        status=429,
        message="Rate limited",
        headers=Headers({"Retry-After": retry_after} if retry_after else {}),
        raw_body_text='{"object":"error","code":"rate_limited"}',
    )


class TestShouldRetry:
    """Tests for _should_retry helper."""

    def test_retries_on_503(self):
        """503 Service Unavailable is retryable."""
        err = _make_http_status_error(503)
        assert _should_retry(err) is True

    def test_retries_on_429(self):
        """429 Rate Limit is retryable."""
        err = _make_http_status_error(429)
        assert _should_retry(err) is True

    def test_does_not_retry_on_404(self):
        """404 Not Found is not retryable."""
        err = _make_http_status_error(404)
        assert _should_retry(err) is False

    def test_does_not_retry_on_400(self):
        """400 Bad Request is not retryable."""
        err = _make_http_status_error(400)
        assert _should_retry(err) is False

    def test_retries_on_rate_limit_error(self):
        """Custom RateLimitError is retryable."""
        err = RateLimitError(retry_after=5.0)
        assert _should_retry(err) is True

    def test_retries_on_503_in_message(self):
        """Exception with '503' in message is retryable (fallback)."""
        err = Exception("Service temporarily unavailable (503)")
        assert _should_retry(err) is True

    def test_retries_on_rate_limit_in_message(self):
        """Exception with 'rate limit' in message is retryable (fallback)."""
        err = Exception("Rate limit exceeded, please slow down")
        assert _should_retry(err) is True

    def test_does_not_retry_generic_exception(self):
        """Generic exception without transient indicators is not retryable."""
        err = Exception("Something went wrong")
        assert _should_retry(err) is False


class TestGetRetryDelay:
    """Tests for _get_retry_delay helper."""

    def test_exponential_backoff(self):
        """Delay doubles with each attempt."""
        err = _make_http_status_error(503)

        delay_0 = _get_retry_delay(0, err)
        delay_1 = _get_retry_delay(1, err)
        delay_2 = _get_retry_delay(2, err)

        assert delay_0 == 2.0  # BASE_DELAY * 2^0
        assert delay_1 == 4.0  # BASE_DELAY * 2^1
        assert delay_2 == 8.0  # BASE_DELAY * 2^2

    def test_delay_capped_at_max(self):
        """Delay is capped at MAX_DELAY."""
        err = _make_http_status_error(503)
        delay = _get_retry_delay(10, err)  # Would be 2048 without cap
        assert delay == 16.0  # MAX_DELAY

    def test_rate_limit_error_uses_retry_after(self):
        """RateLimitError uses its retry_after attribute."""
        err = RateLimitError(retry_after=10.0)
        delay = _get_retry_delay(0, err)
        assert delay == 10.0

    def test_rate_limit_error_minimum_is_base_delay(self):
        """RateLimitError retry_after is floored at BASE_DELAY."""
        err = RateLimitError(retry_after=0.5)
        delay = _get_retry_delay(0, err)
        assert delay == 2.0  # BASE_DELAY

    def test_http_429_with_retry_after_header(self):
        """HTTP 429 with Retry-After header uses the header value."""
        err = _make_http_status_error(429, headers={"Retry-After": "8"})
        delay = _get_retry_delay(0, err)
        assert delay == 8.0

    def test_http_429_retry_after_minimum_is_base_delay(self):
        """HTTP 429 Retry-After header is floored at BASE_DELAY."""
        err = _make_http_status_error(429, headers={"Retry-After": "0.5"})
        delay = _get_retry_delay(0, err)
        assert delay == 2.0  # BASE_DELAY


class TestRawSdkRetryAfter:
    """contract-21: the reorder must not cost the server's own wait interval.

    rev2 moved the mapping outside the retry wrapper so that a Notion 503 is
    retried four times instead of one. The predicate is not the only thing that
    reads the exception, though: `_get_retry_delay` reads it too, and it knew how
    to find `Retry-After` only on a `RateLimitError` (which `map_api_error`
    populates) or an `httpx.HTTPStatusError`. A raw `APIResponseError` carries the
    header in `.headers` and neither branch looked there, so on the ONE status
    where Notion says exactly how long to wait, the new order stopped listening --
    measured by the contract seat as [2, 4, 8] where the round-1 order slept
    [30, 30, 30].
    """

    def test_a_raw_sdk_429_uses_the_servers_retry_after(self):
        assert _get_retry_delay(0, _rate_limited("30")) == 30.0
        # The exponential backoff is what the attempt number would have given.
        assert _get_retry_delay(2, _rate_limited("30")) == 30.0

    def test_the_header_is_floored_at_base_delay_like_every_other_source(self):
        assert _get_retry_delay(0, _rate_limited("0.5")) == 2.0

    def test_an_unparseable_or_absent_header_falls_back_to_exponential(self):
        assert _get_retry_delay(1, _rate_limited("soon")) == 4.0
        assert _get_retry_delay(1, _rate_limited()) == 4.0

    def test_a_transient_5xx_is_unaffected_and_stays_exponential(self):
        five_oh_three = APIResponseError(
            code=APIErrorCode.ServiceUnavailable,
            status=503,
            message="Notion is unavailable.",
            headers=Headers({"Retry-After": "30"}),
            raw_body_text='{"object":"error","code":"service_unavailable"}',
        )
        # Notion does not send Retry-After on a 503, and the rule is 429-only:
        # honouring it here would change a documented backoff on a header nobody
        # sends.
        assert _get_retry_delay(0, five_oh_three) == 2.0

    def test_a_server_specified_wait_is_capped(self):
        """hostile-45: honouring a header is not agreeing to block indefinitely.

        `MAX_DELAY` was applied to the exponential ladder and to nothing else, so a `Retry-After` of `3600` produced three hour-long blocking `time.sleep` calls inside a single revision — while `retry_on_transient`'s own docstring promised "capped at 16s". A header is a hint from a server that may be misconfigured, proxied or hostile, and a synchronous library call has no business honouring it without a ceiling.

        The ceiling is `MAX_RETRY_AFTER`, not `MAX_DELAY`, and that is deliberate. `MAX_DELAY` bounds the *blind* exponential ladder, which is guessing; a server-specified wait is information the ladder does not have, and contract-21 is the finding that says ignoring it costs four rejected attempts and a failure mid-revision. Capping the header at 16 s would put a server asking for 30 s back where contract-21 found it. So the two ceilings are separate, and both are documented.
        """
        assert _get_retry_delay(0, _rate_limited("3600")) == MAX_RETRY_AFTER
        assert _get_retry_delay(0, RateLimitError(retry_after=3600.0)) == MAX_RETRY_AFTER
        assert (
            _get_retry_delay(0, _make_http_status_error(429, headers={"Retry-After": "3600"}))
            == MAX_RETRY_AFTER
        )
        # And the interval contract-21 measured is still honoured in full.
        assert _get_retry_delay(0, _rate_limited("30")) == 30.0
        assert MAX_RETRY_AFTER > MAX_DELAY

    @patch("notion_ops.utils.retry.time.sleep")
    def test_the_wrapper_sleeps_a_capped_interval_end_to_end(self, mock_sleep):
        """The ceiling reaches `time.sleep`, not just the delay function."""
        calls = {"n": 0}

        @retry_on_transient_api
        def rate_limited():
            calls["n"] += 1
            raise _rate_limited("3600")

        with pytest.raises(APIResponseError):
            rate_limited()
        assert calls["n"] == MAX_ATTEMPTS
        assert [c.args[0] for c in mock_sleep.call_args_list] == [MAX_RETRY_AFTER] * (
            MAX_ATTEMPTS - 1
        )

    def test_the_header_floor_discriminates_from_the_exponential_fallback(self):
        """hostile-48: at attempt 0 the fallback is also 2.0, so the floor test above cannot fail for its stated reason.

        At attempt 2 the three candidate behaviours separate: branch present and floored → 2.0; branch absent → 8.0 (the exponential); floor absent → 0.5 (the raw header).
        """
        assert _get_retry_delay(2, _rate_limited("0.5")) == 2.0

    @patch("notion_ops.utils.retry.time.sleep")
    def test_the_wrapper_sleeps_the_server_specified_interval(self, mock_sleep):
        """End to end through the decorator the revision helpers actually use."""
        calls = {"n": 0}

        @retry_on_transient_api
        def rate_limited():
            calls["n"] += 1
            if calls["n"] < MAX_ATTEMPTS:
                raise _rate_limited("30")
            return "recovered"

        assert rate_limited() == "recovered"
        assert calls["n"] == MAX_ATTEMPTS
        assert [c.args[0] for c in mock_sleep.call_args_list] == [30.0, 30.0, 30.0]


class TestRetryOnTransient:
    """Tests for retry_on_transient decorator."""

    @patch("notion_ops.utils.retry.time.sleep")
    def test_retry_succeeds_first_try(self, mock_sleep):
        """Function succeeds on first attempt, no retry needed."""
        call_count = 0

        @retry_on_transient
        def succeeding_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = succeeding_func()

        assert result == "success"
        assert call_count == 1
        mock_sleep.assert_not_called()

    @patch("notion_ops.utils.retry.time.sleep")
    def test_retry_recovers_from_503(self, mock_sleep):
        """Function fails once with 503, succeeds on retry."""
        call_count = 0

        @retry_on_transient
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_http_status_error(503)
            return "recovered"

        result = flaky_func()

        assert result == "recovered"
        assert call_count == 2
        mock_sleep.assert_called_once()

    @patch("notion_ops.utils.retry.time.sleep")
    def test_retry_recovers_from_429(self, mock_sleep):
        """Function fails once with 429 rate limit, succeeds on retry."""
        call_count = 0

        @retry_on_transient
        def rate_limited_func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_http_status_error(429)
            return "recovered"

        result = rate_limited_func()

        assert result == "recovered"
        assert call_count == 2
        mock_sleep.assert_called_once()

    @patch("notion_ops.utils.retry.time.sleep")
    def test_retry_exhausts_max_attempts(self, mock_sleep):
        """Function fails MAX_ATTEMPTS times, then re-raises."""
        call_count = 0

        @retry_on_transient
        def always_failing_func():
            nonlocal call_count
            call_count += 1
            raise _make_http_status_error(503)

        with pytest.raises(HTTPStatusError):
            always_failing_func()

        assert call_count == MAX_ATTEMPTS
        # Sleep is called (MAX_ATTEMPTS - 1) times (between attempts)
        assert mock_sleep.call_count == MAX_ATTEMPTS - 1

    @patch("notion_ops.utils.retry.time.sleep")
    def test_retry_non_transient_not_retried(self, mock_sleep):
        """Non-transient error (404) is not retried, raises immediately."""
        call_count = 0

        @retry_on_transient
        def not_found_func():
            nonlocal call_count
            call_count += 1
            raise _make_http_status_error(404)

        with pytest.raises(HTTPStatusError):
            not_found_func()

        assert call_count == 1
        mock_sleep.assert_not_called()

    @patch("notion_ops.utils.retry.time.sleep")
    def test_retry_respects_retry_after_header(self, mock_sleep):
        """Retry respects the Retry-After header value for sleep duration."""
        call_count = 0

        @retry_on_transient
        def rate_limited_with_header():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_http_status_error(
                    429, headers={"Retry-After": "5"}
                )
            return "recovered"

        result = rate_limited_with_header()

        assert result == "recovered"
        assert call_count == 2
        # Should sleep for 5.0 seconds (from Retry-After header)
        mock_sleep.assert_called_once_with(5.0)

    @patch("notion_ops.utils.retry.time.sleep")
    def test_retry_preserves_function_name(self, mock_sleep):
        """Decorated function preserves its original name (functools.wraps)."""

        @retry_on_transient
        def my_special_function():
            return "ok"

        assert my_special_function.__name__ == "my_special_function"

    @patch("notion_ops.utils.retry.time.sleep")
    def test_retry_with_args_and_kwargs(self, mock_sleep):
        """Decorated function correctly passes args and kwargs through."""
        call_count = 0

        @retry_on_transient
        def func_with_args(a, b, key=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_http_status_error(503)
            return f"{a}-{b}-{key}"

        result = func_with_args("x", "y", key="z")

        assert result == "x-y-z"
        assert call_count == 2


class TestRetryOnTransientAsync:
    """Tests for retry_on_transient_async decorator."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_async_retry_succeeds_first_try(self, mock_sleep):
        """Async function succeeds on first attempt, no retry needed."""
        call_count = 0

        @retry_on_transient_async
        async def succeeding_func():
            nonlocal call_count
            call_count += 1
            return "async_success"

        result = await succeeding_func()

        assert result == "async_success"
        assert call_count == 1
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_async_retry_recovers_from_503(self, mock_sleep):
        """Async function fails once with 503, succeeds on retry."""
        call_count = 0

        @retry_on_transient_async
        async def flaky_async_func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_http_status_error(503)
            return "async_recovered"

        result = await flaky_async_func()

        assert result == "async_recovered"
        assert call_count == 2
        mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_async_retry_exhausts_max_attempts(self, mock_sleep):
        """Async function fails MAX_ATTEMPTS times, then re-raises."""
        call_count = 0

        @retry_on_transient_async
        async def always_failing_async():
            nonlocal call_count
            call_count += 1
            raise _make_http_status_error(503)

        with pytest.raises(HTTPStatusError):
            await always_failing_async()

        assert call_count == MAX_ATTEMPTS
        # Sleep is called (MAX_ATTEMPTS - 1) times (between attempts)
        assert mock_sleep.call_count == MAX_ATTEMPTS - 1
