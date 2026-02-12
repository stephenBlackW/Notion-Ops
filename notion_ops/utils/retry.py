"""Retry utilities for handling transient Notion API errors."""

import functools
import logging
import time
from typing import Any, Callable, TypeVar, cast

from httpx import HTTPStatusError

from notion_ops.exceptions import RateLimitError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Retry configuration
MAX_ATTEMPTS = 4
BASE_DELAY = 2.0  # seconds
MAX_DELAY = 16.0  # seconds


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
        return float(max(exception.retry_after, BASE_DELAY))

    if isinstance(exception, HTTPStatusError) and exception.response.status_code == 429:
        # Check for Retry-After header
        retry_after = exception.response.headers.get("Retry-After")
        if retry_after:
            try:
                parsed_delay = float(retry_after)
                return float(max(parsed_delay, BASE_DELAY))
            except ValueError:
                pass

    # Exponential backoff: 2^attempt * BASE_DELAY
    delay = BASE_DELAY * (2**attempt)
    return float(min(delay, MAX_DELAY))


def retry_on_transient(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator that retries a function on transient Notion API errors.

    Retries on:
    - HTTP 503 (Service Unavailable)
    - HTTP 429 (Rate Limit)

    Retry behavior:
    - Maximum 4 attempts (3 retries after initial attempt)
    - Exponential backoff: 2s, 4s, 8s, capped at 16s
    - For rate limits, respects Retry-After header if present
    - Logs retry attempts at INFO level
    - Re-raises original exception after exhausting retries

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

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        last_exception: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e

                # Check if we should retry this error
                if not _should_retry(e):
                    raise

                # Check if we have attempts remaining
                if attempt >= MAX_ATTEMPTS - 1:
                    logger.warning(
                        f"Max retry attempts ({MAX_ATTEMPTS}) reached for {func.__name__}. "
                        f"Last error: {e}"
                    )
                    raise

                # Calculate delay and retry
                delay = _get_retry_delay(attempt, e)
                logger.info(
                    f"Transient error in {func.__name__} (attempt {attempt + 1}/{MAX_ATTEMPTS}): "
                    f"{type(e).__name__}: {e}. Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)

        # This should never be reached, but for type safety
        if last_exception:
            raise last_exception
        raise RuntimeError(f"Unexpected retry state in {func.__name__}")

    return cast(Callable[..., T], wrapper)


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
        page = await create_page(async_notion_client, page_data)
    """
    import asyncio

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        last_exception: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e

                # Check if we should retry this error
                if not _should_retry(e):
                    raise

                # Check if we have attempts remaining
                if attempt >= MAX_ATTEMPTS - 1:
                    logger.warning(
                        f"Max retry attempts ({MAX_ATTEMPTS}) reached for {func.__name__}. "
                        f"Last error: {e}"
                    )
                    raise

                # Calculate delay and retry
                delay = _get_retry_delay(attempt, e)
                logger.info(
                    f"Transient error in {func.__name__} (attempt {attempt + 1}/{MAX_ATTEMPTS}): "
                    f"{type(e).__name__}: {e}. Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)

        # This should never be reached, but for type safety
        if last_exception:
            raise last_exception
        raise RuntimeError(f"Unexpected retry state in {func.__name__}")

    return cast(Callable[..., T], wrapper)
