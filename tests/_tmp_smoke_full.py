# -*- coding: utf-8 -*-
"""综合冒烟测试：构建整个控件墙 + 新增类 + 媒体播放器渲染"""
import sys

sys.path.insert(0, r"C:\Users\shen_zhe\Desktop\autowork")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

from windows.management.widget_page import (
    _WidgetGalleryTab, _MediaPlayerDialog, _DemoMessageBox, _DemoFluentWindow,
    _SettingsDemoDialog,
)
from qfluentwidgets import FluentIcon

app = QApplication(sys.argv)
ok = []


def t(label, fn):
    try:
        fn()
        ok.append(label)
        print("OK  :", label)
    except Exception as e:
        print("FAIL:", label, "->", type(e).__name__, e)


# 新增图标存在性
for n in ("PAUSE", "CANCEL", "PLAY", "INFO", "STOP"):
    t(f"FluentIcon.{n}", lambda n=n: getattr(FluentIcon, n))

# 控件墙整体构建（触发所有 fill 方法，含新增分组）
tab = _WidgetGalleryTab()
tab.show()

# 对话框基类示例
t("_DemoMessageBox", lambda: _DemoMessageBox())

# 媒体播放器：构造 + 演示渲染
player = _MediaPlayerDialog()
player.show()


def after_render():
    try:
        pix = player._screen.pixmap()
        assert pix is not None and not pix.isNull(), "screen pixmap is null"
        # 跑了几帧表明 timer 在推进
        print(f"OK  : MediaPlayer rendered, frame={player._frame_count}, "
              f"pixmap{player._screen.pixmap().width()}x{player._screen.pixmap().height()}")
        ok.append("MediaPlayer rendered")
    except Exception as e:
        print("FAIL: MediaPlayer render ->", type(e).__name__, e)
    # 截图保存
    try:
        player.grab().save(r"C:\Users\shen_zhe\Desktop\autowork\tests\_tmp_media.png")
        print("OK  : media screenshot saved")
    except Exception as e:
        print("FAIL: media screenshot ->", e)
    player.close()
    app.quit()


QTimer.singleShot(600, after_render)
QTimer.singleShot(4000, app.quit)
app.exec()

print("\n==== 结果 ====")
print(f"通过 {len(ok)} 项")
for x in ok:
    print("  +", x)
