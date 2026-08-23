# -*- coding: utf-8 -*-
"""连接诊断面板（独立窗口模块，只读展示 SSH/SFTP 连接日志）

数据源：core.conn_logger 写出的 logs/autowork_conn.log 及归档文件
（autowork_conn.log.1/.2/.3），纯文本解析，无文件监听：
打开面板加载一次 + 手动「刷新」重新读取即可。

日志行格式（见 ConnLogger._write）：
    ts | LEVEL | [OP] host:port user=xxx | ErrType | msg
    - host/user/ErrType 均可缺省（如 QT 消息行：ts | QT-FATAL | [QT] | msg）
    - 异常记录后跟 4 空格缩进的多行调用栈（归属上一条记录）
"""

import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QApplication, QAbstractItemView, QHeaderView, QTableWidgetItem,
    QStackedWidget, QScrollArea, QWidget, QFrame, QTableWidget)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QShortcut, QKeySequence
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    ComboBox, SwitchButton, FluentIcon, RoundMenu, Action,
    MenuAnimationType, isDarkTheme, Pivot)

from core.app_paths import get_app_dir
from core.perf import is_animation_enabled
from core.theme_qss import apply_window_qss


def _popup_ani_type():
    """按主界面「性能选项-动画效果」开关决定菜单弹出动画类型（与其他面板一致）"""
    return (MenuAnimationType.DROP_DOWN if is_animation_enabled()
            else MenuAnimationType.NONE)


# ==================== 日志解析 ====================

LOG_NAME = 'autowork_conn.log'

# 主记录行：ts | LEVEL | [OP] 剩余部分
_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \| (\S+) \| \[([^\]]*)\]\s?(.*)$')
# 目标段：host:port [user=xxx]
_TARGET_RE = re.compile(r'^(\S+):(\d+)(?:\s+user=(\S+))?$')
# 错误类型：纯标识符（如 SSHException / AuthenticationException），
# 用于区分 "[OP] target | ErrType | msg" 与 "[OP] target | msg"
_ETYPE_RE = re.compile(r'^[A-Za-z_][\w.]*$')

# 视为失败的级别（ERROR/FATAL/CRITICAL 及 QT- 前缀变体）
_FAIL_SUFFIXES = ('ERROR', 'FATAL', 'CRITICAL')


def _is_fail_level(level: str) -> bool:
    lv = level.upper()
    return any(lv == s or lv.endswith('-' + s) for s in _FAIL_SUFFIXES)


def parse_log_text(text: str, source: str):
    """解析单个日志文件文本 → 记录列表（保持文件内时序）

    记录字段：ts/dt/level/op/host/port/user/etype/msg/detail/source/raw/fail
    """
    records = []
    cur = None
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if m:
            ts_s, level, op, rest = m.groups()
            host = port = user = etype = ''
            # rest 形如 "host:port user=x | ErrType | msg"，
            # 无目标时以 '|' 开头（如 QT 行 "[QT] | msg"）
            rest = rest.strip()
            if rest.startswith('|'):
                body = [p.strip() for p in rest[1:].split(' | ')]
            else:
                segs = rest.split(' | ')
                tm = _TARGET_RE.match(segs[0].strip())
                if tm:
                    host, port, user = tm.group(1), tm.group(2), tm.group(3) or ''
                body = [p.strip() for p in segs[1:]]
            msg = ' | '.join(body)
            # 错误类型仅当有 2 段以上且首段为异常标识符时提取
            if len(body) >= 2 and _ETYPE_RE.match(body[0]):
                etype, msg = body[0], ' | '.join(body[1:])
            try:
                dt = datetime.strptime(ts_s, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                dt = datetime.min
            cur = {
                'ts': ts_s, 'dt': dt, 'level': level, 'op': op,
                'host': host, 'port': port, 'user': user,
                'etype': etype, 'msg': msg, 'detail': [],
                'source': source, 'raw': line,
                'fail': _is_fail_level(level),
            }
            records.append(cur)
        elif cur is not None:
            # 缩进续行（调用栈等多行详情）归属上一条记录
            cur['detail'].append(line)
    return records


def load_all_records(log_dir: str):
    """按从旧到新顺序读取归档（.3→.2→.1）+ 当前日志，合并为全局时序列表"""
    base = os.path.join(log_dir, LOG_NAME)
    files = [f'{base}.{i}' for i in (3, 2, 1)] + [base]
    records = []
    for path in files:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        except Exception:
            continue
        records.extend(parse_log_text(text, os.path.basename(path)))
    for i, r in enumerate(records):
        r['seq'] = i  # 全局时序序号（旧→新），用于排序
    return records


# ==================== 统计聚合 ====================
# 成功/失败/重试判定依据（见 workers/network_workers.py 写入调用点）：
#   成功: INFO 且 msg 含「连接成功」（连接成功 (第N次尝试)/连接验证成功）
#   失败: fail 级别且 msg 含「连接失败」（含「连接最终失败」）
#   重试: msg 含「将重试」（连接失败，将重试 (N/M)）

_SUCCESS_MARK = '连接成功'
_FAIL_MARK = '连接失败'
_RETRY_MARK = '将重试'


def is_success_record(r) -> bool:
    return (not r['fail']) and _SUCCESS_MARK in r['msg']


def is_conn_fail_record(r) -> bool:
    return r['fail'] and _FAIL_MARK in r['msg']


def aggregate_stats(records):
    """对记录列表聚合连接质量统计（纯函数，便于脚本验证）

    返回 dict：
      hosts: [{host, total, ok, fail, rate, top_etype, last_ts}]
             按成功率升序（最差在前），同率按失败多者优先
      etypes: [(错误类型, 次数)] 失败原因 TOP 排行（仅连接失败记录）
      retries: [(host, 重试次数)] 按重试次数降序；空列表表示无重试记录
    """
    by_host = {}
    etypes = Counter()
    retries = Counter()
    for r in records:
        host = r['host']
        if _RETRY_MARK in r['msg']:
            if host:
                retries[host] += 1
        if not host:
            continue
        h = by_host.setdefault(host, {
            'host': host, 'total': 0, 'ok': 0, 'fail': 0,
            'etypes': Counter(), 'last_dt': None, 'last_ts': '',
        })
        h['total'] += 1
        if is_success_record(r):
            h['ok'] += 1
        if is_conn_fail_record(r):
            h['fail'] += 1
            et = r['etype'] or '(未标注)'
            h['etypes'][et] += 1
            etypes[et] += 1
        if r['dt'] > (h['last_dt'] or datetime.min):
            h['last_dt'], h['last_ts'] = r['dt'], r['ts']
    hosts = []
    for h in by_host.values():
        rate = h['ok'] / h['total'] if h['total'] else 0.0
        top = h['etypes'].most_common(1)
        hosts.append({
            'host': h['host'], 'total': h['total'], 'ok': h['ok'],
            'fail': h['fail'], 'rate': rate,
            'top_etype': (f"{top[0][0]} ×{top[0][1]}" if top else '—'),
            'last_ts': h['last_ts'],
        })
    hosts.sort(key=lambda h: (h['rate'], -h['fail'], h['host']))
    return {
        'hosts': hosts,
        'etypes': etypes.most_common(),
        'retries': retries.most_common(),
    }


# ==================== 面板窗口 ====================

# 表格列：(表头, 宽度)
_COLUMNS = [
    ("时间", 150),
    ("级别", 70),
    ("操作", 60),
    ("主机", 150),
    ("用户", 80),
    ("错误类型", 150),
    ("消息", 420),
    ("来源文件", 130),
]

_TIME_RANGES = ["全部时间", "今天", "最近 1 小时", "最近 24 小时", "最近 7 天"]

_LEVEL_COLORS = {
    'ERROR': QColor(229, 57, 53),
    'FATAL': QColor(229, 57, 53),
    'CRITICAL': QColor(229, 57, 53),
    'WARN': QColor(245, 124, 0),
}


def _level_color(level: str):
    """级别着色（失败红色/警告琥珀），深色主题略微提亮"""
    key = level.upper().split('-')[-1]
    color = _LEVEL_COLORS.get(key)
    if color is None:
        return None
    if isDarkTheme() and key in ('ERROR', 'FATAL', 'CRITICAL'):
        return QColor(255, 112, 108)
    if isDarkTheme():
        return QColor(255, 183, 77)
    return color


class ConnDiagPanel(QDialog):
    """连接诊断面板：只读展示连接日志，失败记录置顶，支持设备/时间过滤"""

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_window_qss(self)
        self.setWindowTitle("连接诊断")
        self.resize(1150, 600)
        self.setMinimumSize(760, 420)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)

        self._records = []      # 全量记录（未过滤）
        self._filtered = []     # 当前过滤后的记录（与表格行一一对应）
        self._stats_dirty = True  # 统计数据待重建（过滤条件/记录变化时置位）
        self._log_dir = os.path.join(get_app_dir(), 'logs')

        self._init_ui()
        self.reload()

    # ==================== UI 构建 ====================

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        # 明细 / 统计 视图切换
        self._pivot = Pivot(self)
        self._pivot.addItem('detail', '明细')
        self._pivot.addItem('stats', '统计')
        self._pivot.setCurrentItem('detail')
        self._pivot.currentItemChanged.connect(self._on_view_switch)
        toolbar.addWidget(self._pivot)

        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("设备过滤：host / snk / 关键词")
        self._search_edit.setFixedWidth(240)
        self._search_edit.searchSignal.connect(lambda _t: self._apply_filter())
        self._search_edit.returnPressed.connect(self._apply_filter)
        toolbar.addWidget(self._search_edit)

        toolbar.addWidget(QLabel("时间:", self))
        self._range_combo = ComboBox(self)
        self._range_combo.addItems(_TIME_RANGES)
        self._range_combo.setFixedWidth(120)
        self._range_combo.currentIndexChanged.connect(lambda _i: self._apply_filter())
        toolbar.addWidget(self._range_combo)

        toolbar.addWidget(QLabel("只看失败:", self))
        self._sw_fail = SwitchButton(self)
        self._sw_fail.setOnText("开")
        self._sw_fail.setOffText("关")
        self._sw_fail.checkedChanged.connect(lambda _c: self._apply_filter())
        toolbar.addWidget(self._sw_fail)

        toolbar.addStretch(1)

        self._refresh_btn = PushButton(FluentIcon.SYNC, "刷新", self)
        self._refresh_btn.setToolTip("重新读取日志文件及归档")
        self._refresh_btn.clicked.connect(self.reload)
        toolbar.addWidget(self._refresh_btn)

        self._open_dir_btn = PushButton(FluentIcon.FOLDER, "打开日志目录", self)
        self._open_dir_btn.clicked.connect(self._open_log_dir)
        toolbar.addWidget(self._open_dir_btn)

        root.addLayout(toolbar)

        # --- 明细 / 统计 堆叠视图 ---
        self._stack = QStackedWidget(self)
        root.addWidget(self._stack, 1)

        # 页 0：明细表格
        self._table = TableWidget(self)
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_row_menu)

        # Ctrl+C 复制选中行完整内容
        QShortcut(QKeySequence.StandardKey.Copy, self._table).activated.connect(
            self._copy_selected_row)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, (_, w) in enumerate(_COLUMNS):
            self._table.setColumnWidth(i, w)

        self._stack.addWidget(self._table)

        # 页 1：统计视图（滚动容器，内容由 _refresh_stats 重建）
        self._stats_body = QVBoxLayout()
        self._stats_body.setContentsMargins(0, 0, 6, 0)
        self._stats_body.setSpacing(10)
        self._stats_body.addStretch(1)
        holder = QWidget(self._stack)
        holder.setLayout(self._stats_body)
        scroll = QScrollArea(self._stack)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(holder)
        self._stack.addWidget(scroll)

        # --- 底部状态栏 ---
        status = QHBoxLayout()
        self._lbl_info = QLabel("正在加载...", self)
        status.addWidget(self._lbl_info)
        status.addStretch(1)
        self._lbl_hint = QLabel("失败记录置顶 · 右键行可复制完整内容（含调用栈）", self)
        self._lbl_hint.setStyleSheet("color: gray;")
        status.addWidget(self._lbl_hint)
        root.addLayout(status)

        # --- 右键菜单（预构建缓存） ---
        menu = RoundMenu(parent=self)
        act_copy = Action(FluentIcon.COPY, "复制完整内容", self)
        act_copy.triggered.connect(self._copy_selected_row)
        menu.addAction(act_copy)
        self._ctx_menu = menu

    # ==================== 数据加载 / 过滤 ====================

    def reload(self):
        """重新读取日志文件（打开面板 / 手动刷新时调用）"""
        start = datetime.now()
        self._records = load_all_records(self._log_dir)
        self._apply_filter()
        cost_ms = (datetime.now() - start).total_seconds() * 1000
        fail_cnt = sum(1 for r in self._records if r['fail'])
        self._lbl_info.setText(
            f"共 {len(self._records)} 条记录（失败 {fail_cnt} 条），加载耗时 {cost_ms:.0f} ms")

    def _time_cutoff(self):
        """按时间范围下拉框计算截止时刻（早于该时刻的记录被过滤）"""
        idx = self._range_combo.currentIndex()
        now = datetime.now()
        if idx == 1:      # 今天
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        if idx == 2:      # 最近 1 小时
            return now - timedelta(hours=1)
        if idx == 3:      # 最近 24 小时
            return now - timedelta(hours=24)
        if idx == 4:      # 最近 7 天
            return now - timedelta(days=7)
        return None       # 全部

    def _apply_filter(self):
        """按设备关键词 + 时间范围 + 只看失败过滤，失败记录置顶后刷新表格"""
        kw = self._search_edit.text().strip().lower()
        cutoff = self._time_cutoff()
        only_fail = self._sw_fail.isChecked()

        rows = []
        for r in self._records:
            if only_fail and not r['fail']:
                continue
            if cutoff is not None and r['dt'] < cutoff:
                continue
            if kw and not (kw in r['host'].lower() or kw in r['user'].lower()
                           or kw in r['msg'].lower() or kw in r['etype'].lower()):
                continue
            rows.append(r)
        # 失败记录置顶（组内按时序倒序，最新在前）
        rows.sort(key=lambda r: (not r['fail'], -r['seq']))
        self._filtered = rows
        self._populate(rows)
        self._stats_dirty = True
        if self._stack.currentIndex() == 1:
            self._refresh_stats()

    def _populate(self, rows):
        """记录列表 → 表格（批量填充，关闭重绘避免逐行刷新开销）"""
        table = self._table
        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(0)
            table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                host = f"{r['host']}:{r['port']}" if r['host'] else ''
                values = (r['ts'], r['level'], r['op'], host, r['user'],
                          r['etype'], r['msg'], r['source'])
                color = _level_color(r['level'])
                for col, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    if color is not None:
                        item.setForeground(color)
                    table.setItem(i, col, item)
        finally:
            table.setUpdatesEnabled(True)
        shown_fail = sum(1 for r in rows if r['fail'])
        self._lbl_info.setText(
            f"显示 {len(rows)} 条（失败 {shown_fail} 条）/ 共 {len(self._records)} 条")

    # ==================== 操作 ====================

    @staticmethod
    def _record_full_text(r) -> str:
        """记录完整内容：原始行 + 多行详情（调用栈）"""
        text = r['raw']
        if r['detail']:
            text += '\n' + '\n'.join(r['detail'])
        return text

    def _copy_selected_row(self):
        """复制选中行的完整内容到剪贴板"""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._filtered):
            return
        text = self._record_full_text(self._filtered[row])
        QApplication.clipboard().setText(text)
        first_line = text.splitlines()[0][:60] if text else ''
        self._lbl_info.setText(f"已复制到剪贴板: {first_line}...")

    def _show_row_menu(self, pos):
        """表格右键菜单（预构建缓存，动画类型跟随全局开关）"""
        if self._table.currentRow() < 0:
            return
        self._ctx_menu.exec_(self._table.viewport().mapToGlobal(pos),
                             aniType=_popup_ani_type())

    def _open_log_dir(self):
        """在资源管理器中打开日志目录"""
        try:
            if os.path.isdir(self._log_dir):
                os.startfile(self._log_dir)
            else:
                self._lbl_info.setText(f"日志目录不存在: {self._log_dir}")
        except Exception:
            subprocess.run(['explorer', self._log_dir])

    # ==================== 统计视图 ====================

    def _on_view_switch(self, route_key):
        """明细/统计切换：进入统计页时按需重建（脏标记避免重复计算）"""
        if route_key == 'stats':
            if self._stats_dirty:
                self._refresh_stats()
            self._stack.setCurrentIndex(1)
            self._lbl_hint.setText(
                "双击设备行可跳转明细视图并过滤该设备 · 统计遵循上方过滤条件")
        else:
            self._stack.setCurrentIndex(0)
            self._lbl_hint.setText(
                "失败记录置顶 · 右键行可复制完整内容（含调用栈）")

    def _refresh_stats(self):
        """按当前过滤后的记录重建统计视图（刷新/过滤变化后调用）"""
        while self._stats_body.count() > 1:  # 保留尾部 stretch
            w = self._stats_body.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

        if not self._filtered:
            self._stats_body.insertWidget(0, QLabel("当前过滤条件下无记录", self))
        else:
            stats = aggregate_stats(self._filtered)
            self._stats_body.insertWidget(0, self._build_host_table(stats['hosts']))
            self._stats_body.insertWidget(1, self._build_etype_table(stats['etypes']))
            if stats['retries']:
                self._stats_body.insertWidget(2, self._build_retry_table(stats['retries']))
        self._stats_dirty = False

    @staticmethod
    def _make_table(headers, widths, row_count):
        """创建只读、整行选中、隔行变色的统计子表"""
        t = QTableWidget(row_count, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        header = t.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, w in enumerate(widths):
            t.setColumnWidth(i, w)
        return t

    @staticmethod
    def _fill_cell(t, row, col, text, color=None, bold=False):
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        if color is not None:
            item.setForeground(color)
        if bold:
            f = item.font()
            f.setBold(True)
            item.setFont(f)
        t.setItem(row, col, item)

    def _build_host_table(self, hosts):
        """设备连接质量汇总（成功率升序，最差在前）；双击行跳明细并过滤"""
        headers = ["设备", "连接总次数", "成功", "失败", "成功率",
                   "失败原因 TOP1", "最近连接时间"]
        widths = [180, 90, 70, 70, 90, 240, 160]
        t = self._make_table(headers, widths, len(hosts))
        for i, h in enumerate(hosts):
            rate = h['rate']
            if rate < 0.6:
                rc = QColor(229, 57, 53)
            elif rate < 0.9:
                rc = QColor(245, 124, 0)
            else:
                rc = QColor(67, 160, 71)
            self._fill_cell(t, i, 0, h['host'], bold=True)
            self._fill_cell(t, i, 1, str(h['total']))
            self._fill_cell(t, i, 2, str(h['ok']))
            self._fill_cell(t, i, 3, str(h['fail']),
                            QColor(229, 57, 53) if h['fail'] else None)
            self._fill_cell(t, i, 4, f"{rate * 100:.1f}%", rc, bold=True)
            self._fill_cell(t, i, 5, h['top_etype'])
            self._fill_cell(t, i, 6, h['last_ts'])
        t.cellDoubleClicked.connect(
            lambda row, _col: self._jump_to_detail(hosts[row]['host']))
        wrap = QWidget(self)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        title = QLabel(f"设备连接质量汇总（成功率升序，最差在前 · 共 {len(hosts)} 台）", wrap)
        f = title.font(); f.setBold(True); title.setFont(f)
        lay.addWidget(title)
        lay.addWidget(t)
        hint = QLabel("双击设备行 → 跳转明细视图并按该设备过滤", wrap)
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        return wrap

    def _build_etype_table(self, etypes, top_n=10):
        """失败原因 TOP N（按 ErrType 聚合连接失败次数）"""
        rows = etypes[:top_n]
        t = self._make_table(["错误类型", "失败次数"], [360, 100], len(rows))
        for i, (et, cnt) in enumerate(rows):
            self._fill_cell(t, i, 0, et)
            self._fill_cell(t, i, 1, str(cnt), QColor(229, 57, 53), bold=True)
        wrap = QWidget(self)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        title = QLabel(f"失败原因 TOP {len(rows)}（按错误类型）", wrap)
        f = title.font(); f.setBold(True); title.setFont(f)
        lay.addWidget(title)
        lay.addWidget(t)
        return wrap

    def _build_retry_table(self, retries):
        """重试分布（日志中含「将重试」的记录按设备聚合）"""
        t = self._make_table(["设备", "重试次数"], [360, 100], len(retries))
        for i, (host, cnt) in enumerate(retries):
            self._fill_cell(t, i, 0, host)
            self._fill_cell(t, i, 1, str(cnt), QColor(245, 124, 0), bold=True)
        wrap = QWidget(self)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        title = QLabel("重试分布（连接失败将重试）", wrap)
        f = title.font(); f.setBold(True); title.setFont(f)
        lay.addWidget(title)
        lay.addWidget(t)
        return wrap

    def _jump_to_detail(self, host):
        """从统计视图跳转明细：设置设备过滤关键词并切回明细页"""
        self._search_edit.setText(host)
        self._apply_filter()
        self._pivot.setCurrentItem('detail')
        self._lbl_info.setText(f"已过滤设备: {host}")


if __name__ == '__main__':
    # 独立调试：python -m windows.tunnel.conn_diag_panel
    import sys
    import json
    import os
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, Theme, setThemeColor

    def _debug_theme_color():
        """调试入口读取 settings.json 主题强调色（与主程序入口一致）"""
        try:
            # 模块由 windows/ 下移至 windows/tunnel/，需多上溯一层到项目根目录
            p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "settings.json")
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f).get("theme_color", "#00BCD4")
        except Exception:
            return "#00BCD4"

    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    setThemeColor(_debug_theme_color(), lazy=True)
    win = ConnDiagPanel()
    win.show()
    sys.exit(app.exec())
