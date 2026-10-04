# -*- coding: utf-8 -*-
"""端口 SpinBox 数字被上下箭头遮挡 offscreen 冒烟（2026-10-04 用户截图）

复现：qfw `SpinBox` 把自绘上下按钮叠在右侧却不为文本预留空间，固定宽度
（TCP 卡原为 110px）在放大字号下会把数字压到按钮下面。
本脚本用 16pt 字号 + 深色主题复现并验证修复：
  1. 对照组：旧写法（固定 110px）文本侵入按钮区、新写法（FittedSpinBox）不侵入；
  2. 真实远程页「连接」TCP 卡：`tcp_port` / `spin_port` 均为 FittedSpinBox 且文本
     清晰（含最长文本 65535）；
  3. 截图两张到 tools/_scratch/。

运行（唯一全依赖解释器）：
    QT_QPA_PLATFORM=offscreen python tools/smoke/smoke_port_spin_fit.py
"""
import os
import sys
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

from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics   # noqa: E402
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel,  # noqa: E402
                               QVBoxLayout, QWidget)
from qfluentwidgets import SpinBox, Theme, setTheme             # noqa: E402
from core.spin_fit_patch import FittedSpinBox                   # noqa: E402

_SCRATCH = os.path.join(PROJECT_ROOT, "tools", "_scratch")
_PT = 16                     # 放大字号复现用户场景
_PORT = 9897                 # 用户实际连的 frps 端口形态（4 位）

app = QApplication([])
for _f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
           r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
    if os.path.exists(_f):
        QFontDatabase.addApplicationFont(_f)
app.setFont(QFont("Microsoft YaHei", _PT))
setTheme(Theme.DARK)          # 与用户截图同为深色


def _geom(sb):
    """(文本右缘, 按钮左缘, 控件宽)"""
    fm = QFontMetrics(sb.font())
    sample = "65535" if isinstance(sb, FittedSpinBox) else str(sb.value())
    return sb.lineEdit().x() + fm.horizontalAdvance(sample), sb.upButton.x(), sb.width()


# ---------- 1) 对照组：旧写法 vs 新写法 ----------
def _card(title, spin):
    box = QWidget()
    box.setStyleSheet("background: #202020;")
    v = QVBoxLayout(box)
    v.setContentsMargins(18, 14, 18, 14)
    v.setSpacing(6)
    lbl = QLabel(title)
    lbl.setStyleSheet("color: #d0d0d0;")
    v.addWidget(lbl)
    row = QHBoxLayout()
    row.addWidget(QLabel("端口:"))
    row.addWidget(spin)
    row.addStretch(1)
    v.addLayout(row)
    return box


legacy = SpinBox()
legacy.setRange(1, 65535)
legacy.setValue(_PORT)
legacy.setFixedWidth(110)          # 修复前的写法

fixed = FittedSpinBox()
fixed.setRange(1, 65535)
fixed.setValue(_PORT)

root = QWidget()
root.setStyleSheet("background: #202020;")
rl = QVBoxLayout(root)
rl.setContentsMargins(0, 0, 0, 0)
rl.setSpacing(0)
rl.addWidget(_card(f"修复前：setFixedWidth(110)  @ {_PT}pt", legacy))
rl.addWidget(_card(f"修复后：FittedSpinBox 按字体自适应  @ {_PT}pt", fixed))
root.resize(560, 200)
root.show()
app.processEvents()

lg = _geom(legacy)
fg = _geom(fixed)
print(f"[1] 旧写法 文本右缘={lg[0]} 按钮左缘={lg[1]} 宽={lg[2]} "
      f"{'（数字被遮挡，复现用户现象）' if lg[0] > lg[1] else '（本例未遮挡）'}")
print(f"[1] 新写法 文本右缘={fg[0]} 按钮左缘={fg[1]} 宽={fg[2]} "
      f"{'OK 文本在按钮左侧' if fg[0] + 4 <= fg[1] else '仍遮挡'}")
assert fg[0] + 4 <= fg[1], f"FittedSpinBox 仍遮挡：{fg}"
snap1 = os.path.join(_SCRATCH, "smoke_port_spin_compare.png")
root.grab().save(snap1)

# ---------- 2) 真实远程页「连接」TCP 卡 ----------
import windows.remote_session.remote_hub as rh                  # noqa: E402
from core import app_settings as _as                            # noqa: E402

_SETTINGS = {"ssh_user": "newbv", "ssh_pass": "devpw",
             "tcp_ssh_user": "root", "tcp_ssh_pass": "srvpw",
             "tcp_servers": [], "frpc_server": {}}


class _Host(QWidget):
    def _load_settings(self):
        return dict(_SETTINGS)

    def _save_settings(self, patch):
        _SETTINGS.update(patch)

    def _append_log(self, _msg):
        pass

    def _show_info_bar(self, *_a, **_k):
        pass


rh.get_session_manager = lambda: MagicMock()
rh._app_settings_merged = lambda: dict(_SETTINGS)
_as.get_merged = lambda: dict(_SETTINGS)
_as.get = lambda k, default=None: _SETTINGS.get(k, default)
_as.set = lambda k, v: _SETTINGS.__setitem__(k, v)

host = _Host()
host.setStyleSheet("background: #202020;")
lay = QVBoxLayout(host)
lay.setContentsMargins(0, 0, 0, 0)
visitor = rh.VisitorWork(host, None, host)      # 只建「连接」视图，不拉 RemoteHub
lay.addWidget(visitor)
host.resize(900, 320)
host.show()
for _ in range(6):
    app.processEvents()

# XTCP 模式（默认）：量「本地端口」——隐藏的卡片没有几何，必须在可见时量
assert isinstance(visitor.spin_port, FittedSpinBox), type(visitor.spin_port)
_t, _b, _w = _geom(visitor.spin_port)
assert _t + 4 <= _b, f"spin_port 文本侵入按钮区：文本右缘 {_t} / 按钮左缘 {_b}"
print(f"[2] spin_port（XTCP 本地端口）: 宽={_w} 文本右缘={_t} 按钮左缘={_b} OK")

visitor.mode_seg.setCurrentItem("tcp")          # 切到 TCP 直连卡
for _ in range(4):
    app.processEvents()
visitor.tcp_host.setText("49.235.34.253")
visitor.tcp_port.setValue(_PORT)
app.processEvents()

assert isinstance(visitor.tcp_port, FittedSpinBox), type(visitor.tcp_port)
_t, _b, _w = _geom(visitor.tcp_port)
assert _t + 4 <= _b, f"tcp_port 文本侵入按钮区：文本右缘 {_t} / 按钮左缘 {_b}"
print(f"[2] tcp_port（TCP 直连端口）: 宽={_w} 文本右缘={_t} 按钮左缘={_b} OK")
snap2 = os.path.join(_SCRATCH, "smoke_port_spin_fixed.png")
host.grab().save(snap2)

print("SMOKE_OK",
      f"legacy_clipped={lg[0] > lg[1]}",
      f"fitted_width={fg[2]}",
      f"real_tcp_port_width={visitor.tcp_port.width()}",
      f"pt={_PT}",
      f"shots={os.path.basename(snap1)},{os.path.basename(snap2)}")
sys.stdout.flush()

# offscreen 收尾：直接退出解释器时 Qt 静态析构偶发 0xC0000005
import gc                                                       # noqa: E402
root.close()
host.close()
app.processEvents()
del root, host, visitor, legacy, fixed
gc.collect()
os._exit(0)
