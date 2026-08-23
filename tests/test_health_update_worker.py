# -*- coding: utf-8 -*-
"""HealthUpdateWorker（xqzg update_health 重置健康度）单元测试

不启动线程：直接同步调用 run()，信号同步派发到本地收集器。
通过 monkeypatch 假 Session 脚本化接口响应，覆盖：
- 凭据缺失 / 登录失败 → error 信号
- 逐台 POST：URL / JSON body / CSRF headers 校验
- 成功汇总、业务失败（code != 200）、HTTP 错误、超时、非 JSON
- Session 过期（401/403）自动重登重试一次；重登失败
- 空 device_code 直接计失败（不调接口）
"""
import requests

from workers import table_worker
from workers.table_worker import HealthUpdateWorker


class _Resp:
    """脚本化 HTTP 响应；payload=None 时 json() 抛 ValueError"""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _Session:
    """假 Session：每次 post 弹出共享响应队列队首（异常元素直接抛出）"""

    def __init__(self, responses):
        self.responses = responses
        self.posted = []          # [(url, kwargs), ...]
        self.cookies = {"csrftoken": "FAKE_CSRF"}

    def post(self, url, **kwargs):
        self.posted.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch(monkeypatch, responses, username="u", password="p"):
    """凭据 + 假 Session 工厂（共享响应队列），返回 (worker, 已创建 sessions)"""
    created = []

    def _factory():
        s = _Session(responses)
        created.append(s)
        return s

    monkeypatch.setattr(table_worker, "_load_api_credentials",
                        lambda: {"api1": {"username": username,
                                          "password": password}})
    monkeypatch.setattr(table_worker.requests, "Session", _factory)
    return (HealthUpdateWorker([("A", "CODE-A"), ("B", "CODE-B")]), created)


def _run(w):
    """同步执行 run()，返回 {ok, fails, errors}（信号同步派发）"""
    box = {}
    w.result_ready.connect(
        lambda ok, fails: box.update(ok=ok, fails=fails))
    w.error.connect(lambda msg: box.setdefault("errors", []).append(msg))
    w.run()
    return box


def test_no_credentials_emits_error(monkeypatch):
    """凭据未配置：直接 error，不发起任何请求"""
    monkeypatch.setattr(table_worker, "_load_api_credentials",
                        lambda: {"api1": {}})
    box = _run(HealthUpdateWorker([("A", "CODE-A")]))
    assert "ok" not in box
    assert box["errors"] and "未配置" in box["errors"][0]


def test_login_failure_emits_error(monkeypatch):
    """登录返回非 200：整体 error「登录失败」"""
    w, _ = _patch(monkeypatch, [_Resp(status_code=500)])
    box = _run(w)
    assert "ok" not in box
    assert box["errors"] and "登录失败" in box["errors"][0]


def test_update_posts_correct_url_body_and_csrf_headers(monkeypatch):
    """全成功：登录与逐台 update 的 URL/body/CSRF headers 正确"""
    w, created = _patch(monkeypatch, [_Resp(200), _Resp(200, {"code": 200}),
                                      _Resp(200, {"code": 200})])
    box = _run(w)
    assert box["ok"] == ["A", "B"]
    assert box["fails"] == []
    assert "errors" not in box
    s = created[0]
    login_url, login_kw = s.posted[0]
    assert login_url == table_worker.API1_LOGIN_URL
    assert login_kw["json"] == {"username": "u", "password": "p"}
    for idx, code in ((1, "CODE-A"), (2, "CODE-B")):
        url, kwargs = s.posted[idx]
        assert url == table_worker.API1_UPDATE_HEALTH_URL
        assert kwargs["json"] == {"device_code": code, "health": 4000}
        # Django CSRF：Referer 与 X-CSRFToken（取 session cookie）
        assert kwargs["headers"]["Referer"] == f"{table_worker.API1_BASE}/"
        assert kwargs["headers"]["X-CSRFToken"] == "FAKE_CSRF"


def test_business_failure_reported(monkeypatch):
    """HTTP 200 但业务 code != 200：按失败计入（不能只看状态码）"""
    w, _ = _patch(monkeypatch, [_Resp(200),
                                _Resp(200, {"code": 400, "msg": "设备不存在"}),
                                _Resp(200, {"code": 200})])
    box = _run(w)
    assert box["ok"] == ["B"]
    assert box["fails"] == [("A", "设备不存在")]


def test_http_error_reported(monkeypatch):
    """非 200 状态码：失败描述含状态码"""
    w, _ = _patch(monkeypatch, [_Resp(200), _Resp(status_code=500),
                                _Resp(200, {"code": 200})])
    box = _run(w)
    assert box["ok"] == ["B"]
    assert box["fails"] == [("A", "HTTP 500")]


def test_timeout_reported(monkeypatch):
    """请求超时：失败「请求超时」"""
    w, _ = _patch(monkeypatch, [_Resp(200), requests.exceptions.Timeout(),
                                _Resp(200, {"code": 200})])
    box = _run(w)
    assert box["ok"] == ["B"]
    assert box["fails"] == [("A", "请求超时")]


def test_invalid_json_reported(monkeypatch):
    """响应非 JSON：失败「响应解析失败」"""
    w, _ = _patch(monkeypatch, [_Resp(200), _Resp(200, payload=None),
                                _Resp(200, {"code": 200})])
    box = _run(w)
    assert box["ok"] == ["B"]
    assert box["fails"] == [("A", "响应解析失败")]


def test_session_expiry_relogin_retry_once(monkeypatch):
    """首次 403 → 自动重登 → 重试成功，后续设备用新 session"""
    w, created = _patch(monkeypatch, [_Resp(200), _Resp(status_code=403),
                                      _Resp(200), _Resp(200, {"code": 200}),
                                      _Resp(200, {"code": 200})])
    box = _run(w)
    assert box["ok"] == ["A", "B"]
    assert box["fails"] == []
    assert len(created) == 2           # 重登创建第二个 session
    # 新 session：重登 login + A 的重试 + B 的正常请求
    assert len(created[1].posted) == 3


def test_relogin_failure_reported(monkeypatch):
    """403 后重登失败：该台计失败，其余设备继续"""
    w, _ = _patch(monkeypatch, [_Resp(200), _Resp(status_code=403),
                                _Resp(status_code=500),
                                _Resp(200, {"code": 200})])
    box = _run(w)
    assert box["ok"] == ["B"]
    assert box["fails"] == [("A", "会话过期且重新登录失败")]


def test_empty_device_code_fails_without_request(monkeypatch):
    """无设备码：直接计失败，不发 update 请求"""
    monkeypatch.setattr(table_worker, "_load_api_credentials",
                        lambda: {"api1": {"username": "u",
                                          "password": "p"}})
    responses = [_Resp(200), _Resp(200, {"code": 200})]
    created = []

    def _factory():
        s = _Session(responses)
        created.append(s)
        return s

    monkeypatch.setattr(table_worker.requests, "Session", _factory)
    box = _run(HealthUpdateWorker([("A", ""), ("B", "CODE-B")]))
    assert box["ok"] == ["B"]
    assert box["fails"] == [("A", "无设备码（球桌库未匹配到设备）")]
    # 只发出 login + B 的 update（A 未发请求）
    assert len(created[0].posted) == 2
