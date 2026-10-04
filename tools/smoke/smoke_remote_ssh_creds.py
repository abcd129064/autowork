# -*- coding: utf-8 -*-
"""远程 SSH 凭据拆分 offscreen 冒烟：设备（XTCP）与直连主机（TCP）不再互相覆盖

背景（用户反馈 2026-10-04）：「xtcp 中没有修改账号密码的逻辑，如果我修改了
tcp 那边的账号密码，隧道这边就会显示账号密码错误」——直连主机常是 frps
服务器（root），设备账号是 newbv，原先两路共用 ssh_user/ssh_pass，改一路
必然污染另一路。

断言：
  1. XTCP 卡出现「SSH 账号 / SSH 密码」且预填设备凭据（ssh_user/ssh_pass）；
  2. TCP 卡预填直连凭据（tcp_ssh_user/tcp_ssh_pass），两卡初值互不相同；
  3. `_save_ssh_credentials()` 只写 ssh_user/ssh_pass（不碰 tcp_ssh_*）；
  4. `_tcp_connect()` 只写 tcp_ssh_user/tcp_ssh_pass（不碰 ssh_*），并把表单
     凭据显式传给 open_direct_session(username=/password=)。
并截图两张到 tools/_scratch/ 作为验收证据。

运行（唯一全依赖解释器）：
    QT_QPA_PLATFORM=offscreen python tools/smoke/smoke_remote_ssh_creds.py
"""
import os
import sys
import time
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（同 windows/aftersale_panel.py 顶部，规避 conda Qt 冲突） ----
import importlib.util as _iu
try:
    _spec = _iu.find_spec('PySide6')
    if _spec is not None:
        for _d in (list(_spec.submodule_search_locations or []) +
                   [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                 'System32')]):
            if _d and os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass
except Exception:
    pass

from PySide6.QtGui import QFont, QFontDatabase                   # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

import windows.remote_session.remote_hub as rh                   # noqa: E402
from core.visitor_probe import VisitorProber                      # noqa: E402

_SCRATCH = os.path.join(PROJECT_ROOT, "tools", "_scratch")

# 出厂设置：设备凭据 newbv（球桌），直连凭据 root（frps 服务器）——刻意不同
_INITIAL = {
    "ssh_user": "newbv", "ssh_pass": "devpw",
    "tcp_ssh_user": "root", "tcp_ssh_pass": "srvpw",
    "tcp_servers": ["203.0.113.9:22"],
    "frps_server_addr": "127.0.0.1:7000",
}
_RECORDS = [
    {"serverName": "snk_11601", "bindPort": 14687, "tableId": "116-01",
     "source": "snk"},
]


class _Host(QWidget):
    """宿主替身：RemoteHub 以宿主为 Qt parent，TunnelConfWork 构造期即读设置"""

    def __init__(self, settings):
        super().__init__()
        self.settings = dict(settings)
        self.saved = []          # 每次 _save_settings 的增量
        self.logs = []

    def _load_settings(self):
        return dict(self.settings)

    def _save_settings(self, patch):
        self.saved.append(dict(patch))
        self.settings.update(patch)

    def _append_log(self, msg):
        self.logs.append(msg)

    def _show_info_bar(self, msg, kind, duration=4000):
        self.logs.append(f"[bar:{kind}] {msg}")

    def _ensure_session_window(self):
        return QWidget()


mgr = MagicMock()
mgr.records.return_value = [dict(r) for r in _RECORDS]
mgr.is_running.return_value = False
mgr.sessions_on_port.return_value = []
mgr.is_transferring_on_port.return_value = False
mgr.frps_server_addr.return_value = "127.0.0.1:7000"
mgr.admin_port = 7400

frps = MagicMock()
frps.snapshot.return_value = {"proxies": {}, "state": "unconfigured"}
frps.all_proxies.return_value = {}
frps.serverinfo.return_value = None
frps.online.return_value = None
frps.info.return_value = None
frps.client_version.return_value = None

prober = VisitorProber()
prober.start = lambda: None          # 掐掉探测线程：本冒烟不碰网络

rh.get_session_manager = lambda: mgr
rh.get_frps_client = lambda: frps
rh.get_prober = lambda: prober

app = QApplication([])

# venv 的 PySide6 不带 fonts 目录，不注册系统中文字体截图中文全是方框
for _f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
           r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
    if os.path.exists(_f):
        QFontDatabase.addApplicationFont(_f)
app.setFont(QFont("Microsoft YaHei", 9))

host = _Host(_INITIAL)
# 零磁盘读写：remote_hub 内部 `from core import app_settings` 后直接调 set/get，
# 故必须换模块属性（换 rh.app_settings 无效）
from core import app_settings as _as                            # noqa: E402
rh._app_settings_merged = lambda: dict(host.settings)
_as.get_merged = lambda: dict(host.settings)
_as.get = lambda key, default=None: host.settings.get(key, default)
_as.set = lambda key, value: host.settings.__setitem__(key, value)
host.resize(1680, 900)
lay = QVBoxLayout(host)
lay.setContentsMargins(0, 0, 0, 0)
hub = rh.RemoteHub(host)
lay.addWidget(hub)
hub.switchTo(hub.visitor_work)          # 连接视图（showEvent 会回填凭据）
host.show()
app.processEvents()

visitor = hub.visitor_work


def _pump(seconds=0.3):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.02)


# 1) XTCP 卡：新增字段 + 预填设备凭据
assert visitor.xtcp_user.text() == "newbv", visitor.xtcp_user.text()
assert visitor.xtcp_pass.text() == "devpw", visitor.xtcp_pass.text()
assert visitor.tcp_user.text() == "root", visitor.tcp_user.text()
assert visitor.tcp_pass.text() == "srvpw", visitor.tcp_pass.text()
print("[1] 预填：XTCP 卡=", visitor.xtcp_user.text(), "/", visitor.xtcp_pass.text(),
      " TCP 卡=", visitor.tcp_user.text(), "/", visitor.tcp_pass.text())

snap1 = os.path.join(_SCRATCH, "smoke_remote_ssh_creds_xtcp.png")
host.grab().save(snap1)      # XTCP 模式（默认）→ 新字段可见

# 2) 改设备凭据 → 只写 ssh_user/ssh_pass
visitor.xtcp_user.setText("newbv2")
visitor.xtcp_pass.setText("devpw2")
visitor._mark_cred_dirty(True)          # 模拟用户手输（setText 不触发 textEdited）
assert visitor._save_ssh_credentials() is True
assert host.saved[-1] == {"ssh_user": "newbv2", "ssh_pass": "devpw2"}, host.saved[-1]
assert "tcp_ssh_user" not in host.saved[-1] and "tcp_ssh_pass" not in host.saved[-1]
assert host.settings["tcp_ssh_user"] == "root"      # 直连凭据未被污染
print("[2] 设备凭据写回 =", host.saved[-1], "（tcp_ssh_* 保持",
      host.settings["tcp_ssh_user"], "）")

# 3) 刷新（force=False）不得冲掉用户输入
visitor._mark_cred_dirty(True)
visitor._load_ssh_credentials()
assert visitor.xtcp_user.text() == "newbv2", "脏标记下刷新冲掉了输入"
visitor.xtcp_user.setText("newbv")
visitor.xtcp_pass.setText("devpw")
visitor._mark_cred_dirty(False)

# 4) 切到 TCP 模式 → 直连只写 tcp_ssh_*，且显式传凭据
visitor.mode_seg.setCurrentItem("tcp")
_pump(0.3)
assert not visitor.tcp_card.isHidden(), "TCP 卡未显示"
assert visitor.add_card.isHidden(), "TCP 模式下 XTCP 添加卡仍可见"
visitor.tcp_host.setText("203.0.113.9")
visitor.tcp_port.setValue(22)
visitor.tcp_user.setText("root2")
visitor.tcp_pass.setText("srvpw2")
_dev_before = (host.settings["ssh_user"], host.settings["ssh_pass"])
visitor._tcp_connect("ssh")
assert host.saved[-1] == {"tcp_ssh_user": "root2", "tcp_ssh_pass": "srvpw2"}, \
    host.saved[-1]
assert (host.settings["ssh_user"], host.settings["ssh_pass"]) == _dev_before, \
    "直连连接污染了设备凭据！"
_kw = mgr.open_direct_session.call_args
assert _kw.args[0] == "ssh" and _kw.args[1] == "203.0.113.9" and _kw.args[2] == 22
assert _kw.kwargs["username"] == "root2" and _kw.kwargs["password"] == "srvpw2", _kw
print("[3] TCP 直连写回 =", host.saved[-1], "→ open_direct_session",
      _kw.kwargs["username"], "（设备凭据仍为", host.settings["ssh_user"], "）")

snap2 = os.path.join(_SCRATCH, "smoke_remote_ssh_creds_tcp.png")
host.grab().save(snap2)      # TCP 模式 → 直连表单可见

# 5) 敏感键登记（DPAPI 覆盖）+ 键域路由
from core import secrets as _sec                                # noqa: E402
assert "tcp_ssh_pass" in _sec.SENSITIVE_KEYS, _sec.SENSITIVE_KEYS
assert _as.KEY_DOMAIN.get("tcp_ssh_user") == "credentials"
assert _as.KEY_DOMAIN.get("tcp_ssh_pass") == "credentials"
print("[4] 敏感键 =", _sec.SENSITIVE_KEYS)

print("SMOKE_OK",
      f"xtcp={visitor.xtcp_user.text()}/{visitor.xtcp_pass.text()}",
      f"tcp={host.settings['tcp_ssh_user']}/{host.settings['tcp_ssh_pass']}",
      f"device_creds_intact={_dev_before == (host.settings['ssh_user'], host.settings['ssh_pass'])}",
      f"shots={os.path.basename(snap1)},{os.path.basename(snap2)}")
sys.stdout.flush()

# 显式收尾：offscreen 下直接退出解释器时 Qt 静态析构偶发 0xC0000005
# （RDP 冒烟同样只做断言收尾；此处多一步 close + gc，仍崩则 os._exit 兜底）
import gc                                                        # noqa: E402
hub.close()
host.close()
app.processEvents()
del hub, host, visitor, mgr, frps, prober
gc.collect()
os._exit(0)
