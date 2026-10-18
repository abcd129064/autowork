# -*- coding: utf-8 -*-
"""亚克力菜单：几何修正、exec 补丁、可见性增强视图与菜单工厂。"""

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication
from qfluentwidgets import MenuAnimationType, RoundMenu, isDarkTheme
from qfluentwidgets.components.material import AcrylicMenu
from qfluentwidgets.components.material.acrylic_menu import (
    AcrylicMenuActionListWidget, AcrylicMenuBase)
from qfluentwidgets.components.widgets.menu import (
    MenuActionListWidget, MenuAnimationManager)

from core.perf import is_acrylic_enabled, is_animation_enabled


def _ensure_menu_fit(menu, anchor=None):
    """菜单显示后强制几何修正（全屏/高DPI/多屏兜底）：
    ① 按完整条目高度重算 view/菜单尺寸（库在弹出前按屏幕剩余空间压缩
    view，高 DPI 下坐标失配会导致末尾条目被截）；② 清除动画残留 mask；
    ③ 以触发锚点所在屏幕为基准钳制几何。

    库的定位基于「光标所在屏幕」（getCurrentScreen 取 QCursor.pos()），
    全屏/多屏下光标与程序不同屏（键盘触发、光标移走、屏幕交界）时菜单
    会直接落在另一块屏幕上；因此钳制基准必须用 anchor（触发控件全局
    坐标，必在程序所在屏幕）而非 menu.pos()——菜单已落错屏时后者是
    自证式错误，会把菜单继续钉在错误屏幕上。"""
    view = menu.view
    try:
        view.adjustSize()  # 无参 = 完整条目高度（不受屏幕空间压缩）
        menu.adjustSize()
        menu.clearMask()
        # 屏幕基准优先级：anchor 所在屏幕 → 菜单父窗口所在屏幕 → 主屏
        scr = QApplication.screenAt(anchor) if anchor is not None else None
        if scr is None:
            parent = menu.parentWidget()
            if parent is not None:
                scr = QApplication.screenAt(
                    parent.window().geometry().center())
        if scr is None:
            scr = QApplication.primaryScreen()
        rect = scr.availableGeometry()
        w = menu.width() + 5
        h = menu.height()
        x = min(menu.x(), rect.right() - w)
        y = menu.y()
        # 菜单整体已在屏幕上方之外（跨屏错位）时拉回屏幕顶部
        if y < rect.top():
            y = rect.top() + 4
        if y + h > rect.bottom():
            # 优先向上翻转（顶边对齐屏幕顶），仍放不下则钳制底边
            flipped = rect.top() + 4
            y = flipped if flipped + h <= rect.bottom() else rect.bottom() - h
        menu.move(max(rect.left(), x), y)
        view.viewport().update()
    except Exception:
        pass


def _patch_acrylic_exec(menu):
    """PySide6/Shiboken 会将实例级 menu.exec 解析到 C++ QMenu.exec()，
    导致 AcrylicMenuBase.exec()（截屏→模糊→绘制亚克力）永不执行。
    给实例绑定 Python 级 exec 属性可绕过此劫持。

    运行时动态判断：
    - 亚克力开 → AcrylicMenuBase.exec（截屏→模糊→绘制）
    - 亚克力关 → RoundMenu.exec（轻量纯色背景，零 GPU 开销）
    - 动画关 → MenuAnimationType.NONE
    切换开关后下一次弹出即生效，无需重启。"""
    def _exec(pos, ani=True, aniType=None):
        if aniType is None:
            aniType = (MenuAnimationType.DROP_DOWN if is_animation_enabled()
                       else MenuAnimationType.NONE)
        if not is_animation_enabled():
            # 无动画路径：绕过动画管理器，避免残留 mask / 视口不刷新
            mgr = MenuAnimationManager.make(menu, aniType)
            p = mgr._endPosition(pos)
            if is_acrylic_enabled():
                menu.view.acrylicBrush.grabImage(
                    QRect(p, menu.layout().sizeHint()))
            menu.clearMask()
            menu.move(p)
            menu.show()
            # 显示后同步几何修正：以锚点屏幕拉回（库按光标屏幕定位，
            # 光标与程序不同屏时会落在错误屏幕；同步修正保证首帧正确）
            _ensure_menu_fit(menu, pos)
            if menu.isSubMenu:
                menu.menuItem.setSelected(True)
            return
        if is_acrylic_enabled():
            AcrylicMenuBase.exec(menu, pos, ani=ani, aniType=aniType)
        else:
            RoundMenu.exec(menu, pos, ani=ani, aniType=aniType)
        # 动画路径同样兜底：动画结束后校正被压缩/越界的几何
        # （anchor 传 pos，确保最终落在程序所在屏幕而非光标屏幕）
        QTimer.singleShot(300, lambda: _ensure_menu_fit(menu, pos))
    menu.exec = _exec


class _VisibleAcrylicView(AcrylicMenuActionListWidget):
    """增强亚克力菜单视图：优化模糊半径/噪点/着色层参数，使磨砂玻璃效果清晰可见。

    库默认参数（模糊半径35 + 噪点0.03 + 着色alpha 150/200）在浅色均匀背景上
    会把模糊结果洗成近乎纯色，看不出磨砂感；此处调整为：
    适度模糊保留背景轮廓 + 可见噪点纹理 + 更低着色不透明度。

    亚克力开关关闭时跳过 brush 绘制，直接使用普通菜单背景（即时生效）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 库默认模糊半径 35 过强，背景内容糊成均匀色；15 保留更多背景细节，
        # 让磨砂层能透出背后内容的明暗变化
        self.acrylicBrush.setBlurRadius(15)
        # 库默认噪点不透明度 0.03 几乎不可见；实际值 0.03 保持默认，
        # 若调高到 0.15 可呈现亚克力标志性颗粒感，但当前选择保留默认
        self.acrylicBrush.noiseOpacity = 0.03

    def paintEvent(self, e):
        """亚克力关时改绘纯色背景（不手动填充会整体不可见），开则走截屏模糊"""
        if not is_acrylic_enabled():
            # 亚克力关闭：绘制纯色背景替代截屏模糊（transparent 属性使
            # QSS 背景透明，不手动填充会导致菜单整体不可见）
            painter = QPainter(self.viewport())
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(43, 43, 43) if isDarkTheme()
                             else QColor(243, 243, 243))
            painter.drawRect(self.viewport().rect())
            MenuActionListWidget.paintEvent(self, e)
            return
        super().paintEvent(e)

    def _updateAcrylicColor(self):
        """按主题调着色层不透明度：深色 35%/浅色 27%，让模糊细节不被洗白"""
        if isDarkTheme():
            # 深色模式：着色层 90/255≈35%，让模糊内容透出更多
            self.acrylicBrush.tintColor = QColor(32, 32, 32, 90)
            self.acrylicBrush.luminosityColor = QColor(0, 0, 0, 0)
        else:
            # 浅色模式：着色层 70/255≈27%，避免库默认 59% 白色把模糊细节洗白
            self.acrylicBrush.tintColor = QColor(255, 255, 255, 70)
            self.acrylicBrush.luminosityColor = QColor(255, 255, 255, 0)


class VisibleAcrylicMenu(AcrylicMenu):
    """亚克力菜单（增强可见度）：深色/浅色主题均有明显磨砂玻璃效果"""

    def __init__(self, title="", parent=None):
        super().__init__(title, parent)
        self.setUpMenu(_VisibleAcrylicView(self))
        _patch_acrylic_exec(self)


def _create_menu(title="", parent=None):
    """工厂函数：统一创建亚克力菜单实例。
    亚克力/动画开关在弹出时动态判断（_patch_acrylic_exec），
    因此无需根据配置选择不同控件类型，切换即时生效。"""
    return VisibleAcrylicMenu(title, parent)


def _patch_dropdown_button(btn):
    """菜单栏下拉按钮补丁：库内 _showMenu 弹出后不做几何兜底，
    全屏/高DPI 下 view 被屏幕空间压缩导致末尾条目截断；
    包装一层在弹出后强制 _ensure_menu_fit 修正。"""
    orig = btn._showMenu

    def _show_menu_fixed():
        orig()
        m = btn.menu()
        if m is not None:
            # 锚点取按钮全局坐标（按钮必在程序窗口所在屏幕）
            QTimer.singleShot(0, lambda: _ensure_menu_fit(
                m, btn.mapToGlobal(btn.rect().topLeft())))

    btn._showMenu = _show_menu_fixed


def _exec_menu(menu, global_pos):
    """统一菜单弹出：亚克力/动画行为由实例级 exec 动态决定"""
    menu.exec(global_pos)
