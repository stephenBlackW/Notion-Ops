"""File upload operations for Notion's File Upload API.

Implements the three-step Notion file upload flow so that other agents
can attach local files (e.g. PNG figures) to Atom pages as image blocks
backed by ``file_upload`` references.

Flow:
    1. ``POST /v1/file_uploads`` to obtain ``upload_id`` and ``upload_url``.
    2. ``POST <upload_url>`` with multipart ``file=<bytes>`` to upload contents.
    3. Use the resulting ``upload_id`` as ``file_upload.id`` in block payloads.

For files larger than 20MB, multi-part mode is exposed via ``create()``.
For PNGs / typical figures, ``upload_file()`` and ``upload_and_attach()``
single-part helpers are sufficient.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from notion_ops.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    NotionOpsError,
    PermissionError,
    RateLimitError,
    ValidationError,
)
from notion_ops.utils.retry import retry_on_transient, retry_on_transient_async

if TYPE_CHECKING:
    from notion_ops.client import AsyncNotionOps, NotionOps

logger = logging.getLogger(__name__)

# Notion API endpoints
_FILE_UPLOADS_ENDPOINT = "https://api.notion.com/v1/file_uploads"

# Common content type inferences (extends mimetypes for explicit control)
_EXTENSION_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
    ".svg": "image/svg+xml",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Default Notion-Version header used when the underlying SDK does not expose one.
_DEFAULT_NOTION_VERSION = "2022-06-28"


def _infer_content_type(path: Path) -> str:
    """Infer a content type for *path* based on its suffix."""
    suffix = path.suffix.lower()
    if suffix in _EXTENSION_CONTENT_TYPES:
        return _EXTENSION_CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _map_http_error(
    response: httpx.Response,
    *,
    operation: str,
    resource_id: str = "",
) -> NotionOpsError:
    """Map a non-2xx ``httpx.Response`` to a typed ``NotionOpsError`` subclass.

    file_uploads talks to Notion's REST endpoint directly via ``httpx`` rather
    than the notion-client SDK, so it never sees an ``APIResponseError`` and
    cannot use :func:`notion_ops.exceptions.map_api_error`. This is the httpx
    analog: it selects the same subclasses by HTTP status so file_uploads
    raises the same typed errors as every SDK-backed operation (audit F3).

    429 maps to :class:`RateLimitError`, which ``retry_on_transient`` treats as
    transient — so a rate-limited upload retries with backoff like the rest of
    the library instead of failing on the first 429.
    """
    status = response.status_code
    detail = response.text
    if status == 404:
        return NotFoundError("file upload", resource_id or "<unknown>")
    if status == 401:
        return AuthenticationError()
    if status == 403:
        return PermissionError()
    if status == 429:
        retry_after = 1.0
        raw = response.headers.get("Retry-After")
        if raw is not None:
            try:
                retry_after = float(raw)
            except (ValueError, TypeError):
                pass
        return RateLimitError(retry_after=retry_after)
    if status in (400, 422):
        return ValidationError(f"{operation} request invalid ({status}): {detail}")
    if status == 409:
        return ConflictError(f"{operation} conflict: {detail}")
    return NotionOpsError(
        f"{operation} failed with status {status}: {detail}", code=str(status)
    )


def _auth_headers(client: Any, *, json_content: bool = False) -> dict[str, str]:
    """Build Authorization + Notion-Version headers for direct HTTP calls.

    Shared by the sync and async file-upload classes; reads the auth token and
    Notion-Version off whichever client (``NotionOps`` or ``AsyncNotionOps``)
    owns the operation.
    """
    token = getattr(client, "_auth", None) or ""
    # Notion-Version: prefer the version configured on the underlying SDK, then
    # the client's own setting, then the library default. Always a concrete str.
    sdk_version = (
        getattr(client.api, "options", {}).get("notion_version")
        if hasattr(client.api, "options")
        else None
    )
    notion_version: str = (
        sdk_version
        or getattr(client, "_notion_version", None)
        or _DEFAULT_NOTION_VERSION
    )
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": notion_version,
    }
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def _image_block_payload(
    upload_id: str,
    *,
    caption: str | None = None,
) -> dict[str, Any]:
    """Build an ``image`` block payload backed by a ``file_upload`` reference.

    Shared by the sync and async file-upload classes so both produce byte-for-
    byte identical block payloads.
    """
    image_payload: dict[str, Any] = {
        "type": "file_upload",
        "file_upload": {"id": upload_id},
    }
    if caption:
        image_payload["caption"] = [
            {
                "type": "text",
                "text": {"content": caption},
            }
        ]
    return {
        "object": "block",
        "type": "image",
        "image": image_payload,
    }


class FileUploads:
    """Three-step Notion File Upload API operations.

    Use via ``client.file_uploads`` on a :class:`NotionOps` instance.
    """

    def __init__(self, client: NotionOps):
        self._client = client

    # ------------------------------------------------------------------
    # Auth / version helpers
    # ------------------------------------------------------------------

    def _auth_headers(self, *, json_content: bool = False) -> dict[str, str]:
        """Build Authorization + Notion-Version headers for direct HTTP calls."""
        return _auth_headers(self._client, json_content=json_content)

    # ------------------------------------------------------------------
    # Step 1 — create the upload object
    # ------------------------------------------------------------------

    @retry_on_transient
    def create(
        self,
        *,
        filename: str,
        content_type: str,
        mode: str = "single_part",
        number_of_parts: int | None = None,
    ) -> dict[str, Any]:
        """Step 1 — create a file upload and obtain ``upload_url`` / ``id``.

        Args:
            filename: Name to associate with the upload (e.g. ``"figure.png"``).
            content_type: MIME type of the file (e.g. ``"image/png"``).
            mode: ``"single_part"`` (default) or ``"multi_part"``.
            number_of_parts: Required when ``mode == "multi_part"``.

        Returns:
            The raw API response dict — includes ``id``, ``upload_url``,
            ``status``, etc.
        """
        body: dict[str, Any] = {
            "mode": mode,
            "filename": filename,
            "content_type": content_type,
        }
        if mode == "multi_part":
            if not number_of_parts or number_of_parts < 1:
                raise ValueError(
                    "number_of_parts is required and must be >= 1 for multi_part mode"
                )
            body["number_of_parts"] = number_of_parts

        headers = self._auth_headers(json_content=True)
        try:
            response = httpx.post(
                _FILE_UPLOADS_ENDPOINT,
                json=body,
                headers=headers,
                timeout=60.0,
            )
        except httpx.HTTPError as e:
            raise NotionOpsError(
                f"File upload create request failed: {e}"
            ) from e

        if response.status_code >= 500:
            # Surface as HTTPStatusError so the retry decorator engages.
            response.raise_for_status()

        if response.status_code >= 400:
            raise _map_http_error(response, operation="File upload create")

        return response.json()

    # ------------------------------------------------------------------
    # Step 2 — POST the bytes to the upload URL
    # ------------------------------------------------------------------

    @retry_on_transient
    def send(
        self,
        upload_url: str,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        """Step 2 — send file bytes as multipart form to ``upload_url``.

        Args:
            upload_url: Pre-signed URL returned by :meth:`create`.
            file_bytes: Raw file contents.
            filename: Filename to send in the multipart envelope.
            content_type: Content type for the file part.

        Returns:
            Parsed JSON response (Notion returns an updated file upload object).
        """
        headers = self._auth_headers(json_content=False)
        files = {"file": (filename, file_bytes, content_type)}
        try:
            response = httpx.post(
                upload_url,
                files=files,
                headers=headers,
                timeout=120.0,
            )
        except httpx.HTTPError as e:
            raise NotionOpsError(
                f"File upload send request failed: {e}"
            ) from e

        if response.status_code >= 500:
            response.raise_for_status()

        if response.status_code >= 400:
            raise _map_http_error(response, operation="File upload send")

        # Notion returns JSON for the upload object; if the body is empty
        # for any reason, fall back to an empty dict so callers don't crash.
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------

    def upload_file(
        self,
        path: str | Path,
        *,
        content_type: str | None = None,
    ) -> str:
        """High-level: create + send for a local file.

        Infers ``content_type`` from the file extension when not provided
        (``.png`` → ``image/png``, ``.jpg``/``.jpeg`` → ``image/jpeg``,
        ``.pdf`` → ``application/pdf``, ``.svg`` → ``image/svg+xml``,
        ``.gif`` → ``image/gif``).

        Args:
            path: Path to a local file.
            content_type: Optional explicit MIME type.

        Returns:
            The ``file_upload`` id string usable in block payloads.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")

        ctype = content_type or _infer_content_type(p)
        file_bytes = p.read_bytes()

        created = self.create(filename=p.name, content_type=ctype)
        upload_id = created.get("id")
        upload_url = created.get("upload_url")
        if not upload_id or not upload_url:
            raise NotionOpsError(
                "Notion file_uploads.create response missing id or upload_url: "
                f"{created!r}"
            )

        self.send(
            upload_url,
            file_bytes,
            filename=p.name,
            content_type=ctype,
        )
        return upload_id

    # ------------------------------------------------------------------
    # Block payload helpers
    # ------------------------------------------------------------------

    def image_block(
        self,
        upload_id: str,
        *,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Build an ``image`` block payload backed by a ``file_upload``.

        The returned dict is shaped to be passed directly to the Notion
        ``blocks.children.append`` API as a child element.

        Args:
            upload_id: The ``id`` returned by :meth:`upload_file` /
                :meth:`create`.
            caption: Optional caption text.

        Returns:
            A dict like::

                {
                    "object": "block",
                    "type": "image",
                    "image": {
                        "type": "file_upload",
                        "file_upload": {"id": upload_id},
                        "caption": [...],   # only when caption supplied
                    },
                }
        """
        return _image_block_payload(upload_id, caption=caption)

    def upload_and_attach(
        self,
        path: str | Path,
        *,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Upload a local file and return the matching image block payload.

        Convenience wrapper around :meth:`upload_file` and
        :meth:`image_block`. The returned dict is ready to append via
        ``client.api.blocks.children.append`` or to wrap in a
        :class:`notion_ops.models.block.Block` for the higher-level
        ``BlockOperations.append`` flow.

        Args:
            path: Path to a local file (PNG, JPG, PDF, ...).
            caption: Optional caption text to attach to the image block.

        Returns:
            A block payload dict (see :meth:`image_block`).
        """
        upload_id = self.upload_file(path)
        return self.image_block(upload_id, caption=caption)


class AsyncFileUploads:
    """Async counterpart of :class:`FileUploads` (audit F3 async parity).

    Mirrors the sync API using ``httpx.AsyncClient`` + ``retry_on_transient_async``
    so ``await client.file_uploads.upload_file(...)`` works on an
    :class:`~notion_ops.client.AsyncNotionOps` instead of silently running a
    blocking sync upload on the event loop. Block-payload helpers
    (:meth:`image_block`) are pure and produce byte-for-byte identical output to
    the sync class via the shared :func:`_image_block_payload`.
    """

    def __init__(self, client: AsyncNotionOps):
        self._client = client

    def _auth_headers(self, *, json_content: bool = False) -> dict[str, str]:
        """Build Authorization + Notion-Version headers for direct HTTP calls."""
        return _auth_headers(self._client, json_content=json_content)

    @retry_on_transient_async
    async def create(
        self,
        *,
        filename: str,
        content_type: str,
        mode: str = "single_part",
        number_of_parts: int | None = None,
    ) -> dict[str, Any]:
        """Async Step 1 — create a file upload (see :meth:`FileUploads.create`)."""
        body: dict[str, Any] = {
            "mode": mode,
            "filename": filename,
            "content_type": content_type,
        }
        if mode == "multi_part":
            if not number_of_parts or number_of_parts < 1:
                raise ValueError(
                    "number_of_parts is required and must be >= 1 for multi_part mode"
                )
            body["number_of_parts"] = number_of_parts

        headers = self._auth_headers(json_content=True)
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                response = await http.post(
                    _FILE_UPLOADS_ENDPOINT, json=body, headers=headers
                )
        except httpx.HTTPError as e:
            raise NotionOpsError(f"File upload create request failed: {e}") from e

        if response.status_code >= 500:
            response.raise_for_status()
        if response.status_code >= 400:
            raise _map_http_error(response, operation="File upload create")

        return response.json()

    @retry_on_transient_async
    async def send(
        self,
        upload_url: str,
        file_bytes: bytes,
        *,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        """Async Step 2 — send file bytes (see :meth:`FileUploads.send`)."""
        headers = self._auth_headers(json_content=False)
        files = {"file": (filename, file_bytes, content_type)}
        try:
            async with httpx.AsyncClient(timeout=120.0) as http:
                response = await http.post(upload_url, files=files, headers=headers)
        except httpx.HTTPError as e:
            raise NotionOpsError(f"File upload send request failed: {e}") from e

        if response.status_code >= 500:
            response.raise_for_status()
        if response.status_code >= 400:
            raise _map_http_error(response, operation="File upload send")

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    async def upload_file(
        self,
        path: str | Path,
        *,
        content_type: str | None = None,
    ) -> str:
        """Async high-level create + send (see :meth:`FileUploads.upload_file`)."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")

        ctype = content_type or _infer_content_type(p)
        file_bytes = p.read_bytes()

        created = await self.create(filename=p.name, content_type=ctype)
        upload_id = created.get("id")
        upload_url = created.get("upload_url")
        if not upload_id or not upload_url:
            raise NotionOpsError(
                "Notion file_uploads.create response missing id or upload_url: "
                f"{created!r}"
            )

        await self.send(
            upload_url,
            file_bytes,
            filename=p.name,
            content_type=ctype,
        )
        return upload_id

    def image_block(
        self,
        upload_id: str,
        *,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Build an ``image`` block payload (pure; see :meth:`FileUploads.image_block`)."""
        return _image_block_payload(upload_id, caption=caption)

    async def upload_and_attach(
        self,
        path: str | Path,
        *,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Async upload + image-block (see :meth:`FileUploads.upload_and_attach`)."""
        upload_id = await self.upload_file(path)
        return self.image_block(upload_id, caption=caption)
