# -*- coding: utf-8 -*-
"""SSH 故障一键取证包（D2 + D3 死机取证）

SSH 终端工具栏「取证」按钮的后台实现：
- ForensicWorker(QThread)：通过现有 SSH client 的 exec_command（独立 channel，
  不占用/不污染交互 shell 的 channel）逐条执行预置诊断命令组，命令级超时，
  单条失败不影响其他条
- 两种模式：常规诊断（FORENSIC_COMMANDS，现状运行状况巡检）与
  死机/异常停止取证（CRASH_FORENSIC_COMMANDS：启动/关机历史、上次 boot
  日志戛然而止检测、非正常关机脏标记、OOM/MCE/内核死锁/进程崩溃/文件系统
  错误、pstore 残留、lockup 开关，全部只读）
- 死机模式带确定性证据分析 analyze_crash_evidence（规则引擎产出
  时间线/命中签名/初步结论/建议，纯函数可离线 mock 自测），AI 分析在其
  结论之上复核深化（专用提示词 _AI_CRASH_SYSTEM_PROMPT）
- 汇总球桌关联信息（table_db 反查）、设备 kd 状态、会话日志尾部、连接日志，
  生成 Markdown 诊断报告落盘 logs/forensic/（死机报告 crash_ 前缀）

报告生成逻辑 build_forensic_report / build_crash_forensic_report 均为纯函数，
可离线 mock 自测。数据库查询使用独立只读连接（WAL 库支持并发读），不占用
table_db 模块单连接。
"""

import json
import os
import re
import socket
import threading
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from core.app_paths import get_app_dir
from core.ai_providers import resolve_ai_config
from core.conn_logger import conn_logger
from core.secrets import decrypt_settings
from core.utils import cleanup_log_dir

# ─── 预置诊断命令组（模块级常量，便于维护） ─────────────────────────────
# 元素为 (分节标题, shell 命令)；只读采集（写操作会改变现场），每条自带
# journalctl→syslog→messages→echo 降级链，因为老设备上三种日志系统不总在。
FORENSIC_COMMANDS = (
    ("系统时间与运行时长", "date; uptime"),
    ("内核日志错误/警告（最近 50 条）", "dmesg --level=err,warn | tail -50"),
    ("系统错误日志（journalctl/syslog 最近 200 条）",
     "journalctl -p err -n 200 --no-pager 2>/dev/null"
     " || tail -n 200 /var/log/syslog 2>/dev/null"
     " || tail -n 200 /var/log/messages 2>/dev/null"
     " || echo '(journalctl/syslog 均不可用)'"),
    ("systemd 失败的服务", "systemctl --failed --no-pager"),
    ("磁盘空间", "df -h"),
    ("内存使用", "free -m"),
    ("CPU/内存占用 TOP20", "top -bn1 | head -20"),
)

# ─── 死机/异常停止取证命令组（D3，2026-10-29） ──────────────────────────
# 面向「设备死机/无响应/被人工断电重启」场景的定向采集，全部只读：
# 围绕「上次异常停止时刻前后发生了什么」取证——启动/关机历史、上次启动
# 日志是否戛然而止、非正常关机脏标记、OOM/MCE/内核死锁/进程崩溃/文件
# 系统错误、pstore 崩溃残留、内存与 swap 现状。
# 元素为 (分节标题, shell 命令, 单条超时秒)。超时单列是因为 journalctl
# 全历史 grep 在 3G 级持久 journal 上常超过常规组 5 秒的上限——超时按
# 命令的实际代价分别给定，既不拖慢整体进度、也不误杀慢命令。
# 降级链同常规组：journalctl 不可用/未持久化的老设备回退 syslog/messages
# 或明确 echo 说明，报告里永远有「为什么没有这份数据」的交代。
CRASH_FORENSIC_COMMANDS = (
    ("本次启动信息（时间/时长/内核）",
     "date; uptime; uptime -s; who -b; hostname; uname -r", 5),
    ("启动/关机历史（wtmp，最近 25 条）",
     "last -x -F reboot shutdown 2>/dev/null | head -25"
     " || last -x -F 2>/dev/null | head -25"
     " || echo '(wtmp 不可读或 last 未安装)'", 8),
    ("journal 启动列表（含上次 boot 起止时间）",
     "journalctl --list-boots --no-pager 2>/dev/null | tail -10"
     " || echo '(journalctl 不可用或 journal 未持久化)'", 8),
    ("上次启动日志尾部（是否戛然而止）",
     "journalctl -b -1 --no-pager 2>/dev/null | tail -40"
     " || echo '(无上次启动日志：journal 未持久化，或本次是首次开机)'", 10),
    ("上次启动错误级日志",
     "journalctl -b -1 -p err --no-pager 2>/dev/null | tail -40"
     " || true", 10),
    ("上次启动内核异常关键词",
     "journalctl -b -1 -k --no-pager 2>/dev/null | grep -iE"
     " 'segfault|oops|panic|mce|edac|hardware error|i/o error|ext4-fs error"
     "|blocked for more|lockup|hung_task|watchdog' | tail -25"
     " || echo '(上次启动内核无匹配异常关键词)'", 12),
    ("OOM 记录（journal 全历史）",
     "journalctl -k --no-pager 2>/dev/null | grep -iE"
     " 'out of memory|oom-kill|killed process|invoked oom' | tail -20"
     " || echo '(journal 无 OOM 记录)'", 15),
    ("OOM 记录（syslog/kern.log/messages 兜底）",
     "zgrep -ihE 'out of memory|oom-kill|killed process|invoked oom'"
     " /var/log/kern.log /var/log/kern.log.* /var/log/syslog /var/log/syslog.*"
     " /var/log/messages /var/log/messages.* 2>/dev/null | tail -15"
     " || echo '(syslog/kern.log/messages 无 OOM 记录或文件不存在)'", 15),
    ("MCE/EDAC/RAS 硬件错误记录",
     "journalctl --no-pager 2>/dev/null | grep -iE"
     " 'machine check|hardware error|ras report|edac.*(error|fail)' | tail -15"
     " || echo '(无 MCE/EDAC/RAS 记录，或平台无 ECC/不支持上报)'", 15),
    ("非正常关机痕迹（journald/文件系统脏标记）",
     "journalctl -b 0 -k --no-pager 2>/dev/null | grep -iE"
     " 'corrupted or uncleanly|not properly unmounted|recovering journal"
     "|dirty bit' | tail -15"
     " || echo '(本次启动无脏标记类日志)'", 10),
    ("pstore 内核崩溃残留",
     "ls -la /sys/fs/pstore/ 2>&1"
     " || echo '(pstore 不可用)'", 5),
    ("crash/core dump 信息",
     "ls -la /var/crash 2>&1; ls -la /var/lib/systemd/coredump 2>&1 | head -10;"
     " coredumpctl list --no-pager 2>&1 | tail -12;"
     " cat /proc/sys/kernel/core_pattern 2>/dev/null", 8),
    ("lockup/panic 内核开关现状",
     "for f in softlockup_panic hardlockup_panic panic_on_oops panic; do"
     " echo \"$f=$(cat /proc/sys/kernel/$f 2>/dev/null)\"; done", 5),
    ("内存与 swap 现状",
     "free -h; swapon --show; head -4 /proc/meminfo", 5),
    ("磁盘空间与文件系统错误",
     "df -h | head -6;"
     " journalctl -b 0 -k --no-pager 2>/dev/null | grep -iE"
     " 'i/o error|ext4-fs error|xfs.*(error|corrupt)|btrfs error"
     "|read-only' | tail -15"
     " || echo '(无文件系统错误日志)'", 10),
)

FORENSIC_CMD_TIMEOUT = 5      # 单条命令默认超时（秒）
# 单条默认限 5 秒：某条命令挂死会拖住整个取证进度，超时即标记失败跳到下一条
SESSION_TAIL_LINES = 200      # 会话日志截取行数
CONN_LOG_ENTRIES = 30         # 连接日志截取条数


def get_forensic_commands(crash_mode: bool = False):
    """按模式返回取证命令组（2 元组常规组 / 3 元组死机组），返回原始元组不拷贝"""
    return CRASH_FORENSIC_COMMANDS if crash_mode else FORENSIC_COMMANDS


def _iter_commands(commands):
    """命令组归一化迭代：兼容 (标题, 命令) 与 (标题, 命令, 超时) 两种元组

    常规组保持 2 元组不动（老调用方/文档/测试都以 2 元组断言），死机组
    逐条带超时。迭代时统一补默认超时，上层循环就不用写两套解包逻辑。
    """
    for item in commands:
        if len(item) == 3:
            title, cmd, timeout = item
        else:
            title, cmd = item
            timeout = FORENSIC_CMD_TIMEOUT
        yield title, cmd, timeout

# ─── AI 智能分析配置（厂商注册表见 core/ai_providers.py） ──────────────────────────────────────
AI_REQUEST_TIMEOUT = 120         # AI 请求超时（秒）
# 两个截断上限是成本闸门：dmesg/top 动辄上万行，原样送模型会让单次分析
# 的 token 费用不可控，也容易把关键结论挤出上下文窗口。
AI_CMD_OUTPUT_MAX_CHARS = 2000   # 单条命令输出送 AI 的最大字符数
AI_EVIDENCE_MAX_CHARS = 12000    # 证据文本总长度上限（控制 token 成本）

_AI_SYSTEM_PROMPT = (
    "你是一名资深 Linux 运维与嵌入式设备故障诊断专家。"
    "用户将提供一台远程设备（斯诺克球桌智能终端）的取证数据，"
    "包括内核日志错误、系统错误日志（journalctl/syslog）、失败的服务、"
    "磁盘/内存/CPU 状态等。请完成：\n"
    "1. 发现的问题：按严重程度排序列出，每条注明证据来源（哪段日志/哪个指标）\n"
    "2. 可能原因：针对每个问题给出最可能的原因\n"
    "3. 处理建议：给出可操作的排查/修复步骤（命令行优先）\n"
    "若数据中没有明显异常，请明确说明系统状态正常。"
    "用中文回答，Markdown 格式，简洁清晰，不要大段复述原始日志。"
)

# 死机取证专用：与常规诊断不同，重点在「重建时间线 + 排除法归因」，
# 而不是罗列当前系统的问题清单。
_AI_CRASH_SYSTEM_PROMPT = (
    "你是一名资深 Linux 死机/异常停止根因分析专家。"
    "用户将提供一台远程设备（斯诺克球桌智能终端）刚被重启后的取证数据，"
    "包括启动/关机历史、上次启动日志尾部、非正常关机痕迹、OOM/MCE/内核"
    "死锁/进程崩溃/文件系统错误记录、内存与 swap 现状，以及一份本地规则"
    "引擎给出的初步结论。请完成：\n"
    "1. 时间线：最近一次启动时间、上次异常停止的大致时间点（引用日志最后"
    "时间戳）、间隔时长\n"
    "2. 关键证据：支持「异常停止」判断的证据（逐条注明来源），以及能排除的"
    "候选原因（如 OOM、kernel panic、文件系统错误——明确说「未见 X 记录」）\n"
    "3. 根因判断：给出最可能的 1~3 个候选并排序，说明理由\n"
    "4. 下一步建议：只读/低侵入的取证与验证步骤（memtest、SMART、kdump、"
    "内核参数等），不要建议立即执行破坏性操作\n"
    "若数据不支持异常停止的结论，请明确说明并给出口径。"
    "用中文回答，Markdown 格式，先结论后展开，不要大段复述原始日志。"
)

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
    """取证报告目录：{app_dir}/logs/forensic（不存在则创建）

    附带闭环清理：保留 90 天内且不超过 200 个报告，防止无限增长占满磁盘。
    """
    path = os.path.join(get_app_dir(), "logs", "forensic")
    os.makedirs(path, exist_ok=True)
    cleanup_log_dir(path, max_files=200, max_age_days=90, suffix='.md')
    return path


# ─── 本地数据查询（经 table_db 双后端 API，SELECT only） ───────────────

def lookup_table_info(snk: str, host: str) -> dict:
    """从 billiard_tables 反查球桌信息

    匹配优先级：snk_code 精确 → remark 含 snk → remark 含 host。
    返回 {"name","roomName","onlineStatusName","snk_code","remark"}；
    查不到或库不可用返回空 dict。

    经 table_db.get_table_info_by_snk_or_host 路由双后端（主模式读
    MySQL / 本地读 SQLite），不向 UI 暴露裸连接。
    """
    try:
        from database import table_db
        return table_db.get_table_info_by_snk_or_host(snk, host)
    except Exception:
        return {}


def lookup_kd_status(table_id: str, snk: str) -> dict:
    """查 kd_status 该设备最新分区的关键字段

    优先按球桌号 table_id（TRIM 匹配），未命中再按 snk 当 device_code 查；
    均取 file_path 倒序第一条（该设备最近一次上报）。
    返回字段 dict（含 file_path）；查不到返回空 dict。

    经 table_db.query_latest_kd_full 路由双后端（SELECT only）。
    """
    try:
        from database import table_db
        return table_db.query_latest_kd_full(
            table_id, str(snk or "").strip())
    except Exception:
        return {}


# ─── 日志采集 ────────────────────────────────────────────────────────────

def read_session_tail(path: str, n: int = SESSION_TAIL_LINES) -> str:
    """读取会话日志文件最近 n 行；文件不存在/读取失败返回空串。

    用 errors='replace' 而不是严格解码：设备端日志可能混入 GBK 片段或被
    截断在半个多字节字符上，严格模式会抛 UnicodeDecodeError，让整份取证
    报告因为几行乱码而生成失败。
    """
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

    轮转文件按 (.3, .2, .1, 当前) 排列是日志的年龄顺序，反着读会把
    "最近 30 条"拼成跨越几周的老记录。归并缩进行是因为连接异常通常带多行
    traceback，只留首行等于丢掉最关键的报错位置。
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
    # 先按条截取再拼接：逐行截断会把一条多行记录的中间截断，报告里剩下
    # 半截 traceback，读起来比没有更误导。
    tail = entries[-limit:]
    return "".join("".join(e) for e in tail).rstrip()


# ─── AI 智能分析（后台线程调用，失败不影响报告生成） ──────────────

def _load_forensic_settings() -> dict:
    """读取配置门面合并视图（AI 分析相关配置，敏感字段解密），失败时返回空字典"""
    try:
        from core import app_settings
        return app_settings.get_merged()
    except Exception:
        return {}


def build_ai_evidence(cmd_results: list, extra: str = "") -> str:
    """拼装送 AI 的取证证据文本：仅取执行成功的命令输出，
    单条截断到 AI_CMD_OUTPUT_MAX_CHARS，总量截断到 AI_EVIDENCE_MAX_CHARS。

    失败命令不进证据：它们的内容是「执行超时」这类本模块自己的错误文案，
    送进模型只会被当成设备异常来分析，反而污染结论。
    extra 追加在末尾（死机模式下放规则引擎的初步结论，让 AI 校验而非
    从零推断）；同样受总量上限约束。
    """
    parts = []
    for title, cmd, ok, output in cmd_results:
        if not ok:
            continue
        text = (output or "").strip()
        if len(text) > AI_CMD_OUTPUT_MAX_CHARS:
            text = text[:AI_CMD_OUTPUT_MAX_CHARS] + "\n...(输出过长已截断)"
        parts.append(f"### {title}\n$ {cmd}\n{text if text else '(无输出)'}")
    if extra.strip():
        parts.append(extra.strip())
    evidence = "\n\n".join(parts)
    if len(evidence) > AI_EVIDENCE_MAX_CHARS:
        evidence = evidence[:AI_EVIDENCE_MAX_CHARS] + "\n...(证据过长已截断)"
    return evidence


# ─── 死机证据确定性分析（纯函数，可离线 mock 自测） ──────────────────────
# 与 AI 分析互补：规则引擎给出可复现的「硬结论」（脏标记/崩溃标记/OOM 等
# 签名逐条对得上原始输出），AI 负责叙事化的时间线与根因排序。规则先行也
# 是给 AI 兜底——无 Key/无网时报告仍有一份可读的初步判定。

# 签名规则：(正则, 严重度, 发现标题, 说明)。匹配只发生在执行成功的命令
# 输出上；正则全部按行匹配，None 严重度分 high/medium/info。
_CRASH_SIGNATURES = (
    (r"corrupted or uncleanly shut down", "high",
     "journal 文件未干净关闭", "journald 检测到上次停机为非正常关机"),
    (r"not properly unmounted|recovering journal|dirty bit", "high",
     "文件系统存在未正常卸载脏标记", "上次停机未走正常卸载流程"),
    (r"\s-\s+crash\b", "high",
     "wtmp 记录到 crash 会话标记", "用户会话未正常注销（异常停止特征）"),
    (r"invoked oom-killer|oom-kill|killed process", "high",
     "存在 OOM killer 记录", "内存耗尽，内核杀过进程"),
    (r"soft lockup|hard lockup|hung_task|blocked for more than", "high",
     "存在内核死锁/长阻塞记录", "CPU 或任务长时间未调度"),
    (r"machine check|hardware error|ras report", "high",
     "存在 MCE/硬件错误记录", "CPU/内存硬件层报错"),
    (r"i/o error|ext4-fs error|xfs .*(error|corrupt)|btrfs error", "high",
     "存在磁盘/文件系统错误", "块设备或文件系统层报错"),
    (r"segfault|dumped core|core-dump", "medium",
     "存在进程段错误/核心转储记录", "应用层崩溃（未必整机死机，需结合时间线）"),
    (r"\.(dmesg|console)\b", "high",
     "pstore 存在内核崩溃残留文件", "上次发生过内核级崩溃并留下 pstore 记录"),
)

# lockup 检测信号（来自「lockup/panic 内核开关现状」命令的输出行）
_SYSCTL_LINE_RE = re.compile(
    r"(softlockup_panic|hardlockup_panic|panic_on_oops|panic)=(\S+)")


def _match_signatures(cmd_results: list) -> list:
    """在执行成功的命令输出上逐条匹配签名，返回 [(严重度, 标题, 证据行), ...]

    证据行取首个命中行并截断到 160 字符：报告里只需要能回查定位的一行
    摘录，全文复读交给「诊断命令输出」章节。

    否定守卫：命中关键词紧邻的前文（16 字符内）出现 无/未/no/not，或整行
    以「（无…」「(no…」开头的说明性文字，视为否定表述跳过——命令组的降级
    echo（如「(无 OOM 记录)」）和人工注记（「未见 I/O error」）不能当成
    异常证据，否则会出现「查无此错」被报告成「发现此错」的反向事故。
    """
    findings = []
    seen = set()
    for title, cmd, ok, output in cmd_results:
        if not ok:
            continue
        for line in (output or "").splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            negated = bool(re.match(r"^[（(]\s*(无|未见|未检出|no\b)", line_s))
            for pattern, sev, sig_title, _desc in _CRASH_SIGNATURES:
                m = re.search(pattern, line_s, re.IGNORECASE)
                if not m or negated:
                    continue
                prefix = line_s[max(0, m.start() - 16):m.start()]
                if re.search(r"(无|未|no|not)\s*$", prefix, re.IGNORECASE):
                    continue
                key = (sig_title, line_s[:80])
                if key in seen:
                    continue
                seen.add(key)
                evidence = line_s if len(line_s) <= 160 else line_s[:157] + "..."
                findings.append((sev, sig_title, evidence))
    return findings


def _parse_boot_timeline(cmd_results: list) -> list:
    """从命令输出解析时间线 Markdown 行（尽力而为，解析不了就给原始行）

    数据源：uptime -s（本次启动时刻，locale 无关）、journal --list-boots
    尾部行（含上次 boot 的起止时间——上次 boot 的「止」就是最后一条日志
    时间，等于异常停止时刻的上界）、wtmp last -x 行（区分 down/crash）。
    """
    lines = []
    titles = {t for t, _c, _ok, _o in cmd_results}
    for title, cmd, ok, output in cmd_results:
        if not ok or not output:
            continue
        if title.startswith("本次启动信息"):
            m = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", output)
            if m:
                lines.append(f"- 本次启动时刻（uptime -s）：{m.group(0)}")
        elif title.startswith("journal 启动列表"):
            # list-boots 行形如 " -1 Thu 2026-10-29 06:30:15 CST — Thu 2026-10-29 18:59:54 CST"
            # （索引 0 为当前 boot，无负号，正则的 -? 两边都要放行）
            boot_lines = [l.strip() for l in output.splitlines()
                          if re.match(r"\s*-?\d+\s", l)]
            for bl in boot_lines[-5:]:
                lines.append(f"- journal boot：{bl}")
        elif title.startswith("启动/关机历史"):
            # wtmp 行保留原始形态：down/crash/仍运行 的区分就在行尾
            wtmp_lines = [l.rstrip() for l in output.splitlines()
                          if l.strip() and not l.startswith("wtmp begins")]
            for wl in wtmp_lines[:10]:
                lines.append(f"- wtmp：{wl}")
    if not lines:
        lines.append("- (未能从命令输出解析出启动时间线)")
    return lines


def _parse_lockup_switches(cmd_results: list) -> dict:
    """解析 lockup/panic 内核开关输出为 {开关名: 值}"""
    result = {}
    for title, cmd, ok, output in cmd_results:
        if not ok or not title.startswith("lockup/panic"):
            continue
        for m in _SYSCTL_LINE_RE.finditer(output):
            result[m.group(1)] = m.group(2)
    return result


def analyze_crash_evidence(cmd_results: list) -> dict:
    """死机取证的确定性分析入口（纯函数）

    Args:
        cmd_results: [(标题, 命令, ok, 输出), ...]（死机组执行结果，
            结构与常规组一致，可 mock）

    Returns:
        {
          "timeline":  [Markdown 行],          # 最近启动/关机时间线
          "findings":  [(严重度, 标题, 证据摘录)],
          "verdict":   str,                    # 初步结论（先结论后依据）
          "suggestions": [str],                # 下一步建议（只读/低侵入）
        }
    规则只做「有没有」的判定，不做根因排序——排序交给 AI 分析章节，
    两层结论在报告里并排出现，读者可以互相印证。
    """
    timeline = _parse_boot_timeline(cmd_results)
    findings = _match_signatures(cmd_results)
    high = [f for f in findings if f[0] == "high"]
    sig_high = [f for f in high if f[1] != "存在进程段错误/核心转储记录"]

    # 结论组装：先判断「停机方式」，再叠加根因线索
    stop_findings = [f for f in sig_high if f[1] in (
        "journal 文件未干净关闭", "文件系统存在未正常卸载脏标记",
        "wtmp 记录到 crash 会话标记", "pstore 存在内核崩溃残留文件")]
    cause_findings = [f for f in sig_high if f not in stop_findings]

    verdict_parts = []
    if len(stop_findings) >= 2:
        verdict_parts.append(
            "上次停机判定为**异常停止**（非正常关机）——多个独立来源"
            "（journald 脏标记 / 文件系统脏标记 / wtmp crash 标记 / pstore）"
            f"中命中 {len(stop_findings)} 项。")
    elif len(stop_findings) == 1:
        verdict_parts.append(
            f"上次停机疑似异常停止——独立来源命中 1 项（{stop_findings[0][1]}），"
            "建议结合时间线上次 boot 的日志截止时间与 wtmp 记录人工复核。")
    else:
        verdict_parts.append("未发现非正常关机的硬证据"
                             "（journald/文件系统脏标记、wtmp crash 标记、"
                             "pstore 残留均未见）。")
    if cause_findings:
        titles = "、".join(sorted({f[1] for f in cause_findings}))
        verdict_parts.append(f"根因候选线索：{titles}。")
    else:
        # 排除项也是结论的一部分：说清「没查到什么」，下一个排障的人
        # 才不会把已排除的方向再做一遍。
        verdict_parts.append(
            "未检出 OOM / 内核死锁 / MCE 硬件错误 / 磁盘 I/O 与文件系统读写"
            "错误记录（非正常关机脏标记除外）；若确为异常停止，倾向于无日志"
            "输出的整机级冻结或供电事件。")
    verdict = " ".join(verdict_parts)

    # 建议：按命中情况组装，全部只读/低侵入
    suggestions = []
    switches = _parse_lockup_switches(cmd_results)
    if switches and all(v in ("0", "") for k, v in switches.items()
                        if k != "dmesg_restrict"):
        suggestions.append(
            "内核 lockup/panic 开关全为 0（检测到死锁也不会自复位，机器会冻结"
            "在原地直到人工断电）。建议维护窗口开启 softlockup_panic=1 并配置"
            " kdump/pstore，让下次死机自动复位并留下内核现场（需重启生效）。")
    if any(f[1] == "存在 OOM killer 记录" for f in findings):
        suggestions.append(
            "已检出 OOM：对照内存/swap 现状与事发时段的内存增长进程"
            "（ps/journalctl 按 PID 追踪），评估加内存或限制应用内存。")
    if any(f[1] == "存在 MCE/硬件错误记录" for f in findings):
        suggestions.append(
            "已检出 MCE/硬件错误：维护窗口安排 memtest86+ 完整轮次的内存测试。")
    if any("文件系统" in f[1] or "磁盘" in f[1] for f in findings):
        suggestions.append(
            "已检出文件系统/磁盘错误：维护窗口执行 fsck，并安装 smartmontools"
            " 查看 SMART 健康（smartctl -a）。")
    if stop_findings:
        suggestions.append(
            "保留 journal 持久化（/var/log/journal）并关注上限轮转，事发 boot"
            " 的日志是后续根因分析的主要现场。")
    if not suggestions:
        suggestions.append(
            "本次未检出异常签名：如死机复发，优先复查供电/外设（USB 摄像头、"
            "HDMI 转换器热插拔）与事发时段温度。")
    return {"timeline": timeline, "findings": findings,
            "verdict": verdict, "suggestions": suggestions}


def analyze_with_ai(evidence: str, system_prompt: str = "") -> str:
    """调用所选 AI 厂商的 OpenAI 兼容接口分析取证证据，返回分析文本（Markdown）。

    system_prompt 不传时用常规诊断提示词；死机取证传入
    _AI_CRASH_SYSTEM_PROMPT（时间线/排除法归因导向）。其余行为不变：
    厂商/Key/模型由设置面板「AI 分析」分区配置（ai_vendor / ai_api_keys /
    ai_model），兼容旧键 deepseek_api_key/deepseek_model，并支持厂商官方
    环境变量兑底。优先带思考模式参数调用；API 拒绝该参数（400）时降级为
    普通调用。失败抛 RuntimeError（调用方记录到报告，不阻断取证）。
    """
    system_prompt = system_prompt or _AI_SYSTEM_PROMPT
    settings = _load_forensic_settings()
    if not settings.get("forensic_ai_analysis", True):
        raise RuntimeError("AI 分析已在设置面板关闭（forensic_ai_analysis）")
    cfg = resolve_ai_config(settings)
    if not cfg["api_key"]:
        raise RuntimeError(
            f"未配置 {cfg['label']} API Key，"
            f"或设置环境变量 {cfg['env_key']}）")
    # openai 是可选依赖：取证主流程（命令组 + 报告）不依赖它，只有 AI 分析
    # 需要。放在函数内 import，缺包时只让这一节失败，不拖垮整个取证线程。
    try:
        from openai import OpenAI, BadRequestError
    except ImportError:
        raise RuntimeError("未安装 openai SDK（pip install openai）")
    if not evidence.strip():
        raise RuntimeError("无有效取证数据可供分析")

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"],
                    timeout=AI_REQUEST_TIMEOUT)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": evidence},
    ]
    # 先带思考参数试一次，是对"多数厂商已兼容 OpenAI 扩展字段"的下注；
    # 400 说明当前模型/网关不认这些字段，降级为最朴素的调用仍能拿到答案。
    try:
        resp = client.chat.completions.create(
            model=cfg["model"], messages=messages, stream=False,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}})
    except BadRequestError:
        # 模型不支持 reasoning_effort/thinking 参数时降级为普通调用
        resp = client.chat.completions.create(
            model=cfg["model"], messages=messages, stream=False)
    content = str(resp.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("AI 返回空内容")
    return content


# ─── 报告生成（纯函数，可离线自测） ──────────────────────────────────────

def _md_code_block(text: str) -> str:
    """包裹 Markdown 代码块；内容含 ``` 时改用四个反引号围栏防截断"""
    fence = "````" if "```" in text else "```"
    return f"{fence}\n{text.rstrip()}\n{fence}"


def build_forensic_report(meta: dict, cmd_results: list, table_info: dict,
                          kd_info: dict, session_tail: str, conn_log: str,
                          ai_analysis: str = "", ai_error: str = "",
                          ai_label: str = "") -> str:
    """汇总生成 Markdown 诊断报告文本

    Args:
        meta: {"host","port","username","server_name"} 连接元信息
        cmd_results: [(标题, 命令, ok, 输出文本), ...]
        table_info: lookup_table_info 结果（可为空）
        kd_info: lookup_kd_status 结果（可为空）
        session_tail: 会话日志尾部文本
        conn_log: 连接日志记录文本
        ai_analysis: AI 分析结果（成功时非空）
        ai_error: AI 分析未完成的原因（失败时非空）
        ai_label: AI 厂商展示名（如 DeepSeek/通义千问，用于章节标题）
    """
    # 纯函数只读入参、不碰网络与磁盘：单测可以喂构造好的 cmd_results 直接
    # 断言 Markdown 结构，不需要真机 SSH。
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

    # 关联球桌查不到时也要写一行「未关联」并说明原因：报告要发给同事看，
    # 缺行会让人以为报告漏了一段，而不是"这台设备本来就不在球桌库里"。
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
            # status 落库是数字，报告里必须带中文含义：只有 "1" 的一行没人
            # 看得懂是空闲还是使用中。
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
        # 命令原文与输出分开：排障时先看执行的到底是哪条命令、再看结果，
        # 比把两者揉在一段里更容易核对设备上实际发生了什么。
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

    # AI 智能分析（厂商名随配置变化）
    lines += [f"## 六、AI 智能分析（{ai_label or 'AI'}）", ""]
    if ai_analysis:
        lines.append(ai_analysis)
    elif ai_error:
        lines.append(f"> ⚠ 分析未完成：{ai_error}")
    else:
        lines.append("(AI 分析未启用：可在设置面板「AI 分析」页配置"
                     "厂商与 API Key)")
    lines.append("")

    return "\n".join(lines)


# 严重度展示顺序（表格按发现优先级排序用）
_SEV_ORDER = {"high": 0, "medium": 1, "info": 2}
_SEV_LABEL = {"high": "🔴 高", "medium": "🟡 中", "info": "ℹ️ 低"}


def build_crash_forensic_report(meta: dict, cmd_results: list,
                                crash_result: dict, table_info: dict,
                                kd_info: dict, session_tail: str,
                                conn_log: str, ai_analysis: str = "",
                                ai_error: str = "",
                                ai_label: str = "") -> str:
    """组装死机/异常停止取证 Markdown 报告（纯函数，可离线 mock 自测）

    Args:
        meta: {"host","port","username","server_name"} 连接元信息
        cmd_results: [(标题, 命令, ok, 输出), ...]（死机组执行结果）
        crash_result: analyze_crash_evidence 的返回值
            （timeline / findings / verdict / suggestions）
        其余参数语义与 build_forensic_report 相同

    结构与常规报告刻意同构（基本信息 → … → AI 分析），但中段换成
    「时间线 + 异常停止证据与初步结论」：死机排查先看停机方式和时间点，
    命令原始输出退到佐证位置。
    """
    now = datetime.now()
    host = str(meta.get("host") or "")
    port = meta.get("port", "")
    username = str(meta.get("username") or "")
    server_name = str(meta.get("server_name") or "")

    lines = [
        "# SSH 死机/异常停止取证报告",
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

    # 时间线
    lines += ["## 二、最近启动/关机时间线", ""]
    lines += crash_result.get("timeline") or ["- (无时间线数据)"]
    lines.append("")

    # 异常停止证据与初步结论
    lines += ["## 三、异常停止证据与初步结论", ""]
    lines.append("### 初步结论（本地规则引擎）")
    lines.append("")
    lines.append(crash_result.get("verdict") or "(规则引擎未产出结论)")
    lines.append("")
    findings = list(crash_result.get("findings") or [])
    if findings:
        findings.sort(key=lambda f: _SEV_ORDER.get(f[0], 9))
        lines += ["### 命中签名", "",
                  "| 严重度 | 发现 | 证据摘录 |", "| --- | --- | --- |"]
        for sev, title, evidence in findings:
            lines.append(f"| {_SEV_LABEL.get(sev, sev)} | {title} | {evidence} |")
    else:
        lines += ["### 命中签名", "", "(未命中任何异常签名)"]
    lines.append("")
    suggestions = crash_result.get("suggestions") or []
    if suggestions:
        lines += ["### 下一步建议（均为只读/低侵入，未执行）", ""]
        lines += [f"- {s}" for s in suggestions]
        lines.append("")

    # 诊断命令输出
    lines += ["## 四、诊断命令输出", ""]
    for idx, (title, cmd, ok, output) in enumerate(cmd_results, 1):
        lines.append(f"### 4.{idx} {title}")
        lines.append("")
        lines.append(f"$ {cmd}")
        lines.append("")
        if ok:
            lines.append(_md_code_block(output or "(无输出)"))
        else:
            lines.append(f"> ⚠ 执行失败：{output}")
        lines.append("")

    # 设备 kd 状态
    lines += ["## 五、设备 kd 状态（最新分区）", ""]
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

    # 最近会话记录 / 连接日志（网络或 SSH 断开记录的现场）
    lines += [f"## 六、最近会话记录（最近 {SESSION_TAIL_LINES} 行）", ""]
    lines.append(_md_code_block(session_tail) if session_tail
                 else "(当前会话无日志记录)")
    lines.append("")
    lines += [f"## 七、最近连接日志（该 host 最近 {CONN_LOG_ENTRIES} 条）", ""]
    lines.append(_md_code_block(conn_log) if conn_log
                 else "(未找到该 host 的连接日志)")
    lines.append("")

    # AI 智能分析
    lines += [f"## 八、AI 根因分析（{ai_label or 'AI'}）", ""]
    if ai_analysis:
        lines.append(ai_analysis)
    elif ai_error:
        lines.append(f"> ⚠ 分析未完成：{ai_error}")
    else:
        lines.append("(AI 分析未启用：可在设置面板「AI 分析」页配置"
                     "厂商与 API Key)")
    lines.append("")

    return "\n".join(lines)


# ─── 后台取证 Worker ─────────────────────────────────────────────────────

class ForensicWorker(QThread):
    """后台取证线程：逐条执行诊断命令组并汇总生成报告

    exec_command 在 paramiko 内部走独立 channel（同一 transport），
    不占用交互 shell 的 channel，用户会话不受影响。

    crash_mode=False（默认）走常规诊断组 FORENSIC_COMMANDS；
    crash_mode=True 走死机组 CRASH_FORENSIC_COMMANDS（命令级超时 +
    确定性证据分析 + 死机专用 AI 提示词），报告结构与文件名前缀均区分。
    """
    # (当前条序号, 总条数, 阶段描述)
    progress = Signal(int, int, str)
    report_ready = Signal(str)   # 报告文件路径
    failed = Signal(str)         # 致命错误（报告未能生成）
    cancelled = Signal()         # P2-3：用户主动取消（非错误，不生成报告）

    def __init__(self, client, host, port, username,
                 server_name='', session_log_path=None, parent=None,
                 crash_mode=False):
        super().__init__(parent)
        self._client = client
        self._host = host
        self._port = port
        self._username = username
        self._server_name = server_name
        self._session_log_path = session_log_path
        self._crash_mode = crash_mode
        self._commands = get_forensic_commands(crash_mode)
        # P2-3：取消标志——当前命令执行完（或超时）后停止后续命令，不生成报告
        self._cancel_event = threading.Event()

    def cancel(self):
        """请求取消取证（与 network_workers 的 _stop_flag 同构语义）"""
        self._cancel_event.set()

    # ── 命令执行 ──────────────────────────────────────────────────────

    def _exec_one(self, cmd: str, timeout: int = FORENSIC_CMD_TIMEOUT):
        """独立 channel 执行单条命令，返回 (ok, 输出文本)。

        不设置 get_pty：诊断命令没有交互需求，分配伪终端反而会让 systemctl
        等命令输出颜色控制符，污染报告里的纯文本。
        timeout 由命令组逐条给出（死机组 journalctl 全历史 grep 按代价放宽）。
        """
        try:
            stdin, stdout, stderr = self._client.exec_command(
                cmd, timeout=timeout)
            out = stdout.read().decode('utf-8', errors='replace')
            err = stderr.read().decode('utf-8', errors='replace')
            text = out
            if err.strip():
                text += ("\n[stderr]\n" + err) if text.strip() else err
            return True, text.strip()
        # 超时单独归类只为给最常见的一种情况更明确的文案；paramiko 也可能
        # 抛 SSHException，落到下面的 Exception 分支同样会标记为失败。
        except socket.timeout:
            return False, f"执行超时（>{timeout} 秒）"
        except Exception as e:
            return False, f"执行失败：{e}"

    # ── 主流程 ────────────────────────────────────────────────────────

    def run(self):
        try:
            mode_tag = '死机取证' if self._crash_mode else '一键取证'
            conn_logger.info('FORENSIC', f'开始{mode_tag}',
                             host=self._host, port=self._port, user=self._username)
            # 1. 逐条执行诊断命令组（单条失败不影响其他条）
            total = len(self._commands)
            cmd_results = []
            for idx, (title, cmd, timeout) in enumerate(
                    _iter_commands(self._commands), 1):
                # P2-3：取消检查点——命令粒度取消（不打断进行中的命令）
                if self._cancel_event.is_set():
                    conn_logger.info('FORENSIC', '取证已取消',
                                     host=self._host, port=self._port,
                                     user=self._username)
                    self.cancelled.emit()
                    return
                self.progress.emit(idx, total, title)
                ok, output = self._exec_one(cmd, timeout)
                cmd_results.append((title, cmd, ok, output))

            # 2. 反查球桌信息 / 设备 kd 状态（两种模式共用：死机报告同样
            # 需要关联设备身份，方便把报告归档到具体球桌）
            self.progress.emit(total, total, "汇总本地数据")
            # 反查顺序：先用会话别名里的 snk/球桌号，命中不了再用 host 兜底；
            # 球桌库查不到不影响报告，只是少了「关联球桌」那一行。
            snk = self._extract_snk()
            table_id = self._extract_table_id()
            table_info = lookup_table_info(snk, self._host)
            if table_info and not table_id:
                table_id = str(table_info.get("name") or "").strip()
            kd_info = lookup_kd_status(table_id, snk or
                                       str(table_info.get("snk_code") or ""))

            # 3. 会话日志尾部 + 连接日志（死机模式下兼作「网络/SSH 断开
            # 记录」的现场证据）
            session_tail = read_session_tail(self._session_log_path)
            conn_log = collect_conn_log(self._host)

            # 4. 死机模式：先跑确定性证据分析，再让 AI 基于结论复核
            crash_result = {}
            ai_evidence_extra = ""
            ai_system_prompt = _AI_SYSTEM_PROMPT
            if self._crash_mode:
                self.progress.emit(total, total, "分析死机证据")
                crash_result = analyze_crash_evidence(cmd_results)
                ai_evidence_extra = (
                    "### 本地规则引擎初步结论（请校验并在此基础上深化）\n"
                    + crash_result.get("verdict", ""))
                ai_system_prompt = _AI_CRASH_SYSTEM_PROMPT

            # 5. AI 分析（失败仅记录原因，不阻断报告生成）
            self.progress.emit(total, total, "AI 分析系统日志")
            ai_analysis = ""
            ai_error = ""
            ai_label = resolve_ai_config(_load_forensic_settings())["label"]
            try:
                ai_analysis = analyze_with_ai(
                    build_ai_evidence(cmd_results, ai_evidence_extra),
                    ai_system_prompt)
            except Exception as e:
                # 只取异常首行：AI SDK 的报错常带多行堆栈细节，全文塞进报告会淹没结论
                ai_error = (str(e).splitlines()[0] if str(e)
                            else type(e).__name__)
                conn_logger.error('FORENSIC', f'AI 分析未完成: {ai_error}',
                                  host=self._host, port=self._port,
                                  user=self._username)

            # 6. 生成报告并落盘（按模式分流到两个组装函数）
            meta = {"host": self._host, "port": self._port,
                    "username": self._username, "server_name": self._server_name}
            if self._crash_mode:
                report = build_crash_forensic_report(
                    meta, cmd_results, crash_result, table_info, kd_info,
                    session_tail, conn_log, ai_analysis, ai_error, ai_label)
            else:
                report = build_forensic_report(
                    meta, cmd_results, table_info, kd_info, session_tail,
                    conn_log, ai_analysis, ai_error, ai_label)
            path = self._save_report(report)
            conn_logger.info('FORENSIC', f'{mode_tag}报告已生成: {os.path.basename(path)}',
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
        """报告落盘 logs/forensic/{YYYYMMDD}_{HHmmss}_{host}.md，返回文件路径

        死机报告加 crash_ 前缀：两种报告同目录归档，文件名一眼可分，
        也方便按前缀检索复发设备的历次死机记录。
        """
        now = datetime.now()
        host_tag = re.sub(r'[\\/:*?"<>|\s]+', '_', str(self._host)).strip('_') or 'host'
        prefix = "crash_" if self._crash_mode else ""
        path = os.path.join(get_forensic_dir(),
                            f"{prefix}{now:%Y%m%d}_{now:%H%M%S}_{host_tag}.md")
        with open(path, 'w', encoding='utf-8') as f:
            f.write(report)
        return path
