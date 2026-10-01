# -*- coding: utf-8 -*-
"""远程页「RDP」页签 + 操作列移除 RDP 链接 offscreen 冒烟（2026-10-04）

需求（用户原话）：「远程面板中，将操作的 rdp 移除，并单独建一个 rdp 页签」。
原因：xtcp visitor 的 bindPort 只映射远端 22（SSH/SFTP 复用），RDP 需 3389
专用隧道（约定 rdp_<serverName>），属后续功能——行内 RDP 链接点了必然连到
SSH 端口。

本脚本构建**真实 RemoteHub**（manager / frps 感知换替身，质量探测器用真实类
但掐掉 start，绝不联网、绝不启停 frpc），断言：
  1. 会话总览六视图、操作列 4 链接（SSH/SFTP/断开/删除）不含 RDP；
  2. Pivot 页签 6 项且末位「RDP」，RDP 页状态卡「未开通」；
  3. 弹出面板 nav_icons 键集覆盖全部页签（AST 静态校验）。
并截图两张到 tools/_scratch/ 作为验收证据。

运行（唯一全依赖解释器）：
    QT_QPA_PLATFORM=offscreen python tools/smoke/smoke_remote_rdp_tab.py
"""
import ast
import os
import sys
import time
from pathlib import Path

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

from unittest.mock import MagicMock                              # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase                   # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

import windows.remote_session.remote_hub as rh                   # noqa: E402
from core.ops_link_delegate import LINKS_ROLE                     # noqa: E402
from core.visitor_probe import VisitorProber                      # noqa: E402

_SCRATCH = os.path.join(PROJECT_ROOT, "tools", "_scratch")

# 三种行样本：普通 / 已预热 / 已断开（保留注册）
_RECORDS = [
    {"serverName": "snk_11601", "bindPort": 14687, "tableId": "116-01",
     "source": "snk"},
    {"serverName": "snk_4008", "bindPort": 12547, "tableId": "40-08",
     "source": "snk", "prewarmedAt": "2026-10-04 10:00"},
    {"serverName": "snk_12006", "bindPort": 26600, "tableId": "",
     "source": "snk", "disabled": True},
]
_EXPECT_TABS = ["会话总览", "连接", "连接质量", "frps 代理", "隧道配置", "RDP"]


class _Host(QWidget):
    """宿主窗口替身：RemoteHub 以宿主为 Qt parent，且 TunnelConfWork 构造期即读
    `self._win._load_settings()`（故不能先建 hub 再补 _win）"""

    def __init__(self):
        super().__init__()
        self.logs = []
        self.bars = []

    def _load_settings(self):
        return {}

    def _save_settings(self, _patch):
        return None

    def _append_log(self, msg):
        self.logs.append(msg)

    def _show_info_bar(self, msg, kind, duration=4000):
        self.bars.append((msg, kind))

    def _ensure_session_window(self):
        self.session_window_calls = getattr(self, "session_window_calls", 0) + 1
        return QWidget()


# ---- 宿主与页面（替身：manager / frps 感知全换 MagicMock；质量探测器真类但掐 start） ----
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

# venv 的 PySide6 不带 fonts 目录（QFontDatabase 报 cannot find font directory），
# 不注册系统中文字体时截图里中文全是方框，无法作为验收证据。
for _f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
           r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
    if os.path.exists(_f):
        QFontDatabase.addApplicationFont(_f)
app.setFont(QFont("Microsoft YaHei", 9))

host = _Host()
host.resize(1680, 900)
lay = QVBoxLayout(host)
lay.setContentsMargins(0, 0, 0, 0)
hub = rh.RemoteHub(host)
lay.addWidget(hub)
host.show()


def _pump(cond, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def _row_links(work, row):
    item = work.table.item(row, 8)
    assert item is not None, f"第 {row} 行操作列没有委托数据"
    return [link[0] for link in item.data(LINKS_ROLE)]


# 1) 页签集合
labels = [text for _page, text, _icon in hub._page_meta]
assert labels == _EXPECT_TABS, f"页签集合异常：{labels}"
assert hub.pages()[-1] is hub.rdp_work, "RDP 不是末位页签"
names = [page.objectName() for page in hub.pages()]
assert len(set(names)) == len(names), f"objectName 重复：{names}"
print(f"[1] Pivot 页签 {len(labels)} 项 {labels}（末位 objectName="
      f"{hub.rdp_work.objectName()}）")

# 2) 会话总览：操作列不再有 RDP
work = hub.session_work
work._refresh_now()
assert _pump(lambda: work.table.rowCount() == len(_RECORDS)), "会话总览未出数据"
links_by_row = [_row_links(work, r) for r in range(work.table.rowCount())]
assert links_by_row[0] == ["SSH", "SFTP", "断开", "删除"], links_by_row[0]
assert links_by_row[2] == ["SSH", "SFTP", "删除"], links_by_row[2]
assert all("RDP" not in ls for ls in links_by_row), f"仍有 RDP：{links_by_row}"
print(f"[2] 会话总览 {work.table.rowCount()} 行操作列 = {links_by_row}")
snap1 = os.path.join(_SCRATCH, "smoke_remote_rdp_session.png")
host.grab().save(snap1)      # 抓整页：含顶部 Pivot 页签条，可看到末位「RDP」

# 3) RDP 页：状态卡「未开通」+ 打开会话中心可用
rdp = hub.rdp_work
hub.switchTo(rdp)
_pump(lambda: True, timeout=0.6)
rdp.refresh()
app.processEvents()
assert rdp.num_tunnels.text() == str(len(_RECORDS)), rdp.num_tunnels.text()
assert rdp.num_rdp.text() == "0", rdp.num_rdp.text()
assert rdp.num_state.text() == "未开通", rdp.num_state.text()
rdp._on_open_sessions()
assert getattr(host, "session_window_calls", 0) == 1, "「打开会话中心」未回调宿主"
cap = rh.RdpWork._rdp_capable([{"serverName": "rdp_snk_1"},
                               {"serverName": "snk_2", "remotePort": "3389"},
                               {"serverName": "snk_3", "remotePort": 22}])
assert [r["serverName"] for r in cap] == ["rdp_snk_1", "snk_2"], cap
print(f"[3] RDP 页 隧道={rdp.num_tunnels.text()} 可用="
      f"{rdp.num_rdp.text()} 状态={rdp.num_state.text()} 检测位={len(cap)} 命中")
snap2 = os.path.join(_SCRATCH, "smoke_remote_rdp_tab.png")
host.grab().save(snap2)

# 4) 弹出面板 nav_icons 覆盖全部页签（AST 静态校验）
_src = Path(rh.__file__).resolve().parents[2] / "main_window" / "main_window.py"
_keys = set()
for _node in ast.walk(ast.parse(_src.read_text(encoding="utf-8"))):
    if not isinstance(_node, ast.Dict):
        continue
    for _k, _v in zip(_node.keys, _node.values):
        if (isinstance(_k, ast.Constant) and _k.value == "remoteHub"
                and isinstance(_v, ast.Dict)):
            _keys = {_kk.value for _kk in _v.keys
                     if isinstance(_kk, ast.Constant)}
assert _keys == set(labels), f"nav_icons {sorted(_keys)} != 页签 {labels}"
print(f"[4] nav_icons 覆盖 {len(_keys)} 键 {sorted(_keys)}")

print("SMOKE_OK",
      f"tabs={len(labels)}",
      f"ops_links={links_by_row[0]}",
      f"rdp_state={rdp.num_state.text()}",
      f"shots={os.path.basename(snap1)},{os.path.basename(snap2)}")
