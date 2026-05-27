"""Mock-based tests for notion_ops.operations.file_uploads.

All HTTP calls are stubbed via ``unittest.mock.patch`` on ``httpx.post``.
No real network traffic is generated.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import Request, Response

from notion_ops.client import NotionOps
from notion_ops.exceptions import NotionOpsError
from notion_ops.operations.file_uploads import (
    _FILE_UPLOADS_ENDPOINT,
    FileUploads,
    _infer_content_type,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """A NotionOps client with the underlying SDK stubbed out."""
    with patch.dict("os.environ", {"NOTION_API_KEY": "secret-test"}):
        with patch("notion_ops.client.Client"):
            c = NotionOps()
            c._notion = MagicMock()
            return c


@pytest.fixture
def file_uploads(client):
    return FileUploads(client)


def _make_response(status_code: int, json_body=None, text: str = "", url: str = "https://api.notion.com/v1/file_uploads") -> Response:
    request = Request(method="POST", url=url)
    if json_body is not None:
        return Response(status_code=status_code, json=json_body, request=request)
    return Response(status_code=status_code, text=text, request=request)


# ---------------------------------------------------------------------------
# Content type inference
# ---------------------------------------------------------------------------


class TestInferContentType:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("foo.png", "image/png"),
            ("bar.PNG", "image/png"),
            ("baz.jpg", "image/jpeg"),
            ("baz.jpeg", "image/jpeg"),
            ("doc.pdf", "application/pdf"),
            ("vec.svg", "image/svg+xml"),
            ("anim.gif", "image/gif"),
        ],
    )
    def test_known_extensions(self, tmp_path, filename, expected):
        from pathlib import Path

        p = Path(filename)
        assert _infer_content_type(p) == expected


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_posts_correct_body(self, file_uploads):
        resp = _make_response(
            200,
            json_body={
                "id": "upload-123",
                "upload_url": "https://files.notion.com/upload/abc",
                "status": "pending",
            },
        )

        with patch("notion_ops.operations.file_uploads.httpx.post", return_value=resp) as m:
            result = file_uploads.create(filename="figure.png", content_type="image/png")

        assert result["id"] == "upload-123"
        assert result["upload_url"] == "https://files.notion.com/upload/abc"

        m.assert_called_once()
        args, kwargs = m.call_args
        assert args[0] == _FILE_UPLOADS_ENDPOINT
        assert kwargs["json"] == {
            "mode": "single_part",
            "filename": "figure.png",
            "content_type": "image/png",
        }
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret-test"
        assert "Notion-Version" in headers
        assert headers["Content-Type"] == "application/json"

    def test_create_multi_part_includes_number_of_parts(self, file_uploads):
        resp = _make_response(
            200,
            json_body={"id": "u-mp", "upload_url": "https://files.notion.com/u/mp"},
        )

        with patch("notion_ops.operations.file_uploads.httpx.post", return_value=resp) as m:
            file_uploads.create(
                filename="big.bin",
                content_type="application/octet-stream",
                mode="multi_part",
                number_of_parts=4,
            )

        body = m.call_args.kwargs["json"]
        assert body["mode"] == "multi_part"
        assert body["number_of_parts"] == 4

    def test_create_multi_part_requires_number_of_parts(self, file_uploads):
        with pytest.raises(ValueError, match="number_of_parts"):
            file_uploads.create(
                filename="big.bin",
                content_type="application/octet-stream",
                mode="multi_part",
            )

    def test_create_4xx_raises_without_retry(self, file_uploads):
        resp = _make_response(404, text="not found")

        with patch(
            "notion_ops.operations.file_uploads.httpx.post", return_value=resp
        ) as m:
            with pytest.raises(NotionOpsError, match="404"):
                file_uploads.create(filename="x.png", content_type="image/png")

        # 4xx → no retry
        assert m.call_count == 1

    def test_create_5xx_then_success_retries(self, file_uploads):
        bad = _make_response(503, text="busy")
        good = _make_response(
            200, json_body={"id": "u-1", "upload_url": "https://files.notion.com/u/1"}
        )

        with patch(
            "notion_ops.operations.file_uploads.httpx.post",
            side_effect=[bad, good],
        ) as m, patch("notion_ops.utils.retry.time.sleep"):
            result = file_uploads.create(filename="x.png", content_type="image/png")

        assert result["id"] == "u-1"
        assert m.call_count == 2


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------


class TestSend:
    def test_send_posts_multipart_with_file_field(self, file_uploads):
        resp = _make_response(200, json_body={"id": "upload-xyz", "status": "uploaded"})

        with patch("notion_ops.operations.file_uploads.httpx.post", return_value=resp) as m:
            result = file_uploads.send(
                "https://files.notion.com/upload/xyz",
                b"\x89PNGfakebytes",
                filename="figure.png",
                content_type="image/png",
            )

        assert result["id"] == "upload-xyz"

        args, kwargs = m.call_args
        assert args[0] == "https://files.notion.com/upload/xyz"
        files = kwargs["files"]
        assert "file" in files
        sent_filename, sent_bytes, sent_ctype = files["file"]
        assert sent_filename == "figure.png"
        assert sent_bytes == b"\x89PNGfakebytes"
        assert sent_ctype == "image/png"

        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret-test"
        assert "Notion-Version" in headers
        # Multipart upload must NOT set a JSON content-type
        assert "Content-Type" not in headers

    def test_send_4xx_raises_without_retry(self, file_uploads):
        resp = _make_response(404, text="missing", url="https://files.notion.com/up/x")

        with patch(
            "notion_ops.operations.file_uploads.httpx.post", return_value=resp
        ) as m:
            with pytest.raises(NotionOpsError, match="404"):
                file_uploads.send(
                    "https://files.notion.com/up/x",
                    b"data",
                    filename="x.png",
                    content_type="image/png",
                )
        assert m.call_count == 1

    def test_send_5xx_then_success(self, file_uploads):
        bad = _make_response(503, text="busy")
        good = _make_response(200, json_body={"id": "u-2"})

        with patch(
            "notion_ops.operations.file_uploads.httpx.post",
            side_effect=[bad, good],
        ) as m, patch("notion_ops.utils.retry.time.sleep"):
            result = file_uploads.send(
                "https://files.notion.com/u/2",
                b"data",
                filename="x.png",
                content_type="image/png",
            )

        assert result["id"] == "u-2"
        assert m.call_count == 2


# ---------------------------------------------------------------------------
# upload_file()
# ---------------------------------------------------------------------------


class TestUploadFile:
    def test_upload_file_infers_png(self, file_uploads, tmp_path):
        png_path = tmp_path / "figure.png"
        png_path.write_bytes(b"\x89PNG\r\n\x1a\nfakedata")

        create_resp = _make_response(
            200,
            json_body={"id": "upload-png-1", "upload_url": "https://files.notion.com/u/png1"},
        )
        send_resp = _make_response(200, json_body={"id": "upload-png-1", "status": "uploaded"})

        with patch(
            "notion_ops.operations.file_uploads.httpx.post",
            side_effect=[create_resp, send_resp],
        ) as m:
            upload_id = file_uploads.upload_file(png_path)

        assert upload_id == "upload-png-1"
        assert m.call_count == 2

        # Step 1 body
        create_body = m.call_args_list[0].kwargs["json"]
        assert create_body["filename"] == "figure.png"
        assert create_body["content_type"] == "image/png"
        assert create_body["mode"] == "single_part"

        # Step 2: posted to upload_url with multipart "file" field
        send_args = m.call_args_list[1]
        assert send_args.args[0] == "https://files.notion.com/u/png1"
        sent = send_args.kwargs["files"]["file"]
        assert sent[0] == "figure.png"
        assert sent[2] == "image/png"

    def test_upload_file_explicit_content_type(self, file_uploads, tmp_path):
        weird_path = tmp_path / "data.bin"
        weird_path.write_bytes(b"raw")

        create_resp = _make_response(
            200,
            json_body={"id": "u-bin", "upload_url": "https://files.notion.com/u/bin"},
        )
        send_resp = _make_response(200, json_body={"id": "u-bin"})

        with patch(
            "notion_ops.operations.file_uploads.httpx.post",
            side_effect=[create_resp, send_resp],
        ) as m:
            upload_id = file_uploads.upload_file(
                weird_path, content_type="application/x-custom"
            )

        assert upload_id == "u-bin"
        body = m.call_args_list[0].kwargs["json"]
        assert body["content_type"] == "application/x-custom"

    def test_upload_file_missing_path_raises(self, file_uploads, tmp_path):
        missing = tmp_path / "nope.png"
        with pytest.raises(FileNotFoundError):
            file_uploads.upload_file(missing)

    def test_upload_file_missing_id_in_response_raises(self, file_uploads, tmp_path):
        png = tmp_path / "x.png"
        png.write_bytes(b"data")

        bad_create = _make_response(200, json_body={"status": "no-id-field"})

        with patch(
            "notion_ops.operations.file_uploads.httpx.post", return_value=bad_create
        ):
            with pytest.raises(NotionOpsError, match="missing id or upload_url"):
                file_uploads.upload_file(png)


# ---------------------------------------------------------------------------
# image_block()
# ---------------------------------------------------------------------------


class TestImageBlock:
    def test_image_block_no_caption(self, file_uploads):
        block = file_uploads.image_block("upload-aaa")
        assert block == {
            "object": "block",
            "type": "image",
            "image": {
                "type": "file_upload",
                "file_upload": {"id": "upload-aaa"},
            },
        }

    def test_image_block_with_caption(self, file_uploads):
        block = file_uploads.image_block("upload-bbb", caption="Figure 1: results")
        assert block["object"] == "block"
        assert block["type"] == "image"
        img = block["image"]
        assert img["type"] == "file_upload"
        assert img["file_upload"] == {"id": "upload-bbb"}
        assert isinstance(img["caption"], list)
        rt = img["caption"][0]
        assert rt["type"] == "text"
        assert rt["text"]["content"] == "Figure 1: results"


# ---------------------------------------------------------------------------
# upload_and_attach()
# ---------------------------------------------------------------------------


class TestUploadAndAttach:
    def test_upload_and_attach_wires_through(self, file_uploads, tmp_path):
        png = tmp_path / "fig.png"
        png.write_bytes(b"\x89PNGdata")

        create_resp = _make_response(
            200,
            json_body={
                "id": "u-attach",
                "upload_url": "https://files.notion.com/u/attach",
            },
        )
        send_resp = _make_response(200, json_body={"id": "u-attach"})

        with patch(
            "notion_ops.operations.file_uploads.httpx.post",
            side_effect=[create_resp, send_resp],
        ) as m:
            block = file_uploads.upload_and_attach(png, caption="Hello")

        assert m.call_count == 2
        assert block["type"] == "image"
        assert block["image"]["file_upload"] == {"id": "u-attach"}
        assert block["image"]["caption"][0]["text"]["content"] == "Hello"


# ---------------------------------------------------------------------------
# Client integration
# ---------------------------------------------------------------------------


class TestClientIntegration:
    def test_file_uploads_attribute_present(self, client):
        assert isinstance(client.file_uploads, FileUploads)
