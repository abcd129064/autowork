# -*- coding: utf-8 -*-
"""SSH 故障一键取证包（D2）

SSH 终端工具栏"取证"按钮的后台实现：
- ForensicWorker(QThread)：通过现有 SSH client 的 exec_command（独立 channel，
  不占用/不污染交互 shell 的 channel）逐条执行预置诊断命令组，每条 5 秒超时，
  单条失败不影响其他条
- 汇总球桌关联信息（table_db 反查）、设备 kd 状态、会话日志尾部、连接日志，
  生成 Markdown 诊断报告落盘 logs/forensic/

报告生成逻辑 build_forensic_report 为纯函数，可离线 mock 自测。
数据库查询使用独立只读连接（WAL 库支持并发读），不占用 table_db 模块单连接。
"""

import os
import re
import socket
import sqlite3
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from core.app_paths import get_app_dir
from core.conn_logger import conn_logger

# ─── 预置诊断命令组（模块级常量，便于维护） ─────────────────────────────
# 元素为 (报告分节标题, shell 命令)
FORENSIC_COMMANDS = (
    ("系统时间与运行时长", "date; uptime"),
    ("内核日志错误/警告（最近 50 条）", "dmesg --level=err,warn | tail -50"),
    ("systemd 失败的服务", "systemctl --failed --no-pager"),
    ("磁盘空间", "df -h"),
    ("内存使用", "free -m"),
    ("CPU/内存占用 TOP20", "top -bn1 | head -20"),
)

FORENSIC_CMD_TIMEOUT = 5      # 单条命令超时（秒）
SESSION_TAIL_LINES = 200      # 会话日志截取行数
CONN_LOG_ENTRIES = 30         # 连接日志截取条数

# snk 标识提取规则（与 table_db._SNK_PATTERN 一致）
_SNK_RE = re.compile(r"snk[\w\-]*", re.IGNORECASE)

# kd_status.status 数值含义（与 get_latest_kd_status 注释一致）
_KD_STATUS_DESC = {"0": "下线", "1": "空闲", "2": "使用中"}

# kd_status 报告展示字段（按序）
_KD_REPORT_FIELDS = (
    ("file_path", "数据分区(日期)"), ("device_code", "设备编码"),
    ("club_name", "球房名"), ("status", "设备状态"),
    ("error_rate", "错误率"), ("operation_rate", "运维率"),
    ("pic_total", "图片总数"), ("normal_count", "正常数"),
    ("normal_total", "正常总数"), ("except_count", "异常数"),
    ("untreated_count", "未处理数"), ("already_count", "已处理数"),
    ("rubbish_count", "垃圾数"), ("target_directory", "目标目录"),
)


def get_forensic_dir() -> str:
    """取证报告目录：{app_dir}/logs/forensic（不存在则创建）"""
    path = os.path.join(get_app_dir(), "logs", "forensic")
    os.makedirs(path, exist_ok=True)
    return path


# ─── 本地数据查询（独立只读 SQLite 连接，供后台线程安全调用） ───────────

def _open_readonly_db():
    """打开 tables.db 的独立只读连接；库文件不存在/打开失败返回 None"""
    try:
        from database.table_db import DB_PATH
        if not os.path.exists(DB_PATH):
            return None
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout=3000")
        return conn
    except sqlite3.Error:
        return None


def lookup_table_info(snk: str, host: str) -> dict:
    """从 billiard_tables 反查球桌信息

    匹配优先级：snk_code 精确 → remark 含 snk → remark 含 host。
    返回 {"name","roomName","onlineStatusName","snk_code","remark"}；
    查不到或库不可用返回空 dict。
    """
    snk = str(snk or "").strip()
    host = str(host or "").strip()
    conn = _open_readonly_db()
    if conn is None:
        return {}
    try:
        sql = ("SELECT name, roomName, onlineStatusName, snk_code, remark "
               "FROM billiard_tables WHERE ")
        row = None
        if snk:
            row = conn.execute(
                sql + "snk_code = ? COLLATE NOCASE LIMIT 1", (snk,)).fetchone()
            if row is None:
                row = conn.execute(
                    sql + "remark LIKE ? LIMIT 1", (f"%{snk}%",)).fetchone()
        if row is None and host:
            row = conn.execute(
                sql + "remark LIKE ? LIMIT 1", (f"%{host}%",)).fetchone()
        if row is None:
            return {}
        return dict(zip(("name", "roomName", "onlineStatusName",
                         "snk_code", "remark"), row))
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def lookup_kd_status(table_id: str, snk: str) -> dict:
    """查 kd_status 该设备最新分区的关键字段

    优先按球桌号 table_id（TRIM 匹配），未命中再按 snk 当 device_code 查；
    均取 file_path 倒序第一条（该设备最近一次上报）。
    返回字段 dict（含 file_path）；查不到返回空 dict。
    """
    conn = _open_readonly_db()
    if conn is None:
        return {}
    cols = ", ".join(f for f, _ in _KD_REPORT_FIELDS)
    sql = (f"SELECT {cols} FROM kd_status WHERE {{cond}} "
           f"ORDER BY file_path DESC LIMIT 1")
    try:
        row = None
        tid = str(table_id or "").strip()
        if tid:
            row = conn.execute(
                sql.format(cond="TRIM(table_id) = ?"), (tid,)).fetchone()
        if row is None and str(snk or "").strip():
            row = conn.execute(
                sql.format(cond="device_code = ? COLLATE NOCASE"),
                (str(snk).strip(),)).fetchone()
        if row is None:
            return {}
        return dict(zip((f for f, _ in _KD_REPORT_FIELDS), row))
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


# ─── 日志采集 ────────────────────────────────────────────────────────────

def read_session_tail(path: str, n: int = SESSION_TAIL_LINES) -> str:
    """读取会话日志文件最近 n 行；文件不存在/读取失败返回空串"""
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return "".join(lines[-n:]).rstrip()
    except OSError:
        return ""


def collect_conn_log(host: str, limit: int = CONN_LOG_ENTRIES) -> str:
    """从 autowork_conn.log 及归档 .3/.2/.1 中提取该 host 最近 limit 条记录

    按时间顺序（.3 最旧 → 当前文件最新）扫描，含 host:port 的行作为一条
    记录的开头，紧随的缩进行（异常调用栈详情）归并到同一条。
    """
    host = str(host or "").strip()
    if not host:
        return ""
    base = os.path.join(get_app_dir(), "logs", "autowork_conn.log")
    files = [f"{base}.{i}" for i in (3, 2, 1) if os.path.exists(f"{base}.{i}")]
    files.append(base)
    marker = f"{host}:"
    entries = []
    for path in files:
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                current = None
                for line in f:
                    if line.startswith("    "):  # 详情缩进行，归并上一条
                        if current is not None:
                            current.append(line)
                        continue
                    if marker in line:
                        if current is not None:
                            entries.append(current)
                        current = [line]
                    else:
                        if current is not None:
                            entries.append(current)
                        current = None
                if current is not None:
                    entries.append(current)
        except OSError:
            continue
    tail = entries[-limit:]
    return "".join("".join(e) for e in tail).rstrip()


# ─── 报告生成（纯函数，可离线自测） ──────────────────────────────────────

def _md_code_block(text: str) -> str:
    """包裹 Markdown 代码块；内容含 ``` 时改用四个反引号围栏防截断"""
    fence = "````" if "```" in text else "```"
    return f"{fence}\n{text.rstrip()}\n{fence}"


def build_forensic_report(meta: dict, cmd_results: list, table_info: dict,
                          kd_info: dict, session_tail: str, conn_log: str) -> str:
    """汇总生成 Markdown 诊断报告文本

    Args:
        meta: {"host","port","username","server_name"} 连接元信息
        cmd_results: [(标题, 命令, ok, 输出文本), ...]
        table_info: lookup_table_info 结果（可为空）
        kd_info: lookup_kd_status 结果（可为空）
        session_tail: 会话日志尾部文本
        conn_log: 连接日志记录文本
    """
    now = datetime.now()
    host = str(meta.get("host") or "")
    port = meta.get("port", "")
    username = str(meta.get("username") or "")
    server_name = str(meta.get("server_name") or "")

    lines = [
        "# SSH 故障一键取证报告",
        "",
        "## 一、基本信息",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 生成时间 | {now:%Y-%m-%d %H:%M:%S} |",
        f"| 连接目标 | {host}:{port} |",
        f"| 登录用户 | {username or '(未知)'} |",
    ]
    if server_name:
        lines.append(f"| 会话别名 | {server_name} |")

    if table_info:
        t_snk = str(table_info.get("snk_code") or "").strip() or "(无)"
        lines.append(
            f"| 关联球桌 | {table_info.get('name') or '(无名)'}"
            f"（球房：{table_info.get('roomName') or '(未知)'}，"
            f"在线状态：{table_info.get('onlineStatusName') or '(未知)'}，"
            f"snk：{t_snk}） |")
    else:
        lines.append("| 关联球桌 | 未关联（本地球桌库无该 snk/host 记录，"
                     "或球桌数据未同步） |")
    lines.append("")

    # 设备 kd 状态
    lines += ["## 二、设备 kd 状态（最新分区）", ""]
    if kd_info:
        lines += ["| 字段 | 值 |", "| --- | --- |"]
        for field, label in _KD_REPORT_FIELDS:
            val = str(kd_info.get(field) or "").strip() or "(空)"
            if field == "status":
                desc = _KD_STATUS_DESC.get(str(kd_info.get("status") or "").strip())
                if desc:
                    val = f"{val}（{desc}）"
            lines.append(f"| {label} | {val} |")
    else:
        lines.append("本地 kd_status 库中未查到该设备的上报记录"
                     "（可能未同步当日数据，或设备未关联球桌号）。")
    lines.append("")

    # 诊断命令输出
    lines += ["## 三、诊断命令输出", ""]
    for idx, (title, cmd, ok, output) in enumerate(cmd_results, 1):
        lines.append(f"### 3.{idx} {title}")
        lines.append("")
        lines.append(f"$ {cmd}")
        lines.append("")
        if ok:
            lines.append(_md_code_block(output or "(无输出)"))
        else:
            lines.append(f"> ⚠ 执行失败：{output}")
        lines.append("")

    # 最近会话记录
    lines += [f"## 四、最近会话记录（最近 {SESSION_TAIL_LINES} 行）", ""]
    lines.append(_md_code_block(session_tail) if session_tail else "(当前会话无日志记录)")
    lines.append("")

    # 最近连接日志
    lines += [f"## 五、最近连接日志（该 host 最近 {CONN_LOG_ENTRIES} 条）", ""]
    lines.append(_md_code_block(conn_log) if conn_log else "(未找到该 host 的连接日志)")
    lines.append("")

    return "\n".join(lines)


# ─── 后台取证 Worker ─────────────────────────────────────────────────────

class ForensicWorker(QThread):
    """后台取证线程：逐条执行诊断命令组并汇总生成报告

    exec_command 在 paramiko 内部走独立 channel（同一 transport），
    不占用交互 shell 的 channel，用户会话不受影响。
    """
    # (当前条序号, 总条数, 阶段描述)
    progress = Signal(int, int, str)
    report_ready = Signal(str)   # 报告文件路径
    failed = Signal(str)         # 致命错误（报告未能生成）

    def __init__(self, client, host, port, username,
                 server_name='', session_log_path=None, parent=None):
        super().__init__(parent)
        self._client = client
        self._host = host
        self._port = port
        self._username = username
        self._server_name = server_name
        self._session_log_path = session_log_path

    # ── 命令执行 ──────────────────────────────────────────────────────

    def _exec_one(self, cmd: str):
        """独立 channel 执行单条命令，返回 (ok, 输出文本)"""
        try:
            stdin, stdout, stderr = self._client.exec_command(
                cmd, timeout=FORENSIC_CMD_TIMEOUT)
            out = stdout.read().decode('utf-8', errors='replace')
            err = stderr.read().decode('utf-8', errors='replace')
            text = out
            if err.strip():
                text += ("\n[stderr]\n" + err) if text.strip() else err
            return True, text.strip()
        except socket.timeout:
            return False, f"执行超时（>{FORENSIC_CMD_TIMEOUT} 秒）"
        except Exception as e:
            return False, f"执行失败：{e}"

    # ── 主流程 ────────────────────────────────────────────────────────

    def run(self):
        try:
            conn_logger.info('FORENSIC', '开始一键取证',
                             host=self._host, port=self._port, user=self._username)
            # 1. 逐条执行诊断命令组（单条失败不影响其他条）
            total = len(FORENSIC_COMMANDS)
            cmd_results = []
            for idx, (title, cmd) in enumerate(FORENSIC_COMMANDS, 1):
                self.progress.emit(idx, total, title)
                ok, output = self._exec_one(cmd)
                cmd_results.append((title, cmd, ok, output))

            # 2. 反查球桌信息 / 设备 kd 状态
            self.progress.emit(total, total, "汇总本地数据")
            snk = self._extract_snk()
            table_id = self._extract_table_id()
            table_info = lookup_table_info(snk, self._host)
            if table_info and not table_id:
                table_id = str(table_info.get("name") or "").strip()
            kd_info = lookup_kd_status(table_id, snk or
                                       str(table_info.get("snk_code") or ""))

            # 3. 会话日志尾部 + 连接日志
            session_tail = read_session_tail(self._session_log_path)
            conn_log = collect_conn_log(self._host)

            # 4. 生成报告并落盘
            report = build_forensic_report(
                {"host": self._host, "port": self._port,
                 "username": self._username, "server_name": self._server_name},
                cmd_results, table_info, kd_info, session_tail, conn_log)
            path = self._save_report(report)
            conn_logger.info('FORENSIC', f'取证报告已生成: {os.path.basename(path)}',
                             host=self._host, port=self._port, user=self._username)
            self.report_ready.emit(path)
        except Exception as e:
            conn_logger.exception('FORENSIC', '一键取证失败', exc=e,
                                  host=self._host, port=self._port,
                                  user=self._username)
            self.failed.emit(str(e))

    def _extract_snk(self) -> str:
        """从会话别名（如 "A01（snk_001）"）中提取 snk 标识"""
        m = _SNK_RE.search(self._server_name or "")
        return m.group(0) if m else ""

    def _extract_table_id(self) -> str:
        """从会话别名中提取球桌号（括号前部分），无则返回空串"""
        name = str(self._server_name or "").strip()
        for sep in ("（", "("):
            if sep in name:
                name = name.split(sep, 1)[0]
                break
        return name.strip()

    def _save_report(self, report: str) -> str:
        """报告落盘 logs/forensic/{YYYYMMDD}_{HHmmss}_{host}.md，返回文件路径"""
        now = datetime.now()
        host_tag = re.sub(r'[\\/:*?"<>|\s]+', '_', str(self._host)).strip('_') or 'host'
        path = os.path.join(get_forensic_dir(),
                            f"{now:%Y%m%d}_{now:%H%M%S}_{host_tag}.md")
        with open(path, 'w', encoding='utf-8') as f:
            f.write(report)
        return path
