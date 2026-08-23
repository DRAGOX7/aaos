from __future__ import annotations

from pathlib import Path

from aaos.moodle.models import AuthPaths, MoodleSettings


def test_provisioner_uses_only_a_confined_headed_msedge_context(tmp_path: Path) -> None:
    from aaos.moodle.browser_auth import BrowserSessionProvisioner

    calls: list[object] = []

    class Page:
        url = "https://moodle.example.edu/current/my/courses.php"

        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            calls.append((url, wait_until, timeout))

        def wait_for_url(self, url: str, *, timeout: int) -> None:
            calls.append((url, timeout))

    class Context:
        def new_page(self) -> Page:
            calls.append("new_page")
            return Page()

        def close(self) -> None:
            calls.append("close")

    def launch(**kwargs: object) -> Context:
        calls.append(kwargs)
        return Context()

    settings = MoodleSettings(
        "moodle:fixture:browser-smoke", "https://moodle.example.edu", "browser", "fixture"
    )
    result = BrowserSessionProvisioner(
        AuthPaths(tmp_path / "credential", tmp_path / "auth" / "fixture"), launch
    ).provision(settings=settings)

    assert result.code == "BROWSER_AUTHENTICATED"
    assert calls[0] == {
        "channel": "msedge",
        "headless": False,
        "user_data_dir": tmp_path / "auth" / "fixture" / "playwright-edge-profile",
        "accept_downloads": False,
        "service_workers": "block",
    }
    assert calls[1] == "new_page"
    assert calls[2] == (
        "https://moodle.example.edu/current/login/index.php",
        "domcontentloaded",
        600000,
    )
    assert calls[3] == ("https://moodle.example.edu/current/my/courses.php", 600000)
    assert calls[4] == "close"


def test_provisioner_maps_login_and_host_failures_to_fixed_codes(tmp_path: Path) -> None:
    from aaos.moodle.browser_auth import BrowserSessionProvisioner

    class LoginPage:
        url = "https://moodle.example.edu/current/login/index.php"

        def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
            assert wait_until == "domcontentloaded"
            assert timeout == 600000

        def wait_for_url(self, _url: str, *, timeout: int) -> None:
            assert timeout == 600000

    class Context:
        def new_page(self) -> LoginPage:
            return LoginPage()

        def close(self) -> None:
            return None

    settings = MoodleSettings(
        "moodle:fixture:browser-smoke", "https://moodle.example.edu", "browser", "fixture"
    )
    paths = AuthPaths(tmp_path / "credential", tmp_path / "auth" / "fixture")
    assert BrowserSessionProvisioner(paths, lambda **_: Context()).provision(
        settings=settings
    ).code == ("BROWSER_AUTH_REQUIRED")
    assert (
        BrowserSessionProvisioner(paths, lambda **_: (_ for _ in ()).throw(OSError()))
        .provision(settings=settings)
        .code
        == "BROWSER_PROVISIONING_FAILED"
    )
