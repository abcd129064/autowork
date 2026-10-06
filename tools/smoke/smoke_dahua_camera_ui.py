# -*- coding: utf-8 -*-
"""「相机工具」页 offscreen UI 冒烟（2026-10-07）

两段验证：
  A. ToolHub 集成：完整 ToolHub 离屏构造，第 5 页「相机工具」注册成功、优雅降级不炸容器；
  B. 页面全流程（真机 49.235.34.253:4238）：UI 登录 → 固定镜头自动置灰 →
     OSD 自动回填 → 预览开停（软断言：offscreen HWND 渲染不可视）→ 抓图落盘 → 断开复位。

运行（唯一全依赖解释器）：
    QT_QPA_PLATFORM=offscreen python tools/smoke/smoke_dahua_camera_ui.py
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（同 smoke_table_search.py） ----
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

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QWidget

CAM_ADDR = "49.235.34.253:4238"
CAM_USER = "admin"
CAM_PWD = "kaidao12"

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def wait_until(page, cond, timeout_ms: int) -> bool:
    """驱动事件循环直到 cond() 为真（worker 信号经队列连接回主线程）"""
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(50)

    def tick():
        if cond():
            loop.quit()

    timer.timeout.connect(tick)
    t = QTimer(loop)
    t.setSingleShot(True)
    t.timeout.connect(loop.quit)
    t.start(timeout_ms)
    timer.start()
    loop.exec()
    timer.stop()
    return cond()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # ---------- A. ToolHub 集成 ----------
    from main_window.tool_hub import ToolHub

    class _StubWin(QWidget):
        """ToolHub 子页构造期只需要读设置（_show_info_bar 等仅动作期触发）"""
        def _load_settings(self):
            return {}

        def _save_settings(self, _d):
            pass

    hub = ToolHub(_StubWin())
    check("A1 ToolHub 离屏构造成功", hub is not None)
    check("A2 相机页注册且未触发降级",
          getattr(hub, "dahua_camera_work", None) is not None)
    if hub.dahua_camera_work is not None:
        # qfw Pivot 会把页面 reparent 进内部 stackedWidget，用层级查找断言
        check("A3 相机页挂在 ToolHub 视图层级",
              hub.dahua_camera_work in hub.findChildren(QWidget))

    # ---------- B. 页面全流程（真机） ----------
    from windows.tools.dahua_camera import DahuaCameraWork

    page = DahuaCameraWork(None)
    page.show()
    app.processEvents()

    # B1 初始态
    check("B1 初始：PTZ 方向键全部禁用",
          not any(b.isEnabled() for b in page._ptz_btns.values()))
    check("B1b 初始：变倍/聚焦/光圈按钮全部禁用",
          not any(b.isEnabled() for b in page._zf_btns))
    check("B2 初始：OSD 获取/设置按钮全部禁用",
          not any(b.isEnabled() for b in page._iter_osd_buttons()))
    check("B3 初始：断开/预览/抓图禁用",
          not page._btn_disc.isEnabled() and not page._btn_play.isEnabled()
          and not page._btn_snap.isEnabled())
    check("B4 初始：预置点按钮禁用",
          not page._btn_preset_goto.isEnabled())

    # B2 UI 登录
    page._ed_addr.setText(CAM_ADDR)
    page._ed_user.setText(CAM_USER)
    page._ed_pwd.setText(CAM_PWD)
    page._connect()
    ok_conn = wait_until(page, lambda: page._lb_state.text() != "连接中…", 20000)
    check("B5 UI 登录回调返回（未卡死）", ok_conn, page._lb_state.text())
    # 4238 是云台机（2026-10-07 用户实机确认，官方 Demo 可转动）
    check("B6 状态显示PT机型",
          "PT机型" in page._lb_state.text(), page._lb_state.text())
    check("B7 连接后：OSD 按钮解禁",
          all(b.isEnabled() for b in page._iter_osd_buttons()))
    check("B8 PT机型：PTZ 按钮解禁",
          all(b.isEnabled() for b in page._ptz_btns.values()))
    check("B9 PT机型：预置点按钮解禁",
          page._btn_preset_goto.isEnabled())
    check("B10 PT机型：置灰提示隐藏", not page._ptz_note.isVisible())
    check("B11 断开按钮解禁", page._btn_disc.isEnabled())

    # B3 OSD 自动回填（连接成功后页面自动 get chn/time）
    ok_osd = wait_until(page, lambda: page._card_chn["edits"]["x"].text() != "", 10000)
    check("B12 通道标题 OSD 自动回填 X 坐标",
          ok_osd, f"x={page._card_chn['edits']['x'].text()} "
                  f"y={page._card_chn['edits']['y'].text()} "
                  f"show={page._card_chn['show'].isChecked()}")
    ok_time = wait_until(page, lambda: page._card_time["edits"]["x"].text() != "", 10000)
    check("B13 时间标题 OSD 自动回填",
          ok_time, f"x={page._card_time['edits']['x'].text()}")

    # B4 预览开停（软断言：offscreen 无可见渲染，但 RealPlayEx 应能建立）
    page._start_preview()
    ok_play = wait_until(page, lambda: page._btn_stop_play.isEnabled()
                         or page._btn_play.isEnabled(), 15000)
    play_ok = page._btn_stop_play.isEnabled()
    check("B14 预览请求有落点（开或报错回退，未卡死）", ok_play,
          "辅码流开启" if play_ok else "offscreen HWND 预览失败（软断言通过）")
    if play_ok:
        page._stop_preview()
        wait_until(page, lambda: page._btn_play.isEnabled(), 10000)
        check("B15 停止预览复位", page._btn_stop_play.isEnabled() is False)

    # B5 UI 抓图
    page._snap()
    ok_snap = wait_until(page, lambda: bool(page._lb_snap.text()), 15000)
    snap_path = page._lb_snap.text().replace("已存：", "")
    check("B16 抓图回调落盘", ok_snap and os.path.isfile(snap_path), snap_path)
    if ok_snap and os.path.isfile(snap_path):
        with open(snap_path, "rb") as f:
            head = f.read(3)
        check("B17 抓图为有效 JPEG", head == b"\xff\xd8\xff",
              f"{os.path.getsize(snap_path)} 字节")

    # B6 断开复位
    page._disconnect()
    app.processEvents()
    check("B18 断开后：状态复位", page._lb_state.text() == "未连接")
    check("B19 断开后：OSD 按钮禁用",
          not any(b.isEnabled() for b in page._iter_osd_buttons()))
    check("B20 断开后：预览/抓图禁用",
          not page._btn_play.isEnabled() and not page._btn_snap.isEnabled())
    check("B21 断开后：可重新连接", page._btn_conn.isEnabled())

    page._teardown()
    app.processEvents()

    # ---------- 汇总 ----------
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n=== 相机工具 UI 冒烟：{passed}/{len(results)} 通过 ===")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    code = main()
    print("（offscreen 收尾可能段错误，以上结论为准）")
    sys.exit(code)
