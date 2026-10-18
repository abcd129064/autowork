# -*- coding: utf-8 -*-
"""帮助 → 关于 对话框与依赖清单（从 ui_mixin.py 迁出，保持旧导入路径可用）。"""

from PySide6.QtWidgets import QLabel
from qfluentwidgets import BodyLabel, CaptionLabel, MessageBoxBase, TitleLabel


# ==================== 版本信息（帮助→关于） ====================
# 版本号由 core/version.py 根据 git 分支与提交次数自动计算：
# {主.次版本}.{提交数}[-分支]，如 2.5.114-ai_build；主.次版本在
# core/version.py 的 BASE_VERSION 手工维护，修订号随提交自动递增
from core.version import APP_VERSION
GITHUB_REPO = "https://github.com/abcd129064/autowork"

# 关于弹窗「链接」区
ABOUT_LINKS = [
    ("源代码 (GitHub)", GITHUB_REPO),
    ("问题反馈 (Issues)", f"{GITHUB_REPO}/issues"),
    ("更新日志 (Commits)", f"{GITHUB_REPO}/commits"),
    ("许可证 (GPLv3)", "https://www.gnu.org/licenses/gpl-3.0.html"),
]

# 关于弹窗「开源库」区（运行时依赖）——许可证标注以 licenses/INDEX.md 为准，
# 整件作品因 qfluentwidgets 而按 GPLv3 分发，详见 docs/GPLv3依赖合规说明.md
ABOUT_OSS_LIBS = [
    ("PySide6 (Qt for Python)", "https://www.qt.io/qt-for-python"),
    ("PySide6-Fluent-Widgets (GPLv3)",
     "https://github.com/zhiyiYo/PyQt-Fluent-Widgets"),
    ("paramiko", "https://github.com/paramiko/paramiko"),
    ("darkdetect", "https://github.com/albertosottile/darkdetect"),
    ("PyInstaller", "https://github.com/pyinstaller/pyinstaller"),
]


class AboutDialog(MessageBoxBase):
    """关于弹窗：应用名/版本号 + GitHub 链接 + 开源依赖库清单

    链接用 QLabel 富文本 <a href> 实现（openExternalLinks，系统默认浏览器打开）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = TitleLabel("AutoWork", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel(
            "用于视频播放、日志管理与数据记录的桌面自动化工具", self))
        self.viewLayout.addWidget(BodyLabel(
            f"版本: {APP_VERSION} for Windows (x64)", self))

        # 链接区
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(CaptionLabel("链接", self))
        links_lbl = QLabel(self._links_html(ABOUT_LINKS), self)
        links_lbl.setOpenExternalLinks(True)
        self.viewLayout.addWidget(links_lbl)

        # 开源库区
        self.viewLayout.addSpacing(6)
        self.viewLayout.addWidget(CaptionLabel("开源库", self))
        oss_lbl = QLabel(self._links_html(ABOUT_OSS_LIBS), self)
        oss_lbl.setOpenExternalLinks(True)
        oss_lbl.setWordWrap(True)
        self.viewLayout.addWidget(oss_lbl)

        self.viewLayout.addSpacing(8)
        license_lbl = CaptionLabel(
            "本程序为自由软件，依 GNU GPL v3（或你选择的更新版本）分发，"
            "完整对应源码见上方 GitHub 仓库；各第三方组件的许可证原文见安装目录"
            "下的 licenses/。石睿轩创作，由沈喆修改第二版。", self)
        license_lbl.setWordWrap(True)
        self.viewLayout.addWidget(license_lbl)

    @staticmethod
    def _links_html(links) -> str:
        """[(标题, url), ...] → 以竖线分隔的富文本链接串"""
        return "&nbsp;&nbsp;|&nbsp;&nbsp;".join(
            f'<a href="{url}">{title}</a>' for title, url in links)
