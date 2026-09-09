"""Retry utilities for handling transient Notion API errors."""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from httpx import HTTPStatusError

from notion_ops.exceptions import RateLimitError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Retry configuration
MAX_ATTEMPTS = 4
BASE_DELAY = 2.0  # seconds
MAX_DELAY = 16.0  # seconds

#: Ceiling on a wait the *server* asked for, as distinct from :data:`MAX_DELAY`,
#: which bounds the blind exponential ladder. The two are separate on purpose. A
#: ``Retry-After`` is information the ladder does not have, and contract-21
#: measured what ignoring it costs: 2/4/8 against a server asking for 30 is four
#: rejected attempts and a failure in the middle of a revision. But honouring a
#: header is not agreeing to block indefinitely — a header of ``3600`` produced
#: three hour-long ``time.sleep`` calls inside one call, against a docstring
#: promising 16s. So it is honoured in full up to a minute, which is longer than
#: any interval Notion's rate limiter actually asks for and shorter than any wait
#: a synchronous library call should impose without saying so
#: (nops-cycle-3 rev6, hostile-45).
MAX_RETRY_AFTER = 60.0  # seconds

#: HTTP statuses worth another attempt: rate limiting, plus the 5xx family Notion
#: returns when a request never reached a healthy backend.
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


def _should_retry(exception: Exception) -> bool:
    """
    Check if an exception should trigger a retry.

    Args:
        exception: The exception to check

    Returns:
        True if the exception indicates a transient error
    """
    # Handle httpx HTTPStatusError (from notion-client)
    if isinstance(exception, HTTPStatusError):
        status_code = exception.response.status_code
        # Retry on 503 (service unavailable) and 429 (rate limit)
        return status_code in (429, 503)

    # Handle our custom RateLimitError
    if isinstance(exception, RateLimitError):
        return True

    # Check for 503 or 429 in exception message as fallback
    error_msg = str(exception).lower()
    return "503" in error_msg or "429" in error_msg or "rate limit" in error_msg


def _server_specified(seconds: float) -> float:
    """A wait the server asked for, floored at :data:`BASE_DELAY` and ceilinged at
    :data:`MAX_RETRY_AFTER`.

    Both bounds are on the same helper so all three sources of a ``Retry-After`` —
    a mapped :class:`RateLimitError`, an ``httpx.HTTPStatusError``, and a raw
    status-bearing SDK error — get the same answer, which is how the ceiling came
    to be missing in the first place (nops-cycle-3 rev6, hostile-45).
    """
    return float(min(max(seconds, BASE_DELAY), MAX_RETRY_AFTER))


def _get_retry_delay(attempt: int, exception: Exception) -> float:
    """
    Calculate retry delay with exponential backoff.

    Args:
        attempt: Current attempt number (0-indexed)
        exception: The exception that triggered the retry

    Returns:
        Delay in seconds before next retry
    """
    # For rate limit errors, try to extract retry_after if available
    if isinstance(exception, RateLimitError):
        return _server_specified(exception.retry_after)

    if isinstance(exception, HTTPStatusError) and exception.response.status_code == 429:
        # Check for Retry-After header
        retry_after = exception.response.headers.get("Retry-After")
        if retry_after:
            try:
                return _server_specified(float(retry_after))
            except ValueError:
                pass

    # A RAW SDK 429, which is what reaches here now that the mapping happens
    # outside the retry wrapper (contract-5): the header is on the error object
    # itself rather than on a `.response`, and neither branch above looks there.
    # Notion's rate-limit guidance is that `Retry-After` is to be honoured, and
    # 2/4/8 against a server asking for 30 is four rejected attempts and a failure
    # in the middle of a revision (nops-cycle-3 rev4, contract-21).
    if getattr(exception, "status", None) == 429:
        headers = getattr(exception, "headers", None)
        raw = headers.get("Retry-After") if headers is not None else None
        if raw:
            try:
                return _server_specified(float(raw))
            except (TypeError, ValueError):
                pass

    # Exponential backoff: 2^attempt * BASE_DELAY
    delay = BASE_DELAY * (2**attempt)
    return float(min(delay, MAX_DELAY))




def is_transient_api_error(exception: BaseException) -> bool:
    """True when *exception* carries an HTTP status worth another attempt.

    :func:`_should_retry` cannot answer this for the SDK's ``APIResponseError``:
    it is not an ``HTTPStatusError``, it is not a :class:`RateLimitError`, and a
    Notion 503 body reads ``"Notion is unavailable."`` — no digits, so the
    substring fallback misses it too. The status code is on the error object, so
    that is what this reads (nops-cycle-3 rev2, contract-5).
    """
    status = getattr(exception, "status", None)
    return isinstance(status, int) and status in _TRANSIENT_STATUSES


def _plan_retry(
    exception: Exception,
    attempt: int,
    predicate: Callable[[Exception], bool],
    name: str,
) -> float | None:
    """Seconds to wait before the next attempt, or ``None`` meaning re-raise."""
    if not predicate(exception):
        return None
    if attempt >= MAX_ATTEMPTS - 1:
        logger.warning(
            f"Max retry attempts ({MAX_ATTEMPTS}) reached for {name}. "
            f"Last error: {exception}"
        )
        return None
    delay = _get_retry_delay(attempt, exception)
    logger.info(
        f"Transient error in {name} (attempt {attempt + 1}/{MAX_ATTEMPTS}): "
        f"{type(exception).__name__}: {exception}. Retrying in {delay:.1f}s..."
    )
    return delay


def _decorate_sync(
    func: Callable[..., T],
    predicate: Callable[[Exception], bool],
) -> Callable[..., T]:
    """*func* retried while *predicate* says the failure was transient."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                delay = _plan_retry(e, attempt, predicate, func.__name__)
                if delay is None:
                    raise
                time.sleep(delay)
        raise RuntimeError(f"Unexpected retry state in {func.__name__}")

    return cast(Callable[..., T], wrapper)


def _decorate_async(
    func: Callable[..., Any],
    predicate: Callable[[Exception], bool],
) -> Callable[..., Any]:
    """Async twin of :func:`_decorate_sync`."""
    import asyncio

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                delay = _plan_retry(e, attempt, predicate, func.__name__)
                if delay is None:
                    raise
                await asyncio.sleep(delay)
        raise RuntimeError(f"Unexpected retry state in {func.__name__}")

    return wrapper


def retry_on_transient(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator that retries a function on transient Notion API errors.

    Retries on:
    - HTTP 503 (Service Unavailable)
    - HTTP 429 (Rate Limit)

    Retry behavior:
    - Maximum 4 attempts (3 retries after initial attempt)
    - Exponential backoff: 2s, 4s, 8s, capped at ``MAX_DELAY`` (16s)
    - For rate limits, honours ``Retry-After`` in full where it is longer than
      the ladder would have waited, floored at ``BASE_DELAY`` and capped at
      ``MAX_RETRY_AFTER`` (60s) — a separate, larger ceiling, because a header is
      information the ladder does not have and a header is also not a licence to
      block the caller for an hour (nops-cycle-3, contract-21 + hostile-45)
    - Logs retry attempts at INFO level
    - Re-raises original exception after exhausting retries

    The wrapped function is expected to raise ``httpx``/library-typed errors:
    the retry decision goes through :func:`_should_retry`, which recognises an
    ``HTTPStatusError``, a :class:`RateLimitError`, or a status-bearing message.
    A function that maps the SDK's ``APIResponseError`` to a library type *before*
    it escapes needs :func:`retry_on_transient_api` instead — see that function.

    Args:
        func: The function to wrap with retry logic

    Returns:
        Wrapped function with retry behavior

    Example:
        @retry_on_transient
        def create_page(client, data):
            return client.pages.create(**data)

        # Will automatically retry on 503 or 429 errors
        page = create_page(notion_client, page_data)
    """
    return _decorate_sync(func, _should_retry)


def retry_on_transient_async(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Async version of retry_on_transient decorator.

    Provides the same retry behavior as retry_on_transient but for async functions.
    Uses asyncio.sleep for non-blocking delays.

    Args:
        func: The async function to wrap with retry logic

    Returns:
        Wrapped async function with retry behavior

    Example:
        @retry_on_transient_async
        async def create_page(client, data):
            return await client.pages.create(**data)

        # Will automatically retry on 503 or 429 errors
        page = create_page(async_notion_client, page_data)
    """
    return _decorate_async(func, _should_retry)


def retry_on_transient_api(func: Callable[..., T]) -> Callable[..., T]:
    """Retry *func* on a **raw** SDK ``APIResponseError`` with a transient status.

    Wrap the request itself with this and map the error one layer *out*, never
    the other way around: a mapped 503 is a bare ``NotionOpsError`` carrying
    Notion's body text, which :func:`_should_retry` cannot recognise, so mapping
    inside the retry wrapper turns four attempts into one (contract-5, measured).

    A 429 handled this way still waits the interval the server asked for:
    :func:`_get_retry_delay` reads ``Retry-After`` off the raw SDK error's own
    ``.headers``, not only off the mapped :class:`RateLimitError` the old order
    produced (contract-21). That wait is bounded by :data:`MAX_RETRY_AFTER`
    rather than by :data:`MAX_DELAY` — honoured in full, but not past a minute
    (hostile-45).

    Example:
        @retry_on_transient_api
        def _raw(): return client.api.pages.retrieve(page_id=page_id)

        try:
            return _raw()
        except APIResponseError as e:
            raise map_api_error(e, ...) from e
    """
    return _decorate_sync(func, is_transient_api_error)


def retry_on_transient_api_async(func: Callable[..., Any]) -> Callable[..., Any]:
    """Async twin of :func:`retry_on_transient_api`."""
    return _decorate_async(func, is_transient_api_error)
