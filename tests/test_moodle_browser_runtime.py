from __future__ import annotations


def test_runtime_reports_msedge_only_without_opening_a_page() -> None:
    from aaos.moodle.browser_runtime import EdgeRuntime

    runtime = EdgeRuntime(lambda: (_ for _ in ()).throw(OSError("private")))

    result = runtime.inspect_msedge()

    assert result.channel == "msedge"
    assert result.available is False
    assert result.version is None
    assert result.code == "EDGE_UNAVAILABLE"


def test_persistent_factory_uses_only_msedge_and_owned_profile() -> None:
    from aaos.moodle.browser_runtime import launch_msedge_persistent_context

    calls: dict[str, object] = {}

    class Context:
        def close(self) -> None:
            calls["closed"] = True

    class Chromium:
        def launch_persistent_context(self, profile: str, **kwargs: object) -> Context:
            calls["profile"] = profile
            calls["kwargs"] = kwargs
            return Context()

    class Playwright:
        chromium = Chromium()

        def stop(self) -> None:
            calls["stopped"] = True

    context = launch_msedge_persistent_context(
        channel="msedge",
        headless=False,
        user_data_dir="C:/safe/profile",
        accept_downloads=False,
        service_workers="block",
        start=lambda: Playwright(),
    )
    context.close()

    assert calls["profile"] == "C:/safe/profile"
    assert calls["kwargs"] == {
        "channel": "msedge",
        "headless": False,
        "accept_downloads": False,
        "service_workers": "block",
    }
    assert calls["closed"] is True
    assert calls["stopped"] is True
