"""Fail-closed direct web_fetch semantics (CONTRACT v1.8 #7)."""

from __future__ import annotations

import logging
import urllib.request

import pytest

from activegraph import ToolContext, ToolError
from activegraph.tools.web_fetch import WebFetchInput, web_fetch


def _context(**kwargs) -> ToolContext:
    return ToolContext(
        behavior_name="test",
        event_id="evt_test",
        frame=None,
        idempotency_key="key",
        timeout_seconds=1.0,
        logger=logging.getLogger("test.web_fetch"),
        **kwargs,
    )


def test_direct_web_fetch_fails_before_network_contact(monkeypatch) -> None:
    contacted = False

    def forbidden(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("network contacted")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    with pytest.raises(ToolError) as exc_info:
        web_fetch.fn(WebFetchInput(url="https://example.test"), _context())
    assert exc_info.value.reason == "tool.unrecorded_external_io"
    assert contacted is False


def test_explicit_live_unrecorded_web_fetch_is_allowed(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

        def geturl(self):
            return "https://example.test/final"

        def read(self):
            return b"ok"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Response())
    result = web_fetch.fn(
        WebFetchInput(url="https://example.test"),
        _context(external_io_mode="live_unrecorded"),
    )
    assert result.text == "ok"
    assert result.status == 200
    assert result.final_url == "https://example.test/final"
