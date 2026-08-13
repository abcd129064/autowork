# -*- coding: utf-8 -*-
"""kd 图片查看对话框（设备状态页文件列表「预览」入口）

卡片式对话框：标题显示文件名与序号，中间图片（等比缩放），
左右箭头/键盘 ←→ 切换列表内图片，底部复用四个迁移按钮
（使用/精度/问题/废弃），迁移成功自动刷新文件列表。

图片 URL 规则（已探测验证：静态资源无需认证）：
    http://kd.newbv.cn:30005/media/{yyyy/MM/dd}/{device_code}/{分类目录}/{文件名}
分类目录由条目的源分类经 CATEGORY_DIRS 映射；找不到时回退 pic/ 目录再试一次。
"""

import re

import requests
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy
from qfluentwidgets import (
    PushButton, ToolButton, FluentIcon, BodyLabel, CaptionLabel,
    setCustomStyleSheet)

from workers.table_worker import API2_BASE, CATEGORY_DIRS

# 图片扩展名（.bin/版本等非图片条目不启用预览）
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif")

# Django 存储同名文件时自动追加的随机去重后缀（扩展名前 _7位字母数字，
# 如 xxx_WSbTPtl.jpg）。带后缀的是重复上传副本/缩略图（320×200），
# 去后缀才是原图（1920×1080），构造 URL 时需两个变体都尝试
_DJANGO_DEDUP_RE = re.compile(r"_[0-9A-Za-z]{7}(?=\.[A-Za-z0-9]+$)")


def _name_variants(fname: str) -> list:
    """返回文件名候选：去 Django 去重后缀的原图优先，带后缀副本兜底

    带后缀的是重复上传副本（可能是 320×200 缩略图），去后缀才是
    1920×1080 原图；两者都存在时优先展示原图，原图不存在时回退副本。
    """
    stripped = _DJANGO_DEDUP_RE.sub("", fname)
    if stripped != fname:
        return [stripped, fname]
    return [fname]


def is_image_file(fname: str) -> bool:
    return str(fname).lower().endswith(_IMAGE_EXTS)


class _ImageFetchWorker(QThread):
    """后台下载图片字节流（静态资源无需认证头）"""
    done = Signal(str, bytes)   # (url, 内容)
    error = Signal(str, str)    # (url, 错误信息)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            resp = requests.get(self.url, timeout=20)
            if self._cancelled:
                return
            if resp.status_code != 200:
                self.error.emit(self.url, f"HTTP {resp.status_code}")
                return
            self.done.emit(self.url, resp.content)
        except requests.exceptions.Timeout:
            if not self._cancelled:
                self.error.emit(self.url, "下载超时")
        except requests.exceptions.RequestException as e:
            if not self._cancelled:
                self.error.emit(self.url, f"网络错误: {e}")


class ImageViewerDialog(QDialog):
    """图片查看卡片对话框

    Args:
        entries: [(文件名, 源分类), ...] 当前文件列表全量条目（翻页范围）
        index: 初始展示的条目下标
        file_path: 日期路径（yyyy/MM/dd）
        device_code: 设备编码
        device_page: DeviceStatusPage（调用 migrate_file / 迁移后刷新）
        can_migrate: 是否显示底部迁移按钮（总数/正常等视图不可迁移）
        dest_options: 迁移目标分类列表（由调用方注入，避免循环导入）
        btn_qss: 迁移按钮配色字典（由调用方注入）
    """

    def __init__(self, entries, index, file_path, device_code,
                 device_page, can_migrate=True, dest_options=(),
                 btn_qss=None, parent=None):
        super().__init__(parent)
        self._entries = list(entries)
        self._idx = max(0, min(index, len(self._entries) - 1))
        self._file_path = file_path
        self._device_code = device_code
        self._device_page = device_page
        self._dest_options = list(dest_options)
        self._btn_qss = dict(btn_qss or {})
        self._fetch_worker = None
        self._fetch_gen = 0   # 下载代际号：翻页后旧响应回来时丢弃（PySide6 的
                              # QObject.disconnect() 不支持无参调用，用代际号替代）
        self._cache = {}  # url -> QPixmap（翻页回看免重复下载）

        self.setWindowTitle("图片查看")
        self.resize(1000, 740)
        self._init_ui(can_migrate)
        self._show_index(self._idx)

    # ---------- UI ----------

    def _init_ui(self, can_migrate):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # 标题行：文件名（左对齐，与图片左边缘对齐）
        self._lbl_name = BodyLabel("", self)
        self._lbl_name.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._lbl_name)

        # 图片区：左右箭头 + 居中图片标签
        mid = QHBoxLayout()
        mid.setSpacing(6)
        self._btn_prev = ToolButton(FluentIcon.CARE_LEFT_SOLID, self)
        # 实心箭头 glyph 充满图标区域，按钮太小会被裁剪：加大固定尺寸
        self._btn_prev.setFixedSize(40, 40)
        self._btn_prev.setIconSize(QSize(24, 24))
        self._btn_prev.setToolTip("上一张（←）")
        self._btn_prev.clicked.connect(self._prev)
        mid.addWidget(self._btn_prev)

        self._lbl_img = QLabel(self)
        self._lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_img.setMinimumSize(560, 460)
        self._lbl_img.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Expanding)
        self._lbl_img.setStyleSheet("background: rgba(127,127,127,8%);"
                                    " border-radius: 8px;")
        mid.addWidget(self._lbl_img, 1)

        self._btn_next = ToolButton(FluentIcon.CARE_RIGHT_SOLID, self)
        self._btn_next.setFixedSize(40, 40)
        self._btn_next.setIconSize(QSize(24, 24))
        self._btn_next.setToolTip("下一张（→）")
        self._btn_next.clicked.connect(self._next)
        mid.addWidget(self._btn_next)
        layout.addLayout(mid, 1)

        # 状态行：序号 + 下载状态
        self._lbl_status = CaptionLabel("", self)
        self._lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._lbl_status)

        # 底部四个迁移按钮（复用 FileListPanel 配色与语义）：紧凑定宽 + 右对齐
        if can_migrate:
            self._migrate_btns = {}
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 0, 0, 0)
            btn_row.setSpacing(6)
            btn_row.addStretch(1)
            for dest in self._dest_options:
                btn = PushButton(dest, self)
                btn.setFixedWidth(72)
                qss = self._btn_qss.get(dest, "")
                if qss:
                    setCustomStyleSheet(btn, qss, qss)
                btn.setToolTip(f"将当前图片迁移到「{dest}」")
                btn.clicked.connect(lambda _=False, d=dest: self._on_migrate(d))
                self._migrate_btns[dest] = btn
                btn_row.addWidget(btn)
            layout.addLayout(btn_row)

    # ---------- 展示与翻页 ----------

    def _show_index(self, idx):
        if not (0 <= idx < len(self._entries)):
            return
        self._idx = idx
        self._fetch_gen += 1  # 递增代际号，使未完成的旧下载响应失效
        fname, _src = self._entries[idx]
        self._lbl_name.setText(fname)
        self._lbl_status.setText(f"{idx + 1} / {len(self._entries)} · 加载中...")
        self._lbl_img.setText("")
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < len(self._entries) - 1)
        self._load_image(fname, self._entries[idx][1])

    def _prev(self):
        self._show_index(self._idx - 1)

    def _next(self):
        self._show_index(self._idx + 1)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Left:
            self._prev()
        elif e.key() == Qt.Key.Key_Right:
            self._next()
        else:
            super().keyPressEvent(e)

    # ---------- 下载 ----------

    def _image_urls(self, fname, src_cat):
        """候选 URL 列表：分类目录优先，pic/ 目录兜底；每个目录再按
        文件名变体展开（原名优先，去 Django 去重后缀的原图兜底）

        实测：「正常」分类的原始照片实际存放在 pic/ 目录（normal/ 不存在），
        故正常分类直接 pic 优先；带 _7位随机后缀的文件名是重复上传副本，
        去后缀才是 1920×1080 原图。
        """
        base = f"{API2_BASE}/media/{self._file_path}/{self._device_code}"
        dirs = []
        if src_cat == "正常":
            dirs.append("pic")
        else:
            d = CATEGORY_DIRS.get(src_cat)
            if d:
                dirs.append(d)
            dirs.append("pic")
        urls = []
        for d in dirs:
            for v in _name_variants(fname):
                urls.append(f"{base}/{d}/{v}")
        return list(dict.fromkeys(urls))  # 同名无后缀时去重

    def _load_image(self, fname, src_cat):
        # 旧 worker 只 cancel（不 disconnect：PySide6 无参 disconnect 报 TypeError）；
        # 代际号已随 _show_index 递增，旧响应回来会被回调丢弃
        if self._fetch_worker is not None:
            self._fetch_worker.cancel()
            self._fetch_worker = None
        urls = self._image_urls(fname, src_cat)
        self._try_urls(urls)

    def _try_urls(self, urls):
        if not urls:
            self._lbl_img.setText("图片不存在（可能已迁移或已删除）")
            self._lbl_status.setText(
                f"{self._idx + 1} / {len(self._entries)} · 加载失败")
            return
        url = urls[0]
        pix = self._cache.get(url)
        if pix is not None:
            self._apply_pixmap(pix)
            return
        self._pending_urls = urls
        gen = self._fetch_gen
        self._fetch_worker = _ImageFetchWorker(url, self)
        self._fetch_worker.done.connect(
            lambda u, c, g=gen: self._on_fetch_ok(u, c, g))
        self._fetch_worker.error.connect(
            lambda u, m, g=gen: self._on_fetch_fail(u, m, g))
        self._fetch_worker.start()

    def _on_fetch_ok(self, url, content, gen):
        if gen != self._fetch_gen:
            return  # 已翻页，旧响应丢弃
        pix = QPixmap()
        if not pix.loadFromData(content):
            self._on_fetch_fail(url, "无法解析图片数据", gen)
            return
        self._cache[url] = pix
        self._apply_pixmap(pix)

    def _on_fetch_fail(self, url, msg, gen):
        if gen != self._fetch_gen:
            return  # 已翻页，旧响应丢弃
        # 主候选失败 → 尝试下一个候选 URL（pic/ 兜底）
        remaining = [u for u in getattr(self, "_pending_urls", []) if u != url]
        if remaining:
            self._try_urls(remaining)
            return
        self._lbl_img.setText(f"加载失败: {msg}")
        self._lbl_status.setText(
            f"{self._idx + 1} / {len(self._entries)} · 加载失败")

    def _apply_pixmap(self, pix):
        area = self._lbl_img.size()
        scaled = pix.scaled(area, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        self._lbl_img.setPixmap(scaled)
        self._lbl_status.setText(
            f"{self._idx + 1} / {len(self._entries)} · "
            f"{pix.width()}×{pix.height()}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 窗口尺寸变化后按新尺寸重缩放当前图片
        fname, _src = self._entries[self._idx]
        for url, pix in self._cache.items():
            if url.endswith("/" + fname):
                self._apply_pixmap(pix)
                break

    # ---------- 迁移 ----------

    def _on_migrate(self, dest_cat):
        fname, src_cat = self._entries[self._idx]
        # 复用设备页迁移链路（含进行中互斥、成功后静默刷新文件列表）
        self._device_page.migrate_file(fname, src_cat, dest_cat)
        self.accept()
