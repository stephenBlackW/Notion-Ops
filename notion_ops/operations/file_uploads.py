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

from notion_ops.exceptions import NotionOpsError
from notion_ops.utils.retry import retry_on_transient

if TYPE_CHECKING:
    from notion_ops.client import NotionOps

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


class FileUploads:
    """Three-step Notion File Upload API operations.

    Use via ``client.file_uploads`` on a :class:`NotionOps` instance.
    """

    def __init__(self, client: "NotionOps"):
        self._client = client

    # ------------------------------------------------------------------
    # Auth / version helpers
    # ------------------------------------------------------------------

    def _auth_headers(self, *, json_content: bool = False) -> dict[str, str]:
        """Build Authorization + Notion-Version headers for direct HTTP calls."""
        token = getattr(self._client, "_auth", None) or ""
        # Notion-Version: prefer the version configured on the underlying SDK.
        notion_version = (
            getattr(self._client.api, "options", {}).get("notion_version")
            if hasattr(self._client.api, "options")
            else None
        )
        if not notion_version:
            notion_version = getattr(
                self._client, "_notion_version", _DEFAULT_NOTION_VERSION
            )
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": notion_version,
        }
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

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
            raise NotionOpsError(
                f"File upload create failed with status "
                f"{response.status_code}: {response.text}"
            )

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
            raise NotionOpsError(
                f"File upload send failed with status "
                f"{response.status_code}: {response.text}"
            )

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
