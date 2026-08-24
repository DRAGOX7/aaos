from __future__ import annotations

import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, cast

import pytest
import trustme
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from aaos.moodle.browser import MoodleBrowserSource
from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator
from aaos.moodle.collection import CourseDiscovery


@pytest.fixture
def local_moodle_https(tmp_path: Path) -> Iterator[tuple[str, Path, Path]]:
    """Serve only synthetic Moodle-shaped HTML on loopback HTTPS."""
    certificate = tmp_path / "fixture-cert.pem"
    private_key = tmp_path / "fixture-key.pem"
    authority = trustme.CA()
    leaf = authority.issue_cert("localhost", "127.0.0.1")
    leaf.cert_chain_pems[0].write_to_path(certificate)
    leaf.private_key_pem.write_to_path(private_key)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/current/my/courses.php":
                self.send_response(404)
                self.end_headers()
                return
            server = cast(Any, self.server)
            origin = f"https://127.0.0.1:{server.server_port}"
            payload = (
                f"<a data-moodle-course='true' href='{origin}/current/course/view.php?id=42'>"
                "Synthetic</a><nav data-pagination-pages='0' "
                "data-pagination-exhausted='true'></nav>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}", certificate, private_key
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        certificate.unlink(missing_ok=True)
        private_key.unlink(missing_ok=True)


def test_real_msedge_parses_only_local_https_course_index(
    local_moodle_https: tuple[str, Path, Path], tmp_path: Path
) -> None:
    origin, certificate, private_key = local_moodle_https
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(tmp_path / "edge-profile"),
            channel="msedge",
            headless=True,
            accept_downloads=False,
            service_workers="block",
            ignore_https_errors=True,
        )
        navigator = PlaywrightRestrictedNavigator(cast(Any, context), origin)
        try:
            result = MoodleBrowserSource(
                navigator, "moodle:just:browser-smoke", origin
            ).discover_courses()
        finally:
            navigator.close()
    assert isinstance(result, CourseDiscovery)
    assert tuple(course.source_id for course in result.courses) == ("42",)
    assert certificate.exists() and private_key.exists()


def test_production_modules_do_not_import_test_tls_or_disable_tls_validation() -> None:
    root = Path(__file__).parents[1] / "src" / "aaos" / "moodle"
    production = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("browser.py", "browser_auth.py", "browser_runtime.py", "browser_navigation.py")
    )
    assert "trustme" not in production
    assert "ignore_https_errors" not in production


def test_real_msedge_zero_page_context_rejects_fresh_page(tmp_path: Path) -> None:
    """Record the local headed persistent-context lifecycle contradiction safely."""
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(tmp_path / "zero-page-profile"),
            channel="msedge",
            headless=False,
            accept_downloads=False,
            service_workers="block",
        )
        try:
            pages = list(context.pages)
            if not pages:
                pages = [context.new_page()]
            for page in pages:
                page.close()
            assert context.pages == []
            with pytest.raises(PlaywrightError):
                context.new_page()
        finally:
            try:
                context.close()
            except PlaywrightError:
                pass
