# -*- coding: utf-8 -*-
"""sync_transport 离线单测：请求构造、代理绕过、契约校验、错误处理。"""
import json
from types import SimpleNamespace

import pytest

from database import sync_transport as st


class FakeResponse:
    def __init__(self, body):
        self._body = body if isinstance(body, str) else json.dumps(body)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body.encode("utf-8")


class FakeOpener:
    def __init__(self, body=None, error=None):
        self.body = body if body is not None else {"ok": True}
        self.error = error
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append((req, timeout))
        if self.error:
            raise self.error
        return FakeResponse(self.body)


def _transport(opener):
    return st.SyncTransport("http://127.0.0.1:8000", "desk-001", "secret",
                            timeout=3.5, opener=opener)


def test_request_builds_url_method_headers_and_json():
    o = FakeOpener()
    t = _transport(o)
    assert t.query("aftersale_records", cursor="ts|7", page_size=42) == {"ok": True}
    req, timeout = o.calls[0]
    assert req.full_url == "http://127.0.0.1:8000/api/sync/aftersale_records/query"
    assert req.get_method() == "POST"
    assert req.get_header("X-sync-device") == "desk-001"
    assert req.get_header("X-sync-token") == "secret"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data.decode("utf-8")) == {
        "cursor": "ts|7", "page_size": 42, "active_only": False}
    assert timeout == 3.5


def test_health_and_upsert_paths():
    o = FakeOpener()
    t = _transport(o)
    t.health()
    assert o.calls[-1][0].get_method() == "GET"
    assert o.calls[-1][0].full_url.endswith("/api/sync/health")
    t.upsert("aftersale_records", [{"problem": "x"}], dry_run=True)
    assert json.loads(o.calls[-1][0].data.decode())["dry_run"] is True
    assert o.calls[-1][0].full_url.endswith("/api/sync/aftersale_records/upsert")


def test_default_opener_bypasses_system_proxy(monkeypatch):
    # 3.13 的 build_opener 对空 ProxyHandler 会直接不挂载该 handler；
    # 断言口径看行为：即使环境里配了代理，opener 也不带非空代理表。
    import urllib.request
    monkeypatch.setenv("HTTP_PROXY", "http://evil.proxy:8080")
    opener = st.default_opener()
    proxy_handlers = [h for h in opener.handlers
                      if isinstance(h, urllib.request.ProxyHandler)]
    assert not any(getattr(h, "proxies", {}) for h in proxy_handlers)


def test_check_contract_version_mismatch_rejects():
    o = FakeOpener({"contract_version": "9.9.9"})
    with pytest.raises(st.SyncTransportError, match="contract mismatch"):
        _transport(o).check_contract()


def test_check_contract_version_match():
    o = FakeOpener({"contract_version": st.CLIENT_CONTRACT_VERSION})
    assert _transport(o).check_contract()["contract_version"] == st.CLIENT_CONTRACT_VERSION


def test_http_error_becomes_transport_error():
    import urllib.error
    err = urllib.error.HTTPError("u", 401, "Unauthorized", {},
                                 __import__("io").BytesIO(b"denied"))
    with pytest.raises(st.SyncTransportError, match="HTTP 401") as ei:
        _transport(FakeOpener(error=err)).health()
    assert ei.value.status == 401


def test_invalid_json_response():
    with pytest.raises(st.SyncTransportError, match="invalid JSON"):
        _transport(FakeOpener("not-json")).health()


def test_from_settings_disabled_by_default(monkeypatch):
    fake = SimpleNamespace(get_merged=lambda: {})
    monkeypatch.setattr(st, "app_settings", fake)
    assert st.SyncTransport.from_settings() is None


def test_from_settings_builds_transport(monkeypatch):
    cfg = {"sync_api": {"enabled": True, "base_url": "http://x/",
                        "device_id": "d1", "token": "tk"}}
    monkeypatch.setattr(st, "app_settings",
                        SimpleNamespace(get_merged=lambda: cfg))
    t = st.SyncTransport.from_settings()
    assert t.base_url == "http://x" and t.device_id == "d1" and t.token == "tk"
