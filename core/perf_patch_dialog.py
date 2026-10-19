# -*- coding: utf-8 -*-
"""弹窗淡入/淡出降级补丁（B1 patch 层第七片）。

从 core/perf.py 下沉：拦截 MaskDialogBase.showEvent/done，按动画开关、
像素阈值与 fade mode 选择 direct/snapshot/card 路径。快照构建、阴影恢复、
动画开关等实现仍由 core.perf 注入，保留既有 monkeypatch 点。

快照路径是为弱 GPU 准备的：整窗只合成一次再淡入，用首帧延迟换
淡入阶段不掉帧；任一分支失败都必须回落到原始 showEvent。
"""


def apply_dialog_animation_patch(animation_enabled, over_degrade, resolve_mode,
                                 restore_shadow, start_snapshot_fade,
                                 cleanup_snapshot_fade) -> None:
    """把淡入/淡出逻辑挂到 MaskDialogBase 上，重复调用不会重复打补丁。

    Qt 相关的 import 放在函数内：core.perf 在无 Qt 的纯逻辑测试里也会被
    导入，顶层 import 会让那些用例直接失败。
    打补丁前用 _perf_dialog_patched 做幂等判断，避免多次调用后动画叠加。
    """
    try:
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import (QDialog, QGraphicsOpacityEffect)
        from qfluentwidgets.components.dialog_box.mask_dialog_base import (
            MaskDialogBase)
    except Exception:
        return
    if getattr(MaskDialogBase, "_perf_dialog_patched", False):
        return
    orig_show = MaskDialogBase.showEvent
    orig_done = MaskDialogBase.done

    def _restore_shadow(self, blur_radius):
        """按半径恢复弹窗阴影，具体实现由 core.perf 注入。

        走注入而不是在这里直接建阴影，是为了保留既有 monkeypatch 点。
        """
        restore_shadow(self, blur_radius)

    def _stop_fade(self):
        """停掉正在跑的淡入动画并清掉引用。

        不先停就换新动画，两条动画会同时改同一个 opacity，出现闪烁；
        stop 与属性赋值都吞异常，控件已销毁时不能让关闭流程崩掉。
        """
        ani = getattr(self, "_perf_fade_ani", None)
        if ani is not None:
            try:
                ani.stop()
            except Exception:
                pass
        try:
            self._perf_fade_ani = None
        except Exception:
            pass

    def _show(self, e):
        """按动画开关、降级档位与 fade mode 选择显示路径。

        三种路径的取舍：snapshot 整窗快照后淡入，弱 GPU 上帧率最稳；
        direct 直接显示并压阴影，几乎零成本；card 只对内容控件做透明度
        动画，避开快照开销。任一路径异常都回落到原始 showEvent。
        """
        try:
            anim_on = animation_enabled(self)
            degrade = over_degrade(self)
            # 动画关掉时直接当作 none，不再询问 resolve_mode，省一次判定
            mode = "none" if not anim_on else resolve_mode(self)
            if mode == "snapshot":
                blur = 30 if degrade else 60
                # 先清旧快照和图形效果，否则重复 show 会叠加半透明层
                _stop_fade(self)
                cleanup_snapshot_fade(self, show_card=False)
                try:
                    self.setGraphicsEffect(None)
                except Exception:
                    pass
                _restore_shadow(self, blur)
                if start_snapshot_fade(self, blur):
                    super(MaskDialogBase, self).showEvent(e)
                    return
                # 快照没建起来就退回 direct，不能让对话框停在不可见状态
                mode = "direct"
            if mode in ("none", "direct"):
                # 降级档位把阴影半径压到 30，配合更淡的投影保证仍有层次
                if degrade:
                    try:
                        self.setShadowEffect(
                            30, (0, 10), QColor(0, 0, 0, 100))
                    except Exception:
                        pass
                _stop_fade(self)
                cleanup_snapshot_fade(self)
                self.setGraphicsEffect(None)
                self.widget.setGraphicsEffect(None)
                _restore_shadow(self, 30 if degrade else 60)
                super(MaskDialogBase, self).showEvent(e)
                return
            if mode == "card":
                _stop_fade(self)
                cleanup_snapshot_fade(self)
                try:
                    self.setGraphicsEffect(None)
                    _restore_shadow(self, 60)
                    # card 模式只给内容控件套透明度，不动整窗，快照成本为零
                    eff = QGraphicsOpacityEffect(self.widget)
                    eff.setOpacity(0)
                    self.widget.setGraphicsEffect(eff)
                    ani = QPropertyAnimation(eff, b"opacity", self.widget)
                    ani.setStartValue(0)
                    ani.setEndValue(1)
                    ani.setDuration(200)
                    ani.setEasingCurve(QEasingCurve.InSine)
                    ani.finished.connect(lambda: _restore_shadow(self, 60))
                    self._perf_fade_ani = ani
                    ani.start()
                    super(MaskDialogBase, self).showEvent(e)
                    return
                except Exception:
                    restore_shadow(self, 60)
        except Exception:
            pass
        # 所有路径都异常时仍要显示，否则对话框永远出不来
        orig_show(self, e)

    def _done(self, code):
        """关闭弹窗：按模式决定同步关闭还是先淡出再关。

        snapshot/none/direct 直接走 QDialog.done，保证关闭一定发生；
        card 模式播 100ms 淡出，播完才真正 done，异常时回落到原始 done。
        """
        try:
            # 先清快照，避免关闭动画期间还挂着上一帧的整窗位图
            cleanup_snapshot_fade(self)
            anim_on = animation_enabled(self)
            mode = "none" if not anim_on else resolve_mode(self)
            if mode in ("none", "direct", "snapshot"):
                _stop_fade(self)
                self.setGraphicsEffect(None)
                self.widget.setGraphicsEffect(None)
                QDialog.done(self, code)
                return
            if mode == "card":
                _stop_fade(self)
                try:
                    self.setGraphicsEffect(None)
                    eff = QGraphicsOpacityEffect(self.widget)
                    eff.setOpacity(1)
                    self.widget.setGraphicsEffect(eff)
                    ani = QPropertyAnimation(eff, b"opacity", self.widget)
                    ani.setStartValue(1)
                    ani.setEndValue(0)
                    ani.setDuration(100)

                    def _finish(code=code):
                        """淡出结束：清掉图形效果再真正关闭。

                        先清效果再 done，否则关闭瞬间会闪一帧旧 opacity。
                        """
                        try:
                            self.setGraphicsEffect(None)
                            self.widget.setGraphicsEffect(None)
                        except Exception:
                            pass
                        QDialog.done(self, code)

                    ani.finished.connect(_finish)
                    self._perf_fade_ani = ani
                    ani.start()
                    return
                except Exception:
                    pass
        except Exception:
            pass
        # 兜底走原始 done，关闭语义不能被动画补丁改变
        orig_done(self, code)

    MaskDialogBase.showEvent = _show
    MaskDialogBase.done = _done
    MaskDialogBase._perf_dialog_patched = True
