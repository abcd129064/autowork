# -*- coding: utf-8 -*-
"""弹窗淡入/淡出降级补丁（B1 patch 层第七片）。

从 core/perf.py 下沉：拦截 MaskDialogBase.showEvent/done，按动画开关、
像素阈值与 fade mode 选择 direct/snapshot/card 路径。快照构建、阴影恢复、
动画开关等实现仍由 core.perf 注入，保留既有 monkeypatch 点。
"""


def apply_dialog_animation_patch(animation_enabled, over_degrade, resolve_mode,
                                 restore_shadow, start_snapshot_fade,
                                 cleanup_snapshot_fade) -> None:
    """安装 MaskDialogBase 弹窗动画补丁（幂等）。"""
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
        restore_shadow(self, blur_radius)

    def _stop_fade(self):
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
        try:
            anim_on = animation_enabled(self)
            degrade = over_degrade(self)
            mode = "none" if not anim_on else resolve_mode(self)
            if mode == "snapshot":
                blur = 30 if degrade else 60
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
                mode = "direct"
            if mode in ("none", "direct"):
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
        orig_show(self, e)

    def _done(self, code):
        try:
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
        orig_done(self, code)

    MaskDialogBase.showEvent = _show
    MaskDialogBase.done = _done
    MaskDialogBase._perf_dialog_patched = True
