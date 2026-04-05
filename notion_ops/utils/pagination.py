"""Pagination utilities for Notion Operations library."""

from collections.abc import Callable, Iterator
from typing import Any, TypeVar

T = TypeVar("T")


def iterate_paginated(
    fetch_func: Callable[..., dict[str, Any]],
    *,
    page_size: int = 100,
    **kwargs: Any,
) -> Iterator[dict[str, Any]]:
    """
    Iterate over paginated API results.

    Args:
        fetch_func: Function that accepts start_cursor and returns paginated results
        page_size: Number of results per page
        **kwargs: Additional arguments to pass to fetch_func

    Yields:
        Individual result items

    Example:
        for item in iterate_paginated(
            notion.databases.query,
            database_id="xxx",
            page_size=50
        ):
            print(item)
    """
    start_cursor: str | None = None

    while True:
        params = {**kwargs, "page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor

        response = fetch_func(**params)

        yield from response.get("results", [])

        if not response.get("has_more"):
            break

        start_cursor = response.get("next_cursor")


def collect_paginated(
    fetch_func: Callable[..., dict[str, Any]],
    *,
    page_size: int = 100,
    max_results: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """
    Collect all paginated API results into a list.

    Args:
        fetch_func: Function that accepts start_cursor and returns paginated results
        page_size: Number of results per page
        max_results: Maximum number of results to collect (None for all)
        **kwargs: Additional arguments to pass to fetch_func

    Returns:
        List of all result items

    Example:
        results = collect_paginated(
            notion.databases.query,
            database_id="xxx",
            max_results=500
        )
    """
    results: list[dict[str, Any]] = []

    for item in iterate_paginated(fetch_func, page_size=page_size, **kwargs):
        results.append(item)
        if max_results and len(results) >= max_results:
            break

    return results


async def async_iterate_paginated(
    fetch_func: Callable[..., Any],
    *,
    page_size: int = 100,
    **kwargs: Any,
) -> Any:
    """
    Async version of iterate_paginated.

    Args:
        fetch_func: Async function that accepts start_cursor and returns paginated results
        page_size: Number of results per page
        **kwargs: Additional arguments to pass to fetch_func

    Yields:
        Individual result items
    """
    start_cursor: str | None = None

    while True:
        params = {**kwargs, "page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor

        response = await fetch_func(**params)

        for item in response.get("results", []):
            yield item

        if not response.get("has_more"):
            break

        start_cursor = response.get("next_cursor")


async def async_collect_paginated(
    fetch_func: Callable[..., Any],
    *,
    page_size: int = 100,
    max_results: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """
    Async version of collect_paginated.

    Args:
        fetch_func: Async function that accepts start_cursor and returns paginated results
        page_size: Number of results per page
        max_results: Maximum number of results to collect (None for all)
        **kwargs: Additional arguments to pass to fetch_func

    Returns:
        List of all result items
    """
    results: list[dict[str, Any]] = []

    async for item in async_iterate_paginated(fetch_func, page_size=page_size, **kwargs):
        results.append(item)
        if max_results and len(results) >= max_results:
            break

    return results
