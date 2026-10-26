from __future__ import annotations

import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from backend.app.services.net_utils import UnsafeUrlError
from backend.app.services.video_url_fetch import VideoFetchError, fetch_video_url


class _VideoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/clip.mp4")
            self.end_headers()
            return
        if self.path == "/private":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/clip.mp4")
            self.end_headers()
            return
        if self.path == "/large.mp4":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", "2000000")
            self.end_headers()
            return
        if self.path == "/page":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html></html>")
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.end_headers()
        self.wfile.write(b"small video bytes")

    def log_message(self, _format: str, *_args) -> None:
        pass


class VideoUrlFetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _VideoHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def _resolve(self, host: str, _port: int) -> list[str]:
        if host == "public.example":
            return ["127.0.0.1"]  # Only the local test server; production never allows this.
        raise UnsafeUrlError("Target resolves to a private address.")

    def test_direct_file_and_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "backend.app.services.video_url_fetch.resolve_public_host",
                side_effect=self._resolve,
            ):
                path, final_url, filename = fetch_video_url(
                    f"http://public.example:{self.server.server_port}/redirect",
                    Path(directory),
                )
                self.assertEqual(path.read_bytes(), b"small video bytes")
        self.assertEqual(path.name, "download.mp4")
        self.assertEqual(filename, "clip.mp4")
        self.assertTrue(final_url.endswith("/clip.mp4"))

    def test_private_redirect_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "backend.app.services.video_url_fetch.resolve_public_host",
                side_effect=self._resolve,
            ):
                with self.assertRaises(UnsafeUrlError):
                    fetch_video_url(
                        f"http://public.example:{self.server.server_port}/private",
                        Path(directory),
                    )

    def test_size_and_type_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "backend.app.services.video_url_fetch.resolve_public_host",
                side_effect=self._resolve,
            ), patch("backend.app.services.video_url_fetch.settings.max_upload_mb", 1):
                for path, status in (("/large.mp4", 413), ("/page", 415)):
                    with self.subTest(path=path):
                        with self.assertRaises(VideoFetchError) as caught:
                            fetch_video_url(
                                f"http://public.example:{self.server.server_port}{path}",
                                Path(directory),
                            )
                        self.assertEqual(caught.exception.status_code, status)

    def test_blob_url_is_not_a_direct_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UnsafeUrlError):
                fetch_video_url("blob:https://example.com/video", Path(directory))


if __name__ == "__main__":
    unittest.main()
