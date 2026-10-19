# -*- coding: utf-8 -*-
"""kd 图片查看对话框（设备状态页文件列表「预览」入口）

卡片式对话框：标题显示文件名与序号，中间图片，
左右箭头/键盘 ←→ 切换列表内图片，底部复用四个迁移按钮
（使用/精度/问题/废弃），点击迁移后窗口保持开启（连续处理多张），
迁移结果由设备页 InfoBar 提示并自动刷新文件列表。

图片 URL 规则：
    http://kd.newbv.cn:30005/media/{yyyy/MM/dd}/{device_code}/{分类目录}/{文件名}
分类目录由条目的源分类经 CATEGORY_DIRS 映射；找不到时回退 pic/ 目录再试一次。

对话框用 show() 非模态打开：查看期间设备页仍要能刷新列表、弹 InfoBar，用
exec() 会把整个设备页阻塞住。迁移后就地改本地条目而不重建窗口，是为了连续
处理多张时不用反复开窗。
"""

import re

import requests
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QSizePolicy, QApplication)
from qfluentwidgets import (
    PushButton, ToolButton, FluentIcon, BodyLabel, CaptionLabel,
    setCustomStyleSheet, RoundMenu, Action)

from core.theme_qss import apply_window_qss
from workers.table_worker import API2_BASE, CATEGORY_DIRS

# 图片扩展名白名单（.bin/版本等非图片条目不启用预览）
# 用 tuple 而非 set：endswith 只接受元组，且只有 6 项时线性查找比建集合更省
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif")

# Django 存储同名文件时自动追加的随机去重后缀（扩展名前 _7位字母数字，
# 如 xxx_WSbTPtl.jpg）。带后缀的是重复上传副本/缩略图（320×200），
# 去后缀才是原图（1920×1080），构造 URL 时需两个变体都尝试
_DJANGO_DEDUP_RE = re.compile(r"_[0-9A-Za-z]{7}(?=\.[A-Za-z0-9]+$)")


def _name_variants(fname: str) -> list:
    """返回文件名候选：去 Django 去重后缀的原图优先，带后缀副本兜底。

    带 _7 位随机后缀的是 Django 存储重名文件时自动加的副本（可能是
    320×200 缩略图），去后缀才是 1920×1080 原图。两者都可能存在，所以返回
    候选链而不是单一名字：原图 404 时仍能回退副本，不至于整张显示空白。
    """
    stripped = _DJANGO_DEDUP_RE.sub("", fname)
    if stripped != fname:
        return [stripped, fname]
    return [fname]


def is_image_file(fname: str) -> bool:
    """按扩展名判断条目能否预览，非图片条目（.bin、版本文件）不启用预览。

    只看扩展名、不探测文件内容：文件列表动辄上千条，逐条发包探测代价太高，
    而设备产出的扩展名本身是可信的。
    """
    return str(fname).lower().endswith(_IMAGE_EXTS)


def _clip_kd(fname):
    """复制时截取文件名 'kd' 之前的字符（与右侧滑出列表的复制语义一致）。

    设备照片文件名形如 20260802_125112kd-200055-20046-100835.jpg，
    复制时通常只需要时间戳部分（kd 前）。不含 'kd' 的文件名原样返回。
    """
    fname = str(fname)
    idx = fname.find("kd")
    # idx == 0 说明名字以 kd 开头，截出来是空串；复制空文本没意义，原样返回
    return fname[:idx] if idx > 0 else fname


class _SelectableLabel(BodyLabel):
    """可选中复制的文件名标签。

    QLabel 的 TextSelectableByMouse 只保证拖选能力，悬停不会自动切换
    I 型文本光标（Qt 固有行为），此处用 enter/leave 事件手动补上，
    向用户暗示文本可选中；不参与文本交互时离开即恢复默认光标。
    不用 QLineEdit 是因为只读展示不需要输入框的边框、选中高亮与编辑语义。
    """
    def enterEvent(self, e):
        self.setCursor(Qt.CursorShape.IBeamCursor)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.unsetCursor()
        super().leaveEvent(e)


class _ImageFetchWorker(QThread):
    """后台下载图片字节流，静态资源不需要认证头。

    requests 放进 QThread 而不是主线程：单张图几百 KB，弱网下同步下载会把整个
    对话框卡死。cancel() 只置标志、不打断 socket：翻页后旧请求的结果没人要，
    发信号前检查标志即可，没必要引入连接中断的复杂度。
    """
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
            # 20s 上限：图床偶发抽风，不设超时会让 worker 线程一直挂着
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

    dest_options 与 btn_qss 由调用方注入而不是在这里 import 设备页：设备页已经
    import 本模块，反向 import 会形成循环依赖。
    """

    def __init__(self, entries, index, file_path, device_code,
                 device_page, can_migrate=True, dest_options=(),
                 btn_qss=None, parent=None):
        super().__init__(parent)
        apply_window_qss(self)
        self._entries = list(entries)
        # 夹紧下标：列表可能在打开对话框前刚刷新过，越界的 index 不该让 __init__ 崩
        self._idx = max(0, min(index, len(self._entries) - 1))
        self._file_path = file_path
        self._device_code = device_code
        self._device_page = device_page
        self._dest_options = list(dest_options)
        self._btn_qss = dict(btn_qss or {})
        # 同一时刻只保留一个下载 worker：换图先 cancel 旧的，避免并发抢带宽
        self._fetch_worker = None
        self._fetch_gen = 0   # 下载代际号：翻页后旧响应回来时丢弃（PySide6 的
                              # QObject.disconnect() 不支持无参调用，用代际号替代）
        self._cache = {}  # url -> QPixmap（翻页回看免重复下载）

        self.setWindowTitle("图片查看")
        # 初始尺寸要容得下 560×460 图片区加标题/状态/按钮行，否则一打开就触发缩放
        self.resize(1000, 740)
        self._init_ui(can_migrate)
        self._show_index(self._idx)

    # ---------- UI ----------

    def _init_ui(self, can_migrate):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # 标题行：文件名（左对齐，与图片左边缘对齐）
        self._lbl_name = _SelectableLabel("", self)
        self._lbl_name.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # 文件名可选中复制：悬停自动切换为 I 型文本光标，
        # 鼠标拖选 + Ctrl+C 复制，右键菜单复制完整文件名
        self._lbl_name.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        # qfluentwidgets Label 构造时会覆盖焦点策略，此处显式恢复 StrongFocus：
        # 点击/拖选后持有键盘焦点，Ctrl+C 与 Shift+方向键选择才稳定生效
        self._lbl_name.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._lbl_name.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._lbl_name.customContextMenuRequested.connect(self._on_name_context_menu)
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
        # 给图片区一个下限：窗口再缩小也不至于把图挤成一条缝，看不清内容
        self._lbl_img.setMinimumSize(560, 460)
        self._lbl_img.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Expanding)
        # 半透明灰底 + 圆角：白底截图在浅色主题下也能看出图片边界
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
            # 先加弹簧把按钮推到右侧，与文件列表滑出面板的按钮位置保持一致
            btn_row.addStretch(1)
            for dest in self._dest_options:
                btn = PushButton(dest, self)
                # 四个按钮等宽：分类名长短不一，不固定宽度右对齐会参差不齐
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

    def set_entries(self, entries, index):
        """非模态复用：更新文件列表快照并跳转到指定条目。

        面板在查看器打开期间可能已切换设备/日期/分类，复用窗口时需同步最新
        条目，避免展示已失效的旧快照；换成 exec() 模态就没有这个窗口期，但设备页
        也会被一起阻塞。
        """
        self._entries = list(entries)
        self._show_index(index)

    def _show_index(self, idx):
        # 越界直接忽略：首尾按钮在边界已禁用，这里再兜一层防键盘连按
        if not (0 <= idx < len(self._entries)):
            return
        self._idx = idx
        self._fetch_gen += 1  # 递增代际号，使未完成的旧下载响应失效
        fname, _src = self._entries[idx]
        self._lbl_name.setText(fname)
        # 长文件名悬停显示全名（tooltip 不影响文本拖选与复制）
        self._lbl_name.setToolTip(fname)
        self._lbl_status.setText(f"{idx + 1} / {len(self._entries)} · 加载中...")
        # 先清空旧图再异步下载：不清会让翻页瞬间仍显示上一张，用户以为是新图
        self._lbl_img.setText("")
        # 首尾禁用而不做循环翻页，避免用户以为列表是环形的、翻不到尽头
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < len(self._entries) - 1)
        self._load_image(fname, self._entries[idx][1])

    def _prev(self):
        self._show_index(self._idx - 1)

    def _next(self):
        self._show_index(self._idx + 1)

    def _on_name_context_menu(self, pos):
        """文件名右键菜单：复制文件名（截取 kd 前缀，与右侧滑出列表一致）"""
        fname = self._lbl_name.text()
        # 空文件名说明条目已被刷新掉，不弹只有一个空复制的菜单
        if not fname:
            return
        menu = RoundMenu(parent=self)
        act = Action(FluentIcon.COPY, "复制文件名", self)
        act.triggered.connect(
            lambda: QApplication.clipboard().setText(_clip_kd(fname)))
        menu.addAction(act)
        menu.exec(self._lbl_name.mapToGlobal(pos))

    def keyPressEvent(self, e):
        # 方向键在对话框层处理：焦点落在按钮/标签上也能翻页；不注册 QShortcut
        # 是为了不抢文件名标签的 Shift+方向键文本选择
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
        # 目录外层、文件名变体内层：先试完分类目录的所有名字变体，再退 pic/
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
        # 候选链用尽才判定「不存在」；中途失败由 _on_fetch_fail 换下一个候选
        if not urls:
            self._lbl_img.setText("图片不存在（可能已迁移或已删除）")
            self._lbl_status.setText(
                f"{self._idx + 1} / {len(self._entries)} · 加载失败")
            return
        url = urls[0]
        # 命中缓存直接显示：翻页回看不重复下载，也避免同一张图反复占带宽
        pix = self._cache.get(url)
        if pix is not None:
            self._apply_pixmap(pix)
            return
        # 记下整条候选链，失败时才知道还剩哪些 URL 可以退
        self._pending_urls = urls
        gen = self._fetch_gen
        self._fetch_worker = _ImageFetchWorker(url, self)
        # 用默认参数把代际号绑进回调：回调真正执行时 self._fetch_gen 可能已经递增
        self._fetch_worker.done.connect(
            lambda u, c, g=gen: self._on_fetch_ok(u, c, g))
        self._fetch_worker.error.connect(
            lambda u, m, g=gen: self._on_fetch_fail(u, m, g))
        self._fetch_worker.start()

    def _on_fetch_ok(self, url, content, gen):
        if gen != self._fetch_gen:
            return  # 已翻页，旧响应丢弃
        pix = QPixmap()
        # 字节流不是图片（图床 200 却返回 HTML 错误页）时按失败处理，继续换候选
        if not pix.loadFromData(content):
            self._on_fetch_fail(url, "无法解析图片数据", gen)
            return
        self._cache[url] = pix
        self._apply_pixmap(pix)

    def _on_fetch_fail(self, url, msg, gen):
        if gen != self._fetch_gen:
            return  # 已翻页，旧响应丢弃
        # 主候选失败 → 尝试下一个候选 URL（pic/ 兜底）
        # getattr 兜底：首次加载前 _pending_urls 还没赋值，不能直接取属性
        remaining = [u for u in getattr(self, "_pending_urls", []) if u != url]
        if remaining:
            self._try_urls(remaining)
            return
        # 所有候选都失败才落到这里：不再重试，直接把原因显示在图片区
        self._lbl_img.setText(f"加载失败: {msg}")
        self._lbl_status.setText(
            f"{self._idx + 1} / {len(self._entries)} · 加载失败")

    def _apply_pixmap(self, pix):
        # 只把缩放后的位图交给控件，原图留在 _cache：窗口拉伸时按新尺寸重算
        area = self._lbl_img.size()
        scaled = pix.scaled(area, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        self._lbl_img.setPixmap(scaled)
        # 状态行顺带显示原始分辨率：一眼能看出拿到的是原图还是 320×200 缩略图
        self._lbl_status.setText(
            f"{self._idx + 1} / {len(self._entries)} · "
            f"{pix.width()}×{pix.height()}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 缩放结果不缓存，从 _cache 里的原图按新尺寸重算
        fname, _src = self._entries[self._idx]
        # 用文件名后缀匹配缓存项：同一张图可能有多个候选 URL，命中哪个都算当前图
        for url, pix in self._cache.items():
            if url.endswith("/" + fname):
                self._apply_pixmap(pix)
                break

    # ---------- 迁移 ----------

    def _on_migrate(self, dest_cat):
        fname, src_cat = self._entries[self._idx]
        # 复用设备页迁移链路（含进行中互斥、成功后静默刷新文件列表；
        # 迁移结果由设备页 InfoBar 提示）
        self._device_page.migrate_file(fname, src_cat, dest_cat)
        # 不关窗口：连续迁移多张时反复开窗会打断操作；迁移结果由设备页 InfoBar 提示
        # 保持窗口开启：本地条目同步为目标分类（迁移后图片落新目录，
        # 翻页回看按新目录构造 URL 才能命中），状态行给出已提交标记
        self._entries[self._idx] = (fname, dest_cat)
        self._lbl_status.setText(
            f"{self._idx + 1} / {len(self._entries)} · "
            f"已提交迁移「{src_cat} → {dest_cat}」")
