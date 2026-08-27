from __future__ import annotations

import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest
import trustme
from playwright.sync_api import sync_playwright

from aaos.documents.contracts import (
    DocumentIngestionFailure,
    IngestionErrorCode,
    RemoteFileSelection,
)
from aaos.moodle.file_download import BrowserAuthenticatedFetcher, MoodleFileAuthorizer

_HOST = "localtest.me"
_SOURCE = "moodle:local:phase3"


@pytest.fixture
def local_resource_https(
    tmp_path: Path,
) -> Iterator[tuple[str, Path, Path, dict[str, str], list[str]]]:
    """Serve one synthetic Resource chain on loopback with ephemeral test-only TLS."""
    certificate = tmp_path / "phase3-cert.pem"
    private_key = tmp_path / "phase3-key.pem"
    authority = trustme.CA()
    leaf = authority.issue_cert(_HOST)
    leaf.cert_chain_pems[0].write_to_path(certificate)
    leaf.private_key_pem.write_to_path(private_key)
    mode = {"value": "success"}
    served: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            served.append(self.path)
            if self.path == "/current/mod/resource/view.php?id=42":
                locations = {
                    "success": "/current/pluginfile.php/7/mod_resource/content/1/lecture.pdf",
                    "cross_origin": "https://other.example.test/file.pdf",
                    "loop": "/current/mod/resource/view.php?id=42",
                    "overflow": "/current/pluginfile.php/1/mod_resource/content/1/lecture.pdf",
                    "unapproved": "/current/course/view.php?id=42",
                    "oversized": "/current/pluginfile.php/7/mod_resource/content/1/large.pdf",
                }
                self.send_response(302)
                self.send_header("Location", locations[mode["value"]])
                self.end_headers()
                return
            if mode["value"] == "overflow" and "/pluginfile.php/" in self.path:
                context_id = int(self.path.split("/", maxsplit=4)[3])
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"/current/pluginfile.php/{context_id + 1}/mod_resource/content/1/lecture.pdf",
                )
                self.end_headers()
                return
            if self.path.endswith("/lecture.pdf"):
                body = b"%PDF-1.7 synthetic"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", 'attachment; filename="lecture.pdf"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.endswith("/large.pdf"):
                body = b"x" * 32
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, private_key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"https://{_HOST}:{server.server_port}"
    try:
        yield origin, certificate, private_key, mode, served
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        certificate.unlink(missing_ok=True)
        private_key.unlink(missing_ok=True)


def _selection() -> RemoteFileSelection:
    return RemoteFileSelection(1, 2, _SOURCE, "resource-cm:42", "Synthetic")


def _fetch(*, origin: str, tmp_path: Path) -> tuple[BrowserAuthenticatedFetcher, Any]:
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            str(tmp_path / "phase3-edge-profile"),
            channel="msedge",
            headless=True,
            accept_downloads=False,
            service_workers="block",
            ignore_https_errors=True,
        )
    except BaseException:
        playwright.stop()
        raise
    fetcher = BrowserAuthenticatedFetcher(
        context=cast(Any, context),
        authorizer=MoodleFileAuthorizer(
            canonical_wwwroot=origin + "/current",
            expected_source_system=_SOURCE,
        ),
    )
    return fetcher, (context, playwright)


def _close(owned: Any) -> None:
    context, playwright = owned
    try:
        context.close()
    finally:
        playwright.stop()


def test_real_msedge_request_context_fetches_only_the_authorized_resource_chain(
    local_resource_https: tuple[str, Path, Path, dict[str, str], list[str]], tmp_path: Path
) -> None:
    origin, certificate, private_key, _mode, served = local_resource_https
    assert certificate.parent == tmp_path and private_key.parent == tmp_path
    fetcher, owned = _fetch(origin=origin, tmp_path=tmp_path)
    try:
        fetched = fetcher.fetch(_selection())
    finally:
        _close(owned)
    assert fetched.body == b"%PDF-1.7 synthetic"
    assert fetched.source_filename == "lecture.pdf"
    assert served == [
        "/current/mod/resource/view.php?id=42",
        "/current/pluginfile.php/7/mod_resource/content/1/lecture.pdf",
    ]


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("cross_origin", IngestionErrorCode.UNAUTHORIZED_REDIRECT),
        ("loop", IngestionErrorCode.UNAUTHORIZED_REDIRECT),
        ("overflow", IngestionErrorCode.UNAUTHORIZED_REDIRECT),
        ("unapproved", IngestionErrorCode.UNAUTHORIZED_REDIRECT),
        ("oversized", IngestionErrorCode.DOWNLOAD_TOO_LARGE),
    ],
)
def test_real_msedge_request_context_fails_closed_for_unauthorized_or_oversized_chains(
    mode: str,
    code: IngestionErrorCode,
    local_resource_https: tuple[str, Path, Path, dict[str, str], list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if mode == "oversized":
        import aaos.moodle.file_download as module

        monkeypatch.setattr(module, "MAX_PDF_BYTES", 16)
    origin, _certificate, _private_key, fixture_mode, _served = local_resource_https
    fixture_mode["value"] = mode
    fetcher, owned = _fetch(origin=origin, tmp_path=tmp_path)
    try:
        with pytest.raises(DocumentIngestionFailure) as caught:
            fetcher.fetch(_selection())
    finally:
        _close(owned)
    assert caught.value.code is code
