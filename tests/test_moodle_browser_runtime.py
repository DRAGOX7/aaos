from __future__ import annotations


def test_runtime_reports_msedge_only_without_opening_a_page() -> None:
    from aaos.moodle.browser_runtime import EdgeRuntime

    runtime = EdgeRuntime(lambda: (_ for _ in ()).throw(OSError("private")))

    result = runtime.inspect_msedge()

    assert result.channel == "msedge"
    assert result.available is False
    assert result.version is None
    assert result.code == "EDGE_UNAVAILABLE"
