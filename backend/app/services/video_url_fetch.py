"""Download a public, directly addressable video for local frame sampling."""

from __future__ import annotations

import http.client
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

from ..config import settings
from .net_utils import UnsafeUrlError, parse_http_url, resolve_public_host

VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".mpeg", ".mpg", ".ogv"}
MIME_SUFFIXES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
    "video/mpeg": ".mpeg",
    "video/ogg": ".ogv",
}


class VideoFetchError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _public_connection(host: str, port: int, scheme: str) -> http.client.HTTPConnection:
    """Connect to an already validated public IP while retaining TLS SNI/Host."""
    ips = resolve_public_host(host, port)
    if scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=settings.request_timeout, context=ssl.create_default_context()
        )
    else:
        connection = http.client.HTTPConnection(host, port, timeout=settings.request_timeout)

    def connect_pinned(_address, timeout=None, source_address=None):
        last_error: OSError | None = None
        for ip in ips:
            try:
                return socket.create_connection(
                    (ip, port), timeout=timeout, source_address=source_address
                )
            except OSError as exc:
                last_error = exc
        raise last_error or OSError("No public IP could be reached")

    # http.client keeps the original hostname for Host and HTTPS SNI, but the
    # socket connects only to one of the public IPs validated above.
    connection._create_connection = connect_pinned
    return connection


def fetch_video_url(url: str, workdir: Path) -> tuple[Path, str, str]:
    """Return (temporary video path, final URL, display filename).

    Redirects are checked one hop at a time. No cookies or browser credentials
    are forwarded to a third-party video host.
    """
    current = url
    max_bytes = settings.max_upload_mb * 1024 * 1024
    deadline = time.monotonic() + 180

    for _ in range(5):
        parsed = parse_http_url(current)
        if parsed.username or parsed.password:
            raise UnsafeUrlError("Video URLs with embedded credentials are blocked.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        connection = _public_connection(parsed.hostname or "", port, parsed.scheme)
        target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if parsed.query:
            target += "?" + quote(parsed.query, safe="/%:@!$&'()*+,;=-._~?&=")

        try:
            connection.request(
                "GET",
                target,
                headers={
                    "User-Agent": "TAYO/0.1 (local video triage)",
                    "Accept": "video/*, application/octet-stream;q=0.5",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise VideoFetchError("Video redirect has no Location header.")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise VideoFetchError(
                    f"Video server returned HTTP {response.status}.", status_code=502
                )

            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].lower()
            suffix = Path(unquote(parsed.path)).suffix.lower()
            if content_type in {"application/vnd.apple.mpegurl", "application/x-mpegurl"}:
                raise VideoFetchError("Streaming playlists are not a single video file.", 415)
            if not (content_type.startswith("video/") or
                    (content_type == "application/octet-stream" and suffix in VIDEO_SUFFIXES)):
                raise VideoFetchError(
                    f"URL did not return a video file (content-type: {content_type or 'unknown'}).",
                    415,
                )

            length = response.getheader("Content-Length")
            if length and length.isdecimal() and int(length) > max_bytes:
                raise VideoFetchError(
                    f"Video is larger than the {settings.max_upload_mb} MB limit.", 413
                )
            suffix = suffix if suffix in VIDEO_SUFFIXES else MIME_SUFFIXES.get(content_type, ".bin")
            path = workdir / f"download{suffix}"
            total = 0
            with path.open("wb") as handle:
                while True:
                    if time.monotonic() > deadline:
                        raise VideoFetchError("Video download timed out.", 504)
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise VideoFetchError(
                            f"Video is larger than the {settings.max_upload_mb} MB limit.", 413
                        )
                    handle.write(chunk)
            if total == 0:
                raise VideoFetchError("Video URL returned an empty file.", 400)
            filename = Path(unquote(parsed.path)).name[:120] or path.name
            return path, current, filename
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise VideoFetchError(f"Could not fetch video: {exc}", 502) from exc
        finally:
            connection.close()

    raise VideoFetchError("Too many video redirects.", 400)
