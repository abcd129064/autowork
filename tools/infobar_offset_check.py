# -*- coding: utf-8 -*-
"""验证 show_info_bar bottom_offset：滑入全程直接位于抬升坐标，无贴底闪现。

offscreen 下真实创建 DevicePage + FileListPanel，弹带抬升的 InfoBar，
在滑入动画期间多次采样 bar 位置：
1. 动画中途（50ms）y 即应为抬升后的值（旧实现此时还贴在底部 → 遮挡 0.5s）
2. 动画结束后不遮挡迁移按钮行
3. _info_raise 三态
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ".")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer

app = QApplication.instance() or QApplication([])

from core.utils import show_info_bar
from windows.management.device_page import DevicePage


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main():
    page = DevicePage()
    page.resize(1200, 800)
    page.show()
    wait(200)
    fp = page._file_panel
    fp._row = {"device_code": "T", "table_id": "T", "accuracy_files": ["a.jpg"]}
    fp._fields = ["accuracy_files"]
    fp._title = "精度"
    fp.show_files(fp._row, "精度", ["accuracy_files"], can_migrate=True)
    wait(500)  # 面板滑入动画
    btn_top = fp._migrate_wrap.mapTo(
        page, fp._migrate_wrap.rect().topLeft()).y()

    fails = 0
    offset = page._info_raise()
    bar = show_info_bar("测试消息", "info", title="测试",
                        parent=page, duration=-1, bottom_offset=offset)
    # 动画 200ms：50ms 处采样，y 应已等于最终抬升值（横向滑入不影响 y）
    wait(50)
    y_mid = bar.y()
    wait(600)  # 动画结束 + 定时器抬升周期（旧实现在这里才抬）
    y_end = bar.y()
    bottom = y_end + bar.height()
    overlap = bottom > btn_top and bar.x() < fp.x() + fp.width()
    print("滑入中途 y=%d，稳定后 y=%d（应相等=一开始就抬升）" % (y_mid, y_end))
    if abs(y_mid - y_end) > 2:
        print("FAIL 存在先贴底后抬升的闪现过程")
        fails += 1
    if overlap:
        print("FAIL 稳定后仍遮挡按钮行 bottom=%d btn_top=%d" % (bottom, btn_top))
        fails += 1
    else:
        print("PASS 不遮挡 bottom=%d < btn_top=%d" % (bottom, btn_top))
    # 对照组：无抬升应遮挡（证明断言有效）
    bar.close()
    wait(300)
    bar2 = show_info_bar("对照", "info", title="对照",
                         parent=page, duration=-1, bottom_offset=0)
    wait(600)
    o2 = (bar2.y() + bar2.height() > btn_top
          and bar2.x() < fp.x() + fp.width())
    print("对照组(无抬升): %s（预期遮挡）" % ("遮挡" if o2 else "不遮挡"))
    if not o2:
        print("FAIL 对照组未复现遮挡，断言失效")
        fails += 1
    bar2.close()
    wait(300)

    # _info_raise 三态
    for wrap_vis, expect in ((True, 64), (False, 40)):
        fp._migrate_wrap.setVisible(wrap_vis)
        fp.setVisible(True)
        got = page._info_raise()
        ok = got == expect
        print("_info_raise(migrate_wrap=%s)=%d 期望 %d %s" % (
            wrap_vis, got, expect, "PASS" if ok else "FAIL"))
        fails += (not ok)
    fp.setVisible(False)
    got = page._info_raise()
    ok = got == 0
    print("_info_raise(面板隐藏)=%d 期望 0 %s" % (got, "PASS" if ok else "FAIL"))
    fails += (not ok)

    # 普通（无 offset）InfoBar 不受补丁影响：贴底 margin=24
    bar3 = show_info_bar("普通", "info", title="普通",
                         parent=page, duration=-1)
    wait(600)
    expect_y = page.height() - bar3.height() - 24
    ok = abs(bar3.y() - expect_y) <= 2
    print("无抬升条 y=%d 期望贴底 %d %s" % (bar3.y(), expect_y,
                                          "PASS" if ok else "FAIL"))
    fails += (not ok)
    bar3.close()

    print("RESULT:", "ALL PASS" if fails == 0 else f"{fails} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
