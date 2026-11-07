# -*- coding: utf-8 -*-
"""D3 死机/异常停止取证（crash forensics）离线单测

覆盖（全部离线、无网络/无真机/无 GUI）：
- 两组命令组的只读性（段级黑名单：reboot/shutdown/rm/mkfs 等）与元组结构
- _iter_commands 归一化（2 元组补默认超时、3 元组透传）
- analyze_crash_evidence 规则引擎：真实死机证据（journald 脏标记 + wtmp
  crash + FAT 脏标记）→ 异常停止结论；OOM 命中；干净输出 → 无证据结论；
  lockup 开关全 0 → 建议 kdump/softlockup_panic
- build_crash_forensic_report 报告结构（章节/签名表/建议/AI 兜底文案）
- build_ai_evidence 的 extra 追加与截断行为（向后兼容：不传 extra 不变）
- get_forensic_commands 模式路由

UI 文件（ssh_terminal.py）依赖 qfluentwidgets/paramiko，按本仓惯例由
py_compile + 源码断言在 test_source_wiring 覆盖。
"""

import re

import pytest

# ── 直载被测模块（E:\ANACONDA 基线解释器无仓内包上下文也可运行） ──
import os
import importlib.util

_FR_PATH = os.path.join(os.path.dirname(__file__), "..",
                        "windows", "remote_session", "forensic_report.py")
_spec = importlib.util.spec_from_file_location("fr_under_test", _FR_PATH)
fr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fr)


# ==================== 命令组只读性与结构 ====================

# 段首黑名单：命令按 ; && || | 切段后，任何一段的首词命中即视为危险写操作。
# systemctl/service 单独处理：--failed/status 等查询子命令是只读的，
# blanket 禁掉会误伤既有常规组的 systemctl --failed。
_FORBIDDEN_HEADS = {
    "reboot", "shutdown", "halt", "poweroff", "init", "rm", "rmdir", "mkfs",
    "dd", "shred", "wipefs", "fsck", "memtester", "stress", "kill", "pkill",
    "killall", "mv", "cp", "touch", "mkdir", "tee", "chmod", "chown",
}
_SYSTEMCTL_READONLY_SUBS = {
    "--failed", "status", "list-units", "show", "is-active", "is-enabled",
    "cat", "list-boots", "--no-pager",
}
_REDIRECT_RE = re.compile(r"(?<![2>])>\s*(?!/dev/null)|>>")


def _segment_words(cmd: str):
    """按 shell 分隔符切段，返回各段词列表（供 systemctl 子命令校验）"""
    segments = re.split(r"[;|&]{1,2}", cmd)
    return [seg.strip().split() for seg in segments if seg.strip()]


@pytest.mark.parametrize("group_name", ["FORENSIC_COMMANDS", "CRASH_FORENSIC_COMMANDS"])
def test_command_groups_readonly(group_name):
    """两组命令全部只读：任何段的首词不得命中写操作黑名单；
    重定向只允许丢弃输出形态（2>/dev/null、>/dev/null），禁止 >> 与写文件
    （「2>/dev/null」是把 stderr 丢弃的读安全用法，不是写操作）；
    systemctl 仅允许查询类子命令。
    """
    for item in getattr(fr, group_name):
        cmd = item[1]
        assert not _REDIRECT_RE.search(cmd), f"禁止写重定向: {cmd}"
        for words in _segment_words(cmd):
            head = words[0]
            assert head not in _FORBIDDEN_HEADS, f"禁止写操作 {head}: {cmd}"
            if head == "systemctl":
                sub = next((w for w in words[1:] if not w.startswith("-")),
                           words[1] if len(words) > 1 else "")
                assert sub in _SYSTEMCTL_READONLY_SUBS, \
                    f"systemctl 仅允许只读子命令，实为 {sub}: {cmd}"


def test_crash_commands_have_timeout():
    """死机组每条都显式带超时（3 元组），且超时为正整数"""
    for item in fr.CRASH_FORENSIC_COMMANDS:
        assert len(item) == 3, f"死机组命令应为 3 元组: {item}"
        assert isinstance(item[2], int) and item[2] > 0


def test_forensic_commands_backward_compatible():
    """常规组保持 2 元组（老调用方/文档约定不变）"""
    for item in fr.FORENSIC_COMMANDS:
        assert len(item) == 2


def test_crash_commands_cover_required_topics():
    """死机组覆盖死机排查的关键面：journal 脏标记/OOM/pstore/wtmp/lockup"""
    titles = " ".join(t for t, _c, _o in fr.CRASH_FORENSIC_COMMANDS)
    for keyword in ("关机历史", "戛然而止", "非正常关机", "OOM", "pstore",
                    "lockup", "MCE", "core dump", "swap"):
        assert keyword in titles, f"死机组缺少主题「{keyword}」"


# ==================== 命令归一化 ====================

def test_iter_commands_normalization():
    """2 元组补默认超时，3 元组透传自定义超时"""
    mixed = [("t1", "cmd1"), ("t2", "cmd2", 12)]
    result = list(fr._iter_commands(mixed))
    assert result == [("t1", "cmd1", fr.FORENSIC_CMD_TIMEOUT),
                      ("t2", "cmd2", 12)]


def test_get_forensic_commands_routing():
    """模式路由：默认常规组，crash_mode=True 返回死机组"""
    assert fr.get_forensic_commands() is fr.FORENSIC_COMMANDS
    assert fr.get_forensic_commands(False) is fr.FORENSIC_COMMANDS
    assert fr.get_forensic_commands(True) is fr.CRASH_FORENSIC_COMMANDS


# ==================== 规则引擎 analyze_crash_evidence ====================

def _mk(title, output, ok=True):
    return (title, "cmd", ok, output)


# 按今天真实排障看到的证据形态构造 mock（OptiPlex 3070 死机现场）：
# wtmp crash 标记 + journald 脏标记 + FAT 脏标记 + lockup 全 0。
REAL_CRASH_RESULTS = [
    _mk("本次启动信息（时间/时长/内核）",
        "2026年 10月 29日 星期四 19:29:43 CST\n"
        " 19:29:43 up 11 min,  3 users\n"
        "2026-10-29 19:18:34\n"
        "Linux newbv-OptiPlex-3070 6.8.0-84-generic"),
    _mk("启动/关机历史（wtmp，最近 25 条）",
        "reboot   system boot  6.8.0-84-generic Thu Oct 29 19:18:34 2026   still running\n"
        "newbv    :0           :0               Thu Oct 29 06:30:20 2026 - crash                     (12:48)\n"
        "newbv    :0           :0               Wed Oct 28 06:30:22 2026 - down                      (23:59)"),
    _mk("journal 启动列表（含上次 boot 起止时间）",
        " -1 Thu 2026-10-29 06:30:15 CST — Thu 2026-10-29 18:59:54 CST\n"
        "  0 Thu 2026-10-29 19:18:34 CST — Thu 2026-10-29 19:29:43 CST"),
    _mk("非正常关机痕迹（journald/文件系统脏标记）",
        "systemd-journald[240]: File /var/log/journal/.../system.journal corrupted or uncleanly shut down, renaming and replacing.\n"
        "FAT-fs (sda1): Volume was not properly unmounted. Some data may be corrupt. Please run fsck."),
    _mk("lockup/panic 内核开关现状",
        "softlockup_panic=0\nhardlockup_panic=0\npanic_on_oops=0\npanic=0"),
    _mk("OOM 记录（journal 全历史）", "(journal 无 OOM 记录)"),
    _mk("OOM 记录（syslog/kern.log/messages 兜底）", "(syslog/kern.log/messages 无 OOM 记录或文件不存在)"),
    _mk("MCE/EDAC/RAS 硬件错误记录", "(无 MCE/EDAC/RAS 记录，或平台无 ECC/不支持上报)"),
    _mk("pstore 内核崩溃残留", "ls: 无法访问 2>&1"),
    _mk("crash/core dump 信息", "/var/crash 2>&1\n(空)"),
    _mk("上次启动日志尾部（是否戛然而止）",
        "10月 29 18:59:51 ... 当前HDMI输出正常\n"
        "10月 29 18:59:54 ... dhcp4 (wlp2s0): state changed new lease"),
    _mk("上次启动错误级日志", "x86/cpu: SGX disabled by BIOS."),
    _mk("上次启动内核异常关键词", "(上次启动内核无匹配异常关键词)"),
    _mk("内存与 swap 现状", "              总计 7.6Gi"),
    _mk("磁盘空间与文件系统错误", "df -h | head -6"),
]


def test_analyze_real_crash_scenario():
    """真实死机现场 mock → 判定异常停止 + 命中 3 个独立脏标记来源"""
    result = fr.analyze_crash_evidence(REAL_CRASH_RESULTS)
    assert "异常停止" in result["verdict"]
    sig_titles = {f[1] for f in result["findings"]}
    assert "journal 文件未干净关闭" in sig_titles
    assert "文件系统存在未正常卸载脏标记" in sig_titles
    assert "wtmp 记录到 crash 会话标记" in sig_titles
    # 无 OOM/MCE → 排除项进入结论
    assert "未检出 OOM" in result["verdict"]
    # lockup 开关全 0 → 建议 kdump/softlockup_panic
    joined = " ".join(result["suggestions"])
    assert "softlockup_panic" in joined
    # 时间线：本次启动时刻 + journal boot 起止 + wtmp 原始行
    timeline = "\n".join(result["timeline"])
    assert "2026-10-29 19:18:34" in timeline
    assert "- journal boot：" in timeline
    assert "- wtmp：" in timeline


def test_analyze_oom_scenario():
    """OOM 命中 → 高严重度发现 + 根因候选线索 + 内存建议"""
    results = list(REAL_CRASH_RESULTS)
    results[5] = _mk("OOM 记录（journal 全历史）",
                     "10月 29 12:00:00 kernel: Out of memory: Killed process 1234 (SnookerTracking)")
    result = fr.analyze_crash_evidence(results)
    assert any(f[1] == "存在 OOM killer 记录" and f[0] == "high"
               for f in result["findings"])
    assert "OOM" in result["verdict"]
    assert any("OOM" in s or "内存" in s for s in result["suggestions"])


def test_analyze_clean_scenario():
    """干净输出 → 无非正常关机硬证据的结论 + 兜底建议"""
    clean = [_mk("本次启动信息（时间/时长/内核）", "2026-10-29 08:00:00\nup 2 days"),
             _mk("启动/关机历史（wtmp，最近 25 条）",
                 "newbv :0 Thu Oct 28 08:00:00 2026 - down (1 day)"),
             _mk("journal 启动列表（含上次 boot 起止时间）", "(journalctl 不可用)"),
             _mk("上次启动日志尾部（是否戛然而止）",
                 "systemd: Reached target Shutdown\n"
                 "systemd: Shutting down."),
             _mk("lockup/panic 内核开关现状", "softlockup_panic=1"),
             _mk("OOM 记录（journal 全历史）", "(journal 无 OOM 记录)")]
    result = fr.analyze_crash_evidence(clean)
    assert "未发现非正常关机的硬证据" in result["verdict"]
    assert result["findings"] == []
    assert result["suggestions"]


def test_analyze_skips_failed_commands():
    """执行失败的命令输出不参与签名匹配（失败文案不是设备异常证据）"""
    results = [_mk("非正常关机痕迹", "corrupted or uncleanly shut down", ok=False),
               _mk("OOM 记录（journal 全历史）", "(journal 无 OOM 记录)")]
    result = fr.analyze_crash_evidence(results)
    assert result["findings"] == []


def test_analyze_negation_guard():
    """否定表述不触发签名：降级 echo/人工注记里的「无 X 记录」不是证据"""
    results = [
        _mk("磁盘空间与文件系统错误",
            "（无 I/O error / ext4 错误日志；smartctl 未安装）"),
        _mk("OOM 记录（syslog/kern.log/messages 兜底）",
            "(syslog/kern.log/messages 无 OOM 记录或文件不存在)"),
        _mk("上次启动内核异常关键词",
            "journalctl: no I/O error entries found"),
        # 肯定句必须仍然命中
        _mk("上次启动内核异常关键词",
            "10月 29 12:00 kernel: blk_update_request: I/O error, dev sda"),
    ]
    result = fr.analyze_crash_evidence(results)
    titles = {f[1] for f in result["findings"]}
    assert "存在磁盘/文件系统错误" in titles  # 肯定句命中
    assert len([f for f in result["findings"]
                if f[1] == "存在磁盘/文件系统错误"]) == 1  # 否定句只被跳过，不重复计


def test_analyze_segfault_is_medium_not_stop_evidence():
    """进程段错误是 medium 级线索，单独出现不构成异常停止判定"""
    results = [_mk("上次启动内核异常关键词",
                   "10月 29 06:30:31 kernel: Cameraguard[2101]: segfault at 0 ip ..."),
               _mk("OOM 记录（journal 全历史）", "(journal 无 OOM 记录)"),
               _mk("非正常关机痕迹（journald/文件系统脏标记）", "(本次启动无脏标记类日志)")]
    result = fr.analyze_crash_evidence(results)
    seg = [f for f in result["findings"] if f[0] == "medium"]
    assert len(seg) == 1 and "段错误" in seg[0][1]
    assert "未发现非正常关机的硬证据" in result["verdict"]


# ==================== 报告组装 ====================

def test_build_crash_report_structure():
    """死机报告结构：章节齐备、签名表、建议、AI 兜底文案"""
    crash = fr.analyze_crash_evidence(REAL_CRASH_RESULTS)
    report = fr.build_crash_forensic_report(
        {"host": "127.0.0.1", "port": 58431, "username": "newbv",
         "server_name": "球房1号机"},
        REAL_CRASH_RESULTS, crash, {}, {}, "session tail", "conn log")
    assert "# SSH 死机/异常停止取证报告" in report
    for section in ("## 一、基本信息", "## 二、最近启动/关机时间线",
                    "## 三、异常停止证据与初步结论", "## 四、诊断命令输出",
                    "## 五、设备 kd 状态（最新分区）", "## 六、最近会话记录",
                    "## 七、最近连接日志", "## 八、AI 根因分析"):
        assert section in report, f"缺少章节 {section}"
    assert "| 严重度 | 发现 | 证据摘录 |" in report
    assert "corrupted or uncleanly" in report
    assert "- " in report  # 建议列表
    assert "AI 分析未启用" in report
    assert "球房1号机" in report


def test_build_crash_report_ai_error_branch():
    """AI 失败分支：错误原因进报告，不阻断组装"""
    crash = fr.analyze_crash_evidence([_mk("OOM 记录（journal 全历史）", "无")])
    report = fr.build_crash_forensic_report(
        {"host": "h", "port": 1, "username": "u"}, [], crash, {}, {},
        "", "", ai_error="未配置 API Key", ai_label="DeepSeek")
    assert "分析未完成：未配置 API Key" in report
    assert "DeepSeek" in report


def test_build_crash_report_empty_findings():
    """无命中签名时也生成完整结构（明确写「未命中」而不是缺章节）"""
    crash = {"timeline": ["- (无时间线数据)"], "findings": [],
             "verdict": "未发现异常", "suggestions": []}
    report = fr.build_crash_forensic_report(
        {"host": "h", "port": 1, "username": "u"}, [], crash, {}, {}, "", "")
    assert "(未命中任何异常签名)" in report
    assert "- (无时间线数据)" in report


# ==================== AI 证据拼装（extra 参数） ====================

def test_build_ai_evidence_extra_appended():
    """extra（规则引擎结论）追加在证据末尾"""
    cmds = [_mk("标题A", "输出A")]
    evidence = fr.build_ai_evidence(cmds, extra="### 规则引擎结论\n异常停止")
    assert "### 标题A" in evidence
    assert evidence.rstrip().endswith("异常停止")


def test_build_ai_evidence_without_extra_unchanged():
    """不传 extra 行为与旧版一致（向后兼容）"""
    cmds = [_mk("标题A", "输出A"), ("标题B", "cmd", False, "执行超时")]
    evidence = fr.build_ai_evidence(cmds)
    assert "### 标题A" in evidence
    assert "标题B" not in evidence  # 失败命令不进证据


def test_build_ai_evidence_total_truncation_keeps_prefix():
    """总量超限时截断且保留头部（结论性内容在前）"""
    cmds = [_mk(f"标题{i}", "x" * 100) for i in range(200)]
    evidence = fr.build_ai_evidence(cmds)
    assert len(evidence) <= fr.AI_EVIDENCE_MAX_CHARS + 50
    assert evidence.endswith("(证据过长已截断)") or evidence.startswith("###")


# ==================== UI 源码接线（py_compile + 源码断言） ====================

def test_ssh_terminal_source_wiring():
    """ssh_terminal.py 的死机模式接线：下拉菜单 + crash_mode 传参（源码断言）"""
    path = os.path.join(os.path.dirname(__file__), "..",
                        "windows", "remote_session", "ssh_terminal.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "crash_mode=True" in src, "死机取证菜单入口未接线"
    assert "crash_mode=False" in src
    assert "DropDownPushButton(\"一键取证\")" in src, "取证按钮应为下拉形态"
    assert "死机/异常停止取证" in src
    assert "RoundMenu(\"取证模式\"" in src


def test_worker_signature_crash_mode_default_false():
    """ForensicWorker 构造签名：crash_mode 默认 False（老调用不破坏）"""
    import inspect
    sig = inspect.signature(fr.ForensicWorker.__init__)
    assert sig.parameters["crash_mode"].default is False
    assert "crash_mode" in sig.parameters
